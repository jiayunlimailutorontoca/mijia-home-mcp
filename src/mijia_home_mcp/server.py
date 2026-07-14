"""MCP server 本体。读工具永远注册,控制工具要 enable_control 才有。

工具的 docstring 会被客户端展示给模型,所以那些写得比较啰嗦是故意的。
"""

from __future__ import annotations

import functools
import threading
from typing import Any, Literal, Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mijiaAPI import mijiaAPI
from mijiaAPI.errors import (
    APIError,
    DeviceActionError,
    DeviceGetError,
    DeviceNotFoundError,
    DeviceSetError,
    GetDeviceInfoError,
    LoginError,
    MultipleDevicesFoundError,
)

from . import __version__
from .client import DeviceOpError, DeviceResolveError, HomeClient, _is_online
from .config import Settings
from .guard import ControlDenied, ControlGuard
from .history import EventHistory
from .semantics import humanize_value

READ_ONLY = {"readOnlyHint": True, "openWorldHint": True}
WRITE_SAFE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}

SERVER_INSTRUCTIONS = """米家全屋状态 MCP。使用建议:
- 问"家里现在什么情况"→ 直接调 get_home_snapshot,一次拿到全屋语义化状态,不要逐设备查询。
- 问"家里有什么变化"→ 调 get_home_changes,返回自上次快照以来的变化列表。
- 单设备详情用 get_device_status;设备支持哪些属性/动作用 get_device_spec。
- 默认只读。控制类工具(设置属性/执行动作/运行场景)只有在服务端显式开启 --enable-control 时才会出现。
"""


class ServerContext:
    """api/client/登录状态,工具闭包里共享的那份。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.guard = ControlGuard(settings)
        self.api: Optional[mijiaAPI] = None
        self.client: Optional[HomeClient] = None
        self._login_lock = threading.Lock()
        self._login_thread: Optional[threading.Thread] = None
        self._login_status: dict = {"status": "idle"}

    def try_init_api(self) -> Optional[str]:
        """从认证文件初始化,失败返回原因字符串(不抛,启动时也会调)。"""
        if not self.settings.auth_path.exists():
            return (
                f"认证文件不存在: {self.settings.auth_path}。"
                "请先在终端运行 `mijia-home-mcp login` 扫码登录,"
                "或调用 login 工具。"
            )
        try:
            api = mijiaAPI(auth_data_path=self.settings.auth_path)
            if not api.available:
                api._refresh_token()
            if not api.available:
                return "认证已过期且无法自动刷新,请重新扫码登录。"
        except Exception as exc:
            return f"认证初始化失败: {exc}"
        self.api = api
        self.client = HomeClient(api, self.settings)
        return None

    def ready_client(self) -> HomeClient:
        """拿到能用的 client,拿不到就抛 ToolError 告诉模型该怎么办。"""
        if self.api is None:
            error = self.try_init_api()
            if error:
                raise ToolError(error)
        assert self.api is not None and self.client is not None
        if not self.api.available:
            try:
                self.api._refresh_token()
            except LoginError as exc:
                raise ToolError(
                    f"米家认证已失效且自动刷新失败({exc})。"
                    "请在终端运行 `mijia-home-mcp login` 重新扫码,或调用 login 工具。"
                ) from exc
        return self.client

    def adopt_api(self, api: mijiaAPI) -> None:
        self.api = api
        self.client = HomeClient(api, self.settings)


def _friendly_errors(fn):
    """各处的异常统一包成 ToolError,message 里写清下一步,模型才能自救。"""

    @functools.wraps(fn)
    def inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except (DeviceResolveError, DeviceOpError, ControlDenied) as exc:
            raise ToolError(str(exc)) from exc
        except LoginError as exc:
            raise ToolError(
                f"米家认证失效: {exc}。请运行 `mijia-home-mcp login` 重新扫码。"
            ) from exc
        except APIError as exc:
            raise ToolError(f"米家云端 API 错误: {exc}") from exc
        except (
            DeviceNotFoundError,
            MultipleDevicesFoundError,
            DeviceGetError,
            DeviceSetError,
            DeviceActionError,
        ) as exc:
            raise ToolError(f"设备操作失败: {exc}") from exc
        except GetDeviceInfoError as exc:
            raise ToolError(
                f"设备规格查询失败: {exc}。如果传入的是设备名或 did,"
                "请先用 list_devices 确认;spec 站点偶发不可用时可稍后重试。"
            ) from exc
        except ValueError as exc:
            raise ToolError(f"参数无效: {exc}") from exc

    return inner


def build_server(settings: Settings, api: Any = None) -> FastMCP:
    """api 参数是给测试塞假对象用的,正常跑不传。"""
    ctx = ServerContext(settings)
    if api is not None:
        ctx.adopt_api(api)
    else:
        # 认证失败也照常起 server,等工具被调用时再报,报错里带指引
        ctx.try_init_api()

    auth = None
    if settings.http_token:
        from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

        auth = StaticTokenVerifier(
            tokens={settings.http_token: {"client_id": "mijia-home-mcp-client"}}
        )

    mcp = FastMCP(
        "mijia-home-mcp",
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
        auth=auth,
    )

    def _home_or_default(home: Optional[str]) -> Optional[str]:
        # 多家庭账号可以在配置里锁一个默认家庭;工具显式传了以传的为准
        return home if home is not None else settings.home

    # resources:给客户端翻的资料。龙虾会转成 resources_read 工具,
    # Claude Code 里用 @ 引用。devices 便宜随便读,snapshot 有 30s 缓存兜底。

    @mcp.resource(
        "mijia://devices",
        description="设备清单:名称/did/model/在线状态/家庭/房间。几乎不变,读取便宜。",
        mime_type="application/json",
    )
    def resource_devices() -> list[dict]:
        client = ctx.ready_client()
        home = settings.home
        devices = client._filter_devices(home) if home else client.devices()
        return [
            {
                "name": d.get("name"),
                "did": d.get("did"),
                "model": d.get("model"),
                "online": _is_online(d),
                "home": d["_home"],
                "room": d["_room"],
            }
            for d in devices
        ]

    @mcp.resource(
        "mijia://snapshot",
        description="全屋实时状态快照(compact)。读取会拉小米云端,约几秒,30s 内重复读走缓存。",
        mime_type="application/json",
    )
    def resource_snapshot() -> dict:
        client = ctx.ready_client()
        snapshot, _raw = client.build_snapshot(settings.home, "compact", 8)
        return snapshot

    @mcp.resource(
        "mijia://homes",
        description="家庭与房间结构。",
        mime_type="application/json",
    )
    def resource_homes() -> list[dict]:
        client = ctx.ready_client()
        return [
            {
                "name": h.get("name"),
                "id": h.get("id"),
                "rooms": [r.get("name") for r in h.get("roomlist", []) or []],
            }
            for h in client.homes()
        ]

    # 读工具

    @mcp.tool(annotations=READ_ONLY)
    @_friendly_errors
    def get_home_snapshot(
        home: Optional[str] = None,
        room: Optional[str] = None,
        detail: Literal["compact", "full"] = "compact",
        max_props_per_device: int = 8,
    ) -> dict:
        """一次调用获取全屋设备状态快照(推荐入口)。

        返回 家→房间→设备→语义化状态 的结构化结果,并附 attention
        (离线/低电量/故障设备)与统计信息。批量拉取,整屋通常几秒内完成。
        本工具不影响 get_home_changes 的对比基线。

        Args:
            home: 可选,家庭名称或ID;不传用服务端配置的默认家庭,均未配置则包含所有家庭。
            room: 可选,房间名;只关心单个房间时用,更快更省 token。
            detail: compact 返回语义化精简状态;full 附带原始值/属性描述/更新时间/did。
            max_props_per_device: 每台设备最多读取的属性数,默认8(full 模式自动放宽到24)。
        """
        client = ctx.ready_client()
        snapshot, _raw = client.build_snapshot(
            _home_or_default(home), detail, max_props_per_device, room=room
        )
        return snapshot

    @mcp.tool(annotations=READ_ONLY)
    @_friendly_errors
    def get_home_changes(home: Optional[str] = None) -> dict:
        """回答"上次以来家里变了什么":与上一次快照对比,返回变化列表。

        变化类型: prop_changed(属性值变化)/ went_offline / came_online /
        device_added / device_removed。调用后会把本次快照存为新基线
        (基线只由本工具读写,get_home_snapshot 不影响它)。
        首次调用时没有基线,会先建立基线并说明。

        Args:
            home: 可选,家庭名称或ID,须与上次快照的口径一致;不传用服务端默认家庭。
        """
        client = ctx.ready_client()
        home = _home_or_default(home)
        prev = client.load_last_raw(home)
        # diff 必须基于新鲜数据,绕过 30s 快照缓存
        snapshot, raw = client.build_snapshot(home, "compact", 8, force_fresh=True)
        client.save_raw(home, raw)
        if prev is None:
            return {
                "message": "首次调用,已建立基线快照;下次调用即可返回变化列表。",
                "baseline_ts": raw["ts"],
                "stats": snapshot["stats"],
            }
        diff = client.diff_raw(prev, raw)
        diff["attention"] = snapshot["attention"]
        EventHistory(settings.state_dir).append(diff["changes"], home=home)
        return diff

    @mcp.tool(annotations=READ_ONLY)
    @_friendly_errors
    def query_history(
        since: Optional[str] = None,
        until: Optional[str] = None,
        device: Optional[str] = None,
        prop: Optional[str] = None,
        event_type: Optional[
            Literal[
                "prop_changed",
                "went_offline",
                "came_online",
                "device_added",
                "device_removed",
            ]
        ] = None,
        limit: int = 100,
    ) -> dict:
        """查询本地事件历史,回答"今天门开过几次""洗衣机几点跑完"这类问题。

        事件在 `mijia-home-mcp watch` 运行期间、以及每次 get_home_changes
        调用时记录,保留 30 天;记录之外的时段没有数据。

        Args:
            since: 起始时间(ISO 格式,如 2026-07-12T14:00);默认 24 小时前。
            until: 截止时间;默认现在。
            device: 设备名过滤,支持 glob(如 "门锁*")。
            prop: 属性名过滤,支持 glob(如 "door-state")。
            event_type: 事件类型过滤。
            limit: 最多返回条数,默认 100,上限 500。
        """
        try:
            return EventHistory(settings.state_dir).query(
                since=since,
                until=until,
                device=device,
                prop=prop,
                event_type=event_type,
                limit=limit,
            )
        except ValueError as exc:
            raise ToolError(f"时间格式无效: {exc}。请用 ISO 格式,如 2026-07-12T14:00") from exc

    @mcp.tool(annotations=READ_ONLY)
    @_friendly_errors
    def list_homes() -> list[dict]:
        """列出所有家庭及其房间(名称与设备数),用于确定 home 参数取值。"""
        client = ctx.ready_client()
        result = []
        for h in client.homes():
            rooms = [
                {
                    "name": room.get("name"),
                    "device_count": len(room.get("dids", []) or []),
                }
                for room in h.get("roomlist", []) or []
            ]
            result.append({"name": h.get("name"), "id": h.get("id"), "rooms": rooms})
        return result

    @mcp.tool(annotations=READ_ONLY)
    @_friendly_errors
    def list_devices(
        home: Optional[str] = None,
        room: Optional[str] = None,
        name_contains: Optional[str] = None,
        online_only: bool = False,
    ) -> list[dict]:
        """列出设备(名称/did/model/在线状态/所属家庭房间),支持过滤。

        Args:
            home: 按家庭名称或ID过滤;不传用服务端默认家庭。
            room: 按房间名过滤(精确匹配)。
            name_contains: 设备名包含该子串(不区分大小写)。
            online_only: 只看在线设备。
        """
        client = ctx.ready_client()
        home = _home_or_default(home)
        devices = client._filter_devices(home) if home else client.devices()
        out = []
        for dev in devices:
            if room and dev["_room"] != room:
                continue
            if name_contains and name_contains.lower() not in (
                dev.get("name") or ""
            ).lower():
                continue
            online = _is_online(dev)
            if online_only and not online:
                continue
            out.append(
                {
                    "name": dev.get("name"),
                    "did": dev.get("did"),
                    "model": dev.get("model"),
                    "online": online,
                    "home": dev["_home"],
                    "room": dev["_room"],
                }
            )
        return out

    @mcp.tool(annotations=READ_ONLY)
    @_friendly_errors
    def get_device_status(
        device: str, props: Optional[list[str]] = None
    ) -> dict:
        """读取单个设备的详细状态(批量拉取,含原始值与语义化文本)。

        Args:
            device: 设备名称或 did;名称支持唯一子串匹配。
            props: 可选,只读取这些属性名(可从 get_device_spec 获得);不传则读取全部可读属性(至多32个)。
        """
        client = ctx.ready_client()
        dev = client.resolve_device(device)
        if not _is_online(dev):
            return {
                "name": dev.get("name"),
                "did": dev.get("did"),
                "model": dev.get("model"),
                "online": False,
                "message": "设备当前离线,无法读取状态。",
            }
        spec = client.spec(dev.get("model", ""))
        available = {
            p.get("name"): p
            for p in spec.get("properties", [])
            if "r" in p.get("rw", "")
        }
        if props:
            unknown = [n for n in props if n not in available]
            if unknown:
                raise ToolError(
                    f"属性不存在或不可读: {unknown}。可读属性: {sorted(available)}"
                )
            selected = [available[n] for n in props]
        else:
            selected = list(available.values())[:32]

        requests = [
            {
                "did": dev["did"],
                "siid": p["method"]["siid"],
                "piid": p["method"]["piid"],
            }
            for p in selected
        ]
        results = client.batch_get_props(requests)
        state: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for p in selected:
            key = (dev["did"], p["method"]["siid"], p["method"]["piid"])
            r = results.get(key, {})
            if r.get("code", -1) == 0:
                state[p["name"]] = {
                    "value": r.get("value"),
                    "text": humanize_value(p, r.get("value")),
                    "desc": p.get("description", ""),
                    "updated_at": r.get("updateTime"),
                }
            else:
                errors[p["name"]] = r.get("error", f"code={r.get('code')}")
        return {
            "name": dev.get("name"),
            "did": dev.get("did"),
            "model": dev.get("model"),
            "online": True,
            "home": dev.get("_home"),
            "room": dev.get("_room"),
            "state": state,
            "errors": errors,
        }

    @mcp.tool(annotations=READ_ONLY)
    @_friendly_errors
    def get_device_spec(device_or_model: str) -> dict:
        """获取设备规格:支持的属性(类型/读写/范围/枚举值)与动作列表。

        用于确定 get_device_status / set_device_property / run_device_action
        可用的属性名与动作名。

        Args:
            device_or_model: 设备名称、did 或设备型号(如 yeelink.light.lamp4)。
        """
        client = ctx.ready_client()
        # 先按设备名/did 解析(蓝牙设备的 did 形如 blt.3.xxx,同样含点,
        # 不能用"含点即型号"来判断);解析不到再当作型号处理
        try:
            model = client.resolve_device(device_or_model).get("model", "")
        except DeviceResolveError:
            if "." in device_or_model and " " not in device_or_model:
                model = device_or_model
            else:
                raise
        return client.spec(model)

    def _resolve_home_id(client: HomeClient, home: Optional[str]) -> Optional[str]:
        """家庭名称或ID → 上游要求的 home_id;None 原样透传。"""
        if home is None:
            return None
        for h in client.homes():
            if home.strip() in (h.get("name"), str(h.get("id"))):
                return h.get("id")
        known = ", ".join(
            f"{h.get('name')}({h.get('id')})" for h in client.homes()
        )
        raise ToolError(f"未找到家庭「{home}」。可选: {known}")

    @mcp.tool(annotations=READ_ONLY)
    @_friendly_errors
    def list_scenes(home: Optional[str] = None) -> list[dict]:
        """列出米家手动场景(名称/scene_id/所属家庭)。

        Args:
            home: 可选,家庭名称或ID;不传用服务端默认家庭,均未配置则列所有。
        """
        client = ctx.ready_client()
        scenes = client.api.get_scenes_list(
            _resolve_home_id(client, _home_or_default(home))
        )
        return [
            {
                "name": s.get("name"),
                "scene_id": s.get("scene_id"),
                "home_id": s.get("home_id"),
            }
            for s in scenes
        ]

    @mcp.tool(annotations=READ_ONLY)
    @_friendly_errors
    def list_consumables(home: Optional[str] = None) -> dict:
        """耗材状态(滤芯/拖布/刷头/电池等),回答"哪些耗材该换了"。

        status 是米家云端算好的三态:充足/不足/耗尽。needs_attention
        里是不足和耗尽的,items 按紧急程度排序。

        Args:
            home: 可选,家庭名称或ID;不传用服务端默认家庭,均未配置则列所有。
        """
        client = ctx.ready_client()
        return client.consumables(
            _resolve_home_id(client, _home_or_default(home))
        )

    @mcp.tool(annotations=READ_ONLY)
    @_friendly_errors
    def get_battery_report(home: Optional[str] = None) -> dict:
        """全屋电量普查:批量读取所有带电池的设备,按电量升序返回。

        回答"哪些设备该换电池了"用这个,比逐设备查询快得多。
        low 字段列出电量 ≤20% 的设备。

        Args:
            home: 可选,家庭名称或ID;不传用服务端默认家庭。
        """
        client = ctx.ready_client()
        return client.battery_report(home=_home_or_default(home))

    @mcp.tool(annotations=READ_ONLY)
    @_friendly_errors
    def get_device_statistics(
        device: str,
        siid_piid: str,
        granularity: Literal["hour", "day", "week", "month"] = "day",
        limit: int = 7,
    ) -> list[dict]:
        """获取设备历史统计数据(如插座耗电量、净化器使用时长)。

        并非所有设备都支持统计;键 siid_piid 需从设备说明或
        https://home.miot-spec.com 查询(常见如插座功耗为 "7.1")。

        Args:
            device: 设备名称或 did。
            siid_piid: 统计键,格式 "siid.piid",如 "7.1"。
            granularity: 统计粒度 hour/day/week/month。
            limit: 返回最近多少条,默认7。
        """
        import time as _time

        client = ctx.ready_client()
        dev = client.resolve_device(device)
        data_type = f"stat_{granularity}_v3"
        now = int(_time.time())
        span = {"hour": 3, "day": 62, "week": 365, "month": 730}[granularity]
        return client.api.get_statistics(
            {
                "did": dev["did"],
                "key": siid_piid,
                "data_type": data_type,
                "limit": max(1, min(limit, 64)),
                "time_start": now - span * 24 * 3600,
                "time_end": now,
            }
        )

    @mcp.prompt
    def home_briefing() -> str:
        """生成一份全屋晨报/晚报:状态总览 + 异常提醒 + 变化摘要。"""
        return (
            "请给我一份家庭状态简报:\n"
            "1. 调用 get_home_snapshot 获取全屋状态;\n"
            "2. 调用 get_home_changes 看看上次以来有什么变化;\n"
            "3. 用三段话总结:整体状态(几台在线/离线)、需要注意的问题"
            "(离线/低电量/故障/异常开着的设备)、显著变化。"
            "口吻像管家汇报,简洁,中文。"
        )

    if settings.has_notify_channel:

        @mcp.tool(annotations=WRITE_SAFE)
        @_friendly_errors
        def send_notification(message: str, title: str = "米家提醒") -> dict:
            """把消息统一推送到安装时配置的所有通知通道。

            通道在 MCP server 配置中声明(钉钉/飞书/MeoW/webhook/小爱音箱),
            本工具一次调用推送全部通道,返回每个通道的结果。
            适合"提醒我""通知家里人""推送到手机"这类请求。

            Args:
                message: 消息正文。
                title: 消息标题,默认"米家提醒"。
            """
            from .notify import Pusher, SpeakerNotifier

            results: dict[str, str] = {}
            pusher = Pusher(
                dingtalk=settings.dingtalk,
                dingtalk_secret=settings.dingtalk_secret,
                feishu=settings.feishu,
                feishu_secret=settings.feishu_secret,
                meow=settings.meow,
                bark=settings.bark,
                ntfy=settings.ntfy,
                webhook=settings.webhook,
            )
            errors = pusher.push(title, message, {"changes": []})
            error_map = dict(e.split(": ", 1) for e in errors if ": " in e)
            for channel in pusher.channels:
                results[channel] = error_map.get(channel, "ok")
            if settings.speaker:
                client = ctx.ready_client()
                try:
                    name = (
                        None if settings.speaker == "auto" else settings.speaker
                    )
                    notifier = SpeakerNotifier(client, name)
                    notifier.announce(f"{title}:{message}")
                    results[f"小爱音箱({notifier.name})"] = "ok"
                except Exception as exc:
                    results["小爱音箱"] = str(exc)
            ctx.guard.audit(
                "send_notification",
                ",".join(results),
                {"title": title, "message": message},
                all(v == "ok" for v in results.values()),
            )
            return results

    @mcp.tool(annotations=READ_ONLY)
    def auth_status() -> dict:
        """查看当前米家登录状态与认证文件路径,排查认证问题时先调这个。"""
        info: dict[str, Any] = {
            "version": __version__,
            "auth_path": str(settings.auth_path),
            "auth_file_exists": settings.auth_path.exists(),
            "control_enabled": settings.enable_control,
            "default_home": settings.home,
        }
        if ctx.api is None:
            error = ctx.try_init_api()
            info["logged_in"] = error is None
            if error:
                info["hint"] = error
        else:
            try:
                info["logged_in"] = bool(ctx.api.available)
            except Exception as exc:
                info["logged_in"] = False
                info["hint"] = str(exc)
        if not info.get("logged_in"):
            info.setdefault(
                "hint",
                "运行 `mijia-home-mcp login` 扫码登录,或调用 login 工具。",
            )
        return info

    @mcp.tool
    def login() -> str:
        """发起米家扫码登录(凭证过期时使用)。

        先尝试静默刷新 token;不行则返回二维码图片链接,用米家APP在2分钟内
        扫码,然后调用 login_status 查询结果。
        """
        with ctx._login_lock:
            if ctx._login_thread is not None and ctx._login_thread.is_alive():
                return "已有登录正在进行中,请调用 login_status 查询结果。"
            if ctx.api is not None:
                try:
                    if ctx.api.available:
                        return "凭证仍然有效,无需重新登录。"
                    ctx.api._refresh_token()
                    if ctx.api.available:
                        return "Token 刷新成功,无需重新登录。"
                except LoginError:
                    pass

            settings.auth_path.parent.mkdir(parents=True, exist_ok=True)
            new_api = mijiaAPI(auth_data_path=settings.auth_path)
            login_data = new_api._get_qr_login_data()
            if login_data.get("refreshed"):
                ctx.adopt_api(new_api)
                return "Token 刷新成功,无需重新登录。"

            ctx._login_status = {"status": "pending"}

            def worker() -> None:
                try:
                    new_api._complete_qr_login(login_data)
                    # 扫码成功后直接切换凭证,不依赖调用方轮询 login_status
                    with ctx._login_lock:
                        ctx.adopt_api(new_api)
                        ctx._login_status = {"status": "success"}
                except Exception as exc:
                    with ctx._login_lock:
                        ctx._login_status = {"status": "error", "message": str(exc)}

            ctx._login_thread = threading.Thread(target=worker, daemon=True)
            ctx._login_thread.start()
            return (
                "二维码已生成,请在2分钟内用米家APP扫码:\n"
                f"{login_data['qr']}\n"
                "扫码后调用 login_status 查询结果。"
            )

    @mcp.tool(annotations=READ_ONLY)
    def login_status() -> str:
        """查询 login 发起的扫码登录进度(pending/success/error)。"""
        with ctx._login_lock:
            if ctx._login_thread is None:
                return "没有正在进行的登录,请先调用 login。"
            status = ctx._login_status.get("status")
            if status == "success":
                ctx._login_thread = None
                ctx._login_status = {"status": "idle"}
                return "登录成功,已切换为新凭证。"
            if status == "error":
                message = ctx._login_status.get("message", "登录失败")
                ctx._login_thread = None
                ctx._login_status = {"status": "idle"}
                return f"登录失败: {message}"
            return "等待扫码中,请扫描 login 返回的二维码后再次查询。"

    # 控制工具,enable_control 才注册

    if settings.enable_control:

        @mcp.tool(tags={"control"}, annotations=WRITE_SAFE)
        @_friendly_errors
        def set_device_property(device: str, prop_name: str, value: str) -> str:
            """设置设备属性(如开关/亮度/模式)。受 allow/deny 与危险设备策略约束。

            Args:
                device: 设备名称或 did。
                prop_name: 属性名,可从 get_device_spec 获取。
                value: 目标值。布尔传 "true"/"false",数值传数字字符串,枚举传枚举值。
            """
            client = ctx.ready_client()
            dev = client.resolve_device(device)
            args = {"prop_name": prop_name, "value": value}
            try:
                ctx.guard.check_device(dev)
                coerced = client.set_property(dev, prop_name, value)
            except Exception as exc:
                ctx.guard.audit(
                    "set_device_property", dev.get("name", device), args, False, str(exc)
                )
                raise
            ctx.guard.audit("set_device_property", dev.get("name", device), args, True)
            return f"{dev.get('name')} 的 {prop_name} 已设置为 {coerced}"

        @mcp.tool(tags={"control"}, annotations=WRITE_SAFE)
        @_friendly_errors
        def turn_on(device: str) -> str:
            """打开设备。自动匹配开关属性(on/power/switch-status),
            比 set_device_property 少一步查 spec。

            Args:
                device: 设备名称或 did。
            """
            client = ctx.ready_client()
            dev = client.resolve_device(device)
            try:
                ctx.guard.check_device(dev)
                prop = client.set_power(dev, True)
            except Exception as exc:
                ctx.guard.audit("turn_on", dev.get("name", device), {}, False, str(exc))
                raise
            ctx.guard.audit("turn_on", dev.get("name", device), {"prop": prop}, True)
            return f"{dev.get('name')} 已打开"

        @mcp.tool(tags={"control"}, annotations=WRITE_SAFE)
        @_friendly_errors
        def turn_off(device: str) -> str:
            """关闭设备。自动匹配开关属性,同 turn_on。

            Args:
                device: 设备名称或 did。
            """
            client = ctx.ready_client()
            dev = client.resolve_device(device)
            try:
                ctx.guard.check_device(dev)
                prop = client.set_power(dev, False)
            except Exception as exc:
                ctx.guard.audit("turn_off", dev.get("name", device), {}, False, str(exc))
                raise
            ctx.guard.audit("turn_off", dev.get("name", device), {"prop": prop}, True)
            return f"{dev.get('name')} 已关闭"

        @mcp.tool(tags={"control"}, annotations=WRITE_SAFE)
        @_friendly_errors
        def run_device_action(
            device: str, action_name: str, value: Optional[list] = None
        ) -> str:
            """执行设备动作(如宠物喂食、启动清扫)。受控制策略约束。

            Args:
                device: 设备名称或 did。
                action_name: 动作名,可从 get_device_spec 获取。
                value: 可选,动作参数列表,含义见 get_device_spec 中动作定义。
            """
            client = ctx.ready_client()
            dev = client.resolve_device(device)
            args = {"action_name": action_name, "value": value}
            try:
                ctx.guard.check_device(dev)
                client.invoke_action(dev, action_name, value=value)
            except Exception as exc:
                ctx.guard.audit(
                    "run_device_action", dev.get("name", device), args, False, str(exc)
                )
                raise
            ctx.guard.audit("run_device_action", dev.get("name", device), args, True)
            return f"{dev.get('name')} 的动作 {action_name} 执行成功"

        @mcp.tool(tags={"control"}, annotations=WRITE_SAFE)
        @_friendly_errors
        def run_scene(scene: str) -> str:
            """运行米家手动场景。

            注意:场景内容是用户在米家APP里预定义的动作组合,执行时不受
            设备级 allow/deny 白名单约束(场景可能操作任何设备)。

            Args:
                scene: 场景名称或 scene_id(名称需唯一,否则请用 scene_id)。
            """
            client = ctx.ready_client()
            ctx.guard.check_scene()
            scenes = client.api.get_scenes_list()
            target = next((s for s in scenes if s.get("scene_id") == scene), None)
            if target is None:
                named = [s for s in scenes if s.get("name") == scene]
                if not named:
                    raise ToolError(
                        f"场景「{scene}」未找到,可调用 list_scenes 查看全部场景。"
                    )
                if len(named) > 1:
                    raise ToolError(f"有多个场景叫「{scene}」,请改用 scene_id。")
                target = named[0]
            ok = client.api.run_scene(target["scene_id"], target["home_id"])
            ctx.guard.audit("run_scene", target.get("name", scene), {}, bool(ok))
            return f"场景「{target.get('name')}」运行{'成功' if ok else '失败'}"

        @mcp.tool(tags={"control"}, annotations=WRITE_SAFE)
        @_friendly_errors
        def speaker_announce(
            text: str, speaker_name: Optional[str] = None
        ) -> str:
            """让小爱音箱播报一句话(play-text 纯 TTS)。

            只出声音,不会触发任何指令执行,受普通设备控制策略约束
            (不像 run_speaker_command 那样按危险通道对待)。

            Args:
                text: 要播报的内容。
                speaker_name: 可选,指定音箱名称;不传用找到的第一台。
            """
            from .notify import SpeakerNotifier

            client = ctx.ready_client()
            try:
                notifier = SpeakerNotifier(client, speaker_name)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            args = {"text": text}
            try:
                ctx.guard.check_device(notifier.speaker)
                notifier.announce(text)
            except Exception as exc:
                ctx.guard.audit(
                    "speaker_announce", notifier.name, args, False, str(exc)
                )
                raise
            ctx.guard.audit("speaker_announce", notifier.name, args, True)
            return f"已通过「{notifier.name}」播报: {text}"

        @mcp.tool(tags={"control"}, annotations=WRITE_SAFE)
        @_friendly_errors
        def run_speaker_command(
            prompt: str, speaker_name: Optional[str] = None, quiet: bool = True
        ) -> str:
            """通过小爱音箱执行自然语言指令(如"打开卧室台灯")。

            警告:此通道可触达全屋任意设备并绕过设备级白名单,因此按危险
            设备策略把关——需要 --allow-dangerous,或把音箱名精确加入 --allow。
            默认静默执行不播报。

            Args:
                prompt: 自然语言指令。
                speaker_name: 可选,指定音箱名称;不传用找到的第一台小爱音箱。
                quiet: 是否静默执行(不语音播报),默认 True。
            """
            client = ctx.ready_client()
            speakers = [
                d
                for d in client.devices()
                if "xiaomi.wifispeaker" in (d.get("model") or "")
            ]
            if speaker_name:
                speakers = [d for d in speakers if d.get("name") == speaker_name]
            if not speakers:
                raise ToolError(
                    f"未找到小爱音箱{f'「{speaker_name}」' if speaker_name else ''}。"
                )
            speaker = speakers[0]
            args = {"prompt": prompt, "quiet": quiet}
            try:
                ctx.guard.check_speaker_directive(speaker)
                client.invoke_action(
                    speaker,
                    "execute-text-directive",
                    in_args=[prompt, 1 if quiet else 0],
                )
            except Exception as exc:
                ctx.guard.audit(
                    "run_speaker_command", speaker.get("name", "?"), args, False, str(exc)
                )
                raise
            ctx.guard.audit("run_speaker_command", speaker.get("name", "?"), args, True)
            return f"已通过 {speaker.get('name')} 执行: {prompt}"

    return mcp
