"""DeepSeek 调用封装（OpenAI 兼容 Chat Completions）。"""

from __future__ import annotations

from typing import Any

import httpx

from config import settings


class LLMError(RuntimeError):
    pass


async def chat(
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    timeout: float = 120.0,
) -> str:
    if not settings.deepseek_api_key:
        raise LLMError("未配置 DEEPSEEK_API_KEY，助手暂时无法思考")

    url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": settings.deepseek_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)

    if resp.status_code == 401:
        raise LLMError("DeepSeek API Key 无效（401）")
    if resp.status_code == 402:
        raise LLMError("DeepSeek 账户余额不足（402）")
    if resp.status_code >= 400:
        raise LLMError(f"DeepSeek 返回 {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:  # noqa: BLE001
        raise LLMError(f"DeepSeek 返回结构异常: {str(data)[:300]}") from exc


async def chat_json(messages: list[dict[str, str]], **kwargs) -> dict:
    """要求模型只回 JSON，用于结构化抽取（P1/P2 用）。"""
    import json

    text = await chat(messages, **kwargs)
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError(f"模型未返回 JSON: {text[:200]}")
    return json.loads(text[start : end + 1])
