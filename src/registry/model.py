"""双语术语与叙事版本库的领域对象与常量。"""

from __future__ import annotations

from dataclasses import dataclass, field

# 受众层级：学者 / 成年公众 / 儿童
AUDIENCES = ("scholar", "public_adult", "child")
# 载体：展签 / 图录 / 讲稿 / 课程材料
CARRIERS = ("label", "catalogue", "lecture", "course")
# 会签角色：埃及学者确认原语语义，中国策展人判断本地表达
REVIEW_ROLES = ("egyptian_semantics", "chinese_expression")
VERDICTS = ("confirm", "reject")

# 审定状态：open=尚无意见，pending_counter=待会签，adopted/rejected=已终审，disputed=编号争议
REVIEW_OPEN = "open"
REVIEW_PENDING = "pending_counter"
REVIEW_ADOPTED = "adopted"
REVIEW_REJECTED = "rejected"
REVIEW_DISPUTED = "disputed"


class RegistryError(Exception):
    """业务规则被拒绝时抛出。"""


@dataclass
class Opinion:
    role: str
    reviewer: str
    verdict: str
    rationale: str
    at: str


@dataclass
class Term:
    term_id: str
    lemma: str
    transliteration: str
    semantic_range: str
    provenance: str
    dating_context: str
    forbidden: dict[str, str]
    registered_by: str


@dataclass
class Candidate:
    candidate_id: str
    term_id: str
    text: str
    audience: str
    proposed_by: str
    note: str


@dataclass
class Review:
    request_id: str
    candidate_id: str
    fingerprint: str
    submitted_by: str
    provenance: str
    urgent: bool = False
    status: str = REVIEW_OPEN
    opinions: list[Opinion] = field(default_factory=list)
    decided_at: str | None = None


@dataclass
class Passage:
    passage_id: str
    carrier: str
    audience: str
    text_zh: str
    term_usages: list[dict]
    version: int = 1
    published_at: str | None = None


@dataclass
class Errata:
    errata_id: str
    passage_id: str
    passage_version: int
    corrections: list[dict]
    valid_from: str
    valid_to: str | None
    issued_by: str
    fingerprint: str
