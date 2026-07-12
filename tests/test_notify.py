"""notify 模块测试:变化过滤、口播文案、音箱选择(离线)。"""

import pytest

from mijia_home_mcp.client import HomeClient
from mijia_home_mcp.notify import (
    SpeakerNotifier,
    filter_changes,
    format_changes_text,
)

CHANGES = [
    {"type": "prop_changed", "device": "客厅台灯", "prop": "on", "from": True, "to": False},
    {"type": "prop_changed", "device": "洗碗机", "prop": "left-time", "from": 30, "to": 29},
    {"type": "went_offline", "device": "阳台传感器"},
]


def test_filter_only():
    out = filter_changes(CHANGES, only=["客厅*"])
    assert [c["device"] for c in out] == ["客厅台灯"]


def test_filter_ignore_by_prop_and_device():
    out = filter_changes(CHANGES, ignore=["left-time"])
    assert all(c.get("prop") != "left-time" for c in out)
    out2 = filter_changes(CHANGES, ignore=["阳台*"])
    assert all(c["device"] != "阳台传感器" for c in out2)


def test_filter_none_passthrough():
    assert filter_changes(CHANGES) == CHANGES


def test_format_changes_text_limit_and_suffix():
    text = format_changes_text(CHANGES, limit=2)
    assert "客厅台灯" in text and "洗碗机" in text
    assert "另有1项变化" in text
    assert "阳台传感器" not in text


def test_format_offline_text():
    text = format_changes_text([CHANGES[2]])
    assert text == "阳台传感器离线了"


def test_speaker_notifier_selects_and_announces(fake_api, settings):
    client = HomeClient(fake_api, settings)
    notifier = SpeakerNotifier(client)
    assert notifier.name == "小爱音箱"
    notifier.announce("测试播报")
    assert fake_api.action_calls[-1]["in"] == ["测试播报"]
    # play-text 的 siid/aiid 来自假 spec
    assert fake_api.action_calls[-1]["aiid"] == 5


def test_speaker_notifier_unknown_name(fake_api, settings):
    client = HomeClient(fake_api, settings)
    with pytest.raises(ValueError, match="未找到"):
        SpeakerNotifier(client, "不存在的音箱")
