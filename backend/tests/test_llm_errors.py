"""百炼/OpenAI 兼容错误归一化与可复用规则测试。"""

import pytest

from app.services.llm_errors import (
    LLMErrorCategory,
    classify_provider_error,
    normalize_connectivity_error,
)


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (403, {"code": "AllocationQuota.FreeTierOnly", "message": "quota"}),
        (403, {"error": {"message": "The free tier for this model has been exhausted"}}),
        (403, {"message": "Free allocated quota exceeded"}),
    ],
)
def test_free_quota_signals_are_permanent(status, payload):
    error = classify_provider_error(status, payload)
    assert error.category == LLMErrorCategory.FREE_QUOTA_EXHAUSTED
    assert error.can_fallback is True


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("Throttling.RateQuota", "rate"),
        ("Throttling.AllocationQuota", "allocation"),
        ("insufficient_quota", "quota"),
        ("", "Allocated quota exceeded"),
        ("", "Requests rate limit exceeded"),
    ],
)
def test_transient_quota_signals_are_rate_limited(code, message):
    error = classify_provider_error(429, {"error": {"code": code, "message": message}})
    assert error.category == LLMErrorCategory.RATE_LIMITED
    assert error.can_fallback is True


@pytest.mark.parametrize(
    ("status", "code", "category", "fallback"),
    [
        (404, "model_not_found", LLMErrorCategory.MODEL_MISCONFIGURED, True),
        (403, "AccessDenied.Unpurchased", LLMErrorCategory.MODEL_MISCONFIGURED, True),
        (401, "InvalidApiKey", LLMErrorCategory.CREDENTIAL_AUTH, False),
        (403, "Workspace.AccessDenied", LLMErrorCategory.CREDENTIAL_AUTH, False),
        (402, "Arrearage", LLMErrorCategory.BILLING, False),
        (503, "ModelUnavailable", LLMErrorCategory.PROVIDER_TRANSIENT, True),
        (400, "InvalidParameter", LLMErrorCategory.REQUEST_INVALID, False),
    ],
)
def test_error_categories(status, code, category, fallback):
    error = classify_provider_error(status, {"code": code, "message": "test"})
    assert error.category == category
    assert error.can_fallback is fallback


def test_product_not_activated_is_model_misconfiguration():
    error = classify_provider_error(
        400,
        {
            "code": "InvalidParameter",
            "message": "The product is not activated, please activate it first",
        },
    )

    assert error.category == LLMErrorCategory.MODEL_MISCONFIGURED
    assert error.can_fallback is True


def test_fixed_connectivity_probe_can_continue_after_generic_invalid_model_response():
    chat_error = classify_provider_error(
        400,
        {"code": "InvalidParameter", "message": "unsupported model request"},
    )
    probe_error = normalize_connectivity_error(chat_error)

    assert chat_error.category == LLMErrorCategory.REQUEST_INVALID
    assert chat_error.can_fallback is False
    assert probe_error.category == LLMErrorCategory.MODEL_MISCONFIGURED
    assert probe_error.can_fallback is True


def test_billing_error_message_is_provider_neutral():
    error = classify_provider_error(402, {"code": "billing_error", "message": "balance"})

    assert error.category == LLMErrorCategory.BILLING
    assert "当前 API 服务商账户" in error.user_message
    assert "阿里云" not in error.user_message


def test_nested_payload_and_safe_message_redaction():
    error = classify_provider_error(
        429,
        {
            "error": {
                "code": "INSUFFICIENT_QUOTA",
                "type": "quota_error",
                "message": "key sk-secret123456 exceeded",
            },
            "request_id": "req-1",
        },
    )
    assert error.provider_code == "INSUFFICIENT_QUOTA"
    assert error.provider_type == "quota_error"
    assert error.request_id == "req-1"
    assert "sk-secret" not in error.safe_message
