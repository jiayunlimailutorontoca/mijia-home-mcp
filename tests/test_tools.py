"""MCP 工具面测试:注册门控、快照/变化/控制工具端到端(内存传输,离线)。"""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mijia_home_mcp.server import build_server


def _run(coro):
    return asyncio.run(coro)


async def _tool_names(mcp):
    async with Client(mcp) as client:
        tools = await client.list_tools()
        return {t.name for t in tools}


async def _call(mcp, name, args=None):
    async with Client(mcp) as client:
        result = await client.call_tool(name, args or {})
        return result.data


READ_TOOLS = {
    "get_home_snapshot",
    "get_home_changes",
    "list_homes",
    "list_devices",
    "get_device_status",
    "get_device_spec",
    "list_scenes",
    "list_consumables",
    "auth_status",
    "login",
    "login_status",
}
CONTROL_TOOLS = {
    "set_device_property",
    "run_device_action",
    "run_scene",
    "run_speaker_command",
}


def test_readonly_mode_hides_control_tools(fake_api, settings):
    mcp = build_server(settings, api=fake_api)
    names = _run(_tool_names(mcp))
    assert READ_TOOLS <= names
    assert not (CONTROL_TOOLS & names)


def test_control_mode_registers_control_tools(fake_api, settings):
    settings.enable_control = True
    mcp = build_server(settings, api=fake_api)
    names = _run(_tool_names(mcp))
    assert CONTROL_TOOLS <= names


def test_snapshot_tool(fake_api, settings):
    mcp = build_server(settings, api=fake_api)
    data = _run(_call(mcp, "get_home_snapshot"))
    assert data["stats"]["devices_total"] == 8
    assert {h["name"] for h in data["homes"]} == {"我的家", "共享设备"}


def test_changes_tool_baseline_then_diff(fake_api, settings):
    mcp = build_server(settings, api=fake_api)
    first = _run(_call(mcp, "get_home_changes"))
    assert "baseline_ts" in first

    fake_api.prop_values[("did_light", 2, 2)] = 30

    async def second_call():
        # 缓存 TTL 内 devices 列表不变,但属性是实时拉取的
        return await _call(mcp, "get_home_changes")

    second = _run(second_call())
    props_changed = [
        c for c in second["changes"] if c["type"] == "prop_changed"
    ]
    assert any(
        c["prop"] == "brightness" and c["to"] == 30 for c in props_changed
    )


def test_device_status_tool(fake_api, settings):
    mcp = build_server(settings, api=fake_api)
    data = _run(_call(mcp, "get_device_status", {"device": "客厅台灯"}))
    assert data["state"]["on"]["text"] == "开启"
    assert data["state"]["mode"]["text"] == "夜灯模式"

    offline = _run(_call(mcp, "get_device_status", {"device": "离线插座"}))
    assert offline["online"] is False


def test_set_property_and_audit(fake_api, settings):
    settings.enable_control = True
    mcp = build_server(settings, api=fake_api)
    msg = _run(
        _call(
            mcp,
            "set_device_property",
            {"device": "客厅台灯", "prop_name": "on", "value": "false"},
        )
    )
    assert "已设置" in msg
    assert fake_api.set_calls, "应产生一次 set_devices_prop 调用"
    assert fake_api.set_calls[-1]["value"] is False, "字符串 'false' 应强转为 bool"
    audit = settings.audit_log_path.read_text(encoding="utf-8")
    assert "set_device_property" in audit


def test_set_property_on_shared_device(fake_api, settings):
    """设备级共享的设备也能控制(不经 mijiaDevice 的自有设备校验)。"""
    settings.enable_control = True
    mcp = build_server(settings, api=fake_api)
    msg = _run(
        _call(
            mcp,
            "set_device_property",
            {"device": "好友的插座", "prop_name": "brightness", "value": "66"},
        )
    )
    assert "已设置" in msg
    assert fake_api.set_calls[-1]["did"] == "did_shared"


def test_dangerous_device_denied(fake_api, settings):
    settings.enable_control = True
    mcp = build_server(settings, api=fake_api)
    with pytest.raises(ToolError, match="危险类别"):
        _run(
            _call(
                mcp,
                "set_device_property",
                {"device": "卧室门锁", "prop_name": "on", "value": "false"},
            )
        )
    assert not fake_api.set_calls, "被拒绝的控制不应触达云端"


def test_run_scene_tool(fake_api, settings):
    settings.enable_control = True
    mcp = build_server(settings, api=fake_api)
    msg = _run(_call(mcp, "run_scene", {"scene": "回家模式"}))
    assert "成功" in msg
    assert fake_api.scene_calls == [("sc1", "home1")]


def test_speaker_command_blocked_without_explicit_release(fake_api, settings):
    """语音指令通道可绕过设备白名单,默认必须显式放行。"""
    settings.enable_control = True
    mcp = build_server(settings, api=fake_api)
    with pytest.raises(ToolError, match="语音指令"):
        _run(_call(mcp, "run_speaker_command", {"prompt": "打开卧室台灯"}))
    assert not fake_api.action_calls


def test_speaker_command_with_exact_allow(fake_api, settings):
    settings.enable_control = True
    settings.allow = ["小爱音箱"]
    mcp = build_server(settings, api=fake_api)
    msg = _run(
        _call(mcp, "run_speaker_command", {"prompt": "打开卧室台灯"})
    )
    assert "小爱音箱" in msg
    assert fake_api.action_calls, "应触发 execute-text-directive 动作"
    # 与上游一致:文本指令走 'in' 键,quiet 默认 True
    assert fake_api.action_calls[-1]["in"] == ["打开卧室台灯", 1]


def test_snapshot_does_not_touch_changes_baseline(fake_api, settings):
    """get_home_snapshot 不应重置 get_home_changes 的对比基线。"""
    mcp = build_server(settings, api=fake_api)
    _run(_call(mcp, "get_home_changes"))  # 建立基线
    fake_api.prop_values[("did_light", 2, 2)] = 30
    _run(_call(mcp, "get_home_snapshot"))  # 不应吞掉变化
    diff = _run(_call(mcp, "get_home_changes"))
    assert any(
        c["type"] == "prop_changed" and c["prop"] == "brightness" and c["to"] == 30
        for c in diff["changes"]
    )


def test_get_device_spec_with_dotted_did(fake_api, settings):
    """蓝牙设备 did(blt.3.xxx)含点,必须先按设备解析而不是当成型号。"""
    mcp = build_server(settings, api=fake_api)
    spec = _run(_call(mcp, "get_device_spec", {"device_or_model": "blt.3.abc123"}))
    assert spec["name"] == "假温湿度计"


def test_snapshot_room_filter(fake_api, settings):
    mcp = build_server(settings, api=fake_api)
    data = _run(_call(mcp, "get_home_snapshot", {"home": "我的家", "room": "客厅"}))
    rooms = [r["name"] for h in data["homes"] for r in h["rooms"]]
    assert rooms == ["客厅"]
    assert data["stats"]["devices_total"] == 2

    with pytest.raises(ToolError, match="未找到房间"):
        _run(_call(mcp, "get_home_snapshot", {"room": "地下室"}))


def test_list_scenes_accepts_home_name(fake_api, settings):
    mcp = build_server(settings, api=fake_api)
    scenes = _run(_call(mcp, "list_scenes", {"home": "我的家"}))
    assert len(scenes) == 2
    assert fake_api.scenes_list_calls[-1] == "home1", "家庭名应被解析为 home_id"


def test_auth_status_without_login(settings):
    mcp = build_server(settings)  # 不注入 api,认证文件不存在
    data = _run(_call(mcp, "auth_status"))
    assert data["logged_in"] is False
    assert "login" in data["hint"]
