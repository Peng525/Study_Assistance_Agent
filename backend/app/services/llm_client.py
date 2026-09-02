"""大模型客户端：httpx 调用 OpenAI 兼容接口（阿里云百炼）。"""

import json
from typing import AsyncIterator

import httpx

from app.services.llm_errors import (
    LLMErrorCategory,
    ProviderError,
    classify_provider_error,
    local_provider_error,
)


class LLMError(Exception):
    """大模型调用错误，带用户友好文案。"""

    def __init__(self, error: ProviderError | str):
        if isinstance(error, str):
            error = local_provider_error(LLMErrorCategory.UNKNOWN, error)
        self.details = error
        self.message = error.user_message
        self.category = error.category
        self.status_code = error.status_code
        self.provider_code = error.provider_code
        self.provider_type = error.provider_type
        self.request_id = error.request_id
        self.safe_message = error.safe_message
        self.can_fallback = error.can_fallback
        super().__init__(self.message)


async def stream_chat(
    base_url: str,
    api_key: str,
    model_name: str,
    messages: list[dict],
) -> AsyncIterator[str]:
    """流式调用大模型，yield 每个增量文本片段。"""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model_name,
        "messages": messages,
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise LLMError(
                        classify_provider_error(
                            resp.status_code,
                            body,
                            model_name=model_name,
                            request_id=getattr(resp, "headers", {}).get("x-request-id"),
                        )
                    )
                saw_done = False
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        saw_done = True
                        break
                    try:
                        chunk = json.loads(data)
                        if isinstance(chunk, dict) and chunk.get("error"):
                            nested_error = chunk.get("error")
                            nested_status = (
                                nested_error.get("status")
                                if isinstance(nested_error, dict)
                                else None
                            )
                            raise LLMError(
                                classify_provider_error(
                                    nested_status,
                                    chunk,
                                    model_name=model_name,
                                    request_id=getattr(resp, "headers", {}).get("x-request-id"),
                                )
                            )
                        delta = chunk["choices"][0]["delta"].get("content")
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                if not saw_done:
                    raise LLMError(
                        local_provider_error(
                            LLMErrorCategory.STREAM_INTERRUPTED,
                            "stream ended before [DONE]",
                        )
                    )
        except LLMError:
            raise
        except httpx.TimeoutException as exc:
            raise LLMError(
                local_provider_error(LLMErrorCategory.TIMEOUT, str(exc) or "request timeout")
            ) from None
        except httpx.ConnectError as exc:
            raise LLMError(
                local_provider_error(LLMErrorCategory.CONNECTION, str(exc) or "connect error")
            ) from None
        except httpx.TransportError as exc:
            raise LLMError(
                local_provider_error(
                    LLMErrorCategory.STREAM_INTERRUPTED,
                    str(exc) or "transport interrupted",
                )
            ) from None
