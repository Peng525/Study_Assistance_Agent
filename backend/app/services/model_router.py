"""Priority model routing with persistent health and cooldown state."""

from __future__ import annotations

import asyncio
from time import perf_counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Callable

from sqlalchemy.orm import Session

from app.models.models import ModelConfig, ModelRoute, SystemSetting
from app.services.llm_client import LLMError
from app.services.llm_errors import LLMErrorCategory, ProviderError, local_provider_error


DASHSCOPE_CURRENT_PRESET = "dashscope-current"
DASHSCOPE_CURRENT_MODELS: tuple[tuple[str, str], ...] = (
    ("Qwen 3.8 Max", "qwen3.8-max"),
    ("DeepSeek V4 Pro", "deepseek-v4-pro"),
    ("Qwen 3.7 Plus", "qwen3.7-plus"),
    ("智谱 GLM 5.3", "ZHIPU/GLM-5.3"),
    ("GLM 5.2", "glm-5.2"),
    ("Kimi K3", "kimi-k3"),
    ("MiniMax M3", "MiniMax/MiniMax-M3"),
    ("DeepSeek V4 Flash 0731", "deepseek-v4-flash-0731"),
    ("Qwen 3.7 Flash", "qwen3.7-flash"),
    ("Qwen Plus（兼容兜底）", "qwen-plus"),
)

MODEL_ROUTE_PRESETS: dict[str, tuple[tuple[str, str], ...]] = {
    DASHSCOPE_CURRENT_PRESET: DASHSCOPE_CURRENT_MODELS,
}

MAX_ATTEMPTS = 10
REQUEST_DEADLINE_SECONDS = 120
ROUTING_MODE_KEY_PREFIX = "model_routes_v1:"


@dataclass(frozen=True)
class ModelCandidate:
    model_name: str
    route: ModelRoute | None = None


@dataclass
class RoutingOutcome:
    success: bool = False
    answer: str = ""
    model_name: str | None = None
    route_id: int | None = None
    attempted_models: list[str] = field(default_factory=list)
    fallback_count: int = 0
    last_error: ProviderError | None = None
    rejection_message: str | None = None
    request_started_at: float = field(default_factory=perf_counter)
    thinking_ms: int | None = None


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def _connectivity_status(route: ModelRoute) -> str:
    last_failure = route.last_failure_at
    last_success = route.last_success_at
    if last_failure is not None and (
        last_success is None
        or last_failure.replace(tzinfo=None) >= last_success.replace(tzinfo=None)
    ):
        return "failed"
    if last_success is not None:
        return "passed"
    return "untested"


def serialize_route(route: ModelRoute) -> dict:
    return {
        "id": route.id,
        "model_config_id": route.model_config_id,
        "display_name": route.display_name,
        "model_name": route.model_name,
        "priority": route.priority,
        "is_enabled": route.is_enabled,
        "health_status": route.health_status,
        "connectivity_status": _connectivity_status(route),
        "failure_streak": route.failure_streak,
        "cooldown_until": _iso_utc(route.cooldown_until),
        "last_error_category": route.last_error_category,
        "last_error_code": route.last_error_code,
        "last_error_request_id": route.last_error_request_id,
        "last_error_message": route.last_error_message,
        "last_failure_at": _iso_utc(route.last_failure_at),
        "last_success_at": _iso_utc(route.last_success_at),
        "created_at": _iso_utc(route.created_at),
        "updated_at": _iso_utc(route.updated_at),
    }


def list_routes(db: Session, config_id: int) -> list[ModelRoute]:
    return (
        db.query(ModelRoute)
        .filter(ModelRoute.model_config_id == config_id)
        .order_by(ModelRoute.priority.asc(), ModelRoute.id.asc())
        .all()
    )


def routing_mode_key(config_id: int) -> str:
    return f"{ROUTING_MODE_KEY_PREFIX}{config_id}"


def ensure_route_chain_marker(db: Session, config_id: int) -> None:
    key = routing_mode_key(config_id)
    if db.get(SystemSetting, key) is None:
        db.add(SystemSetting(key=key, value="enabled"))


def route_chain_initialized(db: Session, config_id: int) -> bool:
    return db.get(SystemSetting, routing_mode_key(config_id)) is not None


def initialize_route_preset(
    db: Session,
    config: ModelConfig,
    preset_id: str,
) -> list[ModelRoute]:
    """Import an explicitly selected model preset into one API config."""
    preset = MODEL_ROUTE_PRESETS.get(preset_id)
    if preset is None:
        raise ValueError(f"unknown model route preset: {preset_id}")
    existing = list_routes(db, config.id)
    ensure_route_chain_marker(db, config.id)
    if existing:
        db.commit()
        return existing
    for position, (display_name, model_name) in enumerate(preset, start=1):
        db.add(
            ModelRoute(
                model_config_id=config.id,
                display_name=display_name,
                model_name=model_name,
                priority=position * 10,
                is_enabled=True,
                health_status="healthy",
            )
        )
    db.commit()
    return list_routes(db, config.id)


def _utcnow() -> datetime:
    # SQLite stores DateTime without an offset; consistently persist UTC-naive values.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_cooling(route: ModelRoute, now: datetime) -> bool:
    if not route.cooldown_until:
        return False
    cooldown = route.cooldown_until.replace(tzinfo=None)
    return cooldown > now


def get_candidates(db: Session, config: ModelConfig) -> list[ModelCandidate]:
    routes = list_routes(db, config.id)
    if not routes:
        if route_chain_initialized(db, config.id):
            return []
        return [ModelCandidate(model_name=config.model_name)]
    now = _utcnow()
    blocked = {"quota_exhausted", "misconfigured", "credential_error"}
    return [
        ModelCandidate(model_name=route.model_name, route=route)
        for route in routes
        if route.is_enabled
        and route.health_status not in blocked
        and not _is_cooling(route, now)
    ][:MAX_ATTEMPTS]


def reset_route_state(route: ModelRoute) -> None:
    route.health_status = "healthy"
    route.failure_streak = 0
    route.cooldown_until = None
    route.last_error_category = None
    route.last_error_code = None
    route.last_error_request_id = None
    route.last_error_message = None
    route.last_failure_at = None
    route.last_success_at = None


def reset_routes_for_config(db: Session, config_id: int) -> None:
    for route in list_routes(db, config_id):
        reset_route_state(route)
    db.commit()


def record_route_success(db: Session, route: ModelRoute | None) -> None:
    if route is None:
        return
    reset_route_state(route)
    route.last_success_at = _utcnow()
    db.commit()


def record_route_failure(
    db: Session,
    config_id: int,
    route: ModelRoute | None,
    error: ProviderError,
) -> bool:
    """Persist safe failure state; return whether the chain may continue."""
    if route is None:
        return error.can_fallback

    now = _utcnow()
    route.failure_streak += 1
    route.last_failure_at = now
    route.last_error_category = error.category.value
    route.last_error_code = error.provider_code
    route.last_error_request_id = error.request_id
    route.last_error_message = error.safe_message

    if error.category == LLMErrorCategory.FREE_QUOTA_EXHAUSTED:
        route.health_status = "quota_exhausted"
        route.cooldown_until = None
    elif error.category == LLMErrorCategory.MODEL_MISCONFIGURED:
        route.health_status = "misconfigured"
        route.cooldown_until = None
    elif error.category in {LLMErrorCategory.CREDENTIAL_AUTH, LLMErrorCategory.BILLING}:
        for sibling in list_routes(db, config_id):
            sibling.health_status = "credential_error"
            sibling.cooldown_until = None
            sibling.last_error_category = error.category.value
            sibling.last_error_code = error.provider_code
            sibling.last_error_request_id = error.request_id
            sibling.last_error_message = error.safe_message
            sibling.last_failure_at = now
        db.commit()
        return False
    elif error.category == LLMErrorCategory.RATE_LIMITED:
        route.health_status = "cooling"
        delay = min(60 * (2 ** (route.failure_streak - 1)), 15 * 60)
        route.cooldown_until = now + timedelta(seconds=delay)
    elif error.category in {
        LLMErrorCategory.PROVIDER_TRANSIENT,
        LLMErrorCategory.TIMEOUT,
        LLMErrorCategory.CONNECTION,
        LLMErrorCategory.STREAM_INTERRUPTED,
    }:
        route.health_status = "cooling"
        delay = min(30 * (2 ** (route.failure_streak - 1)), 10 * 60)
        route.cooldown_until = now + timedelta(seconds=delay)
    else:
        # Invalid/unknown requests are not route health failures.
        route.failure_streak = max(0, route.failure_streak - 1)

    db.commit()
    return error.can_fallback


async def stream_model_chain(
    db: Session,
    config: ModelConfig,
    api_key: str,
    messages: list[dict],
    outcome: RoutingOutcome,
    stream_fn: Callable[..., AsyncIterator[str]],
    *,
    deadline_seconds: float | None = REQUEST_DEADLINE_SECONDS,
) -> AsyncIterator[dict]:
    candidates = get_candidates(db, config)
    if not candidates:
        outcome.rejection_message = "当前没有可用模型，请在管理后台重置、启用或配置模型链"
        yield {
            "type": "error",
            "error": outcome.rejection_message,
        }
        return

    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds if deadline_seconds is not None else None
    for index, candidate in enumerate(candidates, start=1):
        outcome.attempted_models.append(candidate.model_name)
        fragments: list[str] = []
        try:
            remaining = deadline - loop.time() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise asyncio.TimeoutError
            async with asyncio.timeout(remaining):
                async for delta in stream_fn(
                    config.base_url,
                    api_key,
                    candidate.model_name,
                    messages,
                ):
                    first_delta = not fragments
                    fragments.append(delta)
                    event = {
                        "type": "delta",
                        "delta": delta,
                        "model_name": candidate.model_name,
                        "attempt": index,
                        "fallback_count": index - 1,
                    }
                    if first_delta:
                        outcome.thinking_ms = max(
                            0, int((perf_counter() - outcome.request_started_at) * 1000)
                        )
                        event["thinking_ms"] = outcome.thinking_ms
                    yield event
            record_route_success(db, candidate.route)
            outcome.success = True
            outcome.answer = "".join(fragments)
            outcome.model_name = candidate.model_name
            outcome.route_id = candidate.route.id if candidate.route else None
            outcome.fallback_count = index - 1
            yield {
                "type": "done",
                "done": True,
                "model_name": candidate.model_name,
                "attempt": index,
                "fallback_count": index - 1,
                "thinking_ms": outcome.thinking_ms,
            }
            return
        except asyncio.TimeoutError:
            llm_error = LLMError(
                local_provider_error(LLMErrorCategory.TIMEOUT, "routing deadline exceeded")
            )
        except LLMError as exc:
            llm_error = exc

        outcome.last_error = llm_error.details
        can_continue = record_route_failure(
            db,
            config.id,
            candidate.route,
            llm_error.details,
        )
        if fragments:
            outcome.thinking_ms = None
            yield {
                "type": "attempt_reset",
                "attempt_reset": True,
                "model_name": candidate.model_name,
                "attempt": index,
                "fallback_count": index - 1,
            }
        has_next = (
            can_continue
            and index < len(candidates)
            and (deadline is None or loop.time() < deadline)
        )
        if has_next:
            next_model = candidates[index].model_name
            outcome.fallback_count = index
            yield {
                "type": "fallback",
                "fallback": True,
                "from_model": candidate.model_name,
                "to_model": next_model,
                "model_name": next_model,
                "attempt": index + 1,
                "fallback_count": index,
                "reason": llm_error.category.value,
                "notice": llm_error.message,
            }
            continue
        yield {
            "type": "error",
            "error": llm_error.message,
            "model_name": candidate.model_name,
            "attempt": index,
            "fallback_count": index - 1,
        }
        return
