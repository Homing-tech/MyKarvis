"""本地/服务器自检：不经过企业微信，直接验证 DB + DeepSeek + 三个助手。

用法：
    python tools/selftest.py                          # 三个助手各跑一条示例
    python tools/selftest.py --agent life --text "记一下明天要交周报"
    python tools/selftest.py --agent mood --text "今天被老板当众质疑，很烦"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "gateway"))

import db  # noqa: E402
from agents.fitness import FitnessAgent  # noqa: E402
from agents.life import LifeAgent  # noqa: E402
from agents.mood import MoodAgent  # noqa: E402
from config import settings  # noqa: E402

SAMPLES = {
    "life": "记一下，下周三之前要把洛克新进留存的归因报告发给老板，另外看到一篇讲回流用户分群的文章，先存着",
    "fitness": "今天练了胸，卧推 60kg 5组5次，上斜哑铃 22kg 3组10次，最后跑了 15 分钟",
    "mood": "会上被当众质疑数据口径，我当时脑子一片空白，胸口发闷，觉得他们肯定觉得我不专业",
}

CLASSES = {"life": LifeAgent, "fitness": FitnessAgent, "mood": MoodAgent}


async def run(agent_key: str, text: str) -> None:
    conf = settings.agents[agent_key]
    agent = CLASSES[agent_key](conf)
    print(f"\n{'=' * 60}\n[{conf.display_name} / {agent_key}] 输入：{text}\n{'-' * 60}")
    answer = await agent.reply("selftest", text)
    print(answer)
    print(f"{'=' * 60}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=list(CLASSES))
    parser.add_argument("--text", default="")
    args = parser.parse_args()

    db.init_db()
    if not settings.deepseek_api_key:
        print("!! 未配置 DEEPSEEK_API_KEY，助手只会返回错误信息")

    if args.agent:
        await run(args.agent, args.text or SAMPLES[args.agent])
    else:
        for key in ("life", "fitness", "mood"):
            await run(key, SAMPLES[key])

    print(f"\n数据库：{settings.data_dir / 'karvis.db'}")
    print(f"记忆目录：{settings.data_dir / 'memory'}")


if __name__ == "__main__":
    asyncio.run(main())
