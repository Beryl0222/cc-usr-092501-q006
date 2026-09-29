"""事件存储：只追加日志、JSONL 持久化与恢复。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .envelope import validate_event


def utcnow_iso() -> str:
    """当前 UTC 时间，带时区的 ISO 8601 格式。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_instant(value: object) -> datetime:
    """严格解析事件时间：必须带时区。"""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return parsed


def query_instant(value: object) -> datetime:
    """宽松解析查询时间：缺省时区按 UTC 处理。"""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class EventStore:
    """只追加的事件日志，按聚合维护递增版本号。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._events: list[dict[str, Any]] = []
        self._versions: dict[tuple[str, str], int] = {}
        self._event_ids: set[str] = set()
        if self._path is not None and self._path.exists():
            self._load()

    def _load(self) -> None:
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            errors = validate_event(event)
            if errors:
                raise ValueError(f"事件日志损坏：{event.get('event_id')}：{'；'.join(errors)}")
            self._register(event)

    def _register(self, event: dict[str, Any]) -> None:
        if event["event_id"] in self._event_ids:
            raise ValueError(f"事件标识重复：{event['event_id']}")
        self._event_ids.add(event["event_id"])
        key = (event["aggregate_type"], event["aggregate_id"])
        self._versions[key] = event["version"]
        self._events.append(event)

    def append(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        key = (aggregate_type, aggregate_id)
        event: dict[str, Any] = {
            "event_id": f"EVT-{len(self._events) + 1:06d}",
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "occurred_at": occurred_at or utcnow_iso(),
            "version": self._versions.get(key, 0) + 1,
            "summary": summary,
        }
        if payload:
            event["payload"] = payload
        errors = validate_event(event)
        if errors:
            raise ValueError("；".join(errors))
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._register(event)
        return event

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def events_for(self, aggregate_type: str, aggregate_id: str) -> list[dict[str, Any]]:
        return [
            event
            for event in self._events
            if event["aggregate_type"] == aggregate_type and event["aggregate_id"] == aggregate_id
        ]

    def find(self, event_type: str) -> list[dict[str, Any]]:
        return [event for event in self._events if event["event_type"] == event_type]
