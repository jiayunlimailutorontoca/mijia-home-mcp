"""控制的门:allow/deny 名单、危险设备拦截、写操作审计。"""

from __future__ import annotations

import json
from datetime import datetime
from fnmatch import fnmatch
from typing import Any, Optional

from .config import Settings
from .semantics import is_dangerous_model


class ControlDenied(Exception):
    """被策略拒了。message 会透给模型,所以写清楚原因和怎么解。"""


def _match_any(patterns: list[str], device: dict) -> bool:
    fields = [
        (device.get("name") or ""),
        (device.get("did") or ""),
        (device.get("model") or ""),
    ]
    for pattern in patterns:
        for value in fields:
            if fnmatch(value.lower(), pattern.lower()):
                return True
    return False


def _match_exact(patterns: list[str], device: dict) -> bool:
    """名字或 did 完整匹配,不吃通配符。危险设备放行走这个,
    免得 --allow "*" 把门锁也捎带放了。"""
    fields = {
        (device.get("name") or "").lower(),
        (device.get("did") or "").lower(),
    }
    return any(p.lower() in fields for p in patterns)


class ControlGuard:
    def __init__(self, settings: Settings):
        self.settings = settings

    def check_device(self, device: dict) -> None:
        s = self.settings
        label = f"{device.get('name')}({device.get('model')})"
        if not s.enable_control:
            raise ControlDenied(
                "当前为只读模式,控制工具未启用。如需控制设备,"
                "请在 MCP server 启动参数加 --enable-control "
                "(或设置环境变量 MIJIA_HOME_MCP_ENABLE_CONTROL=1)后重启。"
            )
        if s.deny and _match_any(s.deny, device):
            raise ControlDenied(f"设备 {label} 命中 deny 名单,已拒绝控制。")
        if is_dangerous_model(device.get("model", "")):
            if not (s.allow_dangerous or _match_exact(s.allow, device)):
                raise ControlDenied(
                    f"设备 {label} 属于危险类别(锁/摄像头/燃气或水阀/保险柜),默认禁止控制。"
                    "如确需控制,请把该设备的完整名称或 did 精确加入 --allow 名单,"
                    "或启动时加 --allow-dangerous。"
                )
        if s.allow and not _match_any(s.allow, device):
            raise ControlDenied(
                f"已配置 allow 白名单,设备 {label} 不在名单内,已拒绝控制。"
            )

    def check_scene(self) -> None:
        if not self.settings.enable_control:
            raise ControlDenied(
                "当前为只读模式,运行场景属于控制操作。"
                "请在启动参数加 --enable-control 后重启。"
            )

    def check_speaker_directive(self, speaker: dict) -> None:
        """语音指令能让小爱操作全屋任何设备,等于绕过整个白名单,
        所以音箱本身按危险设备的标准来。"""
        self.check_device(speaker)
        s = self.settings
        if not (s.allow_dangerous or _match_exact(s.allow, speaker)):
            raise ControlDenied(
                "小爱语音指令可以控制全屋任意设备(包括门锁/摄像头),会绕过设备白名单,"
                "因此默认拦截。要启用请把该音箱的完整名称或 did 精确加入 --allow,"
                "或启动时加 --allow-dangerous。"
            )

    def audit(
        self,
        tool: str,
        target: str,
        args: dict[str, Any],
        ok: bool,
        error: Optional[str] = None,
    ) -> None:
        # 审计写不进去就算了,不能因为日志挂了导致控制不可用
        record = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tool": tool,
            "target": target,
            "args": args,
            "ok": ok,
        }
        if error:
            record["error"] = error
        try:
            self.settings.ensure_dirs()
            with open(self.settings.audit_log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
