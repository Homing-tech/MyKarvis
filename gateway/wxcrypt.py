"""企业微信回调消息加解密（WXBizMsgCrypt 等价实现）。

参考企业微信官方算法：
  密文 = Base64(AES-256-CBC(random16 + msg_len(4B, 网络序) + msg + receiveid))
  签名 = SHA1(sort([token, timestamp, nonce, encrypt]))
仅依赖 pycryptodome，无官方 SDK 依赖。
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import xml.etree.ElementTree as ET

from Crypto.Cipher import AES


class WXBizMsgCryptError(Exception):
    pass


class WXBizMsgCrypt:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str):
        self.token = token
        self.receive_id = receive_id
        try:
            self.key = base64.b64decode(encoding_aes_key + "=")
        except Exception as exc:  # noqa: BLE001
            raise WXBizMsgCryptError(f"EncodingAESKey 非法: {exc}") from exc
        if len(self.key) != 32:
            raise WXBizMsgCryptError(f"EncodingAESKey 解码后应为 32 字节，实际 {len(self.key)}")
        self.iv = self.key[:16]

    # ---------- 内部工具 ----------
    @staticmethod
    def _pkcs7_pad(data: bytes) -> bytes:
        pad = 32 - (len(data) % 32)
        return data + bytes([pad]) * pad

    @staticmethod
    def _pkcs7_unpad(data: bytes) -> bytes:
        if not data:
            return data
        pad = data[-1]
        if pad < 1 or pad > 32:
            raise WXBizMsgCryptError("PKCS7 填充非法")
        return data[:-pad]

    def _sign(self, timestamp: str, nonce: str, encrypt: str) -> str:
        raw = "".join(sorted([self.token, str(timestamp), str(nonce), encrypt]))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _decrypt(self, encrypt_b64: str) -> bytes:
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        plain = cipher.decrypt(base64.b64decode(encrypt_b64))
        plain = self._pkcs7_unpad(plain)
        content = plain[16:]
        (msg_len,) = struct.unpack("!I", content[:4])
        msg = content[4 : 4 + msg_len]
        # 尾部 receive_id 不做强校验（企微不同回调类型下可能缺），仅解密用
        return msg

    def _encrypt(self, text: str) -> str:
        msg = text.encode("utf-8")
        payload = (
            os.urandom(16)
            + struct.pack("!I", len(msg))
            + msg
            + self.receive_id.encode("utf-8")
        )
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        return base64.b64encode(cipher.encrypt(self._pkcs7_pad(payload))).decode()

    # ---------- 对外接口 ----------
    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """回调配置时的 GET 校验，返回解密后的 echostr 明文。"""
        if self._sign(timestamp, nonce, echostr) != msg_signature:
            raise WXBizMsgCryptError("回调 URL 校验签名不匹配（Token 填写是否正确？）")
        return self._decrypt(echostr).decode("utf-8")

    def decrypt_message(self, post_data: bytes | str, msg_signature: str,
                        timestamp: str, nonce: str) -> str:
        """解密收到的 POST 消息体，返回明文 XML。"""
        if isinstance(post_data, bytes):
            post_data = post_data.decode("utf-8", errors="ignore")
        try:
            root = ET.fromstring(post_data)
        except ET.ParseError as exc:
            raise WXBizMsgCryptError(f"回调消息体不是合法 XML: {exc}") from exc
        node = root.find("Encrypt")
        if node is None or not node.text:
            raise WXBizMsgCryptError("回调消息体缺少 Encrypt 节点")
        encrypt = node.text
        if self._sign(timestamp, nonce, encrypt) != msg_signature:
            raise WXBizMsgCryptError("消息签名不匹配")
        return self._decrypt(encrypt).decode("utf-8", errors="ignore")

    def encrypt_message(self, reply_xml: str, timestamp: str, nonce: str) -> str:
        """把被动回复的明文 XML 加密成回调响应体。"""
        encrypt = self._encrypt(reply_xml)
        sign = self._sign(timestamp, nonce, encrypt)
        return (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{sign}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )


def parse_wework_xml(xml_text: str) -> dict:
    """解析解密后的消息 XML 为 dict（CDATA 由 ET 自动剥离）。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    return {child.tag: (child.text or "") for child in root}


def build_text_reply(to_user: str, from_user: str, content: str) -> str:
    """构造被动回复的文本消息 XML。"""
    import time

    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "</xml>"
    )


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"
