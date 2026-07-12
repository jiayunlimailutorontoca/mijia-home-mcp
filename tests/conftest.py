"""不联网的假 API。spec 走的是 get_device_info 的磁盘缓存:
它优先读 cache_path 下的 {model}.json,把假 spec 写进去就不会发请求。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mijia_home_mcp.config import Settings

LIGHT_MODEL = "fake.light.v1"
SENSOR_MODEL = "fake.sensor.v1"
LOCK_MODEL = "fake.lock.v1"
SPEAKER_MODEL = "xiaomi.wifispeaker.fake1"

LIGHT_SPEC = {
    "name": "假台灯",
    "model": LIGHT_MODEL,
    "properties": [
        {
            "name": "on",
            "description": "Switch Status / 开关",
            "type": "bool",
            "rw": "rw",
            "range": None,
            "value-list": None,
            "method": {"siid": 2, "piid": 1},
        },
        {
            "name": "brightness",
            "description": "Brightness / 亮度",
            "type": "uint",
            "rw": "rw",
            "range": [1, 100, 1],
            "value-list": None,
            "method": {"siid": 2, "piid": 2},
        },
        {
            "name": "mode",
            "description": "Mode / 模式",
            "type": "uint",
            "rw": "rw",
            "range": None,
            "value-list": [
                {"value": 0, "description": "日光模式"},
                {"value": 1, "description": "夜灯模式"},
            ],
            "method": {"siid": 2, "piid": 3},
        },
    ],
    "actions": [
        {
            "name": "toggle",
            "description": "Toggle / 切换开关",
            "method": {"siid": 2, "aiid": 1},
        }
    ],
}

SENSOR_SPEC = {
    "name": "假温湿度计",
    "model": SENSOR_MODEL,
    "properties": [
        {
            "name": "temperature",
            "description": "Temperature / 温度",
            "type": "float",
            "rw": "r",
            "range": [-40, 125, 0.1],
            "value-list": None,
            "method": {"siid": 3, "piid": 1},
        },
        {
            "name": "battery-level",
            "description": "Battery Level / 电量",
            "type": "uint",
            "rw": "r",
            "range": [0, 100, 1],
            "value-list": None,
            "method": {"siid": 4, "piid": 1},
        },
    ],
    "actions": [],
}

LOCK_SPEC = {
    "name": "假门锁",
    "model": LOCK_MODEL,
    "properties": [
        {
            "name": "on",
            "description": "Lock / 锁定",
            "type": "bool",
            "rw": "rw",
            "range": None,
            "value-list": None,
            "method": {"siid": 2, "piid": 1},
        }
    ],
    "actions": [],
}

SPEAKER_SPEC = {
    "name": "假小爱音箱",
    "model": SPEAKER_MODEL,
    "properties": [],
    "actions": [
        {
            "name": "execute-text-directive",
            "description": "Execute Text Directive / 执行文本指令",
            "method": {"siid": 5, "aiid": 4},
        },
        {
            "name": "play-text",
            "description": "Play Text / 播放文本",
            "method": {"siid": 5, "aiid": 5},
        },
    ],
}

ALL_SPECS = {
    LIGHT_MODEL: LIGHT_SPEC,
    SENSOR_MODEL: SENSOR_SPEC,
    LOCK_MODEL: LOCK_SPEC,
    SPEAKER_MODEL: SPEAKER_SPEC,
}


class FakeAPI:
    """行为与 mijiaAPI 对齐的离线假实现,记录写操作供断言。"""

    def __init__(self):
        self.available = True
        self.set_calls: list = []
        self.action_calls: list = []
        self.scene_calls: list = []
        self.scenes_list_calls: list = []
        self.prop_values = {
            ("did_light", 2, 1): True,
            ("did_light", 2, 2): 80,
            ("did_light", 2, 3): 1,
            ("did_sensor", 3, 1): 25.5,
            ("did_sensor", 4, 1): 10,
            ("did_lock", 2, 1): True,
            # 蓝牙温湿度计(did 含点)与走廊小夜灯(未分房间)、共享插座
            ("blt.3.abc123", 3, 1): 22.0,
            ("blt.3.abc123", 4, 1): 90,
            ("did_norm", 2, 1): False,
            ("did_norm", 2, 2): 50,
            ("did_norm", 2, 3): 0,
            ("did_shared", 2, 1): True,
            ("did_shared", 2, 2): 100,
            ("did_shared", 2, 3): 0,
        }

    def get_homes_list(self):
        return [
            {
                "id": "home1",
                "name": "我的家",
                "roomlist": [
                    {"name": "客厅", "dids": ["did_light", "did_speaker"]},
                    {"name": "卧室", "dids": ["did_sensor", "did_lock", "did_offline", "blt.3.abc123"]},
                ],
                # did_norm 是"未分配房间"的家庭级设备,只出现在 home 级 dids
                "dids": ["did_norm"],
            }
        ]

    def get_devices_list(self, home_id=None):
        return [
            {"did": "did_light", "name": "客厅台灯", "model": LIGHT_MODEL, "isOnline": True, "home_id": "home1"},
            {"did": "did_sensor", "name": "卧室温湿度计", "model": SENSOR_MODEL, "isOnline": True, "home_id": "home1"},
            {"did": "blt.3.abc123", "name": "蓝牙温湿度计", "model": SENSOR_MODEL, "isOnline": True, "home_id": "home1"},
            {"did": "did_lock", "name": "卧室门锁", "model": LOCK_MODEL, "isOnline": True, "home_id": "home1"},
            {"did": "did_speaker", "name": "小爱音箱", "model": SPEAKER_MODEL, "isOnline": True, "home_id": "home1"},
            {"did": "did_offline", "name": "离线插座", "model": "fake.plug.v1", "isOnline": False, "home_id": "home1"},
            {"did": "did_norm", "name": "走廊小夜灯", "model": LIGHT_MODEL, "isOnline": True, "home_id": "home1"},
        ]

    def get_shared_devices_list(self):
        # 设备级共享:只出现在这里,不在 get_devices_list,home_id 为上游哨兵值 'shared'
        return [
            {"did": "did_shared", "name": "好友的插座", "model": LIGHT_MODEL, "isOnline": True, "home_id": "shared"},
        ]

    def get_devices_prop(self, data):
        assert isinstance(data, list)
        out = []
        for item in data:
            key = (item["did"], item["siid"], item["piid"])
            if key in self.prop_values:
                out.append(
                    {
                        "did": item["did"],
                        "siid": item["siid"],
                        "piid": item["piid"],
                        "value": self.prop_values[key],
                        "code": 0,
                        "updateTime": 1751700000,
                    }
                )
            else:
                out.append({**item, "code": -704042011})
        return out

    def set_devices_prop(self, data):
        self.set_calls.append(data)
        if isinstance(data, dict):
            return {**data, "code": 0}
        return [{**item, "code": 0} for item in data]

    def run_action(self, data):
        self.action_calls.append(data)
        return {**data, "code": 0}

    def get_scenes_list(self, home_id=None):
        self.scenes_list_calls.append(home_id)
        return [
            {"scene_id": "sc1", "name": "回家模式", "home_id": "home1"},
            {"scene_id": "sc2", "name": "离家模式", "home_id": "home1"},
        ]

    def run_scene(self, scene_id, home_id):
        self.scene_calls.append((scene_id, home_id))
        return True

    def get_consumable_items(self, home_id=None):
        return [{"name": "滤芯", "value": "剩余 20%"}]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        auth_path=tmp_path / "auth" / "auth.json",
        state_dir=tmp_path / "state",
    )
    s.auth_path.parent.mkdir(parents=True, exist_ok=True)
    s.ensure_dirs()
    # 假 spec 写入磁盘缓存,get_device_info 直接命中,无需联网
    for model, spec in ALL_SPECS.items():
        (s.spec_cache_dir / f"{model}.json").write_text(
            json.dumps(spec, ensure_ascii=False), encoding="utf-8"
        )
    return s


@pytest.fixture
def fake_api() -> FakeAPI:
    return FakeAPI()
