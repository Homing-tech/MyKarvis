"""Karvis 网关：企业微信回调入口。

三个应用 = 三个独立回调路径，发到哪个应用就是哪个助手，零命令语法：
  GET/POST /wecom/life      → 阿龙管家
  GET/POST /wecom/fitness   → 埼玉教练
  GET/POST /wecom/mood      → 阿尼亚督导

流程：验签解密 → 立刻返回 success（不阻塞企微 5 秒超时）→ 后台思考 → 主动推送结果。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

# 让 gateway 目录内的模块可以直接 import（uvicorn 以 gateway.main 启动时也成立）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
from agents.fitness import FitnessAgent  # noqa: E402
from agents.life import LifeAgent  # noqa: E402
from agents.mood import MoodAgent  # noqa: E402
from config import settings  # noqa: E402
from wecom_api import wecom  # noqa: E402
from wxcrypt import WXBizMsgCrypt, WXBizMsgCryptError, parse_wework_xml  # noqa: E402

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("karvis")

app = FastAPI(title="Karvis Gateway", version="0.1.0")

AGENT_CLASSES = {"life": LifeAgent, "fitness": FitnessAgent, "mood": MoodAgent}
AGENTS: dict = {}
_SEEN_MSG: "OrderedDict[str, None]" = OrderedDict()
_SEEN_MAX = 500


@app.on_event("startup")
async def startup() -> None:
    db.init_db()
    for key, conf in settings.agents.items():
        if conf.ready:
            AGENTS[key] = AGENT_CLASSES[key](conf)
            log.info("助手已就绪: %s(%s) agentid=%s", conf.display_name, key, conf.agent_id)
        else:
            log.warning("助手 %s 配置不完整，暂不启用", key)
    if not settings.deepseek_api_key:
        log.warning("未配置 DEEPSEEK_API_KEY，助手将无法正常回复")

    if settings.enable_scheduler:
        from scheduler import start_scheduler

        start_scheduler(AGENTS)
        log.info("定时任务已启用")


# ---------------- 回调：URL 校验 ----------------
@app.get("/wecom/{agent_key}")
async def verify(agent_key: str, msg_signature: str = Query(""), timestamp: str = Query(""),
                 nonce: str = Query(""), echostr: str = Query("")):
    conf = settings.agents.get(agent_key)
    if not conf or not conf.ready:
        return PlainTextResponse(f"agent {agent_key} 未配置", status_code=404)
    try:
        crypt = WXBizMsgCrypt(conf.token, conf.aes_key, settings.corp_id)
        plain = crypt.verify_url(msg_signature, timestamp, nonce, echostr)
    except WXBizMsgCryptError as exc:
        log.error("URL 校验失败 [%s]: %s", agent_key, exc)
        return PlainTextResponse(str(exc), status_code=403)
    log.info("回调校验通过: %s", agent_key)
    return PlainTextResponse(plain)


# ---------------- 回调：接收消息 ----------------
@app.post("/wecom/{agent_key}")
async def callback(
    agent_key: str,
    request: Request,
    msg_signature: str = Query(""),
    timestamp: str = Query(""),
    nonce: str = Query(""),
):
    conf = settings.agents.get(agent_key)
    if not conf or not conf.ready:
        return PlainTextResponse("success")
    if agent_key not in AGENTS:
        return PlainTextResponse("success")

    body = await request.body()
    try:
        crypt = WXBizMsgCrypt(conf.token, conf.aes_key, settings.corp_id)
        plain_xml = crypt.decrypt_message(body, msg_signature, timestamp, nonce)
    except WXBizMsgCryptError as exc:
        log.error("解密失败 [%s]: %s", agent_key, exc)
        return PlainTextResponse("success")

    msg = parse_wework_xml(plain_xml)
    user_id = msg.get("FromUserName", "")
    msg_type = msg.get("MsgType", "")
    msg_id = msg.get("MsgId", "")
    content = (msg.get("Content") or "").strip()

    log.info("收到 [%s] from=%s type=%s len=%d", agent_key, user_id, msg_type, len(content))

    # 企微会重推，做幂等
    if msg_id:
        if msg_id in _SEEN_MSG:
            return PlainTextResponse("success")
        _SEEN_MSG[msg_id] = None
        if len(_SEEN_MSG) > _SEEN_MAX:
            _SEEN_MSG.popitem(last=False)

    if msg_type != "text":
        asyncio.create_task(
            _safe_send(agent_key, user_id, "目前只支持文字消息，语音和图片后续再开。")
        )
        return PlainTextResponse("success")

    if content:
        asyncio.create_task(_process(agent_key, user_id, content))
    return PlainTextResponse("success")


async def _process(agent_key: str, user_id: str, content: str) -> None:
    agent = AGENTS[agent_key]
    answer = await agent.reply(user_id, content)
    await _safe_send(agent_key, user_id, answer)


async def _safe_send(agent_key: str, user_id: str, text: str) -> None:
    """发送，超长自动分段（企微文本上限 2048 字节）。"""
    conf = settings.agents[agent_key]
    chunks = _split(text, 1500)
    for i, chunk in enumerate(chunks):
        if i:
            await asyncio.sleep(0.4)
        try:
            await wecom.send_text(conf, user_id, chunk)
        except Exception as exc:  # noqa: BLE001
            log.error("推送失败 [%s] to=%s: %s", agent_key, user_id, exc)


def _split(text: str, size: int) -> list[str]:
    if not text:
        return ["（空回复）"]
    out, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > size:
            if buf:
                out.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        out.append(buf)
    return out or ["（空回复）"]


# ---------------- 运维 ----------------
@app.get("/")
async def root():
    return JSONResponse(
        {
            "service": "karvis",
            "agents": {
                k: {"display": c.display_name, "ready": c.ready}
                for k, c in settings.agents.items()
            },
            "deepseek": bool(settings.deepseek_api_key),
        }
    )


@app.get("/health")
async def health():
    return PlainTextResponse("ok")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())
