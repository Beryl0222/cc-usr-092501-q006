"""事件日志的落盘与回放。

日志为 JSONL 文件，每行一个事件信封。追加时一次性写入并 fsync；
读取时容忍崩溃截断的最后一行，中间的损坏行视为数据事故。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .model import RegistryError


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, events: list[dict]) -> None:
        if not events:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        events: list[dict] = []
        seen_ids: set[str] = set()
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1:
                    break  # 崩溃时未写完的尾部，丢弃
                raise RegistryError(f"事件日志第 {index + 1} 行损坏")
            event_id = event.get("event_id")
            if event_id in seen_ids:
                continue  # 重复行不重复应用
            seen_ids.add(event_id)
            events.append(event)
        return events
