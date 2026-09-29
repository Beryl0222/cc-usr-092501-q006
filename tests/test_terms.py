from __future__ import annotations

import unittest

import seeds
from src.registry import Registry


class TermRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = seeds.make_registry()

    def test_register_term_records_full_source_context(self) -> None:
        term = seeds.seed_term(self.registry)
        self.assertEqual(term.original_form, "pr-ˁḥ")
        self.assertEqual(term.transliteration, "per-aa")
        self.assertIn("王宫", term.semantic_range)
        self.assertEqual(term.provenance, "《都灵王表》第三卷")
        self.assertIn("新王国", term.dating_context)
        self.assertIn("木乃伊", term.forbidden)

    def test_register_term_requires_core_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "缺少字段"):
            self.registry.terms.register_term(
                "TERM-X",
                original_form="",
                transliteration="x",
                semantic_range="x",
                provenance="x",
                dating_context="x",
            )

    def test_duplicate_term_rejected(self) -> None:
        seeds.seed_term(self.registry)
        with self.assertRaisesRegex(ValueError, "已登记"):
            seeds.seed_term(self.registry)

    def test_forbidden_mistranslation_rejected(self) -> None:
        seeds.seed_term(self.registry)
        with self.assertRaisesRegex(ValueError, "禁用误译"):
            self.registry.terms.propose_candidate("TERM-001", "木乃伊", audience="public", proposed_by=seeds.TRANSLATOR)

    def test_unknown_audience_rejected(self) -> None:
        seeds.seed_term(self.registry)
        with self.assertRaisesRegex(ValueError, "受众层级"):
            self.registry.terms.propose_candidate("TERM-001", "王宫", audience="unknown", proposed_by=seeds.TRANSLATOR)

    def test_candidate_ids_are_sequential_per_term(self) -> None:
        seeds.seed_term(self.registry)
        first = self.registry.terms.propose_candidate("TERM-001", "王宫", audience="public", proposed_by=seeds.TRANSLATOR)
        second = self.registry.terms.propose_candidate("TERM-001", "大宅", audience="public", proposed_by=seeds.TRANSLATOR)
        self.assertEqual(first.candidate_id, "CAND-TERM-001-1")
        self.assertEqual(second.candidate_id, "CAND-TERM-001-2")


if __name__ == "__main__":
    unittest.main()
