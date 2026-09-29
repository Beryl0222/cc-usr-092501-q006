"""源语术语登记与中文候选管理。"""

from __future__ import annotations

from dataclasses import dataclass

from .store import EventStore, query_instant

AUDIENCE_LEVELS = ("academic", "public", "children")  # 学术 / 公众 / 儿童


@dataclass
class SourceTerm:
    """源语术语：原文词形、转写、语义范围、出处、年代语境与禁用误译。"""

    term_id: str
    original_form: str
    transliteration: str
    semantic_range: str
    provenance: str
    dating_context: str
    forbidden: tuple[str, ...]
    registered_by: str
    registered_at: str


@dataclass
class Candidate:
    """中文候选：按受众层级提出，经双边审定后方可采用。"""

    candidate_id: str
    term_id: str
    text: str
    audience: str
    proposed_by: str
    source_ref: str
    rationale: str
    status: str = "proposed"  # proposed / adopted / rejected
    decided_at: str | None = None


class TermRegistry:
    """术语与候选的事件投影。"""

    def __init__(self, store: EventStore) -> None:
        self._store = store
        self._terms: dict[str, SourceTerm] = {}
        self._candidates: dict[str, Candidate] = {}
        self._projected = -1

    def rebuild(self) -> None:
        self._projected = -1
        self._ensure_current()

    def _ensure_current(self) -> None:
        events = self._store.events
        if len(events) == self._projected:
            return
        self._terms = {}
        self._candidates = {}
        for event in events:
            payload = event.get("payload", {})
            if event["event_type"] == "TERM_REGISTERED":
                self._terms[event["aggregate_id"]] = SourceTerm(
                    term_id=event["aggregate_id"],
                    original_form=payload["original_form"],
                    transliteration=payload["transliteration"],
                    semantic_range=payload["semantic_range"],
                    provenance=payload["provenance"],
                    dating_context=payload["dating_context"],
                    forbidden=tuple(payload.get("forbidden", ())),
                    registered_by=payload.get("registered_by", ""),
                    registered_at=event["occurred_at"],
                )
            elif event["event_type"] == "CANDIDATE_PROPOSED":
                self._candidates[event["aggregate_id"]] = Candidate(
                    candidate_id=event["aggregate_id"],
                    term_id=payload["term_id"],
                    text=payload["text"],
                    audience=payload["audience"],
                    proposed_by=payload["proposed_by"],
                    source_ref=payload.get("source_ref", ""),
                    rationale=payload.get("rationale", ""),
                )
            elif event["event_type"] == "BILATERAL_REVIEWED" and event["aggregate_type"] == "translation_candidate":
                stage = payload.get("stage")
                if stage in ("adopted", "rejected"):
                    candidate = self._candidates.get(event["aggregate_id"])
                    if candidate is not None:
                        candidate.status = stage
                        candidate.decided_at = event["occurred_at"]
        self._projected = len(events)

    def register_term(
        self,
        term_id: str,
        original_form: str,
        transliteration: str,
        semantic_range: str,
        provenance: str,
        dating_context: str,
        forbidden: tuple[str, ...] | list[str] = (),
        registered_by: str = "",
        occurred_at: str | None = None,
    ) -> SourceTerm:
        self._ensure_current()
        if term_id in self._terms:
            raise ValueError(f"术语已登记：{term_id}")
        missing = [
            name
            for name, value in (
                ("original_form", original_form),
                ("transliteration", transliteration),
                ("semantic_range", semantic_range),
                ("provenance", provenance),
                ("dating_context", dating_context),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"术语登记缺少字段：{'、'.join(missing)}")
        self._store.append(
            "TERM_REGISTERED",
            "source_term",
            term_id,
            summary=f"登记源语术语 {term_id}：{original_form}",
            payload={
                "original_form": original_form,
                "transliteration": transliteration,
                "semantic_range": semantic_range,
                "provenance": provenance,
                "dating_context": dating_context,
                "forbidden": list(forbidden),
                "registered_by": registered_by,
            },
            occurred_at=occurred_at,
        )
        self._ensure_current()
        return self._terms[term_id]

    def propose_candidate(
        self,
        term_id: str,
        text: str,
        audience: str,
        proposed_by: str,
        source_ref: str = "",
        rationale: str = "",
        occurred_at: str | None = None,
    ) -> Candidate:
        self._ensure_current()
        term = self._terms.get(term_id)
        if term is None:
            raise ValueError(f"术语未登记：{term_id}")
        if audience not in AUDIENCE_LEVELS:
            raise ValueError(f"未知受众层级：{audience}")
        if not text:
            raise ValueError("候选译文不能为空")
        if text in term.forbidden:
            raise ValueError(f"候选命中禁用误译：{text}")
        sequence = sum(1 for candidate in self._candidates.values() if candidate.term_id == term_id) + 1
        candidate_id = f"CAND-{term_id}-{sequence}"
        self._store.append(
            "CANDIDATE_PROPOSED",
            "translation_candidate",
            candidate_id,
            summary=f"译者 {proposed_by} 提出候选 {candidate_id}：{term_id} → {text}（{audience}）",
            payload={
                "term_id": term_id,
                "text": text,
                "audience": audience,
                "proposed_by": proposed_by,
                "source_ref": source_ref,
                "rationale": rationale,
            },
            occurred_at=occurred_at,
        )
        self._ensure_current()
        return self._candidates[candidate_id]

    def get_term(self, term_id: str) -> SourceTerm | None:
        self._ensure_current()
        return self._terms.get(term_id)

    def get_candidate(self, candidate_id: str) -> Candidate | None:
        self._ensure_current()
        return self._candidates.get(candidate_id)

    def adopted_candidate(self, term_id: str, audience: str, on_date: object = None) -> Candidate | None:
        """指定日期之前（含当日）最新采用的候选。"""
        self._ensure_current()
        instant = query_instant(on_date) if on_date is not None else None
        matches = [
            candidate
            for candidate in self._candidates.values()
            if candidate.term_id == term_id
            and candidate.audience == audience
            and candidate.status == "adopted"
            and candidate.decided_at is not None
            and (instant is None or query_instant(candidate.decided_at) <= instant)
        ]
        matches.sort(key=lambda candidate: query_instant(candidate.decided_at or ""))
        return matches[-1] if matches else None
