"""模拟企业微信向网关发一条加密消息，用于无企微后台时的联调。

用法：
    python tools/mock_callback.py life "记一下明天要交周报"
    python tools/mock_callback.py life "你好" http://49.235.107.213:9000
    python tools/mock_callback.py life --verify        # 只做 URL 校验（GET）
"""

from __future__ import annotations

import random
import string
import sys
import time
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "gateway"))

from config import settings  # noqa: E402
from wxcrypt import WXBizMsgCrypt  # noqa: E402


def rand(n: int = 10) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def main() -> None:
    args = [a for a in sys.argv[1:]]
    if not args:
        print(__doc__)
        return
    target = args[0]
    rest = args[1:]

    conf = settings.agents[target]
    if not conf.ready:
        print(f"[FAIL] {target} 配置不完整")
        return

    url = "http://127.0.0.1:9000"
    for a in rest:
        if a.startswith("http"):
            url = a.rstrip("/")
    text = next((a for a in rest if not a.startswith("http") and a != "--verify"), "你好")

    crypt = WXBizMsgCrypt(conf.token, conf.aes_key, settings.corp_id)
    ts = str(int(time.time()))
    nonce = rand()

    if "--verify" in rest:
        echostr = crypt._encrypt("echo-test")
        sig = crypt._sign(ts, nonce, echostr)
        r = httpx.get(
            f"{url}/wecom/{target}",
            params={"msg_signature": sig, "timestamp": ts, "nonce": nonce, "echostr": echostr},
            timeout=10,
        )
        print(f"GET  {r.status_code}  {r.text!r}   （期望 200 + 'echo-test'）")
        return

    plain = (
        f"<xml><ToUserName><![CDATA[{settings.corp_id}]]></ToUserName>"
        "<FromUserName><![CDATA[mockuser]]></FromUserName>"
        f"<CreateTime>{ts}</CreateTime><MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{text}]]></Content><MsgId>{int(time.time() * 1000)}</MsgId>"
        f"<AgentID><![CDATA[{conf.agent_id}]]></AgentID></xml>"
    )
    enc = crypt._encrypt(plain)
    sig = crypt._sign(ts, nonce, enc)
    post = (
        f"<xml><ToUserName><![CDATA[{settings.corp_id}]]></ToUserName>"
        f"<Encrypt><![CDATA[{enc}]]></Encrypt>"
        f"<AgentID><![CDATA[{conf.agent_id}]]></AgentID></xml>"
    ).encode()

    r = httpx.post(
        f"{url}/wecom/{target}",
        params={"msg_signature": sig, "timestamp": ts, "nonce": nonce},
        content=post,
        timeout=15,
    )
    print(f"POST {r.status_code}  {r.text!r}   （期望 200 + 'success'）")
    print("若返回 200 success，说明验签解密与路由已通；助手的回复会异步推送到你的企微。")


if __name__ == "__main__":
    main()
