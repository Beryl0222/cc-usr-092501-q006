"""编辑查询与审计追溯。"""

from __future__ import annotations

from typing import Any

from .passages import PassageRegistry
from .review import ReviewBoard
from .revision import RevisionService
from .store import query_instant
from .terms import TermRegistry


class QueryService:
    """按受众和发布日期给出当时有效表述，并支持从中文回溯原文与证据。"""

    def __init__(
        self,
        terms: TermRegistry,
        passages: PassageRegistry,
        reviews: ReviewBoard,
        revisions: RevisionService,
    ) -> None:
        self._terms = terms
        self._passages = passages
        self._reviews = reviews
        self._revisions = revisions

    def effective_term_text(self, term_id: str, audience: str, on_date: object) -> str:
        """该受众层级在指定日期有效的术语表述。"""
        candidate = self._terms.adopted_candidate(term_id, audience, on_date=query_instant(on_date))
        if candidate is None:
            raise ValueError(f"术语 {term_id} 在 {audience} 受众下于该日期无有效表述")
        return candidate.text

    def effective_passage(self, passage_id: str, on_date: object) -> dict[str, Any]:
        """段落版本按日期取当时内容，再叠加适用期内的勘误。"""
        passage = self._passages.get(passage_id)
        version = self._passages.version_at(passage_id, on_date)
        instant = query_instant(on_date)
        overlay: dict[str, str] = {}
        applied = []
        for errata in self._revisions.errata_for(passage_id):
            if errata["passage_version"] != version.version:
                continue
            if query_instant(errata["valid_from"]) > instant:
                continue
            if errata["valid_until"] and query_instant(errata["valid_until"]) < instant:
                continue
            for change in errata["changes"]:
                overlay[change["term_id"]] = change["to_candidate_id"]
            applied.append(errata["errata_id"])
        return {
            "passage_id": passage_id,
            "version": version.version,
            "state": passage.state,
            "audience": passage.audience,
            "text": self._render(version.segments, overlay),
            "errata_applied": applied,
        }

    def render_current(self, passage_id: str) -> str:
        passage = self._passages.get(passage_id)
        return self._render(passage.current.segments, {})

    def audit_passage(self, passage_id: str) -> dict[str, Any]:
        """从一段中文回到原文词形、出处证据与历次取舍。"""
        passage = self._passages.get(passage_id)
        version = passage.current
        terms_trace = []
        seen = set()
        for segment in version.segments:
            if segment.get("kind") != "term" or segment["term_id"] in seen:
                continue
            seen.add(segment["term_id"])
            term = self._terms.get_term(segment["term_id"])
            candidate = self._terms.get_candidate(segment["candidate_id"])
            reviews = [
                {
                    "request_id": record.request_id,
                    "status": record.status,
                    "egypt_review": record.egypt_review,
                    "curator_review": record.curator_review,
                    "decided_at": record.decided_at,
                }
                for record in self._reviews.reviews_for_candidate(segment["candidate_id"])
            ]
            terms_trace.append(
                {
                    "term_id": segment["term_id"],
                    "original_form": term.original_form if term else None,
                    "transliteration": term.transliteration if term else None,
                    "semantic_range": term.semantic_range if term else None,
                    "provenance": term.provenance if term else None,
                    "dating_context": term.dating_context if term else None,
                    "forbidden": list(term.forbidden) if term else [],
                    "candidate": (
                        {
                            "candidate_id": candidate.candidate_id,
                            "text": candidate.text,
                            "audience": candidate.audience,
                            "proposed_by": candidate.proposed_by,
                            "source_ref": candidate.source_ref,
                            "status": candidate.status,
                        }
                        if candidate
                        else None
                    ),
                    "reviews": reviews,
                }
            )
        errata = self._revisions.errata_for(passage_id)
        revision_ids = sorted(
            {item.revision_id for item in passage.versions if item.revision_id}
            | {item["revision_id"] for item in errata}
        )
        return {
            "passage_id": passage_id,
            "carrier": passage.carrier,
            "audience": passage.audience,
            "state": passage.state,
            "version": version.version,
            "text": self._render(version.segments, {}),
            "terms": terms_trace,
            "errata": errata,
            "revisions": revision_ids,
        }

    def _render(self, segments: tuple[dict, ...], overlay: dict[str, str]) -> str:
        parts = []
        for segment in segments:
            kind = segment.get("kind")
            if kind == "text":
                parts.append(segment["text"])
            elif kind == "term":
                candidate_id = overlay.get(segment["term_id"], segment["candidate_id"])
                candidate = self._terms.get_candidate(candidate_id)
                parts.append(candidate.text if candidate else "？")
        return "".join(parts)
