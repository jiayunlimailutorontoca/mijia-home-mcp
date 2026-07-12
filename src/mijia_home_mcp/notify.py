"""通知通道:小爱音箱 TTS,加钉钉/飞书/MeoW/通用 webhook 四种推送。"""

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
    """only 留设备名命中的,ignore 扔设备名或属性名命中的。

    ignore 主要用来压属性噪音——洗碗机的 left-time 每分钟变一次,
    不扔掉的话音箱能把人念疯。
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
    """压成一句能口播的话。"""
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
        # 钉钉和飞书都是 HTTP 200 + 响应体里的 errcode 报业务错,坑
        code = body.get("errcode", body.get("code", 0))
        if code not in (0, 200, None):
            raise RuntimeError(
                f"推送服务返回业务错误: {json.dumps(body, ensure_ascii=False)[:200]}"
            )


def _changes_markdown_lines(changes: list[dict], limit: int = 10) -> list[str]:
    # 钉钉和飞书的卡片都吃 markdown,共用一份
    lines = []
    for c in changes[:limit]:
        if c["type"] == "prop_changed":
            lines.append(f"- **{c['device']}** {c['prop']}: {c['from']} → {c['to']}")
        else:
            lines.append(f"- **{c['device']}** {_TYPE_TEXT.get(c['type'], c['type'])}")
    rest = len(changes) - limit
    if rest > 0:
        lines.append(f"- …另有 {rest} 项变化")
    return lines


def _dingtalk_sign_url(webhook_url: str, secret: str) -> str:
    ts = str(round(time.time() * 1000))
    sign_str = f"{ts}\n{secret}"
    sign = base64.b64encode(
        hmac.new(
            secret.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256
        ).digest()
    )
    return f"{webhook_url}&timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"


def send_dingtalk(
    webhook_url: str,
    title: str,
    text: str,
    secret: Optional[str] = None,
    changes: Optional[list[dict]] = None,
) -> None:
    """钉钉机器人。带 changes 发 markdown 卡片,不带发纯文本。

    机器人安全设置选"自定义关键词"的话填「米家」就能过
    (标题固定带"米家提醒");选加签就传 secret。
    """
    url = _dingtalk_sign_url(webhook_url, secret) if secret else webhook_url
    if changes:
        md = "\n".join([f"### {title}"] + _changes_markdown_lines(changes))
        payload: dict[str, Any] = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": md},
        }
    else:
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    _post_json(url, payload, check_body=True)


def send_feishu(
    webhook_url: str,
    title: str,
    text: str,
    secret: Optional[str] = None,
    changes: Optional[list[dict]] = None,
) -> None:
    """飞书机器人。带 changes 发 interactive 卡片,不带发纯文本。

    注意飞书的加签跟钉钉不是一个算法:这边是拿 timestamp+secret
    当 HMAC 密钥、消息体为空串,签名放请求体里而不是 URL 上。
    """
    if changes:
        payload: dict[str, Any] = {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "body": {
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": "\n".join(_changes_markdown_lines(changes)),
                        }
                    ]
                },
            },
        }
    else:
        payload = {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
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
    """MeoW 鸿蒙推送。传昵称就打官方 api.chuckfang.com,传 URL 就直接用。"""
    target = nickname_or_url
    if not target.startswith(("http://", "https://")):
        target = f"https://api.chuckfang.com/{urllib.parse.quote(target)}"
    _post_json(target, {"title": title, "msg": text}, check_body=True)


def send_bark(key_or_url: str, title: str, text: str) -> None:
    """Bark(iOS 推送)。传 device key 就打官方 api.day.app,传 URL 用自建。"""
    if key_or_url.startswith(("http://", "https://")):
        base = key_or_url.rstrip("/")
        # 自建服务器给到根地址即可,统一走 /push
        url = base if base.endswith("/push") else f"{base}/push"
        payload = {"title": title, "body": text}
        # 自建 URL 里带 key 的情况:/push 不需要 device_key,兼容两种都传
        _post_json(url, payload, check_body=True)
    else:
        _post_json(
            "https://api.day.app/push",
            {"device_key": key_or_url, "title": title, "body": text},
            check_body=True,
        )


def send_ntfy(topic_or_url: str, title: str, text: str) -> None:
    """ntfy(安卓/桌面推送)。传 topic 走 ntfy.sh,传 URL 用自建。

    走 JSON 发布而不是 Title 头,HTTP 头塞中文要 RFC2047 编码,JSON 没这事。
    """
    if topic_or_url.startswith(("http://", "https://")):
        # 自建:https://my.ntfy.host/mytopic → 根地址 + topic
        base, _, topic = topic_or_url.rstrip("/").rpartition("/")
        _post_json(base, {"topic": topic, "title": title, "message": text})
    else:
        _post_json(
            "https://ntfy.sh", {"topic": topic_or_url, "title": title, "message": text}
        )


def send_generic(url: str, title: str, text: str, diff: dict) -> None:
    """通用 webhook,POST 完整 diff。text 字段是现成摘要,接 Bark/ntfy 直接用。"""
    _post_json(url, {"source": "mijia-home-mcp", "title": title, "text": text, **diff})


class Pusher:
    """把配置的通道都推一遍,谁挂了记谁的错,互相不挡路。"""

    def __init__(
        self,
        dingtalk: Optional[str] = None,
        dingtalk_secret: Optional[str] = None,
        feishu: Optional[str] = None,
        feishu_secret: Optional[str] = None,
        meow: Optional[str] = None,
        bark: Optional[str] = None,
        ntfy: Optional[str] = None,
        webhook: Optional[str] = None,
    ):
        self.dingtalk = dingtalk
        self.dingtalk_secret = dingtalk_secret
        self.feishu = feishu
        self.feishu_secret = feishu_secret
        self.meow = meow
        self.bark = bark
        self.ntfy = ntfy
        self.webhook = webhook

    @property
    def channels(self) -> list[str]:
        out = []
        if self.dingtalk:
            out.append("钉钉")
        if self.bark:
            out.append("Bark")
        if self.ntfy:
            out.append("ntfy")
        if self.feishu:
            out.append("飞书")
        if self.meow:
            out.append("MeoW")
        if self.webhook:
            out.append("webhook")
        return out

    def push(self, title: str, text: str, diff: dict) -> list[str]:
        errors = []
        changes = diff.get("changes") or None
        if self.dingtalk:
            try:
                send_dingtalk(
                    self.dingtalk, title, text, self.dingtalk_secret, changes=changes
                )
            except Exception as exc:
                errors.append(f"钉钉: {exc}")
        if self.feishu:
            try:
                send_feishu(
                    self.feishu, title, text, self.feishu_secret, changes=changes
                )
            except Exception as exc:
                errors.append(f"飞书: {exc}")
        if self.meow:
            try:
                send_meow(self.meow, title, text)
            except Exception as exc:
                errors.append(f"MeoW: {exc}")
        if self.bark:
            try:
                send_bark(self.bark, title, text)
            except Exception as exc:
                errors.append(f"Bark: {exc}")
        if self.ntfy:
            try:
                send_ntfy(self.ntfy, title, text)
            except Exception as exc:
                errors.append(f"ntfy: {exc}")
        if self.webhook:
            try:
                send_generic(self.webhook, title, text, diff)
            except Exception as exc:
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
