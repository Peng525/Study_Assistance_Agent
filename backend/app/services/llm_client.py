"""大模型客户端：httpx 调用 OpenAI 兼容接口（阿里云百炼）。"""

import json
from typing import AsyncIterator

import httpx


class LLMError(Exception):
    """大模型调用错误，带用户友好文案。"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _friendly_error(status_code: int, body: str) -> str:
    if status_code == 401 or status_code == 403:
        return "大模型 API Key 无效，请联系管理员检查配置"
    if status_code == 429:
        return "请求过于频繁，请稍后再试"
    if status_code == 402:
        return "大模型账户余额不足，请联系管理员充值"
    if status_code >= 500:
        return "大模型服务暂时不可用，请稍后重试"
    return f"大模型调用失败（{status_code}）"


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
                    body = (await resp.aread()).decode("utf-8", errors="ignore")
                    raise LLMError(_friendly_error(resp.status_code, body))
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"].get("content")
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except httpx.TimeoutException:
            raise LLMError("请求超时，请检查网络后重试") from None
        except httpx.ConnectError:
            raise LLMError("无法连接大模型服务，请检查网络") from None
