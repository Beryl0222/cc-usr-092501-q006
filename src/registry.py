"""联合考古上下文版本库门面：术语、审定、段落、修订与查询。"""

from __future__ import annotations

from pathlib import Path

from .passages import PassageRegistry
from .query import QueryService
from .review import ReviewBoard
from .revision import RevisionService
from .store import EventStore
from .terms import TermRegistry


class Registry:
    """双语术语与叙事版本库的统一入口。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.store = EventStore(path)
        self.terms = TermRegistry(self.store)
        self.reviews = ReviewBoard(self.store, self.terms)
        self.passages = PassageRegistry(self.store, self.terms)
        self.revisions = RevisionService(self.store, self.terms, self.passages)
        self.query = QueryService(self.terms, self.passages, self.reviews, self.revisions)

    @classmethod
    def open(cls, path: str | Path) -> "Registry":
        """打开既有事件日志（服务恢复入口）。"""
        return cls(path)

    def recover(self, occurred_at: str | None = None) -> dict[str, list[str]]:
        """重放事件日志，继续待会签修订；已发出的勘误不重复发出。"""
        for projection in (self.terms, self.reviews, self.passages, self.revisions):
            projection.rebuild()
        return self.revisions.resume_pending(occurred_at=occurred_at)
