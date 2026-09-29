from __future__ import annotations

import unittest

import seeds


class PassageRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = seeds.make_registry()
        seeds.seed_term(self.registry)
        self.candidate = seeds.adopt_candidate(self.registry)

    def _segments(self, candidate_id: str | None = None) -> list[dict]:
        return [
            {"kind": "text", "text": "此件出自"},
            {"kind": "term", "term_id": "TERM-001", "candidate_id": candidate_id or self.candidate.candidate_id},
            {"kind": "text", "text": "遗址。"},
        ]

    def test_register_and_render(self) -> None:
        self.registry.passages.register("P-1", carrier="label", audience="public", segments=self._segments(), author="撰稿人")
        passage = self.registry.passages.get("P-1")
        self.assertEqual(passage.state, "draft")
        self.assertEqual(self.registry.query.render_current("P-1"), "此件出自王宫遗址。")

    def test_unadopted_candidate_rejected(self) -> None:
        pending = self.registry.terms.propose_candidate("TERM-001", "大宅", audience="public", proposed_by=seeds.TRANSLATOR)
        with self.assertRaisesRegex(ValueError, "尚未审定采用"):
            self.registry.passages.register("P-2", carrier="label", audience="public", segments=self._segments(pending.candidate_id), author="撰稿人")

    def test_audience_mismatch_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "受众层级"):
            self.registry.passages.register("P-3", carrier="label", audience="children", segments=self._segments(), author="撰稿人")

    def test_state_transitions(self) -> None:
        self.registry.passages.register("P-4", carrier="label", audience="public", segments=self._segments(), author="撰稿人")
        with self.assertRaisesRegex(ValueError, "不可流转"):
            self.registry.passages.change_state("P-4", "printed")
        self.registry.passages.change_state("P-4", "published")
        self.registry.passages.change_state("P-4", "printed")
        self.assertEqual(self.registry.passages.get("P-4").state, "printed")
        with self.assertRaisesRegex(ValueError, "不可流转"):
            self.registry.passages.change_state("P-4", "draft")

    def test_taught_only_for_oral_carriers(self) -> None:
        self.registry.passages.register("P-5", carrier="label", audience="public", segments=self._segments(), author="撰稿人")
        self.registry.passages.change_state("P-5", "published")
        with self.assertRaisesRegex(ValueError, "载体"):
            self.registry.passages.change_state("P-5", "taught")

    def test_citations_list_passage_versions(self) -> None:
        self.registry.passages.register("P-6", carrier="catalog", audience="public", segments=self._segments(), author="撰稿人")
        self.assertEqual(self.registry.passages.citations("TERM-001"), [("P-6", 1)])


if __name__ == "__main__":
    unittest.main()
