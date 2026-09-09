"""埼玉教练 v0.2.0 自检：简写解析 / Epley / PR 判定 / 档案读写。

不依赖网络与 LLM（用桩模块替代），只验证确定性逻辑：
    python tools/test_fitness_v02.py
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
GW = HERE / "gateway"
sys.path.insert(0, str(GW))

TMP = Path(tempfile.mkdtemp(prefix="karvis_test_"))

import config  # noqa: E402

config.settings.data_dir = TMP

# ---- 桩掉 llm（避免 httpx 依赖与真实调用）----
fake_llm = types.ModuleType("llm")


class LLMError(RuntimeError):
    pass


async def _noop(*args, **kwargs):  # pragma: no cover - 不会被调用
    return ""


fake_llm.LLMError = LLMError
fake_llm.chat = _noop
fake_llm.chat_json = _noop
sys.modules["llm"] = fake_llm

import db  # noqa: E402
from agents.fitness import (  # noqa: E402
    FitnessAgent,
    epley_1rm,
    parse_shorthand,
)

FAILS: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      got={got!r}" + ("" if ok else f"\n      want={want!r}"))
    if not ok:
        FAILS.append(name)


def main() -> None:
    # ---------- 1. 简写解析 ----------
    check("解析 卧推 60x5x5", parse_shorthand("卧推 60x5x5"),
          [{"name": "卧推", "exercise_key": "卧推", "weight_kg": 60.0, "reps": 5, "sets": 5, "rir": None}])
    check("解析 卧推 60kg×5×5 RIR2",
          parse_shorthand("卧推 60kg×5×5 RIR2")[0]["rir"], 2.0)
    check("解析 深蹲100kg×5（两数字带单位）",
          parse_shorthand("深蹲 100kg×5"),
          [{"name": "深蹲", "exercise_key": "深蹲", "weight_kg": 100.0, "reps": 5, "sets": 1, "rir": None}])
    check("拒绝 卧推 5x5（无单位不误判重量）", parse_shorthand("卧推 5x5"), [])
    check("解析 还剩1个 → RIR1", parse_shorthand("卧推 62.5x5x5 最后一组还剩1个")[0]["rir"], 1.0)
    check("解析 RPE9 → RIR1", parse_shorthand("卧推 60x5x5 RPE9")[0]["rir"], 1.0)
    check("多动作分段",
          [(e["name"], e["weight_kg"], e["reps"], e["sets"]) for e in
           parse_shorthand("今天练了胸，卧推 60x5x5，哑铃飞鸟 12.5x12x3")],
          [("卧推", 60.0, 5, 5), ("哑铃飞鸟", 12.5, 12, 3)])
    check("斤 换算", parse_shorthand("卧推 120斤x5x5")[0]["weight_kg"], 60.0)
    check("纯口语不误命中", parse_shorthand("今天练了胸"), [])

    # ---------- 2. Epley ----------
    check("Epley 60x5", epley_1rm(60, 5), 70.0)
    check("Epley 100x5", epley_1rm(100, 5), 116.7)
    check("Epley >12 次不出 1RM", epley_1rm(20, 15), None)

    # ---------- 3. 档案与 PR ----------
    agent = FitnessAgent(type("C", (), {"display_name": "埼玉教练"})())

    check("档案自动生成", agent._profile_path().exists(), True)
    check("初始档案为空", agent._parse_profile(agent._load_profile()).get("训练地点"), "")
    q1 = agent._next_profile_question()
    check("首个提问是训练地点", q1, "你一般在哪练？健身房还是家里？")

    check("档案写入", agent._apply_profile_updates({"训练地点": "家里"}), ["训练地点=家里"])
    check("已填字段不覆盖", agent._apply_profile_updates({"训练地点": "健身房"}), [])
    check("写入后可解析", agent._parse_profile(agent._load_profile())["训练地点"], "家里")
    check("未知字段被忽略", agent._apply_profile_updates({"随便什么": "x"}), [])
    check("无意义值被忽略", agent._apply_profile_updates({"伤病史": "没有"}), [])
    q2 = agent._next_profile_question()
    check("提问轮换（不再问训练地点）", q2 == q1, False)

    # 第一次记录：建立基线
    r1 = agent._update_prs(
        [{"name": "杠铃卧推", "exercise_key": "卧推", "weight_kg": 57.5, "reps": 5, "sets": 5, "rir": 3}],
        "2026-08-30", 1,
    )
    check("首条记录=基线", r1[0].endswith("第一条 PR 基线。"), True)
    check("基线文案含 1RM", "67.1" in r1[0], True)

    # 第二次：未破 PR（同一重量）
    r2 = agent._update_prs(
        [{"name": "卧推", "exercise_key": "卧推", "weight_kg": 57.5, "reps": 5, "sets": 5, "rir": 3}],
        "2026-09-02", 2,
    )
    check("未破 PR 不播报", r2, [])

    # 第三次：破 PR（动作名不同但包含匹配）
    r3 = agent._update_prs(
        [{"name": "卧推", "exercise_key": "卧推", "weight_kg": 60, "reps": 5, "sets": 5, "rir": 2}],
        "2026-09-09", 3,
    )
    check("破 PR 播报", "破 PR" in r3[0] and "70.0" in r3[0], True)
    check("破 PR 含增幅", "+2.9" in r3[0], True)

    # 高次数不出 1RM
    r4 = agent._update_prs(
        [{"name": "哑铃飞鸟", "exercise_key": "飞鸟", "weight_kg": 12.5, "reps": 15, "sets": 3}],
        "2026-09-09", 4,
    )
    check(">12 次记耐力", "耐力记录" in r4[0], True)

    # ---------- 4. 历史与趋势 ----------
    hint = agent._progression_hint(
        [{"name": "杠铃卧推", "exercise_key": "卧推", "weight_kg": 60, "reps": 5, "sets": 5, "rir": 2}]
    )
    print("\n--- 历史提示块 ---\n" + hint + "\n------------------")
    check("历史包含 3 条", len(re.findall(r"(?m)^\d\d-\d\d ", hint)), 3)
    check("趋势含重量↑", "重量↑" in hint, True)
    check("趋势含 RIR 变化", "RIR 3 → 2" in hint, True)
    check("包含匹配命中（杠铃卧推→卧推）", "【卧推 · 历史】" in hint, True)

    summary = agent._pr_summary()
    print("--- PR 汇总 ---\n" + summary + "\n---------------")
    check("PR 汇总含卧推", "卧推：70.0kg" in summary, True)
    check("PR 汇总不含耐力项", "飞鸟" in summary, False)

    # ---------- 5. 收尾 ----------
    rows = db.query("SELECT COUNT(*) AS n FROM pr_record")
    check("pr_record 共 4 条", rows[0]["n"], 4)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：{FAILS}")
        sys.exit(1)
    print(f"✅ 全部通过（临时数据目录 {TMP}）")


if __name__ == "__main__":
    asyncio.run(asyncio.sleep(0))
    main()
