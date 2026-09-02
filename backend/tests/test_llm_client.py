"""模块 3.3 llm_client 单元测试（mock httpx）。"""

import json

import httpx
import pytest

from app.services.llm_client import LLMError, stream_chat
from app.services.llm_errors import LLMErrorCategory


class _FakeStreamResponse:
    def __init__(self, status_code=200, lines=None):
        self.status_code = status_code
        self._lines = lines or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b"error body"


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        return self._response


@pytest.fixture()
def patch_httpx(monkeypatch):
    holder = {"response": None}

    def _fake_client(timeout=None):
        return _FakeClient(holder["response"])

    monkeypatch.setattr("app.services.llm_client.httpx.AsyncClient", _fake_client)
    return holder


@pytest.mark.asyncio
async def test_stream_chat_yields_deltas(patch_httpx):
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "你"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "好"}}]}),
        "data: [DONE]",
    ]
    patch_httpx["response"] = _FakeStreamResponse(200, lines)
    result = []
    async for delta in stream_chat("https://x", "sk", "m", []):
        result.append(delta)
    assert result == ["你", "好"]


@pytest.mark.asyncio
async def test_stream_chat_401_error(patch_httpx):
    patch_httpx["response"] = _FakeStreamResponse(401)
    with pytest.raises(LLMError, match="API Key 无效"):
        async for _ in stream_chat("https://x", "sk", "m", []):
            pass


@pytest.mark.asyncio
async def test_stream_chat_429_error(patch_httpx):
    patch_httpx["response"] = _FakeStreamResponse(429)
    with pytest.raises(LLMError, match="频繁"):
        async for _ in stream_chat("https://x", "sk", "m", []):
            pass


@pytest.mark.asyncio
async def test_stream_chat_402_error(patch_httpx):
    patch_httpx["response"] = _FakeStreamResponse(402)
    with pytest.raises(LLMError, match="余额不足"):
        async for _ in stream_chat("https://x", "sk", "m", []):
            pass


@pytest.mark.asyncio
async def test_stream_without_done_is_interrupted(patch_httpx):
    patch_httpx["response"] = _FakeStreamResponse(
        200,
        ["data: " + json.dumps({"choices": [{"delta": {"content": "partial"}}]})],
    )
    with pytest.raises(LLMError) as captured:
        async for _ in stream_chat("https://x", "sk", "m", []):
            pass
    assert captured.value.category == LLMErrorCategory.STREAM_INTERRUPTED
    assert captured.value.can_fallback is True


@pytest.mark.asyncio
async def test_stream_error_payload_uses_shared_registry(patch_httpx):
    patch_httpx["response"] = _FakeStreamResponse(
        200,
        [
            "data: "
            + json.dumps(
                {
                    "error": {
                        "code": "AllocationQuota.FreeTierOnly",
                        "message": "The free tier has been exhausted",
                    }
                }
            )
        ],
    )
    with pytest.raises(LLMError) as captured:
        async for _ in stream_chat("https://x", "sk", "m", []):
            pass
    assert captured.value.category == LLMErrorCategory.FREE_QUOTA_EXHAUSTED
