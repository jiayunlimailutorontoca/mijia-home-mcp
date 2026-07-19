"""语义化与危险设备判定。is_dangerous_model 是控制门控的第一道闸,
判错就 fail-open,所以每类危险设备和每个易误伤的正常设备都钉一个用例。
"""

import pytest

from mijia_home_mcp.semantics import (
    humanize_value,
    is_dangerous_model,
    is_fault,
    is_low_battery,
)

# 每类危险设备至少一个真实型号,别只测门锁
DANGEROUS_MODELS = [
    "fake.lock.v1",            # 门锁(类别段 lock)
    "loock.lock.t1",           # 鹿客门锁
    "loock.cateye.hk1",        # 鹿客可视猫眼——子串里没有连续 "lock",旧实现漏判
    "madv.cateye.x1",          # 另一家可视猫眼
    "chuangmi.doorbell.v1",    # 可视门铃
    "isa.camera.hlc7",         # 摄像头(类别段 camera)
    "chuangmi.ipc.1080",       # IP 摄像头(类别段 ipc)
    "lumi.sensor_gas.mcn02",   # 燃气报警器(复合类别段,靠子串 gas 兜住)
    "lumi.valve.agl01",        # 燃气/水阀
    "loock.safe.v1",           # 保险柜
    "yunmi.safebox.x1",        # 保险柜(另一种命名)
]

# 正常设备,一个都不能被误判为危险(否则用户白名单形同虚设)
SAFE_MODELS = [
    "fake.light.v1",
    "yeelink.light.lamp4",
    "zimi.clock.v1",           # clock 含 "lock" 子串,不能误判
    "xiaomi.safety.v1",        # safety 含 "safe" 子串,不能误判
    "xiaomi.wifispeaker.x1",   # 音箱另有 check_speaker_directive 专管,这里不算危险
    "chuangmi.plug.v3",
    "zhimi.airpurifier.mb4",
]


@pytest.mark.parametrize("model", DANGEROUS_MODELS)
def test_dangerous_models_flagged(model):
    assert is_dangerous_model(model) is True, f"{model} 应判为危险设备"


@pytest.mark.parametrize("model", SAFE_MODELS)
def test_safe_models_not_flagged(model):
    assert is_dangerous_model(model) is False, f"{model} 不应判为危险设备"


def test_dangerous_model_empty_and_garbage():
    assert is_dangerous_model("") is False
    assert is_dangerous_model(None) is False  # type: ignore[arg-type]
    assert is_dangerous_model("单段没有点") is False


def test_dangerous_model_case_insensitive():
    assert is_dangerous_model("Loock.Cateye.HK1") is True
    assert is_dangerous_model("FAKE.LOCK.V1") is True


def test_humanize_value_enum_and_bool():
    prop = {"value-list": [{"value": 1, "description": "夜灯模式"}]}
    assert humanize_value(prop, 1) == "夜灯模式"
    # 枚举缺失映射:原值返回,不炸
    assert humanize_value(prop, 99) == 99
    # 无 value-list 的 bool 走开启/关闭
    assert humanize_value({}, True) == "开启"
    assert humanize_value({}, False) == "关闭"
    # 其他类型原样
    assert humanize_value({}, 25.5) == 25.5


def test_low_battery_and_fault():
    assert is_low_battery("battery-level", 10) is True
    assert is_low_battery("battery-level", 80) is False
    assert is_low_battery("battery-level", None) is False  # 脏值不炸
    assert is_low_battery("temperature", 5) is False       # 只看 battery-level
    assert is_fault("fault", 3) is True
    assert is_fault("fault", 0) is False
    assert is_fault("mode", 3) is False
