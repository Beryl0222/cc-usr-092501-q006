"""测试共用的建库流程。"""

from __future__ import annotations

from src.registry import Registry

COORDINATOR = "学术协调人"
EGYPTOLOGIST = "埃及学者·甲"
CURATOR = "中国策展人·乙"
TRANSLATOR = "译者·丙"

T0 = "2026-09-01T09:00:00+08:00"


def make_registry(path=None) -> Registry:
    return Registry(path)


def seed_term(registry: Registry, term_id: str = "TERM-001", forbidden=("木乃伊",), occurred_at: str = T0, **overrides):
    fields = {
        "original_form": "pr-ˁḥ",
        "transliteration": "per-aa",
        "semantic_range": "王宫、大宅；引申指法老政权",
        "provenance": "《都灵王表》第三卷",
        "dating_context": "新王国时期（约公元前1550—前1077年）",
        "registered_by": COORDINATOR,
    }
    fields.update(overrides)
    return registry.terms.register_term(term_id, forbidden=forbidden, occurred_at=occurred_at, **fields)


def adopt_candidate(
    registry: Registry,
    term_id: str = "TERM-001",
    text: str = "王宫",
    audience: str = "public",
    request_id: str = "REQ-1",
    source_ref: str = "《古埃及语词典》卷二",
    occurred_at: str = T0,
    translator: str = TRANSLATOR,
    egyptologist: str = EGYPTOLOGIST,
    curator: str = CURATOR,
):
    """走完提案与双边审定，返回已采用的候选。"""
    candidate = registry.terms.propose_candidate(
        term_id, text, audience=audience, proposed_by=translator, source_ref=source_ref, occurred_at=occurred_at
    )
    registry.reviews.submit(request_id, candidate.candidate_id, submitted_by=COORDINATOR, occurred_at=occurred_at)
    registry.reviews.confirm_semantics(request_id, scholar=egyptologist, opinion="原语语义确认无误", occurred_at=occurred_at)
    registry.reviews.judge_expression(request_id, curator=curator, opinion="中文表达符合本地习惯", occurred_at=occurred_at)
    return candidate


def seed_passage(
    registry: Registry,
    passage_id: str,
    terms: list[tuple[str, str]],
    carrier: str = "label",
    audience: str = "public",
    state: str = "draft",
    occurred_at: str = T0,
):
    """登记一条 开头…术语…结尾 结构的段落。"""
    segments = [{"kind": "text", "text": "开头"}]
    for term_id, candidate_id in terms:
        segments.append({"kind": "term", "term_id": term_id, "candidate_id": candidate_id})
    segments.append({"kind": "text", "text": "结尾"})
    return registry.passages.register(
        passage_id, carrier=carrier, audience=audience, segments=segments, author="撰稿人", state=state, occurred_at=occurred_at
    )
