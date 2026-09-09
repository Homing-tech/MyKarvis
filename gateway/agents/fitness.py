"""埼玉教练 · fitness：训练记录 + 次天感受闭环 + PR/1RM 追踪 + 渐进超负荷建议。

设计原则：
- 代码只负责**取数与计算**（Epley、历史查询、档案读写）
- 「下次上多重」这类判断写进 assistants/fitness/AGENTS.md，改训练哲学不用发版
- 训练档案放 data 层，不随代码更新被覆盖
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import db
import llm
from agents.base import BaseAgent
from config import settings

PLAN_PROMPT = """你是训练日志抽取器。判断用户这条消息的类型并抽取结构化内容。

只输出 JSON：
{
  "type": "workout" | "feedback" | "query" | "chat",
  "plan": "今日训练部位/计划，如「胸+三头」，没有则空",
  "exercises": [
    {"name": "动作名",
     "exercise_key": "标准短名，如 卧推/深蹲/硬拉/引体/划船/肩推，用于长期匹配同一动作",
     "weight_kg": 数字或 null,
     "reps": 数字或 null,
     "sets": 数字或 null,
     "rir": 数字或 null}
  ],
  "cardio": "有氧部分描述，可空",
  "soreness": "酸痛部位与程度，可空",
  "pain": "疼痛描述（区别于酸痛），可空",
  "sleep": "睡眠情况，可空",
  "energy": "精力状况，可空",
  "query": "查询关键词",
  "profile_updates": {"字段名": "值"}
}

判定规则：
- 描述了做了什么动作/组数/重量/练了哪 → workout
- 描述练完之后的身体感受、酸痛、睡眠、状态 → feedback
- 在问历史记录/成绩 → query
- 其余 → chat
- 用户只说「今天练了胸」也算 workout，exercises 可以为空数组

数值字段规则：
- 用户给了重量/次数/组数就填数字，没给填 null，**不要猜**。
- rir = 还剩几次力竭。「还剩 1 个」「还能做 2 次」「RIR2」都算；RPE9 记为 rir=1。没提填 null。
- exercise_key 用通用标准名（去掉「今天」「杠铃」等临时修饰），保证同一动作长期一致。

profile_updates 规则（很重要）：
- 只有用户**明确说出**这些信息时才填，**绝不从训练内容推断**。
- 可选字段只有：主目标 / 次要目标 / 训练年限 / 训练地点 / 可用器械 / 每周频率 / 伤病史 / 禁忌动作 / 体重 / 主要动作当前水平
- 用户说「没有」「不清楚」就不要填。
- 没有可填的就给空对象 {}。
"""

# ---------------- 训练档案 ----------------

PROFILE_TEMPLATE = """# 训练档案

> 由埼玉教练在对话里逐步补全。空白代表未知，教练不会替你填、也不会脑补。
> 想改就直接编辑这个文件，或发「我的档案」让教练念给你听。

## 目标
主目标：
次要目标：
训练年限：

## 条件
训练地点：
可用器械：
每周频率：

## 限制
伤病史：
禁忌动作：

## 基线
体重：
主要动作当前水平：
"""

# 提问顺序：先问影响排动作的（地点/器械/频率），再问目标，再问伤病，最后基线
PROFILE_ORDER = [
    "训练地点",
    "可用器械",
    "每周频率",
    "主目标",
    "训练年限",
    "次要目标",
    "伤病史",
    "禁忌动作",
    "体重",
    "主要动作当前水平",
]

PROFILE_QUESTIONS = {
    "训练地点": "你一般在哪练？健身房还是家里？",
    "可用器械": "有哪些器械可以用？（杠铃／哑铃／器械区／只有自重）",
    "每周频率": "一周大概能练几次？",
    "主目标": "你现在训练主要图什么？增肌、减脂、力量，还是保持状态？",
    "训练年限": "断续练了多久了？",
    "次要目标": "除了主目标，还有想兼顾的吗？没有就说没有。",
    "伤病史": "有没有旧伤或现在不舒服的地方？（腰／肩／膝）没有就直说没有，我就不反复问了。",
    "禁忌动作": "有没有哪个动作做起来不得劲、想避开的？",
    "体重": "现在体重多少？",
    "主要动作当前水平": "卧推／深蹲／硬拉现在大概什么水平？说个大概就行。",
}


class FitnessAgent(BaseAgent):
    key = "fitness"

    # ==================== 主入口 ====================
    async def handle(self, user_id: str, text: str) -> str:
        parsed = parse_shorthand(text)
        if parsed:
            plan = {"type": "workout", "plan": "", "exercises": parsed}
        else:
            plan = await self._plan(text)

        filled = self._apply_profile_updates(plan.get("profile_updates"))
        kind = plan.get("type", "chat")
        q_line = "" if filled else self._onboarding_line()

        if kind == "workout":
            exercises = plan.get("exercises") or []
            row_id = db.insert(
                "workout_log",
                {
                    "log_date": db.today(),
                    "plan": plan.get("plan") or "",
                    "exercises": json.dumps(exercises, ensure_ascii=False),
                    "cardio": plan.get("cardio") or "",
                    "raw_input": text,
                    "created_at": db.now_str(),
                },
            )
            db.append_daily_memory(
                self.key,
                f"**训练** #{row_id} {plan.get('plan') or ''}\n{text}",
            )

            extra = f"已记录训练 #{row_id}（{db.today()}）。\n"
            extra += self._last_feedback_context(plan.get("plan") or "")

            pr_lines = self._update_prs(exercises, db.today(), row_id)
            if pr_lines:
                extra += (
                    "\n系统已按 Epley 公式算好的结果，**数字照抄、不要自己重算**：\n"
                    + "\n".join(f"- {x}" for x in pr_lines)
                    + "\n"
                )
            hint = self._progression_hint(exercises)
            if hint:
                extra += (
                    "\n同动作历史（用它按人格里的决策表推下次负荷）：\n" + hint + "\n"
                )
            extra += q_line
            extra += "\n先确认记录，然后给一条针对下次训练的具体建议。不要长篇大论。"
            if plan.get("pain"):
                extra += "\n注意：用户提到了疼痛（非酸痛），尖锐/持续/关节部位要明确建议就医。"

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
            extra += q_line
            extra += "给出一句话反馈：这次感受说明了什么，下一次怎么调。"
            messages = self.build_messages(user_id, text, extra=extra)
            return await llm.chat(messages, temperature=0.6)

        if kind == "query":
            rows = db.query("SELECT * FROM workout_log ORDER BY id DESC LIMIT 10")
            context = "最近训练记录：\n" + json.dumps(
                [{"date": r["log_date"], "plan": r["plan"], "ex": r["exercises"]} for r in rows],
                ensure_ascii=False,
            )
            context += "\n\n" + self._pr_summary() + "\n"
            if re.search(r"档案|我的资料|profile", text, re.I):
                context += "\n\n训练档案全文：\n" + self._load_profile() + "\n"
            messages = self.build_messages(
                user_id, text, extra=context + "\n\n只基于上面的真实数据回答，没有的就直说没有。"
            )
            return await llm.chat(messages, temperature=0.4)

        messages = self.build_messages(user_id, text, extra=q_line)
        return await llm.chat(messages)

    # ==================== 人格挂载 ====================
    def extra_persona_paths(self) -> list[Path]:
        return [self._profile_path()]

    # ==================== 训练档案 ====================
    def _profile_path(self) -> Path:
        d = settings.data_dir / "fitness"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "profile.md"
        if not p.exists():
            p.write_text(PROFILE_TEMPLATE, encoding="utf-8")
        return p

    def _state_path(self) -> Path:
        return settings.data_dir / "fitness" / "onboarding.json"

    def _load_profile(self) -> str:
        try:
            return self._profile_path().read_text(encoding="utf-8")
        except OSError:
            return PROFILE_TEMPLATE

    @staticmethod
    def _parse_profile(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in (text or "").splitlines():
            m = re.match(r"^\s*(?P<k>[^\s#：:]{2,10})\s*[：:]\s*(?P<v>.*)$", line)
            if m:
                out[m.group("k").strip()] = m.group("v").strip()
        return out

    def _set_profile_field(self, field: str, value: str) -> bool:
        p = self._profile_path()
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        for i, ln in enumerate(lines):
            if re.match(rf"^{re.escape(field)}\s*[：:]", ln):
                lines[i] = f"{field}：{value}"
                p.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return True
        return False

    def _load_state(self) -> dict:
        p = self._state_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8") or "{}")
            except (OSError, ValueError):
                return {}
        return {}

    def _save_state(self, state: dict) -> None:
        try:
            self._state_path().write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def _apply_profile_updates(self, updates) -> list[str]:
        """只填空字段，不覆盖已有内容（避免误抽取污染档案）。"""
        if not isinstance(updates, dict) or not updates:
            return []
        prof = self._parse_profile(self._load_profile())
        done = []
        for field, value in updates.items():
            field = str(field).strip()
            if field not in PROFILE_QUESTIONS:
                continue
            value = str(value).strip()
            if not value or value in {"无", "没有", "未知", "不清楚", "没说", "null", "None"}:
                continue
            if prof.get(field):
                continue
            if self._set_profile_field(field, value):
                done.append(f"{field}={value}")
        return done

    def _next_profile_question(self) -> str:
        prof = self._parse_profile(self._load_profile())
        missing = [f for f in PROFILE_ORDER if not prof.get(f)]
        if not missing:
            return ""
        state = self._load_state()
        asked = state.get("asked") or {}
        # 问得最少的优先，避免同一个问题反复骚扰
        missing.sort(key=lambda f: (asked.get(f, 0), PROFILE_ORDER.index(f)))
        field = missing[0]
        asked[field] = int(asked.get(field, 0)) + 1
        state["asked"] = asked
        self._save_state(state)
        return PROFILE_QUESTIONS.get(field, f"{field}是多少？")

    def _onboarding_line(self) -> str:
        q = self._next_profile_question()
        if not q:
            return ""
        return (
            "\n训练档案还没补全。把下面这句**原样**放在回复的最后一句（不要加引号、不要改写）："
            f"{q}\n"
        )

    # ==================== PR / 1RM ====================
    @staticmethod
    def _pr_history(key: str, limit: int = 5) -> tuple[list[dict], str]:
        """先精确匹配，再取最长包含匹配（避免「卧推」与「哑铃卧推」被拆成两条曲线）。"""
        rows = db.query(
            "SELECT * FROM pr_record WHERE exercise_key=? ORDER BY log_date DESC, id DESC LIMIT ?",
            (key, limit),
        )
        if rows:
            return rows, key
        all_keys = [
            r["exercise_key"]
            for r in db.query("SELECT DISTINCT exercise_key FROM pr_record")
            if r["exercise_key"]
        ]
        for k in sorted(all_keys, key=len, reverse=True):
            if k in key or key in k:
                rows = db.query(
                    "SELECT * FROM pr_record WHERE exercise_key=? ORDER BY log_date DESC, id DESC LIMIT ?",
                    (k, limit),
                )
                if rows:
                    return rows, k
        return [], key

    def _update_prs(self, exercises: list[dict], log_date: str, source_log_id: int) -> list[str]:
        """Epley 估算 1RM + 破 PR 判定，返回播报文案。"""
        reports: list[str] = []
        for ex in exercises or []:
            w = _num(ex.get("weight_kg"))
            reps = _num(ex.get("reps"))
            if w is None or reps is None or w <= 0 or reps <= 0:
                continue
            key = (ex.get("exercise_key") or ex.get("name") or "").strip()
            if not key:
                continue
            est = round(w * (1 + reps / 30.0), 1) if reps <= 12 else None
            rir = _num(ex.get("rir"))
            sets = _num(ex.get("sets")) or 1

            _, matched = self._pr_history(key, 1)
            prev = db.query(
                "SELECT est_1rm FROM pr_record WHERE exercise_key=? AND est_1rm IS NOT NULL "
                "ORDER BY est_1rm DESC, id DESC LIMIT 1",
                (matched,),
            )
            prev_best = prev[0]["est_1rm"] if prev else None

            db.insert(
                "pr_record",
                {
                    "exercise_key": matched,
                    "exercise_name": ex.get("name") or matched,
                    "weight_kg": round(w, 2),
                    "reps": int(reps),
                    "sets": int(sets),
                    "est_1rm": est,
                    "rir": rir,
                    "log_date": log_date,
                    "source_log_id": source_log_id,
                    "created_at": db.now_str(),
                },
            )

            if est is None:
                reports.append(
                    f"{matched} {w:g}kg×{int(reps)} —— 次数 >12，记为耐力记录，不出 1RM。"
                )
            elif prev_best is None:
                reports.append(
                    f"{matched} {w:g}kg×{int(reps)} → 估 1RM **{est}kg**，这是该动作的第一条 PR 基线。"
                )
            elif est - prev_best >= 0.5:
                reports.append(
                    f"{matched} {w:g}kg×{int(reps)} → 估 1RM **{est}kg**，破 PR"
                    f"（原 {prev_best}kg，+{round(est - prev_best, 1)}kg）。"
                )
        return reports

    def _progression_hint(self, exercises: list[dict]) -> str:
        blocks = []
        seen: set[str] = set()
        for ex in exercises or []:
            key = (ex.get("exercise_key") or ex.get("name") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            rows, matched = self._pr_history(key, 3)
            if not rows:
                continue
            lines = [f"【{matched} · 历史】"]
            for r in rows:
                est = f"→ 估 1RM {r['est_1rm']}" if r["est_1rm"] else "（>12 次，不出 1RM）"
                rir = f"RIR{r['rir']:g}" if r["rir"] is not None else "RIR 未填"
                sets = r["sets"] or 1
                lines.append(
                    f"{r['log_date'][5:]}  {r['weight_kg']:g}kg×{r['reps']}×{sets}  {rir}  {est}"
                )
            if len(rows) >= 2:
                lines.append("趋势：" + self._trend_text(rows))
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    @staticmethod
    def _trend_text(rows: list[dict]) -> str:
        new, old = rows[0], rows[-1]
        parts = []
        dw = (new["weight_kg"] or 0) - (old["weight_kg"] or 0)
        parts.append("重量↑" if dw > 0.5 else ("重量↓" if dw < -0.5 else "重量持平"))
        dr = (new["reps"] or 0) - (old["reps"] or 0)
        if dr:
            parts.append(f"次数{'↑' if dr > 0 else '↓'}{abs(dr)}")
        if new["rir"] is not None and old["rir"] is not None and new["rir"] != old["rir"]:
            parts.append(f"RIR {old['rir']:g} → {new['rir']:g}")
        if len(rows) >= 2 and abs(dw) < 0.5 and not dr:
            parts.append("（重量与次数连续无进展）")
        return "，".join(parts)

    def _pr_summary(self) -> str:
        rows = db.query("SELECT * FROM pr_record ORDER BY log_date DESC, id DESC")
        if not rows:
            return "当前 PR：（还没有任何记录，v0.2.0 上线后新记的才开始积累）"
        best: dict[str, dict] = {}
        for r in rows:
            k, e = r["exercise_key"], r["est_1rm"]
            if e is None:
                continue
            if k not in best or e > best[k]["est_1rm"]:
                best[k] = r
        if not best:
            return "当前 PR：（有记录但都超过 12 次，还没算出 1RM）"
        lines = ["当前 PR（Epley 估算 1RM，非实测）："]
        for k, r in sorted(best.items(), key=lambda x: -x[1]["est_1rm"]):
            lines.append(
                f"- {k}：{r['est_1rm']}kg（{r['log_date']}，{r['weight_kg']:g}kg×{r['reps']}）"
            )
        return "\n".join(lines)

    # ==================== 原有辅助 ====================
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


# ==================== 简写解析（确定性，不经过 LLM） ====================

# 三个数字：卧推 60x5x5 / 卧推 60kg×5×5 —— 无单位也可信
_SHORTHAND_3 = re.compile(
    r"(?P<name>[一-龥A-Za-z]{1,12}?)\s*"
    r"(?P<weight>\d+(?:\.\d+)?)\s*(?P<unit>kg|公斤|kgs|斤)?\s*"
    r"[xX×*✕]\s*(?P<reps>\d+)\s*[xX×*✕]\s*(?P<sets>\d+)"
)
# 两个数字：只有带单位才认，否则「卧推 5x5」会被误读成 5kg
_SHORTHAND_2 = re.compile(
    r"(?P<name>[一-龥A-Za-z]{1,12}?)\s*"
    r"(?P<weight>\d+(?:\.\d+)?)\s*(?P<unit>kg|公斤|kgs|斤)\s*"
    r"[xX×*✕]\s*(?P<reps>\d+)"
)


def extract_rir(text: str) -> float | None:
    if not text:
        return None
    m = re.search(r"RIR\s*(\d+(?:\.\d+)?)", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"RPE\s*(\d+(?:\.\d+)?)", text, re.I)
    if m:
        return max(0.0, round(10.0 - float(m.group(1)), 1))
    m = re.search(r"(?:还剩|还能做|还能|留|余)\s*(\d+)\s*(?:个|次|下)?", text)
    if m:
        return float(m.group(1))
    return None


def parse_shorthand(text: str) -> list[dict]:
    """解析「卧推 60x5x5 RIR2」这类简写，命中则跳过 LLM 抽取。

    返回空列表表示没命中（交给 LLM）。
    """
    if not text:
        return []
    global_rir = extract_rir(text)
    out: list[dict] = []
    for seg in re.split(r"[\n，,、;；]+", text):
        seg = seg.strip()
        if not seg:
            continue
        m = _SHORTHAND_3.search(seg) or _SHORTHAND_2.search(seg)
        if not m:
            continue
        name = m.group("name").strip()
        if not name:
            continue
        weight = float(m.group("weight"))
        if m.group("unit") == "斤":
            weight = round(weight / 2, 2)
        reps = int(m.group("reps"))
        sets = int(m.groupdict().get("sets") or 1)
        if weight <= 0 or reps <= 0 or sets <= 0:
            continue
        rir = extract_rir(seg)
        if rir is None:
            rir = global_rir
        out.append(
            {
                "name": name,
                "exercise_key": name,
                "weight_kg": weight,
                "reps": reps,
                "sets": sets,
                "rir": rir,
            }
        )
    return out


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


def epley_1rm(weight_kg: float, reps: int) -> float | None:
    """Epley 估算 1RM；>12 次不估（高次数区误差过大）。"""
    if weight_kg <= 0 or reps <= 0 or reps > 12:
        return None
    return round(weight_kg * (1 + reps / 30.0), 1)
