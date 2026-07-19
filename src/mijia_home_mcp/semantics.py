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

# 危险设备判定。miot model 形如 brand.category.variant,第二段(category)
# 才是设备类别——靠这个判,而不是对整串做子串匹配。子串匹配会漏:
# loock.cateye.hk1(鹿客可视猫眼门锁)里没有连续的 "lock",却是门口
# 摄像头+门锁;也会误伤:"safe" 是 "safety" 的子串。
#
# 危险类别(第二段精确匹配):门锁/可视猫眼/可视门铃/摄像头/燃气或水阀/保险柜。
DANGEROUS_CATEGORIES: frozenset[str] = frozenset(
    {
        "lock",       # 门锁
        "cateye",     # 可视猫眼(常带开锁)
        "doorbell",   # 可视门铃
        "camera",     # 摄像头
        "ipc",        # 部分品牌的 IP 摄像头以 ipc 作类别段
        "gas",        # 燃气报警/切断阀
        "valve",      # 水阀/燃气阀
        "safe",       # 保险柜
        "safebox",    # 保险柜(另一种命名)
    }
)

# 类别段判不出时的子串兜底。只放"绝不会是正常设备名子串"的词,宁可误判
# 危险也不漏放。含 gas/valve 是因为燃气检测常用复合类别段(sensor_gas),
# 类别精确匹配会漏,而 gas/valve 作子串不会误伤正常设备。
# 故意不含 lock(会误伤 zimi.clock.*)和 safe(误伤 xiaomi.safety.*),
# 这两类只走上面的精确类别段。
_DANGEROUS_SUBSTRINGS: tuple[str, ...] = (
    "camera",
    "cateye",
    "doorbell",
    "gas",
    "valve",
)

# 开关属性的常见名字,不同厂商的 spec 叫法不统一
POWER_PROP_ALIASES: tuple[str, ...] = ("on", "power", "switch-status", "switch")

# 耗材 state:云端拿 value 对 inadeq(不足线)/exhaust(耗尽线)两级阈值
# 算出来的状态。枚举无官方文档,由真实数据 + hass-xiaomi-miot#2422 抓包
# + 阈值字段结构交叉验证。4 = 寿命从未上报(蓝牙设备常见),数据缺失
# 而非告警,不该进提醒。
CONSUMABLE_STATE = {1: "充足", 2: "不足", 3: "耗尽", 4: "未上报"}

# 该提醒用户的 state(4 是数据缺失,不算)
CONSUMABLE_ALERT_STATES = (2, 3)


def consumable_status(state) -> str:
    return CONSUMABLE_STATE.get(state, f"未知(state={state})")


def consumable_state_int(state) -> int:
    """state 容错转 int:云端偶发回字符串;转不动的当 0(不告警不排前)。"""
    try:
        return int(state)
    except (TypeError, ValueError):
        return 0

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
    """门锁/摄像头/门铃/燃气水阀/保险柜按危险设备对待。

    优先看 miot model 的类别段(brand.category.variant 的第二段),
    段命中即危险;取不到规范的三段式再退回子串兜底。
    """
    model = (model or "").lower()
    if not model:
        return False
    parts = model.split(".")
    if len(parts) >= 2 and parts[1] in DANGEROUS_CATEGORIES:
        return True
    return any(sub in model for sub in _DANGEROUS_SUBSTRINGS)


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
