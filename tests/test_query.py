from __future__ import annotations

import unittest

import seeds


class QueryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = seeds.make_registry()
        seeds.seed_term(self.registry)

    def test_effective_term_text_by_audience_and_date(self) -> None:
        seeds.adopt_candidate(self.registry, text="王宫", request_id="REQ-1", occurred_at="2026-09-01T09:00:00+08:00")
        seeds.adopt_candidate(self.registry, text="王宫（大宅）", request_id="REQ-2", occurred_at="2026-09-10T09:00:00+08:00")
        query = self.registry.query
        self.assertEqual(query.effective_term_text("TERM-001", "public", "2026-09-05T00:00:00+08:00"), "王宫")
        self.assertEqual(query.effective_term_text("TERM-001", "public", "2026-09-15T00:00:00+08:00"), "王宫（大宅）")
        with self.assertRaisesRegex(ValueError, "无有效表述"):
            query.effective_term_text("TERM-001", "children", "2026-09-15T00:00:00+08:00")

    def test_effective_passage_reflects_migration_date(self) -> None:
        old = seeds.adopt_candidate(self.registry, text="王宫", request_id="REQ-1")
        new = seeds.adopt_candidate(self.registry, text="王宫（大宅）", request_id="REQ-2")
        seeds.seed_passage(
            self.registry, "P-1", [("TERM-001", old.candidate_id)], occurred_at="2026-09-06T09:00:00+08:00"
        )
        revision_id = self.registry.revisions.prepare(
            {"TERM-001": new.candidate_id}, reason="译名统一", prepared_by=seeds.COORDINATOR
        )
        self.registry.revisions.sign(revision_id, signer=seeds.EGYPTOLOGIST, role="egyptologist")
        self.registry.revisions.sign(revision_id, signer=seeds.CURATOR, role="curator")
        self.registry.revisions.apply(revision_id, occurred_at="2026-09-08T09:00:00+08:00")
        query = self.registry.query
        self.assertEqual(query.effective_passage("P-1", "2026-09-07T00:00:00+08:00")["text"], "开头王宫结尾")
        after = query.effective_passage("P-1", "2026-09-09T00:00:00+08:00")
        self.assertEqual(after["text"], "开头王宫（大宅）结尾")
        self.assertEqual(after["version"], 2)

    def test_printed_passage_uses_errata_window(self) -> None:
        old = seeds.adopt_candidate(self.registry, text="王宫", request_id="REQ-1")
        new = seeds.adopt_candidate(self.registry, text="王宫（大宅）", request_id="REQ-2")
        seeds.seed_passage(self.registry, "P-PRINT", [("TERM-001", old.candidate_id)], state="printed")
        revision_id = self.registry.revisions.prepare(
            {"TERM-001": new.candidate_id},
            reason="译名统一",
            prepared_by=seeds.COORDINATOR,
            valid_from="2026-10-01T00:00:00+08:00",
            valid_until="2026-12-31T23:59:59+08:00",
        )
        self.registry.revisions.sign(revision_id, signer=seeds.EGYPTOLOGIST, role="egyptologist")
        self.registry.revisions.sign(revision_id, signer=seeds.CURATOR, role="curator")
        self.registry.revisions.apply(revision_id, occurred_at="2026-09-15T09:00:00+08:00")
        query = self.registry.query
        before = query.effective_passage("P-PRINT", "2026-09-20T00:00:00+08:00")
        self.assertEqual(before["text"], "开头王宫结尾")
        self.assertEqual(before["errata_applied"], [])
        within = query.effective_passage("P-PRINT", "2026-10-15T00:00:00+08:00")
        self.assertEqual(within["text"], "开头王宫（大宅）结尾")
        self.assertEqual(within["errata_applied"], [f"ERR-{revision_id}-P-PRINT"])
        after = query.effective_passage("P-PRINT", "2027-01-15T00:00:00+08:00")
        self.assertEqual(after["text"], "开头王宫结尾")

    def test_audit_traces_back_to_source_and_decisions(self) -> None:
        old = seeds.adopt_candidate(self.registry, text="王宫", request_id="REQ-1")
        new = seeds.adopt_candidate(self.registry, text="王宫（大宅）", request_id="REQ-2")
        seeds.seed_passage(self.registry, "P-PRINT", [("TERM-001", old.candidate_id)], state="printed")
        revision_id = self.registry.revisions.prepare(
            {"TERM-001": new.candidate_id}, reason="译名统一", prepared_by=seeds.COORDINATOR
        )
        self.registry.revisions.sign(revision_id, signer=seeds.EGYPTOLOGIST, role="egyptologist", opinion="同意")
        self.registry.revisions.sign(revision_id, signer=seeds.CURATOR, role="curator", opinion="同意")
        self.registry.revisions.apply(revision_id)
        report = self.registry.query.audit_passage("P-PRINT")
        self.assertEqual(report["carrier"], "label")
        self.assertEqual(report["state"], "printed")
        self.assertEqual(report["text"], "开头王宫结尾")
        self.assertEqual(len(report["terms"]), 1)
        term = report["terms"][0]
        self.assertEqual(term["original_form"], "pr-ˁḥ")
        self.assertEqual(term["transliteration"], "per-aa")
        self.assertEqual(term["provenance"], "《都灵王表》第三卷")
        self.assertIn("新王国", term["dating_context"])
        self.assertIn("木乃伊", term["forbidden"])
        self.assertEqual(term["candidate"]["text"], "王宫")
        self.assertEqual(term["candidate"]["proposed_by"], seeds.TRANSLATOR)
        review = term["reviews"][0]
        self.assertEqual(review["status"], "adopted")
        self.assertEqual(review["egypt_review"]["reviewer"], seeds.EGYPTOLOGIST)
        self.assertEqual(review["curator_review"]["reviewer"], seeds.CURATOR)
        self.assertEqual([errata["errata_id"] for errata in report["errata"]], [f"ERR-{revision_id}-P-PRINT"])
        self.assertEqual(report["revisions"], [revision_id])


if __name__ == "__main__":
    unittest.main()
