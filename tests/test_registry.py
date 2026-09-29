from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

from src.envelope import validate_event
from src.registry import Registry, RegistryError
from src.registry_cli import main as cli_main


class Clock:
    def __init__(self, start: str = "2026-09-20T09:00:00+08:00"):
        self.moment = datetime.fromisoformat(start)

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **kwargs) -> None:
        self.moment += timedelta(**kwargs)


class RegistryCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.log = str(Path(self.dir.name) / "registry.jsonl")
        self.clock = Clock()

    def tearDown(self) -> None:
        self.dir.cleanup()

    def new_registry(self) -> Registry:
        return Registry(self.log, now=self.clock)

    def register_term(self, reg: Registry, term_id: str = "TERM-RA", forbidden=None) -> None:
        reg.register_term(
            term_id=term_id,
            lemma="rꜥ",
            transliteration="Ra",
            semantic_range="太阳神名，亦指太阳本身",
            provenance="《亡灵书》第15章；卢克索神庙铭文",
            dating_context="新王国时期（约前1550—前1070）",
            forbidden=forbidden if forbidden is not None else {"拉神灯": "混淆神名与器物"},
            registered_by="translator:li",
        )

    def adopt(
        self,
        reg: Registry,
        *,
        candidate_id: str,
        term_id: str,
        text: str,
        audience: str,
        request_id: str,
        submitter: str = "editor:zhao",
    ) -> None:
        reg.propose_candidate(
            candidate_id=candidate_id,
            term_id=term_id,
            text=text,
            audience=audience,
            proposed_by="translator:li",
        )
        reg.open_review(request_id=request_id, candidate_id=candidate_id, submitted_by=submitter)
        reg.record_opinion(
            request_id=request_id,
            role="egyptian_semantics",
            reviewer="egyptology:farouk",
            verdict="confirm",
            rationale="原语语义确认",
        )
        reg.record_opinion(
            request_id=request_id,
            role="chinese_expression",
            reviewer="curator:wang",
            verdict="confirm",
            rationale="本地表达妥当",
        )


class ReviewFlowTest(RegistryCase):
    def test_bilateral_adoption_and_effective_rendering(self) -> None:
        reg = self.new_registry()
        self.register_term(reg)
        self.adopt(
            reg,
            candidate_id="CAND-1",
            term_id="TERM-RA",
            text="拉神",
            audience="public_adult",
            request_id="REQ-1",
        )
        review = reg.reviews["REQ-1"]
        self.assertEqual(review.status, "adopted")
        self.assertEqual(len(review.opinions), 2)  # 采用决定留下两方意见
        self.assertEqual(
            {opinion.role for opinion in review.opinions},
            {"egyptian_semantics", "chinese_expression"},
        )
        self.clock.advance(days=1)
        found = reg.effective_rendering(
            term_id="TERM-RA", audience="public_adult", at=self.clock.moment.isoformat()
        )
        self.assertEqual(found["text"], "拉神")
        before = reg.effective_rendering(
            term_id="TERM-RA", audience="public_adult", at="2026-09-19T00:00:00+08:00"
        )
        self.assertIsNone(before)  # 决定生效前没有有效表述
        other_audience = reg.effective_rendering(
            term_id="TERM-RA", audience="child", at=self.clock.moment.isoformat()
        )
        self.assertIsNone(other_audience)

    def test_forbidden_rendering_rejected(self) -> None:
        reg = self.new_registry()
        self.register_term(reg)
        with self.assertRaises(RegistryError):
            reg.propose_candidate(
                candidate_id="CAND-X",
                term_id="TERM-RA",
                text="拉神灯",
                audience="public_adult",
                proposed_by="translator:li",
            )

    def test_separation_of_duties(self) -> None:
        reg = self.new_registry()
        self.register_term(reg)
        reg.propose_candidate(
            candidate_id="CAND-1",
            term_id="TERM-RA",
            text="拉神",
            audience="public_adult",
            proposed_by="translator:li",
        )
        reg.open_review(request_id="REQ-1", candidate_id="CAND-1", submitted_by="editor:zhao")
        reg.record_opinion(
            request_id="REQ-1",
            role="egyptian_semantics",
            reviewer="egyptology:farouk",
            verdict="confirm",
            rationale="原语语义确认",
        )
        # 提交人不得独自完成提交和终审
        with self.assertRaises(RegistryError):
            reg.record_opinion(
                request_id="REQ-1",
                role="chinese_expression",
                reviewer="editor:zhao",
                verdict="confirm",
                rationale="本地表达妥当",
            )
        # 两方意见须由不同人员签署
        with self.assertRaises(RegistryError):
            reg.record_opinion(
                request_id="REQ-1",
                role="chinese_expression",
                reviewer="egyptology:farouk",
                verdict="confirm",
                rationale="本地表达妥当",
            )
        # 必须签署责任说明
        with self.assertRaises(RegistryError):
            reg.record_opinion(
                request_id="REQ-1",
                role="chinese_expression",
                reviewer="curator:wang",
                verdict="confirm",
                rationale="",
            )
        reg.record_opinion(
            request_id="REQ-1",
            role="chinese_expression",
            reviewer="curator:wang",
            verdict="confirm",
            rationale="本地表达妥当",
        )
        self.assertEqual(reg.reviews["REQ-1"].status, "adopted")

    def test_duplicate_request_returns_existing_decision(self) -> None:
        reg = self.new_registry()
        self.register_term(reg)
        self.adopt(
            reg,
            candidate_id="CAND-1",
            term_id="TERM-RA",
            text="拉神",
            audience="public_adult",
            request_id="REQ-1",
        )
        event_count = len(reg.events)
        review, created = reg.open_review(
            request_id="REQ-1", candidate_id="CAND-1", submitted_by="editor:zhao"
        )
        self.assertFalse(created)  # 相同请求重复到达，返回既有决定
        self.assertEqual(review.status, "adopted")
        self.assertEqual(len(reg.events), event_count)  # 不落新事件

    def test_conflicting_request_enters_dispute(self) -> None:
        reg = self.new_registry()
        self.register_term(reg)
        reg.propose_candidate(
            candidate_id="CAND-1",
            term_id="TERM-RA",
            text="拉神",
            audience="public_adult",
            proposed_by="translator:li",
        )
        reg.propose_candidate(
            candidate_id="CAND-2",
            term_id="TERM-RA",
            text="太阳神拉",
            audience="public_adult",
            proposed_by="translator:li",
        )
        reg.open_review(request_id="REQ-1", candidate_id="CAND-1", submitted_by="editor:zhao")
        # 编号相同但候选变化 → 争议
        with self.assertRaises(RegistryError):
            reg.open_review(request_id="REQ-1", candidate_id="CAND-2", submitted_by="editor:zhao")
        self.assertEqual(reg.reviews["REQ-1"].status, "disputed")
        with self.assertRaises(RegistryError):
            reg.record_opinion(
                request_id="REQ-1",
                role="egyptian_semantics",
                reviewer="egyptology:farouk",
                verdict="confirm",
                rationale="争议中不得签署",
            )
        # 编号相同但出处变化 → 同样进入争议
        reg.open_review(request_id="REQ-2", candidate_id="CAND-1", submitted_by="editor:zhao")
        with self.assertRaises(RegistryError):
            reg.open_review(
                request_id="REQ-2",
                candidate_id="CAND-1",
                submitted_by="editor:zhao",
                provenance="另一处铭文",
            )
        self.assertEqual(reg.reviews["REQ-2"].status, "disputed")

    def test_urgent_review_cannot_skip_signatures(self) -> None:
        reg = self.new_registry()
        self.register_term(reg)
        reg.propose_candidate(
            candidate_id="CAND-1",
            term_id="TERM-RA",
            text="拉神",
            audience="public_adult",
            proposed_by="translator:li",
        )
        reg.open_review(
            request_id="REQ-URGENT", candidate_id="CAND-1", submitted_by="editor:zhao", urgent=True
        )
        reg.record_opinion(
            request_id="REQ-URGENT",
            role="egyptian_semantics",
            reviewer="egyptology:farouk",
            verdict="confirm",
            rationale="原语语义确认",
        )
        review = reg.reviews["REQ-URGENT"]
        self.assertEqual(review.status, "pending_counter")  # 紧急纠错也不能跳过会签
        self.assertIsNone(
            reg.effective_rendering(
                term_id="TERM-RA", audience="public_adult", at=self.clock.moment.isoformat()
            )
        )


class RevisionTest(RegistryCase):
    def test_atomic_multi_term_migration(self) -> None:
        reg = self.new_registry()
        self.register_term(reg, "TERM-RA")
        self.register_term(reg, "TERM-PTAH")
        self.adopt(
            reg, candidate_id="C-1", term_id="TERM-RA", text="拉神",
            audience="public_adult", request_id="REQ-1",
        )
        self.adopt(
            reg, candidate_id="C-2", term_id="TERM-PTAH", text="普塔神",
            audience="public_adult", request_id="REQ-2",
        )
        reg.register_passage(
            passage_id="LBL-1",
            carrier="label",
            audience="public_adult",
            text_zh="拉神与普塔神并坐于神龛。",
            term_usages=[
                {"term_id": "TERM-RA", "rendering": "拉神"},
                {"term_id": "TERM-PTAH", "rendering": "普塔神"},
            ],
        )
        self.adopt(
            reg, candidate_id="C-3", term_id="TERM-RA", text="太阳神拉",
            audience="public_adult", request_id="REQ-3",
        )
        self.adopt(
            reg, candidate_id="C-4", term_id="TERM-PTAH", text="普塔",
            audience="public_adult", request_id="REQ-4",
        )
        result = reg.apply_revision(
            term_ids=["TERM-RA", "TERM-PTAH"], audience="public_adult", issued_by="editor:zhao"
        )
        self.assertEqual(len(result["migrated"]), 1)
        self.assertEqual(reg.passages["LBL-1"].text_zh, "太阳神拉与普塔并坐于神龛。")
        migrations = [e for e in reg.events if e["event_type"] == "PASSAGE_MIGRATED"]
        self.assertEqual(len(migrations), 1)  # 同一句中的多个术语在一次事务中切换
        self.assertEqual(len(migrations[0]["payload"]["replacements"]), 2)

    def test_failed_migration_leaves_no_partial_text(self) -> None:
        reg = self.new_registry()
        self.register_term(reg, "TERM-RA")
        self.register_term(reg, "TERM-PTAH")
        self.adopt(
            reg, candidate_id="C-1", term_id="TERM-RA", text="拉神",
            audience="public_adult", request_id="REQ-1",
        )
        self.adopt(
            reg, candidate_id="C-2", term_id="TERM-PTAH", text="普塔神",
            audience="public_adult", request_id="REQ-2",
        )
        reg.register_passage(
            passage_id="LBL-1",
            carrier="label",
            audience="public_adult",
            text_zh="拉神与普塔神并坐。",
            term_usages=[
                {"term_id": "TERM-RA", "rendering": "拉神"},
                {"term_id": "TERM-PTAH", "rendering": "普塔神"},
            ],
        )
        self.adopt(
            reg, candidate_id="C-3", term_id="TERM-RA", text="太阳神拉",
            audience="public_adult", request_id="REQ-3",
        )
        self.adopt(
            reg, candidate_id="C-4", term_id="TERM-PTAH", text="普塔",
            audience="public_adult", request_id="REQ-4",
        )
        # 模拟段落文本在系统外被改动，待替换表述缺失
        reg.passages["LBL-1"].text_zh = "拉神与手工改写文本。"
        event_count = len(reg.events)
        result = reg.apply_revision(
            term_ids=["TERM-RA", "TERM-PTAH"], audience="public_adult", issued_by="editor:zhao"
        )
        self.assertEqual(result["migrated"], [])
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(reg.passages["LBL-1"].text_zh, "拉神与手工改写文本。")
        self.assertEqual(len(reg.events), event_count)  # 失败不留半新半旧

    def test_published_passage_gets_errata_only(self) -> None:
        reg = self.new_registry()
        self.register_term(reg)
        self.adopt(
            reg, candidate_id="C-1", term_id="TERM-RA", text="拉神",
            audience="public_adult", request_id="REQ-1",
        )
        reg.register_passage(
            passage_id="CAT-1",
            carrier="catalogue",
            audience="public_adult",
            text_zh="本像表现拉神乘船巡行。",
            term_usages=[{"term_id": "TERM-RA", "rendering": "拉神"}],
        )
        reg.publish_passage(passage_id="CAT-1")
        published_at = reg.passages["CAT-1"].published_at
        self.clock.advance(days=7)
        self.adopt(
            reg, candidate_id="C-2", term_id="TERM-RA", text="太阳神拉",
            audience="public_adult", request_id="REQ-2",
        )
        result = reg.apply_revision(
            term_ids=["TERM-RA"],
            audience="public_adult",
            issued_by="editor:zhao",
            valid_from="2026-10-01T00:00:00+08:00",
            valid_to="2026-12-31T23:59:59+08:00",
        )
        self.assertEqual(result["migrated"], [])  # 已印刷版本不迁移
        self.assertEqual(len(result["errata"]), 1)
        self.assertFalse(result["errata"][0]["deduplicated"])
        self.assertEqual(reg.passages["CAT-1"].text_zh, "本像表现拉神乘船巡行。")  # 原文不动
        # 适用期内呈现新表述，适用期外仍是旧表述
        inside = reg.render_passage(passage_id="CAT-1", at="2026-10-15T00:00:00+08:00")
        self.assertEqual(inside["text"], "本像表现太阳神拉乘船巡行。")
        before = reg.render_passage(passage_id="CAT-1", at="2026-09-29T00:00:00+08:00")
        self.assertEqual(before["text"], "本像表现拉神乘船巡行。")
        after = reg.render_passage(passage_id="CAT-1", at="2027-01-15T00:00:00+08:00")
        self.assertEqual(after["text"], "本像表现拉神乘船巡行。")
        with self.assertRaises(RegistryError):
            reg.render_passage(passage_id="CAT-1", at="2026-09-01T00:00:00+08:00")
        self.assertIsNotNone(published_at)

    def test_errata_not_duplicated_after_recovery(self) -> None:
        reg = self.new_registry()
        self.register_term(reg)
        self.adopt(
            reg, candidate_id="C-1", term_id="TERM-RA", text="拉神",
            audience="public_adult", request_id="REQ-1",
        )
        reg.register_passage(
            passage_id="CAT-1",
            carrier="catalogue",
            audience="public_adult",
            text_zh="本像表现拉神乘船巡行。",
            term_usages=[{"term_id": "TERM-RA", "rendering": "拉神"}],
        )
        reg.publish_passage(passage_id="CAT-1")
        self.adopt(
            reg, candidate_id="C-2", term_id="TERM-RA", text="太阳神拉",
            audience="public_adult", request_id="REQ-2",
        )
        reg.apply_revision(term_ids=["TERM-RA"], audience="public_adult", issued_by="editor:zhao")
        # 服务恢复后重跑同一修订：不重复发出勘误
        reg2 = self.new_registry()
        result = reg2.apply_revision(
            term_ids=["TERM-RA"], audience="public_adult", issued_by="editor:zhao"
        )
        self.assertEqual(len(result["errata"]), 1)
        self.assertTrue(result["errata"][0]["deduplicated"])
        errata_events = [e for e in reg2.events if e["event_type"] == "ERRATA_ISSUED"]
        self.assertEqual(len(errata_events), 1)
        self.assertEqual(len(reg2.errata), 1)


class RecoveryTest(RegistryCase):
    def test_recovery_resumes_pending_review(self) -> None:
        reg = self.new_registry()
        self.register_term(reg)
        reg.propose_candidate(
            candidate_id="CAND-1",
            term_id="TERM-RA",
            text="拉神",
            audience="public_adult",
            proposed_by="translator:li",
        )
        reg.open_review(request_id="REQ-9", candidate_id="CAND-1", submitted_by="editor:zhao")
        reg.record_opinion(
            request_id="REQ-9",
            role="egyptian_semantics",
            reviewer="egyptology:farouk",
            verdict="confirm",
            rationale="原语语义确认",
        )
        # 模拟服务恢复：从同一日志重建，待会签的修订继续推进
        reg2 = self.new_registry()
        pending = reg2.pending_reviews()
        self.assertEqual([item["request_id"] for item in pending], ["REQ-9"])
        self.assertEqual(pending[0]["opinions"], 1)
        reg2.record_opinion(
            request_id="REQ-9",
            role="chinese_expression",
            reviewer="curator:wang",
            verdict="confirm",
            rationale="本地表达妥当",
        )
        self.assertEqual(reg2.reviews["REQ-9"].status, "adopted")
        found = reg2.effective_rendering(
            term_id="TERM-RA", audience="public_adult", at=self.clock.moment.isoformat()
        )
        self.assertEqual(found["text"], "拉神")


class AuditTest(RegistryCase):
    def test_audit_traces_back_to_source_and_history(self) -> None:
        reg = self.new_registry()
        self.register_term(reg)
        self.adopt(
            reg, candidate_id="C-1", term_id="TERM-RA", text="拉神",
            audience="public_adult", request_id="REQ-1",
        )
        reg.register_passage(
            passage_id="LEC-1",
            carrier="lecture",
            audience="public_adult",
            text_zh="拉神乘船巡行天际。",
            term_usages=[{"term_id": "TERM-RA", "rendering": "拉神"}],
        )
        self.adopt(
            reg, candidate_id="C-2", term_id="TERM-RA", text="太阳神拉",
            audience="public_adult", request_id="REQ-2",
        )
        reg.apply_revision(term_ids=["TERM-RA"], audience="public_adult", issued_by="editor:zhao")
        # 从一段中文定位并回溯
        found = reg.find_passages("太阳神拉")
        self.assertEqual(found, ["LEC-1"])
        audit = reg.audit_passage(passage_id="LEC-1")
        usage = audit["usages"][0]
        self.assertEqual(usage["rendering"], "太阳神拉")
        self.assertEqual(usage["source"]["lemma"], "rꜥ")
        self.assertEqual(usage["source"]["transliteration"], "Ra")
        self.assertIn("亡灵书", usage["source"]["provenance"])
        self.assertIn("新王国", usage["source"]["dating_context"])
        self.assertEqual(usage["decision"]["request_id"], "REQ-2")
        self.assertEqual(len(usage["decision"]["opinions"]), 2)
        self.assertEqual(usage["history"][0]["kind"], "migration")
        self.assertEqual(usage["history"][0]["from_text"], "拉神")
        self.assertEqual(usage["history"][0]["to_text"], "太阳神拉")


class ContractTest(RegistryCase):
    def test_emitted_events_conform_to_envelope(self) -> None:
        reg = self.new_registry()
        self.register_term(reg)
        self.adopt(
            reg, candidate_id="C-1", term_id="TERM-RA", text="拉神",
            audience="public_adult", request_id="REQ-1",
        )
        reg.register_passage(
            passage_id="LBL-1",
            carrier="label",
            audience="public_adult",
            text_zh="拉神像。",
            term_usages=[{"term_id": "TERM-RA", "rendering": "拉神"}],
        )
        reg.publish_passage(passage_id="LBL-1")
        self.adopt(
            reg, candidate_id="C-2", term_id="TERM-RA", text="太阳神拉",
            audience="public_adult", request_id="REQ-2",
        )
        reg.apply_revision(term_ids=["TERM-RA"], audience="public_adult", issued_by="editor:zhao")
        for line in Path(self.log).read_text(encoding="utf-8").splitlines():
            self.assertEqual(validate_event(json.loads(line)), [])

    def test_sample_bilateral_review_is_valid(self) -> None:
        sample = json.loads(
            Path("data/sample_bilateral_review.json").read_text(encoding="utf-8")
        )
        self.assertEqual(validate_event(sample), [])


class CliTest(RegistryCase):
    def run_cli(self, *argv: str) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli_main(["--log", self.log, *argv])
        self.assertEqual(code, 0)
        return buffer.getvalue()

    def test_cli_end_to_end(self) -> None:
        self.run_cli(
            "register-term",
            "--term-id", "TERM-RA",
            "--lemma", "rꜥ",
            "--transliteration", "Ra",
            "--semantic-range", "太阳神名，亦指太阳本身",
            "--provenance", "《亡灵书》第15章",
            "--dating-context", "新王国时期",
            "--forbid", "拉神灯=混淆神名与器物",
            "--by", "translator:li",
        )
        self.run_cli(
            "propose",
            "--candidate-id", "CAND-1",
            "--term-id", "TERM-RA",
            "--text", "拉神",
            "--audience", "public_adult",
            "--by", "translator:li",
        )
        self.run_cli(
            "review-open", "--request-id", "REQ-1", "--candidate-id", "CAND-1", "--by", "editor:zhao"
        )
        self.run_cli(
            "review-opinion",
            "--request-id", "REQ-1",
            "--role", "egyptian_semantics",
            "--by", "egyptology:farouk",
            "--verdict", "confirm",
            "--rationale", "原语语义确认",
        )
        out = self.run_cli(
            "review-opinion",
            "--request-id", "REQ-1",
            "--role", "chinese_expression",
            "--by", "curator:wang",
            "--verdict", "confirm",
            "--rationale", "本地表达妥当",
        )
        self.assertIn("adopted", out)
        out = self.run_cli(
            "render",
            "--term-id", "TERM-RA",
            "--audience", "public_adult",
            "--at", "2027-01-01T00:00:00+08:00",
        )
        self.assertIn("拉神", out)
        out = self.run_cli("pending")
        self.assertEqual(json.loads(out), [])


if __name__ == "__main__":
    unittest.main()
