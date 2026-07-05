"""面向 LLM 的语义化:关键属性优先级、值的人类可读化、危险设备识别。"""

from __future__ import annotations

from typing import Any

# 快照时优先读取的 miot 标准属性名,按重要性排序。
# 名称来自 miot-spec 的 property type 短名(与 mijiaAPI get_device_info 的 name 字段一致)。
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

# model 命中这些子串视为危险设备(锁/摄像头/燃气与水阀/保险柜),默认禁止控制
DANGEROUS_MODEL_PATTERNS: tuple[str, ...] = ("lock", "camera", "gas", "valve", "safe")

LOW_BATTERY_THRESHOLD = 15


def sort_props_for_snapshot(props: list[dict], max_props: int) -> list[dict]:
    """从 spec 的属性列表里选出快照要读的属性:可读、按优先级排序、截断。"""
    readable = [p for p in props if "r" in p.get("rw", "")]
    readable.sort(key=lambda p: _PRIORITY_INDEX.get(p.get("name", ""), len(PRIORITY_PROPS)))
    return readable[:max_props]


def humanize_value(prop: dict, value: Any) -> Any:
    """把原始属性值转成对 LLM 友好的表达。

    枚举值(value-list)映射为描述文本,bool 映射为 开启/关闭,其余原样返回。
    """
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
    """fault 属性非 0/非空视为异常。"""
    if prop_name != "fault":
        return False
    return value not in (0, "0", None, "", False)
