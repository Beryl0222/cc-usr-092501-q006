"""叙事段落版本：载体、受众层级与发布状态。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .store import EventStore, query_instant
from .terms import AUDIENCE_LEVELS, TermRegistry

CARRIERS = ("label", "catalog", "lecture", "course")  # 展签 / 图录 / 讲稿 / 课程
STATES = ("draft", "published", "printed", "taught")  # 未发布 / 已发布 / 已印刷 / 已讲授
PRINT_CARRIERS = ("label", "catalog")
TAUGHT_CARRIERS = ("lecture", "course")

_TRANSITIONS = {
    "draft": {"published"},
    "published": {"printed", "taught"},
    "printed": set(),
    "taught": set(),
}


@dataclass
class PassageVersion:
    """段落的一次内容版本：术语引用随版本固定。"""

    passage_id: str
    version: int
    segments: tuple[dict, ...]
    recorded_at: str
    revision_id: str | None = None


@dataclass
class Passage:
    passage_id: str
    carrier: str
    audience: str
    author: str
    versions: list[PassageVersion] = field(default_factory=list)
    state_history: list[tuple[str, str]] = field(default_factory=list)

    @property
    def current(self) -> PassageVersion:
        return self.versions[-1]

    @property
    def state(self) -> str:
        return self.state_history[-1][0]


class PassageRegistry:
    """段落与版本的事件投影。"""

    def __init__(self, store: EventStore, terms: TermRegistry) -> None:
        self._store = store
        self._terms = terms
        self._passages: dict[str, Passage] = {}
        self._projected = -1

    def rebuild(self) -> None:
        self._projected = -1
        self._ensure_current()

    def _ensure_current(self) -> None:
        events = self._store.events
        if len(events) == self._projected:
            return
        self._passages = {}
        for event in events:
            if event["aggregate_type"] != "narrative_passage":
                continue
            payload = event.get("payload", {})
            passage_id = event["aggregate_id"]
            if event["event_type"] == "PASSAGE_REGISTERED":
                self._passages[passage_id] = Passage(
                    passage_id=passage_id,
                    carrier=payload["carrier"],
                    audience=payload["audience"],
                    author=payload.get("author", ""),
                    versions=[
                        PassageVersion(
                            passage_id=passage_id,
                            version=1,
                            segments=tuple(dict(segment) for segment in payload["segments"]),
                            recorded_at=event["occurred_at"],
                        )
                    ],
                    state_history=[(payload["state"], event["occurred_at"])],
                )
            elif event["event_type"] == "PASSAGE_STATE_CHANGED":
                passage = self._passages[passage_id]
                passage.state_history.append((payload["to"], event["occurred_at"]))
            elif event["event_type"] == "PASSAGE_MIGRATED":
                passage = self._passages[passage_id]
                passage.versions.append(
                    PassageVersion(
                        passage_id=passage_id,
                        version=len(passage.versions) + 1,
                        segments=tuple(dict(segment) for segment in payload["segments"]),
                        recorded_at=event["occurred_at"],
                        revision_id=payload.get("revision_id"),
                    )
                )
        self._projected = len(events)

    def validate_segments(self, audience: str, segments: list[dict] | tuple[dict, ...]) -> None:
        """段落引用的候选必须已审定采用，且受众层级与段落一致。"""
        if not segments:
            raise ValueError("段落内容不能为空")
        for segment in segments:
            kind = segment.get("kind")
            if kind == "text":
                if not segment.get("text"):
                    raise ValueError("文本片段不能为空")
            elif kind == "term":
                candidate = self._terms.get_candidate(str(segment.get("candidate_id", "")))
                if candidate is None:
                    raise ValueError(f"候选不存在：{segment.get('candidate_id')}")
                if candidate.term_id != segment.get("term_id"):
                    raise ValueError(f"候选 {candidate.candidate_id} 与术语 {segment.get('term_id')} 不匹配")
                if candidate.status != "adopted":
                    raise ValueError(f"候选尚未审定采用：{candidate.candidate_id}")
                if candidate.audience != audience:
                    raise ValueError(f"候选受众层级与段落不一致：{candidate.candidate_id}")
            else:
                raise ValueError(f"未知片段类型：{kind}")

    def register(
        self,
        passage_id: str,
        carrier: str,
        audience: str,
        segments: list[dict],
        author: str,
        state: str = "draft",
        occurred_at: str | None = None,
    ) -> Passage:
        self._ensure_current()
        if passage_id in self._passages:
            raise ValueError(f"段落已登记：{passage_id}")
        if carrier not in CARRIERS:
            raise ValueError(f"未知载体：{carrier}")
        if audience not in AUDIENCE_LEVELS:
            raise ValueError(f"未知受众层级：{audience}")
        if state not in STATES:
            raise ValueError(f"未知发布状态：{state}")
        self._check_carrier_state(carrier, state)
        self.validate_segments(audience, segments)
        self._store.append(
            "PASSAGE_REGISTERED",
            "narrative_passage",
            passage_id,
            summary=f"登记段落 {passage_id}（{carrier}/{audience}，{state}）",
            payload={
                "carrier": carrier,
                "audience": audience,
                "author": author,
                "state": state,
                "segments": [dict(segment) for segment in segments],
            },
            occurred_at=occurred_at,
        )
        self._ensure_current()
        return self._passages[passage_id]

    def change_state(self, passage_id: str, new_state: str, actor: str = "", occurred_at: str | None = None) -> None:
        passage = self.get(passage_id)
        current = passage.state
        if new_state not in _TRANSITIONS.get(current, set()):
            raise ValueError(f"状态不可流转：{current} → {new_state}")
        self._check_carrier_state(passage.carrier, new_state)
        self._store.append(
            "PASSAGE_STATE_CHANGED",
            "narrative_passage",
            passage_id,
            summary=f"段落 {passage_id} 状态：{current} → {new_state}",
            payload={"from": current, "to": new_state, "actor": actor},
            occurred_at=occurred_at,
        )
        self._ensure_current()

    def apply_migration(
        self,
        passage_id: str,
        new_segments: list[dict],
        revision_id: str,
        occurred_at: str | None = None,
    ) -> PassageVersion:
        """迁移未发布段落到新版本；全部术语切换写在同一事件内。"""
        passage = self.get(passage_id)
        if passage.state != "draft":
            raise ValueError(f"仅未发布段落可迁移：{passage_id} 当前状态 {passage.state}")
        self.validate_segments(passage.audience, new_segments)
        self._store.append(
            "PASSAGE_MIGRATED",
            "narrative_passage",
            passage_id,
            summary=f"迁移段落 {passage_id} 至版本 {len(passage.versions) + 1}（修订 {revision_id}）",
            payload={
                "revision_id": revision_id,
                "base_version": passage.current.version,
                "segments": [dict(segment) for segment in new_segments],
            },
            occurred_at=occurred_at,
        )
        self._ensure_current()
        return self.get(passage_id).current

    def get(self, passage_id: str) -> Passage:
        self._ensure_current()
        passage = self._passages.get(passage_id)
        if passage is None:
            raise ValueError(f"段落不存在：{passage_id}")
        return passage

    def all_passages(self) -> list[Passage]:
        self._ensure_current()
        return [self._passages[key] for key in sorted(self._passages)]

    def version_at(self, passage_id: str, on_date: object) -> PassageVersion:
        passage = self.get(passage_id)
        instant = query_instant(on_date)
        eligible = [version for version in passage.versions if query_instant(version.recorded_at) <= instant]
        if not eligible:
            raise ValueError(f"段落 {passage_id} 在该日期尚无有效版本")
        return eligible[-1]

    def citations(self, term_id: str) -> list[tuple[str, int]]:
        """当前版本引用该术语的段落及版本号。"""
        self._ensure_current()
        found = []
        for passage in self.all_passages():
            if any(
                segment.get("kind") == "term" and segment.get("term_id") == term_id
                for segment in passage.current.segments
            ):
                found.append((passage.passage_id, passage.current.version))
        return found

    @staticmethod
    def _check_carrier_state(carrier: str, state: str) -> None:
        if state == "printed" and carrier not in PRINT_CARRIERS:
            raise ValueError(f"载体 {carrier} 不能标记为已印刷")
        if state == "taught" and carrier not in TAUGHT_CARRIERS:
            raise ValueError(f"载体 {carrier} 不能标记为已讲授")
