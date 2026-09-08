"""企业微信连通性自检。

用法：
    python tools/check_wecom.py                                  # 只验证三个应用的 access_token
    python tools/check_wecom.py --agent life --user ZhangSan --send "链路测试"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "gateway"))

from config import settings  # noqa: E402
from wecom_api import wecom  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", default="life", choices=list(settings.agents))
    parser.add_argument("--user", default="")
    parser.add_argument("--send", default="")
    args = parser.parse_args()

    for key, conf in settings.agents.items():
        if not conf.ready:
            print(f"[跳过] {conf.display_name}({key}) 未配置")
            continue
        try:
            token = await wecom.get_token(conf.secret)
            print(f"[OK] {conf.display_name}({key}) agentid={conf.agent_id} token={token[:12]}…")
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {conf.display_name}({key}) -> {exc}")

    if args.send:
        conf = settings.agents[args.agent]
        user = args.user or settings.owner_user_id
        if not user:
            print("!! 需要 --user 指定 UserID，或在 .env 配 KARVIS_OWNER_USERID")
            return
        try:
            await wecom.send_text(conf, user, args.send)
            print(f"[OK] 已向 {user} 发送：{args.send}")
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] 发送失败 -> {exc}")


if __name__ == "__main__":
    asyncio.run(main())
