"""把 miot 的原始值翻译成人能看的东西,顺带定义哪些属性值得优先读。"""

from __future__ import annotations

from typing import Any

# 快照时每台设备只读前 N 个属性,按这个顺序挑。
# 名字是 miot-spec 的 property 短名,和 get_device_info 返回的 name 一致。
PRIORITY_PROPS: tuple[str, ...] = (
    "on",
    "power",
    "status",
    "fault",
    "mode",
    "temperature",
    "relative-humidity",
    "humidity",
    "target-temperature",
    "brightness",
    "color-temperature",
    "battery-level",
    "door-state",
    "contact-state",
    "motion-state",
    "occupancy-status",
    "illumination",
    "pm2.5-density",
    "co2-density",
    "tvoc-density",
    "air-quality",
    "filter-life-level",
    "water-level",
    "fan-level",
    "speed-level",
    "target-humidity",
    "charging-state",
    "remain-time",
    "physical-controls-locked",
)

_PRIORITY_INDEX = {name: idx for idx, name in enumerate(PRIORITY_PROPS)}

# model 里带这些词的按危险设备处理,默认不给控制
DANGEROUS_MODEL_PATTERNS: tuple[str, ...] = ("lock", "camera", "gas", "valve", "safe")

# 开关属性的常见名字,不同厂商的 spec 叫法不统一
POWER_PROP_ALIASES: tuple[str, ...] = ("on", "power", "switch-status", "switch")

# 耗材 state:云端拿 value 对 inadeq(不足线)/exhaust(耗尽线)两级阈值
# 算出来的三态。枚举无官方文档,由真实数据 + hass-xiaomi-miot#2422 抓包
# + 阈值字段结构交叉验证。
CONSUMABLE_STATE = {1: "充足", 2: "不足", 3: "耗尽"}


def consumable_status(state) -> str:
    return CONSUMABLE_STATE.get(state, f"未知(state={state})")

LOW_BATTERY_THRESHOLD = 15


def sort_props_for_snapshot(props: list[dict], max_props: int) -> list[dict]:
    """可读属性按优先级排序后截前 max_props 个。"""
    readable = [p for p in props if "r" in p.get("rw", "")]
    readable.sort(key=lambda p: _PRIORITY_INDEX.get(p.get("name", ""), len(PRIORITY_PROPS)))
    return readable[:max_props]


def humanize_value(prop: dict, value: Any) -> Any:
    """枚举值换成描述文本,bool 换成 开启/关闭,其他原样。"""
    value_list = prop.get("value-list")
    if value_list:
        for item in value_list:
            if item.get("value") == value:
                desc = item.get("description") or str(value)
                return desc
        return value
    if isinstance(value, bool):
        return "开启" if value else "关闭"
    return value


def is_dangerous_model(model: str) -> bool:
    model = (model or "").lower()
    return any(pat in model for pat in DANGEROUS_MODEL_PATTERNS)


def is_low_battery(prop_name: str, value: Any) -> bool:
    if prop_name != "battery-level":
        return False
    try:
        return float(value) <= LOW_BATTERY_THRESHOLD
    except (TypeError, ValueError):
        return False


def is_fault(prop_name: str, value: Any) -> bool:
    if prop_name != "fault":
        return False
    return value not in (0, "0", None, "", False)
