"""查 GitHub 有没有新版本。只在 doctor 里主动调,别的地方不联网。"""

from __future__ import annotations

import re
from typing import Optional

import requests

REPO = "jiayunlimailutorontoca/mijia-home-mcp"
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"


def _parse(version: str) -> Optional[tuple[int, ...]]:
    m = re.match(r"v?(\d+(?:\.\d+)*)", version.strip())
    if not m:
        return None
    return tuple(int(x) for x in m.group(1).split("."))


def check_latest(current: str, timeout: float = 5.0) -> dict:
    """返回 {status, current, latest, hint}。

    status: up_to_date / outdated / unknown(网络失败或解析不了)。
    拿不到就 unknown,不抛——更新检查失败不该影响 doctor 其他项。
    """
    result = {"status": "unknown", "current": current, "latest": None, "hint": None}
    try:
        resp = requests.get(
            RELEASE_API,
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        latest_tag = resp.json().get("tag_name", "")
    except Exception:
        return result

    result["latest"] = latest_tag
    cur, new = _parse(current), _parse(latest_tag)
    if cur is None or new is None:
        return result
    if new > cur:
        result["status"] = "outdated"
        result["hint"] = (
            "uvx 会缓存 git 构建,更新要清一下: "
            "uvx --refresh --from git+https://github.com/"
            f"{REPO} mijia-home-mcp --version"
        )
    else:
        result["status"] = "up_to_date"
    return result
