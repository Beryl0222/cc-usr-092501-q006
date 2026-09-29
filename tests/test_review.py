from __future__ import annotations

import unittest

import seeds


class ReviewBoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = seeds.make_registry()
        seeds.seed_term(self.registry)
        self.candidate = self.registry.terms.propose_candidate(
            "TERM-001", "王宫", audience="public", proposed_by=seeds.TRANSLATOR, source_ref="《词典》"
        )

    def _submit(self, request_id: str = "REQ-1"):
        return self.registry.reviews.submit(request_id, self.candidate.candidate_id, submitted_by=seeds.COORDINATOR)

    def test_bilateral_adoption_records_both_opinions(self) -> None:
        self._submit()
        self.registry.reviews.confirm_semantics("REQ-1", scholar=seeds.EGYPTOLOGIST, opinion="语义无误")
        record = self.registry.reviews.judge_expression("REQ-1", curator=seeds.CURATOR, opinion="表达妥当")
        self.assertEqual(record.status, "adopted")
        self.assertEqual(record.egypt_review["opinion"], "语义无误")
        self.assertEqual(record.curator_review["opinion"], "表达妥当")
        self.assertEqual(self.registry.terms.get_candidate(self.candidate.candidate_id).status, "adopted")
        final = [
            event
            for event in self.registry.store.events
            if event["event_type"] == "BILATERAL_REVIEWED" and event["payload"]["stage"] == "adopted"
        ]
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]["payload"]["egypt_review"]["reviewer"], seeds.EGYPTOLOGIST)
        self.assertEqual(final[0]["payload"]["curator_review"]["reviewer"], seeds.CURATOR)

    def test_rejection_when_either_side_disapproves(self) -> None:
        self._submit()
        self.registry.reviews.confirm_semantics("REQ-1", scholar=seeds.EGYPTOLOGIST, opinion="语义有误", approve=False)
        record = self.registry.reviews.judge_expression("REQ-1", curator=seeds.CURATOR, opinion="表达妥当")
        self.assertEqual(record.status, "rejected")
        self.assertEqual(self.registry.terms.get_candidate(self.candidate.candidate_id).status, "rejected")

    def test_proposer_cannot_review_own_candidate(self) -> None:
        self._submit()
        with self.assertRaisesRegex(ValueError, "提交和终审"):
            self.registry.reviews.confirm_semantics("REQ-1", scholar=seeds.TRANSLATOR, opinion="自审")

    def test_submitter_cannot_review(self) -> None:
        self._submit()
        with self.assertRaisesRegex(ValueError, "提交和终审"):
            self.registry.reviews.judge_expression("REQ-1", curator=seeds.COORDINATOR, opinion="自审")

    def test_two_sides_must_be_different_people(self) -> None:
        self._submit()
        self.registry.reviews.confirm_semantics("REQ-1", scholar=seeds.EGYPTOLOGIST, opinion="语义无误")
        with self.assertRaisesRegex(ValueError, "不同人员"):
            self.registry.reviews.judge_expression("REQ-1", curator=seeds.EGYPTOLOGIST, opinion="兼任")

    def test_duplicate_submission_returns_existing_decision(self) -> None:
        self._submit()
        self.registry.reviews.confirm_semantics("REQ-1", scholar=seeds.EGYPTOLOGIST, opinion="语义无误")
        self.registry.reviews.judge_expression("REQ-1", curator=seeds.CURATOR, opinion="表达妥当")
        count = len(self.registry.store.events)
        again = self._submit()
        self.assertEqual(again.status, "adopted")
        self.assertEqual(len(self.registry.store.events), count)

    def test_conflicting_candidate_enters_dispute(self) -> None:
        self._submit()
        other = self.registry.terms.propose_candidate(
            "TERM-001", "大宅", audience="public", proposed_by=seeds.TRANSLATOR, source_ref="《词典》"
        )
        record = self.registry.reviews.submit("REQ-1", other.candidate_id, submitted_by=seeds.COORDINATOR)
        self.assertEqual(record.status, "disputed")
        with self.assertRaisesRegex(ValueError, "争议"):
            self.registry.reviews.confirm_semantics("REQ-1", scholar=seeds.EGYPTOLOGIST, opinion="语义无误")

    def test_changed_source_ref_enters_dispute(self) -> None:
        self._submit()
        record = self.registry.reviews.submit(
            "REQ-1", self.candidate.candidate_id, source_ref="《另一出处》", submitted_by=seeds.COORDINATOR
        )
        self.assertEqual(record.status, "disputed")

    def test_review_before_submit_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.registry.reviews.confirm_semantics("REQ-X", scholar=seeds.EGYPTOLOGIST, opinion="语义无误")


if __name__ == "__main__":
    unittest.main()
