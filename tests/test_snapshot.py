"""快照和 diff 的测试,全离线。"""

import pytest

from mijia_home_mcp.client import DeviceResolveError, HomeClient

TOTAL_DEVICES = 8  # 7 台自有(含未分房间的走廊小夜灯) + 1 台设备级共享
ONLINE_DEVICES = 7


def _client(fake_api, settings) -> HomeClient:
    return HomeClient(fake_api, settings)


def _find_home(snapshot, name):
    return next(h for h in snapshot["homes"] if h["name"] == name)


def test_snapshot_structure_and_semantics(fake_api, settings):
    client = _client(fake_api, settings)
    snapshot, raw = client.build_snapshot()

    assert snapshot["stats"]["devices_total"] == TOTAL_DEVICES
    assert snapshot["stats"]["devices_online"] == ONLINE_DEVICES
    assert snapshot["stats"]["devices_offline"] == 1

    home = _find_home(snapshot, "我的家")
    rooms = {r["name"]: r for r in home["rooms"]}
    assert set(rooms) == {"客厅", "卧室", "未分房间"}

    light = next(d for d in rooms["客厅"]["devices"] if d["name"] == "客厅台灯")
    # bool → 开启/关闭,枚举 → value-list 描述
    assert light["state"]["on"] == "开启"
    assert light["state"]["mode"] == "夜灯模式"
    assert light["state"]["brightness"] == 80

    # 离线设备无 state 且进入 attention
    offline = next(d for d in rooms["卧室"]["devices"] if d["name"] == "离线插座")
    assert offline["online"] is False
    assert "state" not in offline
    assert any("离线插座" in item for item in snapshot["attention"]["offline"])

    # 低电量(10 <= 15)进入 attention
    assert any("卧室温湿度计" in item for item in snapshot["attention"]["low_battery"])

    # raw 里存原始值供 diff
    assert raw["devices"]["did_light"]["values"]["on"] is True


def test_unassigned_room_device_belongs_to_own_home(fake_api, settings):
    """未分配房间的自有设备应归属真实家庭(home_id 为准),而不是「共享设备」。"""
    client = _client(fake_api, settings)
    dev = client.resolve_device("走廊小夜灯")
    assert dev["_home"] == "我的家"
    assert dev["_room"] == "未分房间"

    # 按家庭过滤的快照必须包含它
    snapshot, _ = client.build_snapshot(home="我的家")
    home = _find_home(snapshot, "我的家")
    all_names = [d["name"] for r in home["rooms"] for d in r["devices"]]
    assert "走廊小夜灯" in all_names


def test_shared_device_grouped_separately(fake_api, settings):
    client = _client(fake_api, settings)
    dev = client.resolve_device("好友的插座")
    assert dev["_home"] == "共享设备"


def test_snapshot_full_detail(fake_api, settings):
    client = _client(fake_api, settings)
    snapshot, _ = client.build_snapshot(detail="full")
    home = _find_home(snapshot, "我的家")
    light = next(
        d for r in home["rooms"] for d in r["devices"] if d["name"] == "客厅台灯"
    )
    assert light["did"] == "did_light"
    assert light["state"]["on"]["value"] is True
    assert light["state"]["on"]["text"] == "开启"
    assert light["state"]["on"]["updated_at"] == 1751700000


def test_diff_detects_changes(fake_api, settings):
    client = _client(fake_api, settings)
    _, raw1 = client.build_snapshot()

    # 改属性 + 设备离线
    fake_api.prop_values[("did_light", 2, 2)] = 45
    fake_api.prop_values[("did_light", 2, 1)] = False
    client.invalidate_cache()
    _, raw2 = client.build_snapshot()

    diff = client.diff_raw(raw1, raw2)
    kinds = {(c["type"], c.get("prop")) for c in diff["changes"]}
    assert ("prop_changed", "brightness") in kinds
    assert ("prop_changed", "on") in kinds
    changed = next(c for c in diff["changes"] if c.get("prop") == "brightness")
    assert changed["from"] == 80 and changed["to"] == 45


def test_snapshot_persistence_roundtrip(fake_api, settings):
    client = _client(fake_api, settings)
    assert client.load_last_raw(None) is None
    _, raw = client.build_snapshot()
    client.save_raw(None, raw)
    loaded = client.load_last_raw(None)
    assert loaded == raw
    # home 过滤使用独立的存储键
    assert client.load_last_raw("我的家") is None
    # 原子写不留临时文件
    assert not list(settings.snapshot_dir.glob("*.tmp"))


def test_resolve_device(fake_api, settings):
    client = _client(fake_api, settings)
    assert client.resolve_device("did_light")["name"] == "客厅台灯"
    assert client.resolve_device("客厅台灯")["did"] == "did_light"
    assert client.resolve_device("卧室温湿")["did"] == "did_sensor"
    # did 含点的蓝牙设备按 did 精确解析
    assert client.resolve_device("blt.3.abc123")["name"] == "蓝牙温湿度计"

    with pytest.raises(DeviceResolveError, match="未找到"):
        client.resolve_device("不存在的设备")
    # 「温湿度」同时命中两台 → 要求更精确
    with pytest.raises(DeviceResolveError, match="匹配到"):
        client.resolve_device("温湿度")


def test_set_property_coercion_and_shared_device(fake_api, settings):
    client = _client(fake_api, settings)
    dev = client.resolve_device("客厅台灯")
    coerced = client.set_property(dev, "on", "false")
    assert coerced is False
    assert fake_api.set_calls[-1]["value"] is False

    # 设备级共享的设备同样可写(不经 mijiaDevice 的自有列表校验)
    shared = client.resolve_device("好友的插座")
    client.set_property(shared, "brightness", "66")
    assert fake_api.set_calls[-1]["did"] == "did_shared"
    assert fake_api.set_calls[-1]["value"] == 66

    from mijia_home_mcp.client import DeviceOpError

    with pytest.raises(DeviceOpError, match="超出范围"):
        client.set_property(dev, "brightness", "999")
    with pytest.raises(DeviceOpError, match="枚举"):
        client.set_property(dev, "mode", "7")
    with pytest.raises(DeviceOpError, match="没有属性"):
        client.set_property(dev, "no-such-prop", "1")


def test_snapshot_short_cache(fake_api, settings):
    """30s 内同口径重复调用命中缓存;force_fresh 与不同口径绕过缓存。"""
    client = _client(fake_api, settings)
    snap1, _ = client.build_snapshot()
    assert "cached" not in snap1

    fake_api.prop_values[("did_light", 2, 2)] = 33
    snap2, _ = client.build_snapshot()  # 命中缓存,看不到新值
    assert snap2.get("cached") is True

    snap3, _ = client.build_snapshot(force_fresh=True)  # 强制新鲜
    assert "cached" not in snap3
    light = next(
        d
        for h in snap3["homes"]
        for r in h["rooms"]
        for d in r["devices"]
        if d["name"] == "客厅台灯"
    )
    assert light["state"]["brightness"] == 33

    # 不同口径(home 过滤)不共享缓存
    snap4, _ = client.build_snapshot(home="我的家")
    assert "cached" not in snap4

    # invalidate 后重新拉取
    client.invalidate_cache()
    snap5, _ = client.build_snapshot()
    assert "cached" not in snap5


def test_diff_consumables(fake_api, settings):
    """state 跃迁产出事件;恶化和恢复都报;新增条目不报。"""
    client = _client(fake_api, settings)
    prev = client.consumables()

    # 滤网 2→3 恶化,刷头 3→1 恢复(换了新刷头)
    items = fake_api.get_consumable_items()
    items[0]["details"][1]["state"] = 3  # 滤网
    items[1]["details"]["state"] = 1  # 刷头
    fake_api.get_consumable_items = lambda home_id=None: items
    cur = client.consumables()

    changes = client.diff_consumables(prev, cur)
    by_prop = {c["prop"]: c for c in changes}
    assert by_prop["滤网"]["from"] == "不足" and by_prop["滤网"]["to"] == "耗尽"
    assert by_prop["刷头"]["from"] == "耗尽" and by_prop["刷头"]["to"] == "充足"
    assert all(c["type"] == "consumable_changed" for c in changes)
    assert len(changes) == 2, "拖布没变,不该出现"

    # 无变化 → 空
    assert client.diff_consumables(cur, cur) == []


def test_consumable_change_text():
    from mijia_home_mcp.notify import format_changes_text

    text = format_changes_text(
        [{"type": "consumable_changed", "device": "扫地机", "prop": "滤网",
          "from": "不足", "to": "耗尽"}]
    )
    assert text == "扫地机的耗材滤网耗尽了"


def test_same_name_homes_isolated_by_id(fake_api, settings):
    """两个家庭都叫「我的家」时,按 ID 过滤必须只出一家的设备。"""
    orig_homes = fake_api.get_homes_list

    def homes_with_dup():
        return orig_homes() + [
            {"id": "home2", "name": "我的家",
             "roomlist": [{"id": "r2", "name": "客厅", "dids": ["did_dup"]}]}
        ]

    orig_devs = fake_api.get_devices_list
    fake_api.get_homes_list = homes_with_dup
    fake_api.get_devices_list = lambda home_id=None: orig_devs() + [
        {"did": "did_dup", "name": "另一家的灯", "model": "fake.light.v1",
         "isOnline": True, "home_id": "home2"}
    ]
    client = _client(fake_api, settings)

    only_home1 = client._filter_devices("home1")
    assert all(d["home_id"] == "home1" for d in only_home1)
    only_home2 = client._filter_devices("home2")
    assert [d["name"] for d in only_home2] == ["另一家的灯"]
    # 按名字过滤命中两家(名字就是歧义的),但至少要包含两家全部设备
    by_name = client._filter_devices("我的家")
    assert len(by_name) == len(only_home1) + 1


def test_consumable_state4_not_alerted(fake_api, settings):
    """state=4(寿命未上报)是数据缺失,不进 needs_attention;字符串 state 不崩。"""
    items = fake_api.get_consumable_items()
    items[1]["details"]["state"] = 4  # 刷头 → 未上报
    items[0]["details"][0]["state"] = "2"  # 拖布 → 云端偶发字符串
    fake_api.get_consumable_items = lambda home_id=None: items
    client = _client(fake_api, settings)
    report = client.consumables()
    by_item = {i["item"]: i for i in report["items"]}
    assert by_item["刷头"]["status"] == "未上报"
    assert by_item["拖布"]["state"] == 2, "字符串 state 应转 int"
    needs_items = {i["item"] for i in report["needs_attention"]}
    assert "刷头" not in needs_items
    assert needs_items == {"拖布", "滤网"}


def test_diff_consumables_same_name_devices(fake_api, settings):
    """两台同名设备靠 did 区分,状态不变时不得产生误报。"""
    items = fake_api.get_consumable_items() + [
        {"name": "假扫地机", "did": "did_vacuum2", "room_id": "room_keting",
         "details": [{"description": "滤网", "value": "90", "state": 1,
                      "inadeq": "{}", "left_time": "", "reset_method": ""}]},
    ]
    fake_api.get_consumable_items = lambda home_id=None: items
    client = _client(fake_api, settings)
    snap = client.consumables()
    assert client.diff_consumables(snap, snap) == [], "同名设备状态未变不该报"


def test_filter_devices_by_home(fake_api, settings):
    client = _client(fake_api, settings)
    assert len(client._filter_devices("我的家")) == 7
    assert len(client._filter_devices("home1")) == 7
    assert len(client._filter_devices("共享设备")) == 1

    with pytest.raises(DeviceResolveError):
        client._filter_devices("不存在的家")
