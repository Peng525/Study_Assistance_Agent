"""Provider error normalization and reusable routing rules."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable


class LLMErrorCategory(str, Enum):
    FREE_QUOTA_EXHAUSTED = "free_quota_exhausted"
    RATE_LIMITED = "rate_limited"
    MODEL_MISCONFIGURED = "model_misconfigured"
    CREDENTIAL_AUTH = "credential_auth"
    BILLING = "billing"
    PROVIDER_TRANSIENT = "provider_transient"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    STREAM_INTERRUPTED = "stream_interrupted"
    REQUEST_INVALID = "request_invalid"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderError:
    category: LLMErrorCategory
    status_code: int | None = None
    provider_code: str | None = None
    provider_type: str | None = None
    safe_message: str = ""
    request_id: str | None = None
    user_message: str = "大模型请求失败"
    can_fallback: bool = False


@dataclass(frozen=True)
class ErrorRule:
    category: LLMErrorCategory
    user_message: str
    can_fallback: bool
    codes: frozenset[str] = frozenset()
    message_fragments: tuple[str, ...] = ()
    predicate: Callable[[int | None, str, str], bool] | None = None

    def matches(self, status: int | None, code: str, message: str) -> bool:
        normalized_code = code.casefold()
        if self.codes and normalized_code in self.codes:
            return True
        normalized_message = message.casefold()
        if self.message_fragments and any(
            fragment.casefold() in normalized_message for fragment in self.message_fragments
        ):
            return True
        return bool(self.predicate and self.predicate(status, normalized_code, normalized_message))


def _free_tier_exhausted(_status: int | None, _code: str, message: str) -> bool:
    return ("free tier" in message and "exhausted" in message) or (
        "free allocated quota exceeded" in message
    )


COMMON_ERROR_RULES: tuple[ErrorRule, ...] = (
    ErrorRule(
        LLMErrorCategory.FREE_QUOTA_EXHAUSTED,
        "当前模型免费额度已用完，正在切换备用模型",
        True,
        codes=frozenset({"allocationquota.freetieronly"}),
        predicate=_free_tier_exhausted,
    ),
    ErrorRule(
        LLMErrorCategory.MODEL_MISCONFIGURED,
        "当前模型未开通或不可用，正在切换备用模型",
        True,
        codes=frozenset(
            {
                "modelnotfound",
                "model_not_found",
                "model.accessdenied",
                "endpoint.accessdenied",
                "accessdenied.unpurchased",
            }
        ),
        message_fragments=(
            "model not found",
            "model does not exist",
            "product is not activated",
            "model is not activated",
        ),
    ),
    ErrorRule(
        LLMErrorCategory.CREDENTIAL_AUTH,
        "大模型 API Key 无效，或工作空间、Base URL 无权访问，请检查模型配置",
        False,
        codes=frozenset(
            {"invalidapikey", "invalid_api_key", "workspace.accessdenied", "app.accessdenied"}
        ),
        predicate=lambda status, _code, _message: status == 401,
    ),
    ErrorRule(
        LLMErrorCategory.BILLING,
        "大模型账户余额不足或计费状态异常，请检查当前 API 服务商账户",
        False,
        codes=frozenset(
            {"prepaidbilloverdue", "postpaidbilloverdue", "arrearage", "billing_error"}
        ),
        message_fragments=("bill overdue", "account balance", "欠费", "余额不足"),
        predicate=lambda status, _code, _message: status == 402,
    ),
    ErrorRule(
        LLMErrorCategory.RATE_LIMITED,
        "当前模型请求过于频繁，正在切换备用模型",
        True,
        codes=frozenset(
            {
                "throttling.ratequota",
                "throttling.allocationquota",
                "insufficient_quota",
                "limitrequests",
                "resourceexhausted",
            }
        ),
        message_fragments=(
            "allocated quota exceeded",
            "requests rate limit exceeded",
            "rate limit exceeded",
            "too many requests",
        ),
        predicate=lambda status, _code, _message: status == 429,
    ),
    ErrorRule(
        LLMErrorCategory.PROVIDER_TRANSIENT,
        "模型服务暂时不可用，正在切换备用模型",
        True,
        codes=frozenset({"modelunavailable", "serviceunavailable"}),
        predicate=lambda status, _code, _message: status is not None and status >= 500,
    ),
)

# Add model-specific entries here. They run before the common registry.
MODEL_ERROR_RULES: dict[str, tuple[ErrorRule, ...]] = {}


_SECRET_RE = re.compile(r"(?i)\bsk-[a-z0-9_-]{6,}\b")


def sanitize_message(value: Any, limit: int = 256) -> str:
    text = " ".join(str(value or "").split())
    text = _SECRET_RE.sub("[REDACTED]", text)
    return text[:limit]


def _parse_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {"message": payload}
        except json.JSONDecodeError:
            return {"message": payload}
    return {"message": str(payload or "")}


def classify_provider_error(
    status_code: int | None,
    payload: Any,
    *,
    model_name: str | None = None,
    request_id: str | None = None,
) -> ProviderError:
    data = _parse_payload(payload)
    nested = data.get("error") if isinstance(data.get("error"), dict) else {}
    provider_code = nested.get("code") or data.get("code")
    provider_type = nested.get("type") or data.get("type")
    message = nested.get("message") or data.get("message") or data.get("error") or ""
    payload_request_id = (
        nested.get("request_id")
        or data.get("request_id")
        or data.get("requestId")
        or data.get("RequestId")
    )
    code_text = sanitize_message(provider_code, 128)
    safe_message = sanitize_message(message)

    rules = MODEL_ERROR_RULES.get(model_name or "", ()) + COMMON_ERROR_RULES
    for rule in rules:
        if rule.matches(status_code, code_text, safe_message):
            return ProviderError(
                category=rule.category,
                status_code=status_code,
                provider_code=code_text or None,
                provider_type=sanitize_message(provider_type, 128) or None,
                safe_message=safe_message,
                request_id=sanitize_message(request_id or payload_request_id, 128) or None,
                user_message=rule.user_message,
                can_fallback=rule.can_fallback,
            )

    if status_code is not None and 400 <= status_code < 500:
        category = LLMErrorCategory.REQUEST_INVALID
        user_message = "请求参数或内容不符合模型要求，请调整后重试"
    else:
        category = LLMErrorCategory.UNKNOWN
        user_message = "大模型请求失败，请稍后重试"
    return ProviderError(
        category=category,
        status_code=status_code,
        provider_code=code_text or None,
        provider_type=sanitize_message(provider_type, 128) or None,
        safe_message=safe_message,
        request_id=sanitize_message(request_id or payload_request_id, 128) or None,
        user_message=user_message,
        can_fallback=False,
    )


def local_provider_error(category: LLMErrorCategory, message: str) -> ProviderError:
    fallback_categories = {
        LLMErrorCategory.TIMEOUT,
        LLMErrorCategory.CONNECTION,
        LLMErrorCategory.STREAM_INTERRUPTED,
        LLMErrorCategory.PROVIDER_TRANSIENT,
    }
    labels = {
        LLMErrorCategory.TIMEOUT: "模型响应超时，正在切换备用模型",
        LLMErrorCategory.CONNECTION: "无法连接模型服务，正在切换备用模型",
        LLMErrorCategory.STREAM_INTERRUPTED: "模型输出中断，正在切换备用模型",
    }
    return ProviderError(
        category=category,
        safe_message=sanitize_message(message),
        user_message=labels.get(category, "大模型请求失败，请稍后重试"),
        can_fallback=category in fallback_categories,
    )


def normalize_connectivity_error(error: ProviderError) -> ProviderError:
    """Treat an isolated model probe failure as route-level misconfiguration.

    Normal chat requests keep their stricter behavior: unknown/invalid 4xx errors do
    not fall back because the user request itself may be invalid. Connectivity tests
    use a fixed minimal prompt, so the same categories indicate that this route is
    not currently usable and the remaining models may still be tested.
    """
    if error.category not in {LLMErrorCategory.REQUEST_INVALID, LLMErrorCategory.UNKNOWN}:
        return error
    return replace(
        error,
        category=LLMErrorCategory.MODEL_MISCONFIGURED,
        user_message="模型连通性检测失败，请检查模型 ID、开通状态或接口兼容性",
        can_fallback=True,
    )
