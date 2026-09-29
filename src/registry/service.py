"""双语术语与叙事版本库：命令、查询与审计。

设计要点：
- 一切事实以事件落盘（JSONL），状态由回放重建；服务恢复后可继续待会签的审定。
- 同一段落里的多个术语在一次迁移事件中整体切换，校验失败则整段不落事件。
- 已发布段落不改原文，只追加带适用期的勘误；勘误按指纹去重，重启后不会重复发出。
- 审定请求按（候选、出处）指纹幂等：重复到达返回既有决定，编号冲突进入争议。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from ..envelope import validate_event
from .model import (
    AUDIENCES,
    CARRIERS,
    REVIEW_ADOPTED,
    REVIEW_DISPUTED,
    REVIEW_OPEN,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    REVIEW_ROLES,
    VERDICTS,
    Candidate,
    Errata,
    Opinion,
    Passage,
    RegistryError,
    Review,
    Term,
)
from .store import EventLog


def _canonical(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _fingerprint(payload: object) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RegistryError("时间必须包含时区")
    return parsed


class Registry:
    """术语与叙事版本库。log_path 为事件日志路径，now 可注入时钟便于测试。"""

    def __init__(self, log_path: str, now=None):
        self._log = EventLog(log_path)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.terms: dict[str, Term] = {}
        self.candidates: dict[str, Candidate] = {}
        self.reviews: dict[str, Review] = {}
        self.passages: dict[str, Passage] = {}
        self.errata: list[Errata] = []
        self.adoptions: list[dict] = []
        self.events: list[dict] = []
        self._versions: dict[str, int] = {}
        for event in self._log.load():
            self._apply(event)
        self._recover()

    # ---- 事件写入与回放 ----

    def _emit_batch(self, specs: list[tuple]) -> list[dict]:
        """把一组事件作为一次写入落盘（同一事务），再依次应用。"""
        events = []
        versions = dict(self._versions)
        for event_type, aggregate_type, aggregate_id, summary, payload in specs:
            version = versions.get(aggregate_id, 0) + 1
            versions[aggregate_id] = version
            event = {
                "event_id": f"{aggregate_id}-v{version}",
                "event_type": event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "occurred_at": self._now().isoformat(),
                "version": version,
                "summary": summary,
                "payload": payload,
            }
            errors = validate_event(event)
            if errors:
                raise RegistryError("；".join(errors))
            events.append(event)
        self._log.append(events)
        for event in events:
            self._apply(event)
        return events

    def _emit(self, event_type, aggregate_type, aggregate_id, summary, payload) -> dict:
        return self._emit_batch([(event_type, aggregate_type, aggregate_id, summary, payload)])[0]

    def _apply(self, event: dict) -> None:
        self.events.append(event)
        self._versions[event["aggregate_id"]] = event["version"]
        event_type = event["event_type"]
        payload = event.get("payload", {})
        aggregate_id = event["aggregate_id"]
        if event_type == "TERM_REGISTERED":
            self.terms[aggregate_id] = Term(
                term_id=aggregate_id,
                lemma=payload["lemma"],
                transliteration=payload["transliteration"],
                semantic_range=payload["semantic_range"],
                provenance=payload["provenance"],
                dating_context=payload["dating_context"],
                forbidden=dict(payload.get("forbidden", {})),
                registered_by=payload["registered_by"],
            )
        elif event_type == "CANDIDATE_PROPOSED":
            self.candidates[aggregate_id] = Candidate(
                candidate_id=aggregate_id,
                term_id=payload["term_id"],
                text=payload["text"],
                audience=payload["audience"],
                proposed_by=payload["proposed_by"],
                note=payload.get("note", ""),
            )
        elif event_type == "REVIEW_OPENED":
            self.reviews[aggregate_id] = Review(
                request_id=aggregate_id,
                candidate_id=payload["candidate_id"],
                fingerprint=payload["fingerprint"],
                submitted_by=payload["submitted_by"],
                provenance=payload["provenance"],
                urgent=payload.get("urgent", False),
            )
        elif event_type == "OPINION_RECORDED":
            review = self.reviews[aggregate_id]
            review.opinions.append(
                Opinion(
                    role=payload["role"],
                    reviewer=payload["reviewer"],
                    verdict=payload["verdict"],
                    rationale=payload["rationale"],
                    at=event["occurred_at"],
                )
            )
            if review.status in (REVIEW_OPEN, REVIEW_PENDING):
                review.status = REVIEW_PENDING
        elif event_type == "BILATERAL_REVIEWED":
            review = self.reviews[aggregate_id]
            review.status = REVIEW_ADOPTED if payload["outcome"] == "adopted" else REVIEW_REJECTED
            review.decided_at = event["occurred_at"]
            if payload["outcome"] == "adopted":
                self.adoptions.append(
                    {
                        "term_id": payload["term_id"],
                        "audience": payload["audience"],
                        "text": payload["text"],
                        "candidate_id": payload["candidate_id"],
                        "request_id": aggregate_id,
                        "effective_from": payload["effective_from"],
                    }
                )
        elif event_type == "REVIEW_DISPUTED":
            self.reviews[aggregate_id].status = REVIEW_DISPUTED
        elif event_type == "PASSAGE_REGISTERED":
            self.passages[aggregate_id] = Passage(
                passage_id=aggregate_id,
                carrier=payload["carrier"],
                audience=payload["audience"],
                text_zh=payload["text_zh"],
                term_usages=[dict(usage) for usage in payload["term_usages"]],
            )
        elif event_type == "PASSAGE_PUBLISHED":
            passage = self.passages[aggregate_id]
            passage.published_at = payload["published_at"]
            passage.version = event["version"]
        elif event_type == "PASSAGE_MIGRATED":
            passage = self.passages[aggregate_id]
            passage.text_zh = payload["new_text_zh"]
            for usage in passage.term_usages:
                for replacement in payload["replacements"]:
                    if (
                        usage["term_id"] == replacement["term_id"]
                        and usage["rendering"] == replacement["from_text"]
                    ):
                        usage["rendering"] = replacement["to_text"]
            passage.version = event["version"]
        elif event_type == "ERRATA_ISSUED":
            self.errata.append(
                Errata(
                    errata_id=aggregate_id,
                    passage_id=payload["passage_id"],
                    passage_version=payload["passage_version"],
                    corrections=[dict(correction) for correction in payload["corrections"]],
                    valid_from=payload["valid_from"],
                    valid_to=payload.get("valid_to"),
                    issued_by=payload["issued_by"],
                    fingerprint=payload["fingerprint"],
                )
            )

    def _recover(self) -> None:
        """回放结束后补齐崩溃前未及落盘的终审（双方意见已齐但无决定）。"""
        for review in list(self.reviews.values()):
            if review.status in (REVIEW_OPEN, REVIEW_PENDING) and len(review.opinions) == 2:
                self._emit_batch([self._decision_spec(review, review.opinions)])

    # ---- 术语与候选 ----

    def register_term(
        self,
        *,
        term_id: str,
        lemma: str,
        transliteration: str,
        semantic_range: str,
        provenance: str,
        dating_context: str,
        forbidden: dict | None = None,
        registered_by: str,
    ) -> Term:
        if term_id in self.terms:
            raise RegistryError(f"术语已登记：{term_id}")
        for label, value in (
            ("原文词形", lemma),
            ("转写", transliteration),
            ("语义范围", semantic_range),
            ("出处", provenance),
            ("年代语境", dating_context),
            ("登记人", registered_by),
        ):
            if not value:
                raise RegistryError(f"缺少{label}")
        self._emit(
            "TERM_REGISTERED",
            "source_term",
            term_id,
            f"登记术语 {lemma}",
            {
                "lemma": lemma,
                "transliteration": transliteration,
                "semantic_range": semantic_range,
                "provenance": provenance,
                "dating_context": dating_context,
                "forbidden": dict(forbidden or {}),
                "registered_by": registered_by,
            },
        )
        return self.terms[term_id]

    def propose_candidate(
        self,
        *,
        candidate_id: str,
        term_id: str,
        text: str,
        audience: str,
        proposed_by: str,
        note: str = "",
    ) -> Candidate:
        term = self._term(term_id)
        if audience not in AUDIENCES:
            raise RegistryError(f"未知受众层级：{audience}")
        if candidate_id in self.candidates:
            raise RegistryError(f"候选编号已存在：{candidate_id}")
        if not text:
            raise RegistryError("缺少候选表述")
        if text in term.forbidden:
            raise RegistryError(f"候选命中禁用误译：{text}（{term.forbidden[text]}）")
        self._emit(
            "CANDIDATE_PROPOSED",
            "translation_candidate",
            candidate_id,
            f"候选“{text}”（{audience}）",
            {
                "term_id": term_id,
                "text": text,
                "audience": audience,
                "proposed_by": proposed_by,
                "note": note,
            },
        )
        return self.candidates[candidate_id]

    # ---- 双边会签 ----

    def open_review(
        self,
        *,
        request_id: str,
        candidate_id: str,
        submitted_by: str,
        provenance: str | None = None,
        urgent: bool = False,
    ) -> tuple[Review, bool]:
        """发起审定请求。相同请求重复到达返回既有记录；编号冲突进入争议。"""
        candidate = self._candidate(candidate_id)
        term = self._term(candidate.term_id)
        provenance = provenance or term.provenance
        fingerprint = _fingerprint(
            {"candidate_id": candidate_id, "text": candidate.text, "provenance": provenance}
        )
        existing = self.reviews.get(request_id)
        if existing is not None:
            if existing.fingerprint == fingerprint:
                return existing, False
            if existing.status != REVIEW_DISPUTED:
                self._emit(
                    "REVIEW_DISPUTED",
                    "review_request",
                    request_id,
                    f"审定请求 {request_id} 编号冲突进入争议",
                    {
                        "recorded_fingerprint": existing.fingerprint,
                        "incoming_fingerprint": fingerprint,
                        "incoming_candidate_id": candidate_id,
                        "incoming_provenance": provenance,
                    },
                )
            raise RegistryError(f"审定请求编号冲突，已进入争议：{request_id}")
        self._emit(
            "REVIEW_OPENED",
            "review_request",
            request_id,
            f"发起审定 {request_id}（候选“{candidate.text}”）",
            {
                "candidate_id": candidate_id,
                "fingerprint": fingerprint,
                "submitted_by": submitted_by,
                "provenance": provenance,
                "urgent": urgent,
            },
        )
        return self.reviews[request_id], True

    def record_opinion(
        self,
        *,
        request_id: str,
        role: str,
        reviewer: str,
        verdict: str,
        rationale: str,
    ) -> Review:
        review = self._review(request_id)
        if review.status == REVIEW_DISPUTED:
            raise RegistryError("请求处于争议中，须先解决编号冲突")
        if review.status in (REVIEW_ADOPTED, REVIEW_REJECTED):
            raise RegistryError("请求已终审，不得再签署")
        if role not in REVIEW_ROLES:
            raise RegistryError(f"未知审定角色：{role}")
        if verdict not in VERDICTS:
            raise RegistryError(f"未知审定结论：{verdict}")
        if not rationale:
            raise RegistryError("必须签署责任说明，紧急纠错也不例外")
        if any(opinion.role == role for opinion in review.opinions):
            raise RegistryError("该方已签署意见")
        if any(opinion.reviewer == reviewer for opinion in review.opinions):
            raise RegistryError("两方意见须由不同人员签署")
        completing = len(review.opinions) == 1
        if completing and reviewer == review.submitted_by:
            raise RegistryError("提交人不得独自完成提交和终审")
        opinion = {"role": role, "reviewer": reviewer, "verdict": verdict, "rationale": rationale}
        specs = [
            (
                "OPINION_RECORDED",
                "review_request",
                request_id,
                f"{'埃方语义' if role == 'egyptian_semantics' else '中方表达'}意见：{verdict}",
                opinion,
            )
        ]
        if completing:
            opinions = review.opinions + [Opinion(at="", **opinion)]
            specs.append(self._decision_spec(review, opinions))
        self._emit_batch(specs)
        return self.reviews[request_id]

    def _decision_spec(self, review: Review, opinions: list[Opinion]) -> tuple:
        candidate = self._candidate(review.candidate_id)
        outcome = "adopted" if all(opinion.verdict == "confirm" for opinion in opinions) else "rejected"
        decided_at = self._now().isoformat()
        summary = f"审定 {review.request_id}：{'采用' if outcome == 'adopted' else '否决'}“{candidate.text}”"
        payload = {
            "outcome": outcome,
            "candidate_id": review.candidate_id,
            "term_id": candidate.term_id,
            "audience": candidate.audience,
            "text": candidate.text,
            "effective_from": decided_at,
            "opinions": [
                {
                    "role": opinion.role,
                    "reviewer": opinion.reviewer,
                    "verdict": opinion.verdict,
                    "rationale": opinion.rationale,
                }
                for opinion in opinions
            ],
        }
        return ("BILATERAL_REVIEWED", "review_request", review.request_id, summary, payload)

    # ---- 叙事段落 ----

    def register_passage(
        self,
        *,
        passage_id: str,
        carrier: str,
        audience: str,
        text_zh: str,
        term_usages: list[dict],
    ) -> Passage:
        if passage_id in self.passages:
            raise RegistryError(f"段落已登记：{passage_id}")
        if carrier not in CARRIERS:
            raise RegistryError(f"未知载体：{carrier}")
        if audience not in AUDIENCES:
            raise RegistryError(f"未知受众层级：{audience}")
        if not text_zh:
            raise RegistryError("缺少段落文本")
        usages = []
        for usage in term_usages:
            self._term(usage["term_id"])
            rendering = usage.get("rendering", "")
            if not rendering:
                raise RegistryError("术语用法缺少表述")
            if rendering not in text_zh:
                raise RegistryError(f"表述未出现在段落中：{rendering}")
            usages.append({"term_id": usage["term_id"], "rendering": rendering})
        self._emit(
            "PASSAGE_REGISTERED",
            "narrative_passage",
            passage_id,
            f"登记段落 {passage_id}（{carrier}/{audience}）",
            {
                "carrier": carrier,
                "audience": audience,
                "text_zh": text_zh,
                "term_usages": usages,
            },
        )
        return self.passages[passage_id]

    def publish_passage(self, *, passage_id: str) -> Passage:
        passage = self._passage(passage_id)
        if passage.published_at is not None:
            raise RegistryError("段落已发布，不得重复发布")
        published_at = self._now().isoformat()
        self._emit(
            "PASSAGE_PUBLISHED",
            "narrative_passage",
            passage_id,
            f"段落 {passage_id} 已印刷或讲授",
            {"published_at": published_at, "text_zh": passage.text_zh},
        )
        return passage

    # ---- 修订：影响分析、原子迁移、勘误 ----

    def impact_analysis(self, *, term_ids: list[str], audience: str) -> list[dict]:
        """计算术语修订对展签、图录、讲稿和课程材料的影响，不落任何事件。"""
        targets = {}
        for term_id in term_ids:
            self._term(term_id)
            adoption = self._latest_adoption(term_id, audience)
            if adoption is None:
                raise RegistryError(f"术语 {term_id} 在受众 {audience} 下尚无已审定表述")
            targets[term_id] = adoption["text"]
        report = []
        for passage in self.passages.values():
            if passage.audience != audience:
                continue
            replacements: dict[tuple, dict] = {}
            for usage in passage.term_usages:
                new_text = targets.get(usage["term_id"])
                if new_text is None or usage["rendering"] == new_text:
                    continue
                replacements.setdefault(
                    (usage["term_id"], usage["rendering"]),
                    {
                        "term_id": usage["term_id"],
                        "from_text": usage["rendering"],
                        "to_text": new_text,
                    },
                )
            if not replacements:
                continue
            report.append(
                {
                    "passage_id": passage.passage_id,
                    "carrier": passage.carrier,
                    "state": "published" if passage.published_at else "draft",
                    "version": passage.version,
                    "replacements": list(replacements.values()),
                }
            )
        return report

    def apply_revision(
        self,
        *,
        term_ids: list[str],
        audience: str,
        issued_by: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> dict:
        """执行修订：未发布段落整体迁移，已发布段落只追加带适用期的勘误。"""
        report = self.impact_analysis(term_ids=term_ids, audience=audience)
        valid_from = valid_from or self._now().isoformat()
        result = {"migrated": [], "errata": [], "failed": []}
        for item in report:
            passage = self.passages[item["passage_id"]]
            replacements = item["replacements"]
            if item["state"] == "draft":
                new_text = passage.text_zh
                missing = [r["from_text"] for r in replacements if r["from_text"] not in new_text]
                if missing:
                    # 整段不切换，不留下半新半旧的译文
                    result["failed"].append(
                        {
                            "passage_id": passage.passage_id,
                            "reason": f"段落中找不到待替换表述：{'、'.join(missing)}",
                        }
                    )
                    continue
                for replacement in replacements:
                    new_text = new_text.replace(replacement["from_text"], replacement["to_text"])
                event = self._emit(
                    "PASSAGE_MIGRATED",
                    "narrative_passage",
                    passage.passage_id,
                    f"迁移段落 {passage.passage_id} 至新表述",
                    {
                        "from_version": passage.version,
                        "replacements": replacements,
                        "new_text_zh": new_text,
                    },
                )
                result["migrated"].append(
                    {"passage_id": passage.passage_id, "version": event["version"]}
                )
            else:
                fingerprint = _fingerprint(
                    {
                        "passage_id": passage.passage_id,
                        "passage_version": passage.version,
                        "corrections": sorted(
                            replacements, key=lambda r: (r["term_id"], r["from_text"])
                        ),
                    }
                )
                existing = next((e for e in self.errata if e.fingerprint == fingerprint), None)
                if existing is not None:
                    # 相同勘误已发出（含服务恢复后重跑），直接复用既有决定
                    result["errata"].append(
                        {"errata_id": existing.errata_id, "passage_id": passage.passage_id, "deduplicated": True}
                    )
                    continue
                errata_id = f"ERR-{fingerprint[:12]}"
                self._emit(
                    "ERRATA_ISSUED",
                    "errata_release",
                    errata_id,
                    f"就段落 {passage.passage_id} 发出勘误",
                    {
                        "passage_id": passage.passage_id,
                        "passage_version": passage.version,
                        "corrections": replacements,
                        "valid_from": valid_from,
                        "valid_to": valid_to,
                        "issued_by": issued_by,
                        "fingerprint": fingerprint,
                    },
                )
                result["errata"].append(
                    {"errata_id": errata_id, "passage_id": passage.passage_id, "deduplicated": False}
                )
        return result

    # ---- 编辑查询与审计 ----

    def effective_rendering(self, *, term_id: str, audience: str, at: str) -> dict | None:
        """编辑 API：按受众与日期给出当时有效的表述。"""
        self._term(term_id)
        moment = _parse_ts(at)
        best = None
        for adoption in self.adoptions:
            if adoption["term_id"] != term_id or adoption["audience"] != audience:
                continue
            if _parse_ts(adoption["effective_from"]) > moment:
                continue
            # 同一时刻的多次采用，以日志中后到的决定为准
            if best is None or _parse_ts(adoption["effective_from"]) >= _parse_ts(
                best["effective_from"]
            ):
                best = adoption
        if best is None:
            return None
        return {
            "term_id": term_id,
            "audience": audience,
            "text": best["text"],
            "candidate_id": best["candidate_id"],
            "request_id": best["request_id"],
            "effective_from": best["effective_from"],
        }

    def render_passage(self, *, passage_id: str, at: str | None = None) -> dict:
        """渲染段落在某日期的有效文本：未发布取当前稿，已发布按适用期套用勘误。"""
        passage = self._passage(passage_id)
        if passage.published_at is None:
            return {
                "passage_id": passage_id,
                "state": "draft",
                "version": passage.version,
                "text": passage.text_zh,
                "errata_applied": [],
            }
        moment = _parse_ts(at) if at else self._now()
        if moment < _parse_ts(passage.published_at):
            raise RegistryError("该日期尚无已发布版本")
        applicable = [
            errata
            for errata in self.errata
            if errata.passage_id == passage_id
            and _parse_ts(errata.valid_from) <= moment
            and (errata.valid_to is None or moment <= _parse_ts(errata.valid_to))
        ]
        text = passage.text_zh
        applied = []
        if applicable:
            # 每份勘误相对印刷原文自包含；适用期重叠时取最新一份
            latest = max(applicable, key=lambda errata: _parse_ts(errata.valid_from))
            for correction in latest.corrections:
                text = text.replace(correction["from_text"], correction["to_text"])
            applied.append(latest.errata_id)
        return {
            "passage_id": passage_id,
            "state": "published",
            "version": passage.version,
            "text": text,
            "errata_applied": applied,
        }

    def audit_passage(self, *, passage_id: str) -> dict:
        """从一段中文回到原文词形、证据（出处、语义范围、年代语境）与历次取舍。"""
        passage = self._passage(passage_id)
        usages = []
        for usage in passage.term_usages:
            term = self.terms.get(usage["term_id"])
            adoption = next(
                (
                    item
                    for item in reversed(self.adoptions)
                    if item["term_id"] == usage["term_id"] and item["text"] == usage["rendering"]
                ),
                None,
            )
            decision = None
            if adoption is not None:
                review = self.reviews[adoption["request_id"]]
                decision = {
                    "request_id": review.request_id,
                    "candidate_id": review.candidate_id,
                    "decided_at": review.decided_at,
                    "opinions": [opinion.__dict__ for opinion in review.opinions],
                }
            history = []
            for event in self.events:
                payload = event.get("payload", {})
                if event["event_type"] == "PASSAGE_MIGRATED" and event["aggregate_id"] == passage_id:
                    for replacement in payload["replacements"]:
                        if replacement["term_id"] == usage["term_id"]:
                            history.append(
                                {
                                    "kind": "migration",
                                    "at": event["occurred_at"],
                                    "from_text": replacement["from_text"],
                                    "to_text": replacement["to_text"],
                                }
                            )
                if (
                    event["event_type"] == "ERRATA_ISSUED"
                    and payload.get("passage_id") == passage_id
                ):
                    for correction in payload["corrections"]:
                        if correction["term_id"] == usage["term_id"]:
                            history.append(
                                {
                                    "kind": "errata",
                                    "errata_id": event["aggregate_id"],
                                    "at": event["occurred_at"],
                                    "from_text": correction["from_text"],
                                    "to_text": correction["to_text"],
                                    "valid_from": payload["valid_from"],
                                    "valid_to": payload.get("valid_to"),
                                }
                            )
            usages.append(
                {
                    "term_id": usage["term_id"],
                    "rendering": usage["rendering"],
                    "source": {
                        "lemma": term.lemma,
                        "transliteration": term.transliteration,
                        "semantic_range": term.semantic_range,
                        "provenance": term.provenance,
                        "dating_context": term.dating_context,
                    },
                    "decision": decision,
                    "history": history,
                }
            )
        return {
            "passage_id": passage_id,
            "carrier": passage.carrier,
            "audience": passage.audience,
            "version": passage.version,
            "state": "published" if passage.published_at else "draft",
            "text_zh": passage.text_zh,
            "usages": usages,
        }

    def find_passages(self, snippet: str) -> list[str]:
        """按中文片段定位段落。"""
        return [
            passage.passage_id
            for passage in self.passages.values()
            if snippet in passage.text_zh
        ]

    def pending_reviews(self) -> list[dict]:
        """待会签的审定请求（服务恢复后据此继续）。"""
        return [
            {
                "request_id": review.request_id,
                "candidate_id": review.candidate_id,
                "status": review.status,
                "urgent": review.urgent,
                "opinions": len(review.opinions),
            }
            for review in self.reviews.values()
            if review.status in (REVIEW_OPEN, REVIEW_PENDING)
        ]

    # ---- 内部 ----

    def _term(self, term_id: str) -> Term:
        term = self.terms.get(term_id)
        if term is None:
            raise RegistryError(f"术语未登记：{term_id}")
        return term

    def _candidate(self, candidate_id: str) -> Candidate:
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            raise RegistryError(f"候选不存在：{candidate_id}")
        return candidate

    def _review(self, request_id: str) -> Review:
        review = self.reviews.get(request_id)
        if review is None:
            raise RegistryError(f"审定请求不存在：{request_id}")
        return review

    def _passage(self, passage_id: str) -> Passage:
        passage = self.passages.get(passage_id)
        if passage is None:
            raise RegistryError(f"段落不存在：{passage_id}")
        return passage

    def _latest_adoption(self, term_id: str, audience: str) -> dict | None:
        best = None
        for adoption in self.adoptions:
            if adoption["term_id"] == term_id and adoption["audience"] == audience:
                if best is None or adoption["effective_from"] >= best["effective_from"]:
                    best = adoption
        return best
