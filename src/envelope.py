"""领域事件信封的基础校验。"""

from __future__ import annotations

from datetime import datetime

REQUIRED = ("event_id", "event_type", "aggregate_type", "aggregate_id", "occurred_at", "version", "summary")

EVENT_TYPES = (
    "TERM_REGISTERED",
    "CANDIDATE_PROPOSED",
    "BILATERAL_REVIEWED",
    "PASSAGE_REGISTERED",
    "PASSAGE_STATE_CHANGED",
    "PASSAGE_MIGRATED",
    "ERRATA_ISSUED",
    "REVISION_PREPARED",
    "REVISION_SIGNED",
    "REVISION_APPLIED",
)

AGGREGATE_TYPES = (
    "source_term",
    "translation_candidate",
    "narrative_passage",
    "errata_release",
    "term_revision",
)

def validate_event(record: object) -> list[str]:
    if not isinstance(record, dict):
        return ["事件必须是 JSON 对象"]
    errors = [f"缺少字段：{name}" for name in REQUIRED if name not in record]
    event_type = record.get("event_type")
    if event_type is not None and event_type not in EVENT_TYPES:
        errors.append(f"event_type 必须是约定的事件类型：{event_type}")
    aggregate_type = record.get("aggregate_type")
    if aggregate_type is not None and aggregate_type not in AGGREGATE_TYPES:
        errors.append(f"aggregate_type 必须是约定的聚合类型：{aggregate_type}")
    if "version" in record and (not isinstance(record["version"], int) or isinstance(record["version"], bool) or record["version"] < 1):
        errors.append("version 必须是正整数")
    if "occurred_at" in record:
        try:
            parsed = datetime.fromisoformat(str(record["occurred_at"]).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                errors.append("occurred_at 必须包含时区")
        except ValueError:
            errors.append("occurred_at 必须是 ISO 8601 时间")
    return errors
