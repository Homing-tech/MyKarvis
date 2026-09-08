"""阿龙管家 · life：收集箱 / 待办 / 笔记，随手丢进去，定期提醒。"""

from __future__ import annotations

import json

import db
import llm
from agents.base import BaseAgent

INTENT_PROMPT = """你是信息抽取器。判断用户这条消息属于哪一类，并抽取结构化内容。

只输出 JSON，不要解释：
{
  "intent": "capture" | "query" | "chat",
  "items": [
    {"kind": "todo" | "idea" | "note" | "link" | "person",
     "title": "一句话概要，不超过 20 字",
     "detail": "原文要点",
     "tags": "逗号分隔标签，可空",
     "due_date": "YYYY-MM-DD 或 空字符串"}
  ],
  "query": "如果用户在查询，写出检索关键词"
}

判定规则：
- 用户在陈述、记录、交代事情、贴链接、说「记得…」「要…」「有个想法」→ capture
- 用户在问「我记过什么」「有没有…」「待办有哪些」→ query
- 其余（打招呼、闲聊、问知识）→ chat
- 一句话里有多件事就拆成多个 item
- due_date 只有明确提到日期或「明天/后天/下周X」时才填，按当前日期换算
"""


class LifeAgent(BaseAgent):
    key = "life"

    async def handle(self, user_id: str, text: str) -> str:
        if text.strip() in {"清空上下文", "重置对话", "清空记录"}:
            n = db.clear_chat(self.key, user_id)
            return f"已清空我们的对话上下文（{n} 条）。收集箱里的条目不受影响。"

        plan = await self._plan(text)
        intent = plan.get("intent", "chat")

        if intent == "capture" and plan.get("items"):
            saved = []
            for it in plan["items"]:
                row = {
                    "ts": db.now_str(),
                    "kind": it.get("kind") or "note",
                    "title": it.get("title") or text[:20],
                    "detail": it.get("detail") or text,
                    "tags": it.get("tags") or "",
                    "status": "open",
                    "due_date": it.get("due_date") or "",
                    "last_reminded": "",
                    "raw_input": text,
                    "created_at": db.now_str(),
                }
                row_id = db.insert("inbox_item", row)
                saved.append(f"#{row_id} [{row['kind']}] {row['title']}")
            db.append_daily_memory(self.key, f"**收集**\n" + "\n".join(saved))

            context = "已存入收集箱：\n" + "\n".join(saved)
            messages = self.build_messages(
                user_id,
                text,
                extra=context + "\n\n用一两句话确认你记下了什么。不要复述全文，不要说「已为您记录」这类客服腔。",
            )
            return await llm.chat(messages, temperature=0.5)

        if intent == "query":
            kw = plan.get("query") or text
            rows = db.query(
                "SELECT * FROM inbox_item WHERE status!='done' AND "
                "(title LIKE ? OR detail LIKE ? OR tags LIKE ?) "
                "ORDER BY id DESC LIMIT 20",
                (f"%{kw}%", f"%{kw}%", f"%{kw}%"),
            )
            if not rows:
                rows = db.query(
                    "SELECT * FROM inbox_item WHERE status!='done' ORDER BY id DESC LIMIT 20"
                )
            context = "收集箱当前内容：\n" + json.dumps(
                [
                    {
                        "id": r["id"],
                        "kind": r["kind"],
                        "title": r["title"],
                        "due": r["due_date"],
                        "status": r["status"],
                    }
                    for r in rows
                ],
                ensure_ascii=False,
            )
            messages = self.build_messages(
                user_id,
                text,
                extra=context + "\n\n只基于上面这些真实数据回答，不要编造条目。",
            )
            return await llm.chat(messages, temperature=0.4)

        messages = self.build_messages(user_id, text)
        return await llm.chat(messages)

    async def _plan(self, text: str) -> dict:
        messages = [
            {"role": "system", "content": INTENT_PROMPT},
            {"role": "user", "content": f"当前时间：{db.now_str()}\n\n用户说：{text}"},
        ]
        try:
            return await llm.chat_json(messages, temperature=0.2, max_tokens=800)
        except Exception:  # noqa: BLE001
            return {"intent": "chat"}
