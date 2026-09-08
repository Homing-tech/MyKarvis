"""助手基类：人格加载、上下文拼装、会话记录。"""

from __future__ import annotations

from pathlib import Path

import db
import llm
from config import BASE_DIR, AgentConf

ASSISTANTS_DIR = BASE_DIR / "assistants"


class BaseAgent:
    key = "base"
    display_name = "助手"

    def __init__(self, conf: AgentConf):
        self.conf = conf
        self.display_name = conf.display_name

    # ---------- 人格 ----------
    def system_prompt(self) -> str:
        parts = []
        shared = ASSISTANTS_DIR / "_shared" / "profile.md"
        if shared.exists():
            parts.append(shared.read_text(encoding="utf-8"))
        own = ASSISTANTS_DIR / self.key / "AGENTS.md"
        if own.exists():
            parts.append(own.read_text(encoding="utf-8"))
        if not parts:
            parts.append(f"你是{self.display_name}，一位可靠的个人助手。回答简洁、直接、结论先行。")
        parts.append(
            f"\n\n---\n当前时间：{db.now_str()}　助手标识：{self.key}　显示名：{self.display_name}\n"
            "你在企业微信里被单独对话，用户不需要任何命令前缀。"
        )
        return "\n\n".join(parts)

    # ---------- 上下文 ----------
    def build_messages(self, user_id: str, user_text: str, extra: str = "") -> list[dict]:
        messages = [{"role": "system", "content": self.system_prompt()}]
        if extra:
            messages.append({"role": "system", "content": extra})
        messages.extend(db.recent_chat(self.key, user_id))
        messages.append({"role": "user", "content": user_text})
        return messages

    # ---------- 主入口 ----------
    async def handle(self, user_id: str, text: str) -> str:
        """子类可覆盖；默认走通用对话。"""
        messages = self.build_messages(user_id, text)
        return await llm.chat(messages)

    async def reply(self, user_id: str, text: str) -> str:
        db.append_chat(self.key, user_id, "user", text)
        try:
            answer = await self.handle(user_id, text)
        except llm.LLMError as exc:
            answer = f"（{self.display_name}暂时无法思考）\n{exc}"
        except Exception as exc:  # noqa: BLE001
            answer = f"（{self.display_name}处理出错了）\n{type(exc).__name__}: {exc}"
        db.append_chat(self.key, user_id, "assistant", answer)
        return answer

    # ---------- 供定时任务的主动发言 ----------
    async def proactive(self, user_id: str, instruction: str) -> str:
        messages = [{"role": "system", "content": self.system_prompt()}]
        messages.append({"role": "user", "content": instruction})
        try:
            out = await llm.chat(messages)
        except Exception as exc:  # noqa: BLE001
            out = f"（主动任务失败）{exc}"
        db.append_chat(self.key, user_id, "assistant", out)
        return out
