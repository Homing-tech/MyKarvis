"""阿尼亚督导 · mood：CBT 五步情绪梳理。

输出格式完全由 assistants/mood/AGENTS.md 定义，代码只负责：抽取四段 → 落库 → 注入缺失项提示。
"""

from __future__ import annotations

import json

import db
import llm
from agents.base import BaseAgent

EXTRACT_PROMPT = """你是 CBT 记录抽取器。从用户的口语叙述中抽取四段式内容。

只输出 JSON：
{
  "event": "发生了什么（客观事实，可空）",
  "feeling": "情绪关键词（可空）",
  "body": "身体反应（可空）",
  "thought": "当时脑子里的想法 / 自动思维（可空）",
  "intensity": 1-10 的整数，推测不到就填 5
}

规则：
- 用户怎么说都照收，口语、碎片、一句话都可以
- 分不清「事实」和「想法」时，把主观判断的部分放进 thought
- 某一项真的没有就留空字符串，不要脑补
"""


class MoodAgent(BaseAgent):
    key = "mood"

    async def handle(self, user_id: str, text: str) -> str:
        # 1) 先处理「上次微行动完成没」的回填
        closed = await self._maybe_close_action(user_id, text)
        if closed:
            return closed

        # 2) 抽取四段
        try:
            fields = await llm.chat_json(
                [
                    {"role": "system", "content": EXTRACT_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.2,
                max_tokens=500,
            )
        except Exception:  # noqa: BLE001
            fields = {}

        row = {
            "ts": db.now_str(),
            "event": fields.get("event") or "",
            "feeling": fields.get("feeling") or "",
            "body": fields.get("body") or "",
            "thought": fields.get("thought") or "",
            "intensity": fields.get("intensity") or 5,
            "raw_input": text,
            "created_at": db.now_str(),
        }
        row_id = db.insert("mood_log", row)
        db.append_daily_memory(
            self.key,
            f"**情绪** #{row_id}\n事件：{row['event']}\n感受：{row['feeling']}\n"
            f"身体：{row['body']}\n想法：{row['thought']}\n强度：{row['intensity']}",
        )

        # 3) 缺失项提示：最多追问一项，优先 thought（CBT 的核心）
        missing = [k for k in ("event", "feeling", "body", "thought") if not row[k]]
        extra = f"已记录本次情绪 #{row_id}。抽取到的四段：\n{json.dumps(row, ensure_ascii=False)}\n\n"
        if "thought" in missing:
            extra += (
                "缺失项：想法（自动思维）。这是 CBT 最关键的一环，"
                "你必须在回答的最后，用一句话追问「当时脑子里具体在说什么」。只追问这一项。\n"
            )
        elif missing:
            extra += f"缺失项：{', '.join(missing)}。不要追问，缺什么就跳过那部分分析。\n"
        else:
            extra += "四段齐全，不要追问，直接按五步输出。\n"

        messages = self.build_messages(user_id, text, extra=extra)
        return await llm.chat(messages, temperature=0.7, max_tokens=2500)

    async def _maybe_close_action(self, user_id: str, text: str) -> str | None:
        """若用户在回应上次给出的 24 小时微行动，则回填 action_done。"""
        rows = db.query(
            "SELECT id, micro_action FROM mood_log WHERE micro_action IS NOT NULL "
            "AND micro_action != '' AND action_done IS NULL ORDER BY id DESC LIMIT 1"
        )
        if not rows:
            return None
        judge = await llm.chat_json(
            [
                {
                    "role": "system",
                    "content": "判断用户是否在回应上次的微行动。只输出 JSON：{\"done\": 1|0|null,"
                               " \"reply\": \"一句简短回应\"}。确定在做回应填 1 或 0，不是回应填 null。",
                },
                {
                    "role": "user",
                    "content": f"上次的微行动：{rows[0]['micro_action']}\n\n用户现在说：{text}",
                },
            ],
            temperature=0.1,
            max_tokens=200,
        )
        if judge.get("done") in (0, 1):
            db.update("mood_log", rows[0]["id"], {"action_done": int(judge["done"])})
            return judge.get("reply") or "记下了。"
        return None
