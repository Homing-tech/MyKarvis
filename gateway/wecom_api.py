"""企业微信主动调用：access_token 缓存 + 发送应用消息。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from config import settings

_QYAPI = "https://qyapi.weixin.qq.com/cgi-bin"


@dataclass
class _Token:
    value: str = ""
    expire_at: float = 0.0


class WeComClient:
    """按 CorpSecret 维度缓存 access_token（企微有效期 7200s，提前 300s 续期）。"""

    def __init__(self) -> None:
        self._tokens: dict[str, _Token] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get_token(self, secret: str) -> str:
        token = self._tokens.setdefault(secret, _Token())
        if token.value and time.time() < token.expire_at:
            return token.value

        async with self._lock(secret):
            if token.value and time.time() < token.expire_at:
                return token.value
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{_QYAPI}/gettoken",
                    params={"corpid": settings.corp_id, "corpsecret": secret},
                )
                data = resp.json()
            errcode = data.get("errcode", 0)
            if errcode != 0:
                raise RuntimeError(
                    f"获取 access_token 失败: errcode={errcode} errmsg={data.get('errmsg')}"
                    "（常见原因：Secret 填错，或服务器 IP 未加入企业可信 IP）"
                )
            token.value = data["access_token"]
            token.expire_at = time.time() + data.get("expires_in", 7200) - 300
            return token.value

    async def send_text(self, agent: "object", user_id: str, content: str) -> None:
        """发送文本应用消息。agent 需具备 agent_id / secret 属性。"""
        token = await self.get_token(agent.secret)  # type: ignore[attr-defined]
        payload = {
            "touser": user_id,
            "msgtype": "text",
            "agentid": int(agent.agent_id),  # type: ignore[attr-defined]
            "text": {"content": content},
            "safe": 0,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_QYAPI}/message/send", params={"access_token": token}, json=payload
            )
            data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(
                f"发送消息失败: errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
                "（常见原因：用户不在该应用的可见范围，或 UserID 不正确）"
            )

    async def send_markdown(self, agent: "object", user_id: str, content: str) -> None:
        token = await self.get_token(agent.secret)  # type: ignore[attr-defined]
        payload = {
            "touser": user_id,
            "msgtype": "markdown",
            "agentid": int(agent.agent_id),  # type: ignore[attr-defined]
            "markdown": {"content": content},
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_QYAPI}/message/send", params={"access_token": token}, json=payload
            )
            data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(
                f"发送消息失败: errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
            )


wecom = WeComClient()
