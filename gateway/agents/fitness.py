"""埼玉教练 · fitness：训练记录 + 次天感受闭环。"""

from __future__ import annotations

import json

import db
import llm
from agents.base import BaseAgent

PLAN_PROMPT = """你是训练日志抽取器。判断用户这条消息的类型并抽取结构化内容。

只输出 JSON：
{
  "type": "workout" | "feedback" | "query" | "chat",
  "plan": "今日训练部位/计划，如「胸+三头」，没有则空",
  "exercises": [{"name": "动作名", "weight": "重量，可空", "reps": "次数，可空", "sets": "组数，可空", "rpe": "主观强度，可空"}],
  "cardio": "有氧部分描述，可空",
  "soreness": "酸痛部位与程度，可空",
  "pain": "疼痛描述（区别于酸痛），可空",
  "sleep": "睡眠情况，可空",
  "energy": "精力状况，可空",
  "query": "查询关键词"
}

判定规则：
- 描述了做了什么动作/组数/重量/练了哪 → workout
- 描述练完之后的身体感受、酸痛、睡眠、状态 → feedback
- 在问历史记录 → query
- 其余 → chat
- 用户只说「今天练了胸」也算 workout，exercises 可以为空数组
"""


class FitnessAgent(BaseAgent):
    key = "fitness"

    async def handle(self, user_id: str, text: str) -> str:
        plan = await self._plan(text)
        kind = plan.get("type", "chat")

        if kind == "workout":
            row_id = db.insert(
                "workout_log",
                {
                    "log_date": db.today(),
                    "plan": plan.get("plan") or "",
                    "exercises": json.dumps(plan.get("exercises") or [], ensure_ascii=False),
                    "cardio": plan.get("cardio") or "",
                    "raw_input": text,
                    "created_at": db.now_str(),
                },
            )
            db.append_daily_memory(
                self.key,
                f"**训练** #{row_id} {plan.get('plan') or ''}\n{text}",
            )
            extra = (
                f"已记录训练 #{row_id}（{db.today()}）。\n"
                + self._last_feedback_context(plan.get("plan") or "")
                + "\n先确认记录，然后给一条针对下次训练的具体建议。不要长篇大论。"
            )
            messages = self.build_messages(user_id, text, extra=extra)
            return await llm.chat(messages, temperature=0.6)

        if kind == "feedback":
            target = self._pending_feedback_date()
            db.insert(
                "workout_feedback",
                {
                    "for_date": target or db.today(),
                    "report_date": db.today(),
                    "soreness": plan.get("soreness") or "",
                    "pain": plan.get("pain") or "",
                    "sleep": plan.get("sleep") or "",
                    "energy": plan.get("energy") or "",
                    "raw_input": text,
                    "created_at": db.now_str(),
                },
            )
            db.append_daily_memory(self.key, f"**感受**（对应 {target or db.today()}）\n{text}")
            extra = f"已记录次天感受，对应训练日 {target or db.today()}。\n"
            if plan.get("pain"):
                extra += "注意：用户提到了疼痛（非酸痛），如果描述尖锐/持续/关节部位，明确建议就医，不要硬扛。\n"
            extra += "给出一句话反馈：这次感受说明了什么，下一次怎么调。"
            messages = self.build_messages(user_id, text, extra=extra)
            return await llm.chat(messages, temperature=0.6)

        if kind == "query":
            rows = db.query(
                "SELECT * FROM workout_log ORDER BY id DESC LIMIT 10"
            )
            context = "最近训练记录：\n" + json.dumps(
                [{"date": r["log_date"], "plan": r["plan"], "ex": r["exercises"]} for r in rows],
                ensure_ascii=False,
            )
            messages = self.build_messages(
                user_id, text, extra=context + "\n\n只基于真实数据回答。"
            )
            return await llm.chat(messages, temperature=0.4)

        messages = self.build_messages(user_id, text)
        return await llm.chat(messages)

    # ---------- 辅助 ----------
    def _pending_feedback_date(self) -> str | None:
        """找最近一个还没填过感受的训练日。"""
        rows = db.query(
            "SELECT DISTINCT log_date FROM workout_log ORDER BY log_date DESC LIMIT 10"
        )
        for r in rows:
            d = r["log_date"]
            exists = db.query(
                "SELECT id FROM workout_feedback WHERE for_date=? LIMIT 1", (d,)
            )
            if not exists:
                return d
        return None

    def _last_feedback_context(self, plan: str) -> str:
        """次天闭环：把同部位上次训练后的真实感受带进上下文。"""
        if not plan:
            return ""
        rows = db.query(
            "SELECT w.log_date, w.plan, f.soreness, f.pain, f.sleep, f.energy "
            "FROM workout_log w JOIN workout_feedback f ON f.for_date=w.log_date "
            "ORDER BY w.log_date DESC LIMIT 5"
        )
        hits = [r for r in rows if r["plan"] and r["plan"][:2] in plan]
        if not hits:
            return ""
        r = hits[0]
        return (
            f"历史闭环：{r['log_date']} 练「{r['plan']}」后，你的次天感受是——"
            f"酸痛：{r['soreness'] or '未提'}；睡眠：{r['sleep'] or '未提'}；精力：{r['energy'] or '未提'}"
            + (f"；疼痛：{r['pain']}" if r["pain"] else "")
        )

    async def _plan(self, text: str) -> dict:
        messages = [
            {"role": "system", "content": PLAN_PROMPT},
            {"role": "user", "content": f"当前时间：{db.now_str()}\n\n用户说：{text}"},
        ]
        try:
            return await llm.chat_json(messages, temperature=0.2, max_tokens=900)
        except Exception:  # noqa: BLE001
            return {"type": "chat"}
