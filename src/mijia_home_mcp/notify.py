"""watch 的通知通道:小爱音箱 TTS 播报,以及钉钉/飞书/MeoW/通用 webhook。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from fnmatch import fnmatch
from typing import Any, Optional

import requests

WEBHOOK_TIMEOUT_S = 10

_TYPE_TEXT = {
    "went_offline": "离线了",
    "came_online": "上线了",
    "device_added": "新增设备",
    "device_removed": "移除设备",
}


def filter_changes(
    changes: list[dict],
    only: Optional[list[str]] = None,
    ignore: Optional[list[str]] = None,
) -> list[dict]:
    """按 glob 过滤变化列表。

    only: 只保留设备名命中任一模式的变化。
    ignore: 丢弃设备名或属性名命中任一模式的变化(用于压掉
            left-time 这类倒计时噪音)。
    """
    out = []
    for c in changes:
        device = c.get("device") or ""
        prop = c.get("prop") or ""
        if only and not any(fnmatch(device, p) for p in only):
            continue
        if ignore and any(
            fnmatch(device, p) or (prop and fnmatch(prop, p)) for p in ignore
        ):
            continue
        out.append(c)
    return out


def format_changes_text(changes: list[dict], limit: int = 5) -> str:
    """把变化列表压成一句适合口播/推送的中文。"""
    parts = []
    for c in changes[:limit]:
        if c["type"] == "prop_changed":
            parts.append(f"{c['device']}的{c['prop']}从{c['from']}变为{c['to']}")
        else:
            parts.append(f"{c['device']}{_TYPE_TEXT.get(c['type'], c['type'])}")
    text = ";".join(parts)
    rest = len(changes) - limit
    if rest > 0:
        text += f";另有{rest}项变化"
    return text


# ---------------- 推送 provider ----------------
# 每个 provider 一个函数:输入 (标题, 文本, 完整 diff),自行组织请求体。
# 抛出的异常由调用方(watch 循环)捕获降级,不中断监控。


def _post_json(url: str, payload: dict, check_body: bool = False) -> None:
    resp = requests.post(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=WEBHOOK_TIMEOUT_S,
    )
    resp.raise_for_status()
    if check_body:
        try:
            body = resp.json()
        except ValueError:
            return
        # 钉钉/飞书 HTTP 200 但业务失败时 errcode/code 非 0
        code = body.get("errcode", body.get("code", 0))
        if code not in (0, 200, None):
            raise RuntimeError(
                f"推送服务返回业务错误: {json.dumps(body, ensure_ascii=False)[:200]}"
            )


def send_dingtalk(
    webhook_url: str, title: str, text: str, secret: Optional[str] = None
) -> None:
    """钉钉自定义机器人(text 消息)。

    机器人安全设置用「自定义关键词」时,把关键词设为「米家」即可
    (标题固定含「米家提醒」);用「加签」时传 secret。
    """
    url = webhook_url
    if secret:
        ts = str(round(time.time() * 1000))
        sign_str = f"{ts}\n{secret}"
        sign = base64.b64encode(
            hmac.new(
                secret.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256
            ).digest()
        )
        url += f"&timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"
    _post_json(
        url,
        {"msgtype": "text", "text": {"content": f"{title}\n{text}"}},
        check_body=True,
    )


def send_feishu(
    webhook_url: str, title: str, text: str, secret: Optional[str] = None
) -> None:
    """飞书自定义机器人(text 消息),可选「签名校验」。

    飞书加签与钉钉算法不同:以 timestamp+"\\n"+secret 为 HMAC 密钥、
    空串为消息体计算 SHA256,签名放请求体的 timestamp/sign 字段。
    """
    payload: dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": f"{title}\n{text}"},
    }
    if secret:
        ts = str(int(time.time()))
        key = f"{ts}\n{secret}".encode("utf-8")
        sign = base64.b64encode(
            hmac.new(key, b"", digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        payload["timestamp"] = ts
        payload["sign"] = sign
    _post_json(webhook_url, payload, check_body=True)


def send_meow(nickname_or_url: str, title: str, text: str) -> None:
    """MeoW(鸿蒙消息推送, api.chuckfang.com)。

    参数可以是 MeoW 昵称,也可以是完整 URL(自建/指定协议时)。
    """
    target = nickname_or_url
    if not target.startswith(("http://", "https://")):
        target = f"https://api.chuckfang.com/{urllib.parse.quote(target)}"
    _post_json(target, {"title": title, "msg": text}, check_body=True)


def send_generic(url: str, title: str, text: str, diff: dict) -> None:
    """通用 webhook:POST 完整 diff JSON,附 title/text 摘要字段。"""
    _post_json(url, {"source": "mijia-home-mcp", "title": title, "text": text, **diff})


class Pusher:
    """聚合多个推送通道;单通道失败互不影响,错误列表返回给调用方打印。"""

    def __init__(
        self,
        dingtalk: Optional[str] = None,
        dingtalk_secret: Optional[str] = None,
        feishu: Optional[str] = None,
        feishu_secret: Optional[str] = None,
        meow: Optional[str] = None,
        webhook: Optional[str] = None,
    ):
        self.dingtalk = dingtalk
        self.dingtalk_secret = dingtalk_secret
        self.feishu = feishu
        self.feishu_secret = feishu_secret
        self.meow = meow
        self.webhook = webhook

    @property
    def channels(self) -> list[str]:
        out = []
        if self.dingtalk:
            out.append("钉钉")
        if self.feishu:
            out.append("飞书")
        if self.meow:
            out.append("MeoW")
        if self.webhook:
            out.append("webhook")
        return out

    def push(self, title: str, text: str, diff: dict) -> list[str]:
        errors = []
        if self.dingtalk:
            try:
                send_dingtalk(self.dingtalk, title, text, self.dingtalk_secret)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"钉钉: {exc}")
        if self.feishu:
            try:
                send_feishu(self.feishu, title, text, self.feishu_secret)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"飞书: {exc}")
        if self.meow:
            try:
                send_meow(self.meow, title, text)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"MeoW: {exc}")
        if self.webhook:
            try:
                send_generic(self.webhook, title, text, diff)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"webhook: {exc}")
        return errors


class SpeakerNotifier:
    """通过小爱音箱 play-text 动作播报文字(纯 TTS,不会触发指令执行)。"""

    def __init__(self, client: Any, speaker_name: Optional[str] = None):
        speakers = [
            d
            for d in client.devices()
            if "xiaomi.wifispeaker" in (d.get("model") or "")
        ]
        if speaker_name:
            named = [d for d in speakers if d.get("name") == speaker_name]
            if not named:
                candidates = ", ".join(d.get("name", "?") for d in speakers) or "无"
                raise ValueError(
                    f"未找到名为「{speaker_name}」的小爱音箱。可选: {candidates}"
                )
            speakers = named
        if not speakers:
            raise ValueError("账号下没有找到小爱音箱设备")
        self.client = client
        self.speaker = speakers[0]

    @property
    def name(self) -> str:
        return self.speaker.get("name", "?")

    def announce(self, text: str) -> None:
        self.client.invoke_action(self.speaker, "play-text", in_args=[text])
