from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import seeds
from src.registry import Registry


class RevisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = seeds.make_registry()
        seeds.seed_term(self.registry)
        self.old = seeds.adopt_candidate(self.registry, text="王宫", request_id="REQ-1")
        self.new = seeds.adopt_candidate(self.registry, text="王宫（大宅）", request_id="REQ-2")

    def _prepare_signed(self, updates, **kwargs) -> str:
        revision_id = self.registry.revisions.prepare(updates, reason=kwargs.pop("reason", "译名统一"), prepared_by=seeds.COORDINATOR, **kwargs)
        self.registry.revisions.sign(revision_id, signer=seeds.EGYPTOLOGIST, role="egyptologist", opinion="同意")
        self.registry.revisions.sign(revision_id, signer=seeds.CURATOR, role="curator", opinion="同意")
        return revision_id

    def test_impact_classifies_by_carrier_state(self) -> None:
        seeds.seed_passage(self.registry, "P-DRAFT", [("TERM-001", self.old.candidate_id)], state="draft")
        seeds.seed_passage(self.registry, "P-PRINT", [("TERM-001", self.old.candidate_id)], state="printed")
        seeds.seed_passage(self.registry, "P-PUB", [("TERM-001", self.old.candidate_id)], state="published")
        seeds.seed_passage(self.registry, "P-LECT", [("TERM-001", self.old.candidate_id)], carrier="lecture", state="taught")
        revision_id = self.registry.revisions.prepare(
            {"TERM-001": self.new.candidate_id}, reason="译名统一", prepared_by=seeds.COORDINATOR
        )
        impact = {item["passage_id"]: item for item in self.registry.revisions.impact(revision_id)}
        self.assertEqual(impact["P-DRAFT"]["action"], "migrate")
        self.assertEqual(impact["P-PRINT"]["action"], "errata")
        self.assertEqual(impact["P-PUB"]["action"], "errata")
        self.assertEqual(impact["P-LECT"]["action"], "errata")
        self.assertEqual({item["carrier"] for item in impact.values()}, {"label", "lecture"})

    def test_apply_migrates_draft_and_issues_errata_with_signatures(self) -> None:
        seeds.seed_passage(self.registry, "P-DRAFT", [("TERM-001", self.old.candidate_id)], state="draft")
        seeds.seed_passage(self.registry, "P-PRINT", [("TERM-001", self.old.candidate_id)], state="printed")
        revision_id = self._prepare_signed(
            {"TERM-001": self.new.candidate_id}, valid_from="2026-10-01T00:00:00+08:00"
        )
        result = self.registry.revisions.apply(revision_id)
        self.assertEqual(result["migrated"], ["P-DRAFT"])
        self.assertEqual(result["errata"], [f"ERR-{revision_id}-P-PRINT"])
        draft = self.registry.passages.get("P-DRAFT")
        self.assertEqual(draft.current.version, 2)
        self.assertEqual(self.registry.query.render_current("P-DRAFT"), "开头王宫（大宅）结尾")
        printed = self.registry.passages.get("P-PRINT")
        self.assertEqual(printed.current.version, 1)
        errata = self.registry.revisions.errata_for("P-PRINT")
        self.assertEqual(len(errata), 1)
        self.assertEqual(errata[0]["valid_from"], "2026-10-01T00:00:00+08:00")
        self.assertEqual({signature["role"] for signature in errata[0]["signatures"]}, {"egyptologist", "curator"})

    def test_multi_term_switch_is_single_event(self) -> None:
        seeds.seed_term(
            self.registry,
            term_id="TERM-002",
            original_form="ḥtp",
            transliteration="hetep",
            semantic_range="祭品、安息",
            provenance="《萨卡拉铭文集》",
            dating_context="古王国时期",
        )
        old2 = seeds.adopt_candidate(self.registry, term_id="TERM-002", text="祭品", request_id="REQ-3")
        new2 = seeds.adopt_candidate(self.registry, term_id="TERM-002", text="供品", request_id="REQ-4")
        seeds.seed_passage(
            self.registry,
            "P-MULTI",
            [("TERM-001", self.old.candidate_id), ("TERM-002", old2.candidate_id)],
        )
        revision_id = self._prepare_signed({"TERM-001": self.new.candidate_id, "TERM-002": new2.candidate_id})
        self.registry.revisions.apply(revision_id)
        migrations = [
            event
            for event in self.registry.store.events
            if event["event_type"] == "PASSAGE_MIGRATED" and event["aggregate_id"] == "P-MULTI"
        ]
        self.assertEqual(len(migrations), 1)
        self.assertEqual(self.registry.query.render_current("P-MULTI"), "开头王宫（大宅）供品结尾")

    def test_failed_revision_leaves_no_partial_translation(self) -> None:
        seeds.seed_term(
            self.registry,
            term_id="TERM-002",
            original_form="ḥtp",
            transliteration="hetep",
            semantic_range="祭品、安息",
            provenance="《萨卡拉铭文集》",
            dating_context="古王国时期",
        )
        old2 = seeds.adopt_candidate(self.registry, term_id="TERM-002", text="祭品", request_id="REQ-3")
        bad = self.registry.terms.propose_candidate(
            "TERM-002", "安息", audience="public", proposed_by=seeds.TRANSLATOR
        )  # 未审定，prepare 即应拒绝
        seeds.seed_passage(
            self.registry,
            "P-MULTI",
            [("TERM-001", self.old.candidate_id), ("TERM-002", old2.candidate_id)],
        )
        count = len(self.registry.store.events)
        with self.assertRaisesRegex(ValueError, "尚未审定采用"):
            self.registry.revisions.prepare(
                {"TERM-001": self.new.candidate_id, "TERM-002": bad.candidate_id},
                reason="译名统一",
                prepared_by=seeds.COORDINATOR,
            )
        self.assertEqual(len(self.registry.store.events), count)
        self.assertEqual(self.registry.query.render_current("P-MULTI"), "开头王宫祭品结尾")

    def test_interrupted_apply_resumes_without_duplicates(self) -> None:
        seeds.seed_passage(self.registry, "P-A", [("TERM-001", self.old.candidate_id)])
        seeds.seed_passage(self.registry, "P-B", [("TERM-001", self.old.candidate_id)])
        revision_id = self._prepare_signed({"TERM-001": self.new.candidate_id})
        original_append = self.registry.store.append

        def flaky_append(event_type, aggregate_type, aggregate_id, summary, payload=None, occurred_at=None):
            if event_type == "PASSAGE_MIGRATED" and aggregate_id == "P-B":
                raise RuntimeError("模拟崩溃")
            return original_append(event_type, aggregate_type, aggregate_id, summary, payload=payload, occurred_at=occurred_at)

        self.registry.store.append = flaky_append
        with self.assertRaises(RuntimeError):
            self.registry.revisions.apply(revision_id)
        self.registry.store.append = original_append
        self.assertEqual(self.registry.passages.get("P-A").current.version, 2)
        self.assertEqual(self.registry.passages.get("P-B").current.version, 1)
        result = self.registry.revisions.apply(revision_id)
        self.assertEqual(result["migrated"], ["P-B"])
        self.assertEqual(self.registry.passages.get("P-B").current.version, 2)
        migrations = [
            event
            for event in self.registry.store.events
            if event["event_type"] == "PASSAGE_MIGRATED" and event["aggregate_id"] == "P-A"
        ]
        self.assertEqual(len(migrations), 1)

    def test_apply_is_idempotent(self) -> None:
        seeds.seed_passage(self.registry, "P-DRAFT", [("TERM-001", self.old.candidate_id)])
        revision_id = self._prepare_signed({"TERM-001": self.new.candidate_id})
        result = self.registry.revisions.apply(revision_id)
        count = len(self.registry.store.events)
        again = self.registry.revisions.apply(revision_id)
        self.assertEqual(again, result)
        self.assertEqual(len(self.registry.store.events), count)

    def test_emergency_revision_still_requires_signatures(self) -> None:
        seeds.seed_passage(self.registry, "P-PRINT", [("TERM-001", self.old.candidate_id)], state="printed")
        revision_id = self.registry.revisions.prepare(
            {"TERM-001": self.new.candidate_id}, reason="紧急纠错", prepared_by=seeds.COORDINATOR, emergency=True
        )
        with self.assertRaisesRegex(ValueError, "会签"):
            self.registry.revisions.apply(revision_id)

    def test_cosign_requires_two_distinct_signers(self) -> None:
        revision_id = self.registry.revisions.prepare(
            {"TERM-001": self.new.candidate_id}, reason="译名统一", prepared_by=seeds.COORDINATOR
        )
        self.registry.revisions.sign(revision_id, signer=seeds.EGYPTOLOGIST, role="egyptologist")
        with self.assertRaisesRegex(ValueError, "不同人员"):
            self.registry.revisions.sign(revision_id, signer=seeds.EGYPTOLOGIST, role="curator")

    def test_recovery_continues_pending_and_skips_issued_errata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "events.jsonl")
            registry = Registry(store_path)
            seeds.seed_term(registry)
            old = seeds.adopt_candidate(registry, text="王宫", request_id="REQ-1")
            new = seeds.adopt_candidate(registry, text="王宫（大宅）", request_id="REQ-2")
            seeds.seed_term(
                registry,
                term_id="TERM-002",
                original_form="ḥtp",
                transliteration="hetep",
                semantic_range="祭品、安息",
                provenance="《萨卡拉铭文集》",
                dating_context="古王国时期",
            )
            old2 = seeds.adopt_candidate(registry, term_id="TERM-002", text="祭品", request_id="REQ-3")
            new2 = seeds.adopt_candidate(registry, term_id="TERM-002", text="供品", request_id="REQ-4")
            seeds.seed_passage(registry, "P-PRINT", [("TERM-001", old.candidate_id)], state="printed")
            seeds.seed_passage(
                registry, "P-DRAFT", [("TERM-001", old.candidate_id), ("TERM-002", old2.candidate_id)]
            )
            applied_id = registry.revisions.prepare(
                {"TERM-001": new.candidate_id}, reason="译名统一", prepared_by=seeds.COORDINATOR
            )
            registry.revisions.sign(applied_id, signer=seeds.EGYPTOLOGIST, role="egyptologist")
            registry.revisions.sign(applied_id, signer=seeds.CURATOR, role="curator")
            registry.revisions.apply(applied_id)
            pending_id = registry.revisions.prepare(
                {"TERM-002": new2.candidate_id}, reason="补充修订", prepared_by=seeds.COORDINATOR
            )
            registry.revisions.sign(pending_id, signer=seeds.EGYPTOLOGIST, role="egyptologist")
            # 服务恢复：重放日志，待会签修订保留，已发勘误不重复
            reopened = Registry(store_path)
            report = reopened.recover()
            self.assertEqual(report["applied"], [])
            self.assertEqual(report["awaiting_signature"], [pending_id])
            errata_events = [event for event in reopened.store.events if event["event_type"] == "ERRATA_ISSUED"]
            self.assertEqual(len(errata_events), 1)
            # 齐签后继续：迁移补做，勘误仍不重复
            reopened.revisions.sign(pending_id, signer=seeds.CURATOR, role="curator")
            report = reopened.recover()
            self.assertEqual(report["applied"], [pending_id])
            self.assertEqual(reopened.passages.get("P-DRAFT").current.version, 3)
            self.assertEqual(reopened.query.render_current("P-DRAFT"), "开头王宫（大宅）供品结尾")
            errata_events = [event for event in reopened.store.events if event["event_type"] == "ERRATA_ISSUED"]
            self.assertEqual(len(errata_events), 1)


if __name__ == "__main__":
    unittest.main()
