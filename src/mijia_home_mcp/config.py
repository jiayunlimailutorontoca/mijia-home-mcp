"""配置。CLI 参数和 MIJIA_HOME_MCP_* 环境变量,前者优先。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_PREFIX = "MIJIA_HOME_MCP_"

# 与上游 mijiaAPI 共用认证文件,避免用户重复扫码
DEFAULT_AUTH_PATH = Path.home() / ".config" / "mijia-api" / "auth.json"
DEFAULT_STATE_DIR = Path.home() / ".config" / "mijia-home-mcp"


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(ENV_PREFIX + name, default)


def _env_bool(name: str) -> bool:
    val = _env(name)
    return val is not None and val.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str) -> list[str]:
    val = _env(name)
    if not val:
        return []
    return [item.strip() for item in val.split(",") if item.strip()]


@dataclass
class Settings:
    auth_path: Path = DEFAULT_AUTH_PATH
    state_dir: Path = DEFAULT_STATE_DIR
    enable_control: bool = False
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    allow_dangerous: bool = False
    snapshot_chunk_size: int = 20
    spec_workers: int = 4
    # 默认家庭。多家庭账号在这锁定一个,所有工具不传 home 时都用它
    home: str | None = None
    # 通知通道(安装 MCP 时配置,send_notification 工具统一推送)
    dingtalk: str | None = None
    dingtalk_secret: str | None = None
    feishu: str | None = None
    feishu_secret: str | None = None
    meow: str | None = None
    bark: str | None = None
    ntfy: str | None = None
    webhook: str | None = None
    speaker: str | None = None  # 小爱音箱名称,或 "auto" 用第一台
    http_token: str | None = None  # http 传输的 Bearer token;不设则无鉴权

    @property
    def has_notify_channel(self) -> bool:
        return bool(
            self.dingtalk
            or self.feishu
            or self.meow
            or self.bark
            or self.ntfy
            or self.webhook
            or self.speaker
        )

    @property
    def spec_cache_dir(self) -> Path:
        # 与上游 mijiaAPI 内置 MCP 共用 spec 缓存目录(auth.json 同级)
        return self.auth_path.parent

    @property
    def audit_log_path(self) -> Path:
        return self.state_dir / "audit.log"

    @property
    def snapshot_dir(self) -> Path:
        return self.state_dir / "snapshots"

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls()
        auth = _env("AUTH")
        if auth:
            settings.auth_path = Path(auth).expanduser()
        state_dir = _env("STATE_DIR")
        if state_dir:
            settings.state_dir = Path(state_dir).expanduser()
        settings.enable_control = _env_bool("ENABLE_CONTROL")
        settings.allow_dangerous = _env_bool("ALLOW_DANGEROUS")
        settings.allow = _env_list("ALLOW")
        settings.deny = _env_list("DENY")
        settings.home = _env("HOME_NAME")  # 不叫 HOME,和系统变量撞名
        settings.dingtalk = _env("DINGTALK")
        settings.dingtalk_secret = _env("DINGTALK_SECRET")
        settings.feishu = _env("FEISHU")
        settings.feishu_secret = _env("FEISHU_SECRET")
        settings.meow = _env("MEOW")
        settings.bark = _env("BARK")
        settings.ntfy = _env("NTFY")
        settings.webhook = _env("WEBHOOK")
        settings.speaker = _env("SPEAKER")
        settings.http_token = _env("HTTP_TOKEN")
        return settings

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
