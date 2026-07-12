"""watch 的通知通道:小爱音箱 TTS 播报与 webhook POST。"""

from __future__ import annotations

import json
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
    """把变化列表压成一句适合口播的中文。"""
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


def send_webhook(url: str, payload: dict[str, Any]) -> None:
    """POST JSON 到 webhook。抛出 requests 异常由调用方决定如何降级。"""
    resp = requests.post(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=WEBHOOK_TIMEOUT_S,
    )
    resp.raise_for_status()


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
