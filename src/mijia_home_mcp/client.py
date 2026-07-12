"""HomeClient:在 mijiaAPI 之上做缓存、批量拉取、快照构建与 diff。

上游 mijiaAPI 的读属性是逐属性一次云端调用;这里全部走批量接口
get_devices_prop([{did,siid,piid},...]),整屋快照只需少量请求。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Optional, Union

from mijiaAPI import get_device_info

from .config import Settings
from .semantics import (
    humanize_value,
    is_fault,
    is_low_battery,
    sort_props_for_snapshot,
)

UNKNOWN_ROOM = "未分房间"
SHARED_HOME = "共享设备"
_CACHE_TTL_S = 60.0


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_online(device: dict) -> bool:
    for key in ("isOnline", "is_online", "online"):
        if key in device:
            return bool(device[key])
    # 字段缺失时按在线处理,让属性读取自己失败并暴露错误
    return True


class DeviceResolveError(Exception):
    """设备名/did 无法唯一定位时抛出,message 面向 LLM 可读。"""


class DeviceOpError(Exception):
    """设备读写操作失败(值非法/云端返回错误码),message 面向 LLM 可读。"""


class HomeClient:
    def __init__(self, api: Any, settings: Settings):
        self.api = api
        self.settings = settings
        self._cache: dict[str, tuple[float, Any]] = {}
        self._spec_memo: dict[str, dict] = {}

    # ---------- 基础数据(带 TTL 缓存) ----------

    def _cached(self, key: str, loader):
        hit = self._cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < _CACHE_TTL_S:
            return hit[1]
        value = loader()
        self._cache[key] = (time.monotonic(), value)
        return value

    def invalidate_cache(self) -> None:
        self._cache.clear()

    def homes(self) -> list[dict]:
        return self._cached("homes", self.api.get_homes_list)

    def devices(self) -> list[dict]:
        """全部设备(含共享设备),并标注 _home/_room。"""

        def load() -> list[dict]:
            own = self.api.get_devices_list()
            try:
                shared = self.api.get_shared_devices_list()
            except Exception:
                shared = []
            did_seen = set()
            merged = []
            for dev in list(own) + list(shared):
                if dev.get("did") in did_seen:
                    continue
                did_seen.add(dev.get("did"))
                merged.append(dict(dev))

            # 家庭归属以设备自带的 home_id 为准(上游为共享设备写入 'shared');
            # roomlist 只用来确定房间,未分配房间的自有设备落在 UNKNOWN_ROOM。
            home_names: dict[str, str] = {}
            room_of: dict[str, str] = {}
            for home in self.homes():
                home_name = home.get("name") or str(home.get("id"))
                home_names[str(home.get("id"))] = home_name
                for room in home.get("roomlist", []) or []:
                    for did in room.get("dids", []) or []:
                        room_of[did] = room.get("name") or UNKNOWN_ROOM
            for dev in merged:
                home_id = str(dev.get("home_id")) if dev.get("home_id") is not None else ""
                dev["_home"] = home_names.get(home_id, SHARED_HOME)
                dev["_room"] = room_of.get(dev.get("did"), UNKNOWN_ROOM)
            return merged

        return self._cached("devices", load)

    # ---------- 设备定位 ----------

    def resolve_device(self, ident: str) -> dict:
        """按 did 精确 → 名称精确 → 名称唯一子串 的顺序定位设备。"""
        ident = (ident or "").strip()
        if not ident:
            raise DeviceResolveError("设备标识为空,请提供设备名称或 did")
        devices = self.devices()

        by_did = [d for d in devices if d.get("did") == ident]
        if by_did:
            return by_did[0]

        exact = [d for d in devices if d.get("name") == ident]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            dids = ", ".join(d.get("did", "?") for d in exact)
            raise DeviceResolveError(
                f"有 {len(exact)} 个设备都叫「{ident}」,请改用 did 指定(候选: {dids})"
            )

        fuzzy = [d for d in devices if ident.lower() in (d.get("name") or "").lower()]
        if len(fuzzy) == 1:
            return fuzzy[0]
        if len(fuzzy) > 1:
            names = ", ".join(f"{d.get('name')}({d.get('did')})" for d in fuzzy[:10])
            raise DeviceResolveError(
                f"「{ident}」匹配到 {len(fuzzy)} 个设备,请用更精确的名称或 did。候选: {names}"
            )
        raise DeviceResolveError(
            f"未找到设备「{ident}」。可先调用 list_devices 查看全部设备名称"
        )

    # ---------- spec ----------

    def spec(self, model: str) -> dict:
        if model in self._spec_memo:
            return self._spec_memo[model]
        info = get_device_info(model, cache_path=self.settings.spec_cache_dir)
        self._spec_memo[model] = info
        return info

    def _prefetch_specs(self, models: list[str]) -> dict[str, dict]:
        """并发拉取(命中磁盘缓存则近乎零开销),失败的 model 映射到 {'error': ...}。"""
        out: dict[str, dict] = {}

        def fetch(model: str) -> tuple[str, dict]:
            try:
                return model, self.spec(model)
            except Exception as exc:  # noqa: BLE001 - spec 失败不应中断快照
                return model, {"error": str(exc)}

        unique = sorted(set(models))
        if not unique:
            return out
        with ThreadPoolExecutor(max_workers=self.settings.spec_workers) as pool:
            for model, info in pool.map(fetch, unique):
                out[model] = info
        return out

    # ---------- 批量属性读取 ----------

    def batch_get_props(self, requests: list[dict]) -> dict[tuple, dict]:
        """分块批量读取属性,返回 {(did, siid, piid): result}。

        单块失败降级记录 error,不中断整体。
        刻意串行:上游 mijiaAPI 共享 requests.Session 且请求内可能触发
        token 刷新重建 session,并发调用有竞态风险。
        """
        results: dict[tuple, dict] = {}
        chunk = self.settings.snapshot_chunk_size
        for i in range(0, len(requests), chunk):
            part = requests[i : i + chunk]
            payload = [
                {"did": r["did"], "siid": r["siid"], "piid": r["piid"]} for r in part
            ]
            try:
                ret = self.api.get_devices_prop(payload)
                if isinstance(ret, dict):
                    ret = [ret]
                for item in ret:
                    key = (item.get("did"), item.get("siid"), item.get("piid"))
                    results[key] = item
            except Exception as exc:  # noqa: BLE001 - 单块失败降级
                for r in part:
                    results[(r["did"], r["siid"], r["piid"])] = {
                        "code": -1,
                        "error": str(exc),
                    }
        return results

    # ---------- 快照 ----------

    def _filter_devices(self, home: Optional[str]) -> list[dict]:
        devices = self.devices()
        if not home:
            return devices
        home = home.strip()
        # 按家庭名称或 id 匹配
        matched_names = set()
        for h in self.homes():
            if home in (h.get("name"), str(h.get("id"))):
                matched_names.add(h.get("name") or str(h.get("id")))
        if home == SHARED_HOME:
            matched_names.add(SHARED_HOME)
        if not matched_names:
            known = ", ".join(sorted({d["_home"] for d in devices}))
            raise DeviceResolveError(f"未找到家庭「{home}」。可选: {known}")
        return [d for d in devices if d["_home"] in matched_names]

    def build_snapshot(
        self,
        home: Optional[str] = None,
        detail: str = "compact",
        max_props_per_device: int = 8,
        room: Optional[str] = None,
    ) -> tuple[dict, dict]:
        """构建全屋快照。

        返回 (snapshot, raw_state):
        - snapshot: 给 LLM 的结构化结果(home→room→device→语义化状态)
        - raw_state: 用于 diff 持久化的原始值映射
        """
        t0 = time.monotonic()
        devices = self._filter_devices(home)
        if room:
            room = room.strip()
            room_devices = [d for d in devices if d["_room"] == room]
            if not room_devices:
                known = ", ".join(sorted({d["_room"] for d in devices}))
                raise DeviceResolveError(f"未找到房间「{room}」。可选: {known}")
            devices = room_devices
        full = detail == "full"
        if full:
            max_props_per_device = max(max_props_per_device, 24)

        online_devices = [d for d in devices if _is_online(d)]
        specs = self._prefetch_specs([d.get("model", "") for d in online_devices])

        # 组装批量请求,同时记住 (did,siid,piid) → (device, prop)
        requests: list[dict] = []
        req_meta: dict[tuple, tuple[dict, dict]] = {}
        spec_errors: dict[str, str] = {}
        for dev in online_devices:
            spec_info = specs.get(dev.get("model", ""), {})
            if "error" in spec_info:
                spec_errors[dev.get("name", dev.get("did", "?"))] = spec_info["error"]
                continue
            props = sort_props_for_snapshot(
                spec_info.get("properties", []), max_props_per_device
            )
            for prop in props:
                method = prop.get("method", {})
                key = (dev["did"], method.get("siid"), method.get("piid"))
                requests.append(
                    {"did": dev["did"], "siid": method.get("siid"), "piid": method.get("piid")}
                )
                req_meta[key] = (dev, prop)

        prop_results = self.batch_get_props(requests) if requests else {}

        # 按设备聚合状态
        device_state: dict[str, dict] = {}
        attention_low_battery: list[str] = []
        attention_faults: list[str] = []
        fetch_error_count = 0
        for key, result in prop_results.items():
            meta = req_meta.get(key)
            if meta is None:
                # 云端返回了未请求的键(或类型不一致),跳过
                continue
            dev, prop = meta
            did = dev["did"]
            state = device_state.setdefault(did, {"state": {}, "raw": {}})
            prop_name = prop.get("name", f"{key[1]}.{key[2]}")
            if result.get("code", -1) == 0:
                raw_value = result.get("value")
                state["raw"][prop_name] = raw_value
                if full:
                    state["state"][prop_name] = {
                        "value": raw_value,
                        "text": humanize_value(prop, raw_value),
                        "desc": prop.get("description", ""),
                        "updated_at": result.get("updateTime"),
                    }
                else:
                    state["state"][prop_name] = humanize_value(prop, raw_value)
                if is_low_battery(prop_name, raw_value):
                    attention_low_battery.append(
                        f"{dev.get('name')}: 电量 {raw_value}%"
                    )
                if is_fault(prop_name, raw_value):
                    attention_faults.append(
                        f"{dev.get('name')}: fault={humanize_value(prop, raw_value)}"
                    )
            else:
                fetch_error_count += 1
                if full:
                    state["state"][prop_name] = {
                        "error": result.get("error", f"code={result.get('code')}")
                    }

        # home → room → devices 分组
        grouped: dict[str, dict[str, list[dict]]] = {}
        offline_names: list[str] = []
        raw_state: dict[str, dict] = {}
        for dev in devices:
            online = _is_online(dev)
            entry: dict[str, Any] = {"name": dev.get("name"), "online": online}
            if full:
                entry["did"] = dev.get("did")
                entry["model"] = dev.get("model")
            if online:
                ds = device_state.get(dev["did"], {"state": {}, "raw": {}})
                entry["state"] = ds["state"]
                raw_values = ds["raw"]
            else:
                offline_names.append(f"{dev.get('name')}({dev['_room']})")
                raw_values = {}
            grouped.setdefault(dev["_home"], {}).setdefault(dev["_room"], []).append(
                entry
            )
            raw_state[dev["did"]] = {
                "name": dev.get("name"),
                "online": online,
                "values": raw_values,
            }

        snapshot = {
            "ts": _now_iso(),
            "homes": [
                {
                    "name": home_name,
                    "rooms": [
                        {"name": room_name, "devices": devs}
                        for room_name, devs in rooms.items()
                    ],
                }
                for home_name, rooms in grouped.items()
            ],
            "attention": {
                "offline": offline_names,
                "low_battery": attention_low_battery,
                "faults": attention_faults,
                "spec_errors": spec_errors,
            },
            "stats": {
                "devices_total": len(devices),
                "devices_online": len(online_devices),
                "devices_offline": len(devices) - len(online_devices),
                "props_fetched": len(prop_results) - fetch_error_count,
                "props_failed": fetch_error_count,
                "elapsed_s": round(time.monotonic() - t0, 2),
            },
        }
        raw = {"ts": snapshot["ts"], "devices": raw_state}
        return snapshot, raw

    # ---------- 设备写操作(不经 mijiaDevice,避免其每次全量拉设备列表,
    # 且支持设备级共享的设备) ----------

    def _find_spec_entry(self, dev: dict, name: str, kind: str) -> dict:
        spec = self.spec(dev.get("model", ""))
        entries = spec.get("properties" if kind == "property" else "actions", [])
        for entry in entries:
            if entry.get("name") == name:
                return entry
        known = sorted(e.get("name", "?") for e in entries)
        label = "属性" if kind == "property" else "动作"
        raise DeviceOpError(
            f"设备 {dev.get('name')} 没有{label}「{name}」。可用{label}: {known}"
        )

    @staticmethod
    def _coerce_value(prop: dict, value: Union[bool, int, float, str]):
        """把 LLM 传来的字符串值按 spec 类型强转并校验范围/枚举。"""
        ptype = prop.get("type")
        try:
            if ptype == "bool":
                if isinstance(value, bool):
                    coerced: Any = value
                elif str(value).strip().lower() in ("true", "1", "on", "yes"):
                    coerced = True
                elif str(value).strip().lower() in ("false", "0", "off", "no"):
                    coerced = False
                else:
                    raise ValueError(f"无效布尔值: {value}")
            elif ptype in ("int", "uint"):
                coerced = int(float(value))
            elif ptype == "float":
                coerced = float(value)
            else:
                coerced = str(value)
        except (TypeError, ValueError) as exc:
            raise DeviceOpError(
                f"值「{value}」无法转换为 {ptype} 类型: {exc}"
            ) from exc

        value_list = prop.get("value-list")
        if value_list:
            valid = [item.get("value") for item in value_list]
            if coerced not in valid:
                options = ", ".join(
                    f"{item.get('value')}({item.get('description')})"
                    for item in value_list
                )
                raise DeviceOpError(f"值「{value}」不在枚举内。可选: {options}")
        prange = prop.get("range")
        if prange and isinstance(coerced, (int, float)):
            low, high = prange[0], prange[1]
            if not (low <= coerced <= high):
                raise DeviceOpError(
                    f"值 {coerced} 超出范围,应在 [{low}, {high}] 之间"
                )
        return coerced

    def set_property(self, dev: dict, prop_name: str, value) -> Any:
        """设置设备属性,返回实际下发的强转值。"""
        prop = self._find_spec_entry(dev, prop_name, "property")
        if "w" not in prop.get("rw", ""):
            raise DeviceOpError(f"属性「{prop_name}」不可写入")
        coerced = self._coerce_value(prop, value)
        method = prop.get("method", {})
        ret = self.api.set_devices_prop(
            {
                "did": dev["did"],
                "siid": method.get("siid"),
                "piid": method.get("piid"),
                "value": coerced,
            }
        )
        result = ret[0] if isinstance(ret, list) else ret
        code = result.get("code", -1)
        if code not in (0, 1):  # 1 = 网关已接收,无法确认执行结果
            raise DeviceOpError(
                f"设置 {dev.get('name')} 的 {prop_name} 失败,云端返回 code={code}"
            )
        return coerced

    def invoke_action(
        self,
        dev: dict,
        action_name: str,
        value: Optional[list] = None,
        in_args: Optional[list] = None,
    ) -> None:
        """执行设备动作。value 对应云端 'value' 键,in_args 对应 'in' 键
        (小爱音箱 execute-text-directive 使用后者,与上游行为一致)。"""
        action = self._find_spec_entry(dev, action_name, "action")
        method = action.get("method", {})
        payload: dict[str, Any] = {
            "did": dev["did"],
            "siid": method.get("siid"),
            "aiid": method.get("aiid"),
        }
        if value is not None:
            payload["value"] = list(value)
        if in_args is not None:
            payload["in"] = list(in_args)
        ret = self.api.run_action(payload)
        result = ret[0] if isinstance(ret, list) else ret
        code = result.get("code", -1)
        if code not in (0, 1):
            raise DeviceOpError(
                f"执行 {dev.get('name')} 的动作 {action_name} 失败,云端返回 code={code}"
            )

    # ---------- diff 与持久化 ----------

    def _snapshot_path(self, home: Optional[str]):
        key = hashlib.md5((home or "__all__").encode("utf-8")).hexdigest()[:12]
        return self.settings.snapshot_dir / f"last_{key}.json"

    def load_last_raw(self, home: Optional[str]) -> Optional[dict]:
        path = self._snapshot_path(home)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def save_raw(self, home: Optional[str], raw: dict) -> None:
        """原子写基线文件:先写临时文件再 os.replace,避免并发/中断产生半截 JSON。"""
        self.settings.ensure_dirs()
        path = self._snapshot_path(home)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        os.replace(tmp, path)

    @staticmethod
    def diff_raw(prev: dict, new: dict) -> dict:
        changes: list[dict] = []
        prev_devices = prev.get("devices", {})
        new_devices = new.get("devices", {})
        for did, nd in new_devices.items():
            pd = prev_devices.get(did)
            name = nd.get("name", did)
            if pd is None:
                changes.append({"type": "device_added", "device": name})
                continue
            if pd.get("online") and not nd.get("online"):
                changes.append({"type": "went_offline", "device": name})
            elif not pd.get("online") and nd.get("online"):
                changes.append({"type": "came_online", "device": name})
            for prop, new_value in nd.get("values", {}).items():
                if prop in pd.get("values", {}) and pd["values"][prop] != new_value:
                    changes.append(
                        {
                            "type": "prop_changed",
                            "device": name,
                            "prop": prop,
                            "from": pd["values"][prop],
                            "to": new_value,
                        }
                    )
        for did, pd in prev_devices.items():
            if did not in new_devices:
                changes.append(
                    {"type": "device_removed", "device": pd.get("name", did)}
                )
        return {
            "since": prev.get("ts"),
            "until": new.get("ts"),
            "change_count": len(changes),
            "changes": changes,
        }
