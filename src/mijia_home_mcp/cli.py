"""CLI 入口:login(终端扫码)与 serve(启动 MCP server,默认 stdio)。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import DEFAULT_AUTH_PATH, Settings


def _setup_stderr_logging() -> None:
    """stdio 传输下 stdout 是协议通道,所有日志必须走 stderr。"""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        # 注意:mijiaAPI 包属性 logger 是子模块,Logger 实例在 mijiaAPI.logger.logger
        from mijiaAPI.logger import logger as mijia_logger

        mijia_logger.handlers = [
            h
            for h in mijia_logger.handlers
            if not (
                isinstance(h, logging.StreamHandler) and h.stream is sys.stdout
            )
        ]
    except Exception:  # noqa: BLE001 - 上游日志结构变化不应阻塞启动
        pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mijia-home-mcp",
        description="米家全屋状态快照 MCP server(默认只读)",
    )
    from . import __version__

    parser.add_argument(
        "--version", action="version", version=f"mijia-home-mcp {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    p_login = sub.add_parser("login", help="终端扫码登录米家账号并保存凭证")
    p_login.add_argument(
        "--auth",
        type=Path,
        default=None,
        help=f"认证文件路径(默认 {DEFAULT_AUTH_PATH},与 mijiaAPI 共用)",
    )

    p_snapshot = sub.add_parser(
        "snapshot", help="终端直接查看全屋状态快照(不需要 MCP 客户端)"
    )
    p_snapshot.add_argument("--auth", type=Path, default=None, help="认证文件路径")
    p_snapshot.add_argument("--home", default=None, help="只看指定家庭(名称或ID)")
    p_snapshot.add_argument("--room", default=None, help="只看指定房间")
    p_snapshot.add_argument(
        "--full", action="store_true", help="完整模式(更多属性与原始值)"
    )
    p_snapshot.add_argument(
        "--json", action="store_true", dest="as_json", help="输出原始 JSON"
    )

    p_devices = sub.add_parser("devices", help="终端列出所有设备")
    p_devices.add_argument("--auth", type=Path, default=None, help="认证文件路径")
    p_devices.add_argument(
        "--json", action="store_true", dest="as_json", help="输出原始 JSON"
    )

    p_doctor = sub.add_parser(
        "doctor", help="自检:认证有效性、云端连通性、缓存目录"
    )
    p_doctor.add_argument("--auth", type=Path, default=None, help="认证文件路径")

    p_battery = sub.add_parser("battery", help="终端查看全屋电量普查")
    p_battery.add_argument("--auth", type=Path, default=None, help="认证文件路径")

    p_say = sub.add_parser("say", help="让小爱音箱播报一句话(纯 TTS)")
    p_say.add_argument("text", help="要播报的内容")
    p_say.add_argument("--auth", type=Path, default=None, help="认证文件路径")
    p_say.add_argument(
        "--speaker-name", default=None, help="指定音箱名称;不传用找到的第一台"
    )

    p_watch = sub.add_parser(
        "watch", help="持续监控全屋状态变化(轮询 diff,Ctrl-C 退出)"
    )
    p_watch.add_argument("--auth", type=Path, default=None, help="认证文件路径")
    p_watch.add_argument("--home", default=None, help="只监控指定家庭")
    p_watch.add_argument(
        "--interval",
        type=int,
        default=60,
        help="轮询间隔秒数,默认60;请勿设置过小以免触发云端限流",
    )
    p_watch.add_argument(
        "--speak",
        action="store_true",
        help="有变化时通过小爱音箱 TTS 播报(play-text,纯播报不执行指令)",
    )
    p_watch.add_argument(
        "--speaker-name",
        default=None,
        help="指定播报用的小爱音箱名称;不传用找到的第一台",
    )
    p_watch.add_argument(
        "--dingtalk",
        default=None,
        metavar="WEBHOOK_URL",
        help="钉钉机器人 webhook 地址(安全设置建议自定义关键词「米家」,或配合 --dingtalk-secret 加签)",
    )
    p_watch.add_argument(
        "--dingtalk-secret",
        default=None,
        metavar="SECRET",
        help="钉钉机器人加签密钥(SEC 开头)",
    )
    p_watch.add_argument(
        "--feishu",
        default=None,
        metavar="WEBHOOK_URL",
        help="飞书自定义机器人 webhook 地址",
    )
    p_watch.add_argument(
        "--meow",
        default=None,
        metavar="NICKNAME",
        help="MeoW(鸿蒙推送)昵称,或完整 API URL",
    )
    p_watch.add_argument(
        "--webhook",
        default=None,
        metavar="URL",
        help="通用 webhook:有变化时把 diff JSON POST 到该 URL",
    )
    p_watch.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="PATTERN",
        help="只关注设备名命中该 glob 的变化(可多次传入)",
    )
    p_watch.add_argument(
        "--ignore",
        action="append",
        default=None,
        metavar="PATTERN",
        help="忽略设备名或属性名命中该 glob 的变化,如 --ignore left-time(可多次传入)",
    )

    p_serve = sub.add_parser("serve", help="启动 MCP server(默认 stdio)")
    p_serve.add_argument("--auth", type=Path, default=None, help="认证文件路径")
    p_serve.add_argument(
        "--enable-control",
        action="store_true",
        help="开启控制工具(设置属性/执行动作/运行场景);默认只读",
    )
    p_serve.add_argument(
        "--allow",
        action="append",
        default=None,
        metavar="PATTERN",
        help="控制白名单(设备名/did/model 的 glob 模式,可多次传入);配置后仅名单内设备可控",
    )
    p_serve.add_argument(
        "--deny",
        action="append",
        default=None,
        metavar="PATTERN",
        help="控制黑名单,优先于白名单",
    )
    p_serve.add_argument(
        "--allow-dangerous",
        action="store_true",
        help="允许控制危险设备(锁/摄像头/燃气与水阀/保险柜);默认拦截",
    )
    p_serve.add_argument(
        "--dingtalk",
        default=None,
        metavar="WEBHOOK_URL",
        help="通知通道:钉钉机器人 webhook(供 send_notification 工具统一推送)",
    )
    p_serve.add_argument(
        "--dingtalk-secret", default=None, metavar="SECRET", help="钉钉加签密钥"
    )
    p_serve.add_argument(
        "--feishu", default=None, metavar="WEBHOOK_URL", help="通知通道:飞书机器人 webhook"
    )
    p_serve.add_argument(
        "--feishu-secret", default=None, metavar="SECRET", help="飞书签名校验密钥"
    )
    p_serve.add_argument(
        "--meow", default=None, metavar="NICKNAME", help="通知通道:MeoW 昵称或完整 URL"
    )
    p_serve.add_argument(
        "--webhook", default=None, metavar="URL", help="通知通道:通用 webhook"
    )
    p_serve.add_argument(
        "--speaker",
        default=None,
        metavar="NAME",
        help="通知通道:小爱音箱名称(传 auto 用第一台)",
    )
    p_serve.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="传输方式:本地个人使用选 stdio(默认),局域网共享选 http",
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="http 监听地址")
    p_serve.add_argument("--port", type=int, default=8423, help="http 监听端口")
    return parser


def _cmd_login(args: argparse.Namespace) -> int:
    # QRlogin 会在终端打印二维码,这里是交互命令,允许使用 stdout
    from mijiaAPI import mijiaAPI

    # 与 serve 一致:--auth > MIJIA_HOME_MCP_AUTH > 默认路径
    auth_path = args.auth or Settings.from_env().auth_path
    auth_path = auth_path.expanduser()
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    api = mijiaAPI(auth_data_path=auth_path)
    api.login()
    print(f"登录成功,凭证已保存到: {auth_path}")
    print("现在可以启动 MCP server: mijia-home-mcp serve")
    return 0


def _make_client(args: argparse.Namespace):
    """终端子命令共用:构建已认证的 HomeClient。"""
    from mijiaAPI import mijiaAPI

    from .client import HomeClient

    settings = Settings.from_env()
    if getattr(args, "auth", None):
        settings.auth_path = args.auth.expanduser()
    settings.ensure_dirs()
    if not settings.auth_path.exists():
        print(f"认证文件不存在: {settings.auth_path}")
        print("请先运行: mijia-home-mcp login")
        raise SystemExit(1)
    api = mijiaAPI(auth_data_path=settings.auth_path)
    if not api.available:
        api._refresh_token()
    return HomeClient(api, settings), settings


def _fmt_state(state: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in list(state.items())[:6])


def _cmd_snapshot(args: argparse.Namespace) -> int:
    import json

    client, _ = _make_client(args)
    snapshot, _raw = client.build_snapshot(
        home=args.home,
        detail="full" if args.full else "compact",
        room=args.room,
    )
    if args.as_json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=1))
        return 0

    stats = snapshot["stats"]
    for home in snapshot["homes"]:
        print(f"\n■ {home['name']}")
        for room in home["rooms"]:
            print(f"  ▸ {room['name']}")
            for dev in room["devices"]:
                mark = "·" if dev["online"] else "✗离线"
                state = _fmt_state(dev.get("state", {})) if dev["online"] else ""
                print(f"    {mark} {dev['name']}  {state}")
    att = snapshot["attention"]
    issues = att["offline"] + att["low_battery"] + att["faults"]
    if issues:
        print("\n⚠ 需要注意:")
        for item in issues:
            print(f"  - {item}")
    print(
        f"\n{stats['devices_online']}/{stats['devices_total']} 台在线,"
        f"{stats['props_fetched']} 个属性,耗时 {stats['elapsed_s']}s"
    )
    return 0


def _cmd_devices(args: argparse.Namespace) -> int:
    import json

    client, _ = _make_client(args)
    devices = client.devices()
    if args.as_json:
        slim = [
            {
                "name": d.get("name"),
                "did": d.get("did"),
                "model": d.get("model"),
                "online": bool(d.get("isOnline", True)),
                "home": d["_home"],
                "room": d["_room"],
            }
            for d in devices
        ]
        print(json.dumps(slim, ensure_ascii=False, indent=1))
        return 0
    width = max((len(d.get("name") or "") for d in devices), default=10)
    for d in sorted(devices, key=lambda x: (x["_home"], x["_room"])):
        online = "  " if d.get("isOnline", True) else "✗ "
        print(
            f"{online}{(d.get('name') or '?'):<{width}}  "
            f"{d['_home']}/{d['_room']}  {d.get('model')}  {d.get('did')}"
        )
    print(f"\n共 {len(devices)} 台设备")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    import time as _time

    settings = Settings.from_env()
    if getattr(args, "auth", None):
        settings.auth_path = args.auth.expanduser()

    def check(name: str, ok: bool, detail: str = "") -> bool:
        print(f"  [{'OK' if ok else '!!'}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    print("mijia-home-mcp 自检:")
    all_ok = check(
        "认证文件存在",
        settings.auth_path.exists(),
        str(settings.auth_path),
    )
    if not all_ok:
        print("\n先运行: mijia-home-mcp login")
        return 1

    from mijiaAPI import mijiaAPI

    try:
        api = mijiaAPI(auth_data_path=settings.auth_path)
        fresh = api.available
        if not fresh:
            api._refresh_token()
        check("凭证有效", api.available, "已自动刷新" if not fresh else "无需刷新")
    except Exception as exc:  # noqa: BLE001
        check("凭证有效", False, f"{exc};请重新 login")
        return 1

    try:
        t0 = _time.monotonic()
        homes = api.get_homes_list()
        dt = _time.monotonic() - t0
        check(
            "米家云端连通",
            True,
            f"{len(homes)} 个家庭,{dt:.1f}s",
        )
    except Exception as exc:  # noqa: BLE001
        check("米家云端连通", False, str(exc))
        return 1

    check("状态目录可写", True, str(settings.state_dir))
    specs = list(settings.spec_cache_dir.glob("*.json"))
    check("spec 缓存", True, f"{len(specs)} 个型号已缓存")
    print("\n一切正常。启动 MCP: mijia-home-mcp serve")
    return 0


def _cmd_battery(args: argparse.Namespace) -> int:
    client, _ = _make_client(args)
    report = client.battery_report()
    for row in report["devices"]:
        level = row["battery"]
        bar = "!" if isinstance(level, (int, float)) and level <= 20 else " "
        print(f" {bar} {level:>3}%  {row['name']}  ({row['room']})")
    print(f"\n共 {report['count']} 台带电池设备,{len(report['low'])} 台低电量(≤20%)")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    import time as _time
    from datetime import datetime

    from .history import EventHistory
    from .notify import (
        Pusher,
        SpeakerNotifier,
        filter_changes,
        format_changes_text,
    )

    client, settings = _make_client(args)
    history = EventHistory(settings.state_dir)
    interval = max(15, args.interval)
    if interval != args.interval:
        print(f"(间隔已提升到最小值 {interval}s,保护云端接口)", flush=True)

    # CLI 参数优先,未传时回退到环境变量配置的常驻通道
    speaker = None
    speak_name = args.speaker_name or (
        None if settings.speaker in (None, "auto") else settings.speaker
    )
    if args.speak or settings.speaker:
        try:
            speaker = SpeakerNotifier(client, speak_name)
            print(f"变化将通过「{speaker.name}」播报", flush=True)
        except ValueError as exc:
            print(f"播报不可用: {exc}", flush=True)
            return 1
    pusher = Pusher(
        dingtalk=args.dingtalk or settings.dingtalk,
        dingtalk_secret=args.dingtalk_secret or settings.dingtalk_secret,
        feishu=args.feishu or settings.feishu,
        feishu_secret=settings.feishu_secret,
        meow=args.meow or settings.meow,
        webhook=args.webhook or settings.webhook,
    )
    if pusher.channels:
        print(f"变化将推送到: {'、'.join(pusher.channels)}", flush=True)

    print(
        f"每 {interval}s 轮询一次,监控范围: {args.home or '全部家庭'}。Ctrl-C 退出。\n",
        flush=True,
    )

    _, prev = client.build_snapshot(home=args.home)
    print(f"[{datetime.now():%H:%M:%S}] 基线已建立,开始监控…", flush=True)
    try:
        while True:
            _time.sleep(interval)
            client.invalidate_cache()
            try:
                _, cur = client.build_snapshot(home=args.home)
            except Exception as exc:  # noqa: BLE001 - 单轮失败不退出
                print(f"[{datetime.now():%H:%M:%S}] 本轮拉取失败: {exc}", flush=True)
                continue
            diff = client.diff_raw(prev, cur)
            prev = cur
            ts = f"[{datetime.now():%H:%M:%S}]"
            diff["changes"] = filter_changes(
                diff["changes"], only=args.only, ignore=args.ignore
            )
            if not diff["changes"]:
                print(f"{ts} 无变化", flush=True)
                continue
            history.append(diff["changes"], home=args.home)
            summary = format_changes_text(diff["changes"])
            if speaker is not None:
                try:
                    speaker.announce("米家提醒:" + summary)
                except Exception as exc:  # noqa: BLE001 - 通知失败不中断监控
                    print(f"{ts} 播报失败: {exc}", flush=True)
            for err in pusher.push("米家提醒", summary, {"home": args.home, **diff}):
                print(f"{ts} 推送失败 {err}", flush=True)
            for c in diff["changes"]:
                if c["type"] == "prop_changed":
                    print(
                        f"{ts} {c['device']}: {c['prop']} "
                        f"{c['from']} → {c['to']}",
                        flush=True,
                    )
                elif c["type"] == "went_offline":
                    print(f"{ts} ✗ {c['device']} 离线了", flush=True)
                elif c["type"] == "came_online":
                    print(f"{ts} ✓ {c['device']} 上线了", flush=True)
                else:
                    print(f"{ts} {c['type']}: {c['device']}", flush=True)
    except KeyboardInterrupt:
        print("\n已停止监控。", flush=True)
        return 0


def _cmd_say(args: argparse.Namespace) -> int:
    from .notify import SpeakerNotifier

    client, _ = _make_client(args)
    try:
        notifier = SpeakerNotifier(client, args.speaker_name)
    except ValueError as exc:
        print(str(exc))
        return 1
    notifier.announce(args.text)
    print(f"已通过「{notifier.name}」播报: {args.text}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    _setup_stderr_logging()
    settings = Settings.from_env()
    if args.auth:
        settings.auth_path = args.auth.expanduser()
    if args.enable_control:
        settings.enable_control = True
    if args.allow is not None:
        settings.allow = args.allow
    if args.deny is not None:
        settings.deny = args.deny
    if args.allow_dangerous:
        settings.allow_dangerous = True
    for channel in ("dingtalk", "dingtalk_secret", "feishu", "feishu_secret", "meow", "webhook", "speaker"):
        value = getattr(args, channel, None)
        if value is not None:
            setattr(settings, channel, value)
    settings.ensure_dirs()

    from .server import build_server

    mcp = build_server(settings)
    logging.getLogger(__name__).info(
        "mijia-home-mcp 启动: transport=%s control=%s auth=%s",
        args.transport,
        "on" if settings.enable_control else "off(只读)",
        settings.auth_path,
    )
    if args.transport == "http":
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            logging.getLogger(__name__).warning(
                "http 传输当前没有内置鉴权,监听 %s 意味着同网段任何人都能"
                "读取(以及在开启控制时操作)你的米家设备。请确保仅在可信局域网"
                "使用并配合防火墙,切勿暴露公网。",
                args.host,
            )
        mcp.run(transport="http", host=args.host, port=args.port, show_banner=False)
    else:
        mcp.run(show_banner=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "login": _cmd_login,
        "snapshot": _cmd_snapshot,
        "devices": _cmd_devices,
        "doctor": _cmd_doctor,
        "battery": _cmd_battery,
        "say": _cmd_say,
        "watch": _cmd_watch,
        "serve": _cmd_serve,
    }
    if args.command is None:
        # 无子命令时按默认参数 serve(stdio 只读)
        args = parser.parse_args(["serve"])
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
