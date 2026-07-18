"""本地事件历史。watch 跑着的时候每条变化落一行 JSONL,
按天一个文件,留 30 天,query_history 从这里读。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

RETENTION_DAYS = 30
MAX_QUERY_LIMIT = 500


def _day_file(history_dir: Path, day: datetime) -> Path:
    return history_dir / f"events-{day:%Y%m%d}.jsonl"


def _parse_ts(value: str) -> datetime:
    # 带时区的转成本地 naive 再比,免得 naive 和 aware 比较直接炸
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


class EventHistory:
    def __init__(self, state_dir: Path):
        self.history_dir = state_dir / "history"

    def append(self, changes: list[dict], home: Optional[str] = None) -> None:
        # 尽力而为,写不进去不能影响 watch 主流程
        if not changes:
            return
        now = datetime.now()
        record_common = {"ts": now.isoformat(timespec="seconds"), "home": home}
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            with open(_day_file(self.history_dir, now), "a", encoding="utf-8") as fh:
                for change in changes:
                    fh.write(
                        json.dumps(
                            {**record_common, **change}, ensure_ascii=False
                        )
                        + "\n"
                    )
        except OSError:
            return
        self._cleanup(now)

    def _cleanup(self, now: datetime) -> None:
        cutoff = f"events-{now - timedelta(days=RETENTION_DAYS):%Y%m%d}.jsonl"
        try:
            for f in self.history_dir.glob("events-*.jsonl"):
                if f.name < cutoff:
                    os.remove(f)
        except OSError:
            pass

    def query(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        device: Optional[str] = None,
        prop: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> dict:
        """查事件,新的在前。since 不传默认查最近 24 小时。"""
        since_dt = _parse_ts(since) if since else datetime.now() - timedelta(hours=24)
        until_dt = _parse_ts(until) if until else datetime.now()
        limit = max(1, min(limit, MAX_QUERY_LIMIT))

        matched: list[dict] = []
        truncated = False
        if self.history_dir.exists():
            day = until_dt.date()
            # 从最近的天倒着读,凑够 limit 提前停
            while day >= since_dt.date():
                f = _day_file(self.history_dir, datetime.combine(day, datetime.min.time()))
                if f.exists():
                    day_events = []
                    with open(f, encoding="utf-8") as fh:
                        for line in fh:
                            try:
                                ev = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            try:
                                ts = _parse_ts(ev.get("ts", ""))
                            except ValueError:
                                continue
                            if not (since_dt <= ts <= until_dt):
                                continue
                            if device and not fnmatch(ev.get("device", ""), device):
                                continue
                            if prop and not fnmatch(ev.get("prop", "") or "", prop):
                                continue
                            if event_type and ev.get("type") != event_type:
                                continue
                            day_events.append(ev)
                    matched.extend(reversed(day_events))  # 天内新→旧
                    # 严格大于才停:恰好凑满 limit 时还得看更早的天
                    # 有没有货,否则 truncated 会误报 False
                    if len(matched) > limit:
                        truncated = True
                        matched = matched[:limit]
                        break
                day -= timedelta(days=1)

        return {
            "since": since_dt.isoformat(timespec="seconds"),
            "until": until_dt.isoformat(timespec="seconds"),
            "count": len(matched),
            "truncated": truncated,
            "events": matched,
            "note": (
                "事件由 `mijia-home-mcp watch` 运行期间记录;watch 未运行的时段没有数据。"
                if not matched
                else None
            ),
        }
