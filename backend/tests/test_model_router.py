"""模型链初始化、降级和持久状态测试。"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.models import ModelConfig, ModelRoute
from app.services.llm_client import LLMError
from app.services.llm_errors import classify_provider_error
from app.services.model_router import (
    DASHSCOPE_CURRENT_MODELS,
    DASHSCOPE_CURRENT_PRESET,
    RoutingOutcome,
    get_candidates,
    initialize_route_preset,
    serialize_route,
    stream_model_chain,
)


def _config(db_session):
    cfg = ModelConfig(
        name="test",
        base_url="https://example.test/v1",
        api_key_encrypted="encrypted-only",
        model_name="legacy-model",
        is_default=True,
    )
    db_session.add(cfg)
    db_session.commit()
    return cfg


def test_dashscope_preset_order_and_idempotency(db_session):
    cfg = _config(db_session)
    first = initialize_route_preset(db_session, cfg, DASHSCOPE_CURRENT_PRESET)
    second = initialize_route_preset(db_session, cfg, DASHSCOPE_CURRENT_PRESET)
    assert [route.model_name for route in first] == [item[1] for item in DASHSCOPE_CURRENT_MODELS]
    assert [route.id for route in second] == [route.id for route in first]
    assert db_session.query(ModelRoute).count() == 10
    assert cfg.api_key_encrypted == "encrypted-only"

    db_session.query(ModelRoute).delete()
    db_session.commit()
    assert get_candidates(db_session, cfg) == []


def test_no_routes_preserves_legacy_single_model(db_session):
    cfg = _config(db_session)
    candidates = get_candidates(db_session, cfg)
    assert [item.model_name for item in candidates] == ["legacy-model"]
    assert candidates[0].route is None


def test_route_timestamps_are_serialized_with_utc_offset(db_session):
    cfg = _config(db_session)
    route = ModelRoute(
        model_config_id=cfg.id,
        display_name="time",
        model_name="time",
        priority=10,
        cooldown_until=datetime(2026, 8, 23, 12, 0, 0),
        last_success_at=datetime(2026, 8, 23, 11, 0, 0),
    )
    db_session.add(route)
    db_session.commit()
    db_session.refresh(route)
    payload = serialize_route(route)
    assert payload["cooldown_until"] == "2026-08-23T12:00:00+00:00"
    assert payload["last_success_at"] == "2026-08-23T11:00:00+00:00"
    assert payload["connectivity_status"] == "passed"
    assert payload["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_partial_stream_is_reset_then_lower_model_reanswers(db_session):
    cfg = _config(db_session)
    first = ModelRoute(model_config_id=cfg.id, display_name="first", model_name="first", priority=10)
    second = ModelRoute(model_config_id=cfg.id, display_name="second", model_name="second", priority=20)
    db_session.add_all([first, second])
    db_session.commit()

    async def fake_stream(_base_url, _api_key, model_name, _messages):
        if model_name == "first":
            yield "残片"
            raise LLMError(
                classify_provider_error(
                    403,
                    {"code": "AllocationQuota.FreeTierOnly", "message": "free tier exhausted"},
                    model_name=model_name,
                )
            )
        yield "完整"
        yield "答案"

    outcome = RoutingOutcome()
    events = [
        event
        async for event in stream_model_chain(db_session, cfg, "secret", [], outcome, fake_stream)
    ]
    assert [event["type"] for event in events] == [
        "delta",
        "attempt_reset",
        "fallback",
        "delta",
        "delta",
        "done",
    ]
    assert outcome.answer == "完整答案"
    assert outcome.attempted_models == ["first", "second"]
    assert outcome.fallback_count == 1
    db_session.refresh(first)
    assert first.health_status == "quota_exhausted"


@pytest.mark.asyncio
async def test_auth_error_stops_chain_and_marks_shared_routes(db_session):
    cfg = _config(db_session)
    routes = [
        ModelRoute(model_config_id=cfg.id, display_name=str(i), model_name=f"m{i}", priority=i)
        for i in range(1, 4)
    ]
    db_session.add_all(routes)
    db_session.commit()

    async def fail_auth(_base_url, _api_key, _model_name, _messages):
        if False:
            yield ""
        raise LLMError(classify_provider_error(401, {"code": "InvalidApiKey"}))

    outcome = RoutingOutcome()
    events = [
        event
        async for event in stream_model_chain(db_session, cfg, "secret", [], outcome, fail_auth)
    ]
    assert outcome.attempted_models == ["m1"]
    assert events[-1]["type"] == "error"
    assert {route.health_status for route in db_session.query(ModelRoute).all()} == {"credential_error"}


def test_disabled_and_active_cooldown_are_skipped_but_expired_route_is_half_open(db_session):
    cfg = _config(db_session)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    disabled = ModelRoute(
        model_config_id=cfg.id,
        display_name="disabled",
        model_name="disabled",
        priority=10,
        is_enabled=False,
    )
    cooling = ModelRoute(
        model_config_id=cfg.id,
        display_name="cooling",
        model_name="cooling",
        priority=20,
        health_status="cooling",
        cooldown_until=now + timedelta(minutes=1),
    )
    half_open = ModelRoute(
        model_config_id=cfg.id,
        display_name="half-open",
        model_name="half-open",
        priority=30,
        health_status="cooling",
        cooldown_until=now - timedelta(seconds=1),
    )
    db_session.add_all([disabled, cooling, half_open])
    db_session.commit()
    assert [candidate.model_name for candidate in get_candidates(db_session, cfg)] == ["half-open"]


@pytest.mark.asyncio
async def test_successful_half_open_probe_clears_temporary_failure(db_session):
    cfg = _config(db_session)
    route = ModelRoute(
        model_config_id=cfg.id,
        display_name="recovering",
        model_name="recovering",
        priority=10,
        health_status="cooling",
        failure_streak=3,
        cooldown_until=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1),
        last_error_category="rate_limited",
        last_error_code="Throttling.RateQuota",
        last_error_message="rate limited",
    )
    db_session.add(route)
    db_session.commit()

    async def succeeds(*_args):
        yield "ok"

    outcome = RoutingOutcome()
    events = [event async for event in stream_model_chain(db_session, cfg, "secret", [], outcome, succeeds)]
    assert events[-1]["done"] is True
    db_session.refresh(route)
    assert route.health_status == "healthy"
    assert route.failure_streak == 0
    assert route.cooldown_until is None
    assert route.last_error_code is None
    assert route.last_success_at is not None


@pytest.mark.asyncio
async def test_all_transient_failures_exhaust_chain(db_session):
    cfg = _config(db_session)
    routes = [
        ModelRoute(model_config_id=cfg.id, display_name=str(i), model_name=f"m{i}", priority=i)
        for i in range(1, 3)
    ]
    db_session.add_all(routes)
    db_session.commit()

    async def unavailable(_base_url, _api_key, _model_name, _messages):
        if False:
            yield ""
        raise LLMError(classify_provider_error(503, {"code": "ModelUnavailable"}))

    outcome = RoutingOutcome()
    events = [
        event
        async for event in stream_model_chain(db_session, cfg, "secret", [], outcome, unavailable)
    ]
    assert outcome.success is False
    assert outcome.attempted_models == ["m1", "m2"]
    assert [event["type"] for event in events] == ["fallback", "error"]
    assert {route.health_status for route in db_session.query(ModelRoute).all()} == {"cooling"}
