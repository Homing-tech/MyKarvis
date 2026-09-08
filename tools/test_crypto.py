"""企微加解密自测：用 .env 里的真实凭证做回环验证，不需要联网。

用法：python tools/test_crypto.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "gateway"))

from config import settings  # noqa: E402
from wxcrypt import WXBizMsgCrypt, parse_wework_xml  # noqa: E402

TS, NONCE = "1700000000", "abcdefg"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'OK' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        sys.exit(1)


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "life"
    conf = settings.agents[target]
    if not conf.ready:
        check(f"{target} 配置完整", False, "AgentId/Secret/Token/AESKey 有缺失")
    check(f"{conf.display_name}({target}) 凭证已加载", True, f"agentid={conf.agent_id}")

    crypt = WXBizMsgCrypt(conf.token, conf.aes_key, settings.corp_id)
    check("EncodingAESKey 长度合法", True, f"{len(crypt.key)} 字节")

    # 1) URL 校验回环
    plain_xml = (
        f"<xml><ToUserName><![CDATA[{settings.corp_id}]]></ToUserName>"
        "<FromUserName><![CDATA[sysadmin]]></FromUserName>"
        "<CreateTime>1700000000</CreateTime><MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[你好]]></Content><MsgId>1234567890</MsgId>"
        f"<AgentID>{conf.agent_id}</AgentID></xml>"
    )
    enc = crypt._encrypt(plain_xml)
    sig = crypt._sign(TS, NONCE, enc)
    echo = crypt.verify_url(sig, TS, NONCE, enc)
    check("回调 URL 校验（echostr 解密）", echo == plain_xml)

    # 2) 消息解密回环
    post = (
        f"<xml><ToUserName><![CDATA[{settings.corp_id}]]></ToUserName>"
        f"<Encrypt><![CDATA[{enc}]]></Encrypt>"
        f"<AgentID><![CDATA[{conf.agent_id}]]></AgentID></xml>"
    )
    got = crypt.decrypt_message(post.encode(), sig, TS, NONCE)
    check("消息解密", got == plain_xml)

    msg = parse_wework_xml(got)
    check(
        "XML 字段解析",
        msg.get("Content") == "你好" and msg.get("MsgType") == "text",
        f"FromUserName={msg.get('FromUserName')} Content={msg.get('Content')}",
    )

    # 3) 签名篡改应被拒绝
    try:
        crypt.verify_url("0" * 40, TS, NONCE, enc)
        check("错误签名被拒绝", False)
    except Exception:  # noqa: BLE001
        check("错误签名被拒绝", True)

    print("\n加解密链路正常。若企微后台仍报「签名验证失败」，核对 Token 是否复制完整（43 位）。")


if __name__ == "__main__":
    main()
