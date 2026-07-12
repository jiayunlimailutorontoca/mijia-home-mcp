"""allow/deny/危险设备那套门控。"""

import json

import pytest

from mijia_home_mcp.config import Settings
from mijia_home_mcp.guard import ControlDenied, ControlGuard

LIGHT = {"name": "客厅台灯", "did": "did_light", "model": "fake.light.v1"}
LOCK = {"name": "卧室门锁", "did": "did_lock", "model": "fake.lock.v1"}


def _settings(tmp_path, **kw) -> Settings:
    return Settings(
        auth_path=tmp_path / "auth.json", state_dir=tmp_path / "state", **kw
    )


def test_readonly_denies_everything(tmp_path):
    guard = ControlGuard(_settings(tmp_path, enable_control=False))
    with pytest.raises(ControlDenied, match="只读模式"):
        guard.check_device(LIGHT)
    with pytest.raises(ControlDenied, match="只读模式"):
        guard.check_scene()


def test_control_enabled_allows_normal_device(tmp_path):
    guard = ControlGuard(_settings(tmp_path, enable_control=True))
    guard.check_device(LIGHT)  # 不应抛
    guard.check_scene()


def test_dangerous_device_blocked_by_default(tmp_path):
    guard = ControlGuard(_settings(tmp_path, enable_control=True))
    with pytest.raises(ControlDenied, match="危险类别"):
        guard.check_device(LOCK)


def test_dangerous_device_via_allow_dangerous(tmp_path):
    guard = ControlGuard(
        _settings(tmp_path, enable_control=True, allow_dangerous=True)
    )
    guard.check_device(LOCK)


def test_dangerous_device_via_explicit_allow(tmp_path):
    guard = ControlGuard(
        _settings(tmp_path, enable_control=True, allow=["卧室门锁"])
    )
    guard.check_device(LOCK)


def test_wildcard_allow_does_not_release_dangerous(tmp_path):
    """--allow "*" 放行普通设备,但不能顺带解锁危险设备(需要精确名/did)。"""
    guard = ControlGuard(_settings(tmp_path, enable_control=True, allow=["*"]))
    guard.check_device(LIGHT)
    with pytest.raises(ControlDenied, match="危险类别"):
        guard.check_device(LOCK)
    # 通配含锁名也不行,必须精确
    guard2 = ControlGuard(_settings(tmp_path, enable_control=True, allow=["卧室*"]))
    with pytest.raises(ControlDenied, match="危险类别"):
        guard2.check_device(LOCK)


SPEAKER = {"name": "小爱音箱", "did": "did_speaker", "model": "xiaomi.wifispeaker.x1"}


def test_speaker_directive_blocked_by_default(tmp_path):
    guard = ControlGuard(_settings(tmp_path, enable_control=True))
    with pytest.raises(ControlDenied, match="语音指令"):
        guard.check_speaker_directive(SPEAKER)


def test_speaker_directive_released_by_exact_allow(tmp_path):
    guard = ControlGuard(
        _settings(tmp_path, enable_control=True, allow=["小爱音箱"])
    )
    guard.check_speaker_directive(SPEAKER)
    guard2 = ControlGuard(
        _settings(tmp_path, enable_control=True, allow_dangerous=True)
    )
    guard2.check_speaker_directive(SPEAKER)


def test_allowlist_excludes_others(tmp_path):
    guard = ControlGuard(
        _settings(tmp_path, enable_control=True, allow=["卧室*"])
    )
    with pytest.raises(ControlDenied, match="白名单"):
        guard.check_device(LIGHT)


def test_denylist_wins(tmp_path):
    guard = ControlGuard(
        _settings(
            tmp_path,
            enable_control=True,
            allow=["*"],
            deny=["*light*"],
        )
    )
    with pytest.raises(ControlDenied, match="deny"):
        guard.check_device(LIGHT)


def test_audit_log_written(tmp_path):
    settings = _settings(tmp_path, enable_control=True)
    guard = ControlGuard(settings)
    guard.audit("set_device_property", "客厅台灯", {"prop_name": "on"}, True)
    guard.audit("run_scene", "回家模式", {}, False, error="boom")
    lines = settings.audit_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["tool"] == "set_device_property"
    assert first["ok"] is True
    second = json.loads(lines[1])
    assert second["error"] == "boom"
