"""事件历史的落盘和查询。"""

import json
from datetime import datetime, timedelta

from mijia_home_mcp.history import EventHistory

CHANGES = [
    {"type": "prop_changed", "device": "门锁", "prop": "door-state", "from": "Locked", "to": "Open"},
    {"type": "prop_changed", "device": "客厅台灯", "prop": "on", "from": False, "to": True},
    {"type": "went_offline", "device": "阳台传感器"},
]


def _history(tmp_path) -> EventHistory:
    return EventHistory(tmp_path / "state")


def test_append_and_query_roundtrip(tmp_path):
    h = _history(tmp_path)
    h.append(CHANGES, home="我的家")
    result = h.query()
    assert result["count"] == 3
    # 新→旧:最后写入的在前(同批次内为逆序)
    assert result["events"][0]["type"] == "went_offline"
    assert all(ev["home"] == "我的家" for ev in result["events"])
    assert all("ts" in ev for ev in result["events"])


def test_query_filters(tmp_path):
    h = _history(tmp_path)
    h.append(CHANGES)
    assert h.query(device="门锁")["count"] == 1
    assert h.query(device="*台灯*")["count"] == 1
    assert h.query(prop="door-*")["count"] == 1
    assert h.query(event_type="went_offline")["count"] == 1
    assert h.query(device="不存在")["count"] == 0


def test_query_time_window(tmp_path):
    h = _history(tmp_path)
    h.append(CHANGES[:1])
    future = (datetime.now() + timedelta(hours=1)).isoformat()
    past = (datetime.now() - timedelta(hours=1)).isoformat()
    assert h.query(since=past, until=future)["count"] == 1
    assert h.query(until=past)["count"] == 0


def test_query_limit_and_truncated(tmp_path):
    h = _history(tmp_path)
    for i in range(7):
        h.append([{"type": "prop_changed", "device": f"设备{i}", "prop": "on", "from": 0, "to": 1}])
    result = h.query(limit=5)
    assert result["count"] == 5
    assert result["truncated"] is True
    # 新→旧
    assert result["events"][0]["device"] == "设备6"


def test_empty_history_has_note(tmp_path):
    result = _history(tmp_path).query()
    assert result["count"] == 0
    assert "watch" in result["note"]


def test_retention_cleanup(tmp_path):
    h = _history(tmp_path)
    h.history_dir.mkdir(parents=True)
    old_day = datetime.now() - timedelta(days=40)
    old_file = h.history_dir / f"events-{old_day:%Y%m%d}.jsonl"
    old_file.write_text(
        json.dumps({"ts": old_day.isoformat(), "type": "prop_changed", "device": "旧"}) + "\n",
        encoding="utf-8",
    )
    h.append(CHANGES[:1])  # append 触发清理
    assert not old_file.exists()


def test_corrupt_lines_skipped(tmp_path):
    h = _history(tmp_path)
    h.append(CHANGES[:1])
    day_file = next(h.history_dir.glob("events-*.jsonl"))
    with open(day_file, "a", encoding="utf-8") as fh:
        fh.write("not-json\n")
    assert h.query()["count"] == 1
