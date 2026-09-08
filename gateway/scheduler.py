"""定时主动推送（P3）。ENABLE_SCHEDULER=1 且配置了 KARVIS_OWNER_USERID 才生效。"""

from __future__ import annotations

import logging

log = logging.getLogger("karvis.scheduler")


def start_scheduler(agents: dict) -> None:
    from datetime import datetime

    import db
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from config import settings
    from wecom_api import wecom

    if not settings.owner_user_id:
        log.warning("未配置 KARVIS_OWNER_USERID，定时任务不会推送")
        return

    sched = AsyncIOScheduler(timezone=settings.timezone)
    owner = settings.owner_user_id

    async def push(agent_key: str, instruction: str):
        agent = agents.get(agent_key)
        if not agent:
            return
        try:
            text = await agent.proactive(owner, instruction)
        except Exception as exc:  # noqa: BLE001
            log.error("定时任务异常 [%s]: %s", agent_key, exc)
            return
        try:
            await wecom.send_text(agent.conf, owner, _clip(text))
        except Exception as exc:  # noqa: BLE001
            log.error("定时推送失败 [%s]: %s", agent_key, exc)

    def _clip(text: str, size: int = 1500) -> str:
        return text if len(text) <= size else text[: size - 20] + "\n…（已截断）"

    # 每天 08:30 · 阿龙管家：今日待办
    async def daily_todo():
        rows = db.query(
            "SELECT * FROM inbox_item WHERE status!='done' AND due_date!='' "
            "AND due_date<=? ORDER BY due_date LIMIT 20",
            (datetime.now().strftime("%Y-%m-%d"),),
        )
        if not rows:
            return  # 没有到期项就不打扰
        listing = "\n".join(f"#{r['id']} {r['title']}（截止 {r['due_date']}）" for r in rows)
        await push(
            "life",
            f"早上好。以下是今天到期和已逾期的待办：\n{listing}\n\n"
            "用三五句话提醒我，按紧急度排序，别写成表格。",
        )

    # 每天 21:30 · 埼玉教练：连续 3 天没练才提醒
    async def fitness_nudge():
        from datetime import timedelta

        recent = db.query(
            "SELECT DISTINCT log_date FROM workout_log ORDER BY log_date DESC LIMIT 1"
        )
        if recent:
            last = datetime.strptime(recent[0]["log_date"], "%Y-%m-%d")
            if (datetime.now() - last) < timedelta(days=3):
                return
        await push(
            "fitness",
            "我已经连续三天没有训练记录了。用一句话提醒我，不要说教，不要内疚感。",
        )

    # 周日 10:00 · 阿龙管家：周度整理
    async def weekly_review():
        rows = db.query("SELECT * FROM inbox_item WHERE status='open' ORDER BY id DESC LIMIT 40")
        if not rows:
            return
        listing = "\n".join(f"#{r['id']} [{r['kind']}] {r['title']}" for r in rows)
        await push(
            "life",
            f"周末整理时间。当前未处理条目：\n{listing}\n\n"
            "帮我分成三类：该继续推进的、该放弃了、该重新归类的。每类不超过三条，给出理由。",
        )

    # 周日 21:00 · 阿尼亚督导：情绪周复盘
    async def mood_weekly():
        from datetime import timedelta

        since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
        rows = db.query(
            "SELECT * FROM mood_log WHERE ts>=? ORDER BY ts", (since,)
        )
        if len(rows) < 2:
            log.info("本周情绪样本不足 2 条，跳过周复盘")
            return
        brief = "\n".join(
            f"{r['ts'][:10]} 强度{r['intensity']} | 事件：{r['event']} | 想法：{r['thought']}"
            for r in rows
        )
        await push(
            "mood",
            f"本周情绪记录（{len(rows)} 条）：\n{brief}\n\n"
            "按你的人格文件里的「周复盘结构」输出。样本量小于 3 条的地方必须标注「样本少，待观察」。",
        )

    sched.add_job(daily_todo, "cron", hour=8, minute=30, id="daily_todo")
    sched.add_job(fitness_nudge, "cron", hour=21, minute=30, id="fitness_nudge")
    sched.add_job(weekly_review, "cron", day_of_week="sun", hour=10, minute=0, id="weekly_review")
    sched.add_job(mood_weekly, "cron", day_of_week="sun", hour=21, minute=0, id="mood_weekly")
    sched.start()
    log.info("已注册 4 个定时任务")
