"""术语修订：影响计算、双边会签、事务化迁移与勘误。"""

from __future__ import annotations

from typing import Any

from .passages import PassageRegistry
from .store import EventStore, utcnow_iso
from .terms import TermRegistry

SIGN_ROLES = ("egyptologist", "curator")  # 埃方 / 中方会签


def _switch_segments(segments: tuple[dict, ...], changes: list[dict]) -> list[dict]:
    """把同一段话里的多个术语一次性切换到新候选。"""
    mapping = {change["term_id"]: change for change in changes}
    new_segments = []
    for segment in segments:
        if segment.get("kind") == "term" and segment.get("term_id") in mapping:
            change = mapping[segment["term_id"]]
            if segment.get("candidate_id") != change["from_candidate_id"]:
                raise ValueError(f"段落版本已变化，无法事务化切换术语 {segment['term_id']}")
            new_segments.append({**segment, "candidate_id": change["to_candidate_id"]})
        else:
            new_segments.append(dict(segment))
    return new_segments


class RevisionService:
    """修订计划、会签与勘误的事件投影。"""

    def __init__(self, store: EventStore, terms: TermRegistry, passages: PassageRegistry) -> None:
        self._store = store
        self._terms = terms
        self._passages = passages
        self._revisions: dict[str, dict[str, Any]] = {}
        self._errata: dict[str, dict[str, Any]] = {}
        self._projected = -1

    def rebuild(self) -> None:
        self._projected = -1
        self._ensure_current()

    def _ensure_current(self) -> None:
        events = self._store.events
        if len(events) == self._projected:
            return
        self._revisions = {}
        self._errata = {}
        for event in events:
            payload = event.get("payload", {})
            event_type = event["event_type"]
            if event_type == "REVISION_PREPARED":
                self._revisions[event["aggregate_id"]] = {
                    "plan": payload["plan"],
                    "signatures": {},
                    "applied": False,
                    "result": None,
                }
            elif event_type == "REVISION_SIGNED":
                revision = self._revisions.get(event["aggregate_id"])
                if revision is not None:
                    revision["signatures"][payload["role"]] = {
                        "signer": payload["signer"],
                        "opinion": payload.get("opinion", ""),
                    }
            elif event_type == "REVISION_APPLIED":
                revision = self._revisions.get(event["aggregate_id"])
                if revision is not None:
                    revision["applied"] = True
                    revision["result"] = {
                        "revision_id": event["aggregate_id"],
                        "migrated": payload.get("migrated", []),
                        "errata": payload.get("errata", []),
                    }
            elif event_type == "ERRATA_ISSUED":
                self._errata[event["aggregate_id"]] = {
                    "errata_id": event["aggregate_id"],
                    "revision_id": payload["revision_id"],
                    "passage_id": payload["passage_id"],
                    "passage_version": payload["passage_version"],
                    "carrier": payload["carrier"],
                    "audience": payload["audience"],
                    "changes": payload["changes"],
                    "valid_from": payload["valid_from"],
                    "valid_until": payload.get("valid_until"),
                    "signatures": payload["signatures"],
                    "reason": payload["reason"],
                    "emergency": payload["emergency"],
                    "issued_at": event["occurred_at"],
                }
        self._projected = len(events)

    def prepare(
        self,
        updates: dict[str, str | list[str]],
        reason: str,
        prepared_by: str,
        emergency: bool = False,
        valid_from: str | None = None,
        valid_until: str | None = None,
        occurred_at: str | None = None,
    ) -> str:
        """登记术语修订并先计算对展签、图录、讲稿和课程材料的影响。

        updates: {术语ID: 候选ID 或 [候选ID, …]}；同一术语可按受众层级给出多个候选。
        """
        self._ensure_current()
        normalized = self._normalize_updates(updates)
        if not reason:
            raise ValueError("修订必须说明理由")
        plan = {
            "updates": normalized,
            "reason": reason,
            "emergency": bool(emergency),
            "prepared_by": prepared_by,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "impact": self._compute_impact(normalized),
        }
        revision_id = f"REV-{len(self._revisions) + 1:04d}"
        self._store.append(
            "REVISION_PREPARED",
            "term_revision",
            revision_id,
            summary=f"术语修订 {revision_id} 已计算影响（{'紧急' if emergency else '常规'}）",
            payload={"plan": plan},
            occurred_at=occurred_at,
        )
        self._ensure_current()
        return revision_id

    def _normalize_updates(self, updates: dict[str, str | list[str]]) -> list[dict[str, str]]:
        if not updates:
            raise ValueError("修订至少包含一项术语更新")
        normalized = []
        for term_id, candidates in updates.items():
            if self._terms.get_term(term_id) is None:
                raise ValueError(f"术语未登记：{term_id}")
            if isinstance(candidates, str):
                candidates = [candidates]
            for candidate_id in candidates:
                candidate = self._terms.get_candidate(candidate_id)
                if candidate is None:
                    raise ValueError(f"候选不存在：{candidate_id}")
                if candidate.term_id != term_id:
                    raise ValueError(f"候选 {candidate_id} 不属于术语 {term_id}")
                if candidate.status != "adopted":
                    raise ValueError(f"候选尚未审定采用：{candidate_id}")
                normalized.append({"term_id": term_id, "candidate_id": candidate_id})
        return normalized

    def _compute_impact(self, updates: list[dict[str, str]]) -> list[dict[str, Any]]:
        """按载体与发布状态分类：未发布迁移，已发布/已印刷/已讲授只追加勘误。"""
        by_term: dict[str, list[str]] = {}
        for update in updates:
            by_term.setdefault(update["term_id"], []).append(update["candidate_id"])
        impact = []
        for passage in self._passages.all_passages():
            changes = []
            missing_audience = False
            for segment in passage.current.segments:
                if segment.get("kind") != "term":
                    continue
                candidate_ids = by_term.get(segment.get("term_id"))
                if not candidate_ids:
                    continue
                target = None
                for candidate_id in candidate_ids:
                    candidate = self._terms.get_candidate(candidate_id)
                    if (
                        candidate is not None
                        and candidate.audience == passage.audience
                        and candidate_id != segment.get("candidate_id")
                    ):
                        target = candidate
                        break
                if target is None:
                    missing_audience = True
                    continue
                current = self._terms.get_candidate(segment["candidate_id"])
                changes.append(
                    {
                        "term_id": segment["term_id"],
                        "from_candidate_id": segment["candidate_id"],
                        "to_candidate_id": target.candidate_id,
                        "from_text": current.text if current else "",
                        "to_text": target.text,
                    }
                )
            item = {
                "passage_id": passage.passage_id,
                "carrier": passage.carrier,
                "audience": passage.audience,
                "state": passage.state,
            }
            if not changes:
                if missing_audience:
                    impact.append({**item, "action": "skipped", "changes": [], "note": "无对应受众层级的候选"})
                continue
            action = "migrate" if passage.state == "draft" else "errata"
            impact.append({**item, "action": action, "changes": changes, "note": ""})
        return impact

    def impact(self, revision_id: str) -> list[dict[str, Any]]:
        """按当前状态重新计算影响（准备时的预报见 REVISION_PREPARED 事件）。"""
        self._ensure_current()
        revision = self._revisions.get(revision_id)
        if revision is None:
            raise ValueError(f"修订不存在：{revision_id}")
        return self._compute_impact(revision["plan"]["updates"])

    def sign(
        self,
        revision_id: str,
        signer: str,
        role: str,
        opinion: str = "",
        occurred_at: str | None = None,
    ) -> None:
        """责任会签：埃方与中方各一人，紧急纠错也不能跳过。"""
        self._ensure_current()
        revision = self._revisions.get(revision_id)
        if revision is None:
            raise ValueError(f"修订不存在：{revision_id}")
        if revision["applied"]:
            raise ValueError(f"修订已应用：{revision_id}")
        if role not in SIGN_ROLES:
            raise ValueError(f"未知会签角色：{role}")
        if role in revision["signatures"]:
            raise ValueError(f"该方已会签：{role}")
        for other in revision["signatures"].values():
            if other["signer"] == signer:
                raise ValueError("会签须由两方不同人员完成")
        self._store.append(
            "REVISION_SIGNED",
            "term_revision",
            revision_id,
            summary=f"修订 {revision_id} 获 {role} 会签（{signer}）",
            payload={"role": role, "signer": signer, "opinion": opinion},
            occurred_at=occurred_at,
        )
        self._ensure_current()

    def apply(self, revision_id: str, occurred_at: str | None = None) -> dict[str, Any]:
        """应用修订：未发布内容迁移，已印刷或讲授的版本只追加勘误和适用期。

        先完整校验再落盘；同一句话中的多个术语在同一事件内切换，
        失败不会留下半新半旧的译文。重复应用返回既有结果。
        """
        self._ensure_current()
        revision = self._revisions.get(revision_id)
        if revision is None:
            raise ValueError(f"修订不存在：{revision_id}")
        if revision["applied"]:
            return revision["result"]
        missing = [role for role in SIGN_ROLES if role not in revision["signatures"]]
        if missing:
            raise ValueError(f"修订缺少会签（紧急纠错也不能跳过责任签署）：{'、'.join(missing)}")
        plan = revision["plan"]
        impact = self._compute_impact(plan["updates"])
        # 第一阶段：完整校验所有段落的切换结果，任何失败都不写入事件。
        migrations: list[tuple[str, list[dict]]] = []
        for item in impact:
            if item["action"] != "migrate":
                continue
            passage = self._passages.get(item["passage_id"])
            if passage.current.revision_id == revision_id:
                continue  # 恢复场景：该段落已随本次修订迁移
            new_segments = _switch_segments(passage.current.segments, item["changes"])
            self._passages.validate_segments(passage.audience, new_segments)
            migrations.append((item["passage_id"], new_segments))
        # 第二阶段：逐段落落盘；每段落的全量术语切换写在同一事件内。
        migrated = []
        for passage_id, new_segments in migrations:
            self._passages.apply_migration(passage_id, new_segments, revision_id, occurred_at=occurred_at)
            migrated.append(passage_id)
        issued = []
        for item in impact:
            if item["action"] != "errata":
                continue
            errata_id = f"ERR-{revision_id}-{item['passage_id']}"
            if errata_id in self._errata:
                continue  # 服务恢复后不重复发出勘误
            passage = self._passages.get(item["passage_id"])
            self._store.append(
                "ERRATA_ISSUED",
                "errata_release",
                errata_id,
                summary=f"勘误 {errata_id}：段落 {item['passage_id']} 版本 {passage.current.version} 术语表述更正",
                payload={
                    "revision_id": revision_id,
                    "passage_id": item["passage_id"],
                    "passage_version": passage.current.version,
                    "carrier": passage.carrier,
                    "audience": passage.audience,
                    "changes": item["changes"],
                    "valid_from": plan["valid_from"] or occurred_at or utcnow_iso(),
                    "valid_until": plan["valid_until"],
                    "signatures": [
                        {"role": role, **signature} for role, signature in revision["signatures"].items()
                    ],
                    "reason": plan["reason"],
                    "emergency": plan["emergency"],
                },
                occurred_at=occurred_at,
            )
            issued.append(errata_id)
            self._ensure_current()
        result = {"revision_id": revision_id, "migrated": migrated, "errata": issued}
        self._store.append(
            "REVISION_APPLIED",
            "term_revision",
            revision_id,
            summary=f"修订 {revision_id} 已应用：迁移 {len(migrated)} 段，勘误 {len(issued)} 条",
            payload=result,
            occurred_at=occurred_at,
        )
        self._ensure_current()
        return result

    def pending(self) -> list[str]:
        self._ensure_current()
        return [revision_id for revision_id, revision in self._revisions.items() if not revision["applied"]]

    def resume_pending(self, occurred_at: str | None = None) -> dict[str, list[str]]:
        """服务恢复后继续待会签修订：已齐签的补应用，未齐签的继续等待。"""
        self._ensure_current()
        applied, awaiting = [], []
        for revision_id in self.pending():
            revision = self._revisions[revision_id]
            if all(role in revision["signatures"] for role in SIGN_ROLES):
                self.apply(revision_id, occurred_at=occurred_at)
                applied.append(revision_id)
            else:
                awaiting.append(revision_id)
        return {"applied": applied, "awaiting_signature": awaiting}

    def errata_for(self, passage_id: str) -> list[dict[str, Any]]:
        self._ensure_current()
        return [errata for errata in self._errata.values() if errata["passage_id"] == passage_id]
