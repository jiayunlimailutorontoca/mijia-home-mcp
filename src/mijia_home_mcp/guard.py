"""控制门控与审计:allow/deny 白黑名单、危险设备拦截、写操作审计日志。"""

from __future__ import annotations

import json
from datetime import datetime
from fnmatch import fnmatch
from typing import Any, Optional

from .config import Settings
from .semantics import is_dangerous_model


class ControlDenied(Exception):
    """控制请求被策略拒绝。message 面向 LLM,说明原因与解除方式。"""


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
    """精确匹配设备名或 did(不允许通配),用于危险设备的显式放行:
    `--allow "*"` 这类宽泛白名单不应顺带解锁门锁。"""
    fields = {
        (device.get("name") or "").lower(),
        (device.get("did") or "").lower(),
    }
    return any(p.lower() in fields for p in patterns)


class ControlGuard:
    def __init__(self, settings: Settings):
        self.settings = settings

    def check_device(self, device: dict) -> None:
        """不允许控制时抛 ControlDenied。"""
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
            # 危险设备只接受精确放行:--allow-dangerous,或 allow 名单里
            # 精确写出设备名/did(通配符不算,防止 --allow "*" 顺带解锁)
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
        """小爱语音指令通道能触达全屋任意设备(包括门锁等危险设备),
        绕过设备级 allow/deny,因此按危险设备同等策略把关。"""
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
        """追加一条 JSONL 审计记录;审计失败静默(不阻塞主流程)。"""
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
