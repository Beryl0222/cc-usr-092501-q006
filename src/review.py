"""双边审定：提交幂等、两方会签与争议。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .store import EventStore
from .terms import TermRegistry

REVIEW_ROLES = ("egyptologist", "curator")  # 埃及学者确认原语语义 / 中国策展人判断本地表达


def _fingerprint(candidate_id: str, source_ref: str) -> str:
    material = json.dumps({"candidate_id": candidate_id, "source_ref": source_ref}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class ReviewRecord:
    """一次审定请求：两方意见与最终决定全部留痕。"""

    request_id: str
    candidate_id: str
    source_ref: str
    fingerprint: str
    submitted_by: str
    status: str = "pending"  # pending / adopted / rejected / disputed
    egypt_review: dict | None = None
    curator_review: dict | None = None
    decided_at: str | None = None


class ReviewBoard:
    """审定请求的事件投影与写入。"""

    def __init__(self, store: EventStore, terms: TermRegistry) -> None:
        self._store = store
        self._terms = terms
        self._reviews: dict[str, ReviewRecord] = {}
        self._projected = -1

    def rebuild(self) -> None:
        self._projected = -1
        self._ensure_current()

    def _ensure_current(self) -> None:
        events = self._store.events
        if len(events) == self._projected:
            return
        self._reviews = {}
        for event in events:
            if event["event_type"] != "BILATERAL_REVIEWED":
                continue
            payload = event.get("payload", {})
            request_id = payload.get("request_id")
            stage = payload.get("stage")
            if request_id is None:
                continue
            if stage == "submitted":
                self._reviews[request_id] = ReviewRecord(
                    request_id=request_id,
                    candidate_id=payload["candidate_id"],
                    source_ref=payload.get("source_ref", ""),
                    fingerprint=payload["fingerprint"],
                    submitted_by=payload.get("submitted_by", ""),
                )
            record = self._reviews.get(request_id)
            if record is None:
                continue
            if stage == "egyptologist":
                record.egypt_review = {
                    "reviewer": payload["reviewer"],
                    "opinion": payload.get("opinion", ""),
                    "approve": bool(payload.get("approve", True)),
                }
            elif stage == "curator":
                record.curator_review = {
                    "reviewer": payload["reviewer"],
                    "opinion": payload.get("opinion", ""),
                    "approve": bool(payload.get("approve", True)),
                }
            elif stage in ("adopted", "rejected"):
                record.status = stage
                record.decided_at = event["occurred_at"]
                record.egypt_review = payload.get("egypt_review", record.egypt_review)
                record.curator_review = payload.get("curator_review", record.curator_review)
            elif stage == "disputed":
                record.status = "disputed"
        self._projected = len(events)

    def submit(
        self,
        request_id: str,
        candidate_id: str,
        source_ref: str | None = None,
        submitted_by: str = "",
        occurred_at: str | None = None,
    ) -> ReviewRecord:
        """提交审定请求。

        相同请求重复到达返回既有决定；编号相同但候选或出处变化进入争议。
        """
        self._ensure_current()
        candidate = self._terms.get_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"候选不存在：{candidate_id}")
        source_ref = candidate.source_ref if source_ref is None else source_ref
        fingerprint = _fingerprint(candidate_id, source_ref)
        existing = self._reviews.get(request_id)
        if existing is not None:
            if existing.fingerprint == fingerprint:
                return existing
            if existing.status != "disputed":
                self._store.append(
                    "BILATERAL_REVIEWED",
                    "translation_candidate",
                    candidate_id,
                    summary=f"审定请求 {request_id} 编号冲突（候选或出处变化），进入争议",
                    payload={
                        "stage": "disputed",
                        "request_id": request_id,
                        "candidate_id": candidate_id,
                        "source_ref": source_ref,
                        "fingerprint": fingerprint,
                        "previous_fingerprint": existing.fingerprint,
                    },
                    occurred_at=occurred_at,
                )
                self._ensure_current()
            return self._reviews[request_id]
        self._store.append(
            "BILATERAL_REVIEWED",
            "translation_candidate",
            candidate_id,
            summary=f"提交审定请求 {request_id}：候选 {candidate_id}",
            payload={
                "stage": "submitted",
                "request_id": request_id,
                "candidate_id": candidate_id,
                "source_ref": source_ref,
                "fingerprint": fingerprint,
                "submitted_by": submitted_by,
            },
            occurred_at=occurred_at,
        )
        self._ensure_current()
        return self._reviews[request_id]

    def confirm_semantics(
        self,
        request_id: str,
        scholar: str,
        opinion: str,
        approve: bool = True,
        occurred_at: str | None = None,
    ) -> ReviewRecord:
        """埃及学者确认原语语义。"""
        return self._review(request_id, "egyptologist", scholar, opinion, approve, occurred_at)

    def judge_expression(
        self,
        request_id: str,
        curator: str,
        opinion: str,
        approve: bool = True,
        occurred_at: str | None = None,
    ) -> ReviewRecord:
        """中国策展人判断本地表达。"""
        return self._review(request_id, "curator", curator, opinion, approve, occurred_at)

    def _review(
        self,
        request_id: str,
        role: str,
        reviewer: str,
        opinion: str,
        approve: bool,
        occurred_at: str | None,
    ) -> ReviewRecord:
        self._ensure_current()
        record = self._reviews.get(request_id)
        if record is None:
            raise ValueError(f"审定请求不存在：{request_id}")
        if record.status == "disputed":
            raise ValueError(f"审定请求 {request_id} 处于争议状态，须先解决争议")
        if record.status != "pending":
            raise ValueError(f"审定请求 {request_id} 已完成终审")
        candidate = self._terms.get_candidate(record.candidate_id)
        barred = {record.submitted_by, candidate.proposed_by if candidate else None} - {None, ""}
        if reviewer in barred:
            raise ValueError("任何人不得独自完成提交和终审：提案人或提交人不得担任审定人")
        other = record.curator_review if role == "egyptologist" else record.egypt_review
        if other is not None and other["reviewer"] == reviewer:
            raise ValueError("双边审定须由不同人员分别完成")
        current = record.egypt_review if role == "egyptologist" else record.curator_review
        if current is not None:
            raise ValueError(f"该方已完成审定：{role}")
        self._store.append(
            "BILATERAL_REVIEWED",
            "translation_candidate",
            record.candidate_id,
            summary=f"审定请求 {request_id}：{role} 意见已记录",
            payload={
                "stage": role,
                "request_id": request_id,
                "candidate_id": record.candidate_id,
                "reviewer": reviewer,
                "opinion": opinion,
                "approve": bool(approve),
            },
            occurred_at=occurred_at,
        )
        self._ensure_current()
        record = self._reviews[request_id]
        if record.egypt_review is not None and record.curator_review is not None:
            adopted = record.egypt_review["approve"] and record.curator_review["approve"]
            stage = "adopted" if adopted else "rejected"
            self._store.append(
                "BILATERAL_REVIEWED",
                "translation_candidate",
                record.candidate_id,
                summary=f"审定请求 {request_id} 终审：{'采用' if adopted else '驳回'}候选 {record.candidate_id}",
                payload={
                    "stage": stage,
                    "request_id": request_id,
                    "candidate_id": record.candidate_id,
                    "egypt_review": record.egypt_review,
                    "curator_review": record.curator_review,
                },
                occurred_at=occurred_at,
            )
            self._ensure_current()
        return self._reviews[request_id]

    def get(self, request_id: str) -> ReviewRecord | None:
        self._ensure_current()
        return self._reviews.get(request_id)

    def reviews_for_candidate(self, candidate_id: str) -> list[ReviewRecord]:
        self._ensure_current()
        return [record for record in self._reviews.values() if record.candidate_id == candidate_id]
