from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import seeds
from src.registry import Registry

ROOT = Path(__file__).parents[1]


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store_path = str(Path(self._tmp.name) / "events.jsonl")
        registry = Registry(self.store_path)
        seeds.seed_term(registry)
        old = seeds.adopt_candidate(registry, text="王宫", request_id="REQ-1")
        new = seeds.adopt_candidate(registry, text="王宫（大宅）", request_id="REQ-2")
        seeds.seed_passage(registry, "P-PRINT", [("TERM-001", old.candidate_id)], state="printed")
        revision_id = registry.revisions.prepare(
            {"TERM-001": new.candidate_id},
            reason="译名统一",
            prepared_by=seeds.COORDINATOR,
            valid_from="2026-10-01T00:00:00+08:00",
        )
        registry.revisions.sign(revision_id, signer=seeds.EGYPTOLOGIST, role="egyptologist", opinion="同意")
        registry.revisions.sign(revision_id, signer=seeds.CURATOR, role="curator", opinion="同意")
        registry.revisions.apply(revision_id)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "src.cli", *args], cwd=ROOT, text=True, capture_output=True
        )

    def test_check_command_accepts_sample(self) -> None:
        result = self.run_cli("check", "data/sample.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("事件有效", result.stdout)

    def test_audit_command_traces_source_and_errata(self) -> None:
        result = self.run_cli("audit", self.store_path, "P-PRINT")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("原文词形：pr-ˁḥ", result.stdout)
        self.assertIn("埃及方", result.stdout)
        self.assertIn("中方", result.stdout)
        self.assertIn("勘误记录", result.stdout)
        self.assertIn("适用期 2026-10-01", result.stdout)

    def test_effective_command_prints_text_for_date(self) -> None:
        before = self.run_cli("effective", self.store_path, "P-PRINT", "--date", "2026-09-20T00:00:00+08:00")
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertEqual(before.stdout.strip(), "开头王宫结尾")
        within = self.run_cli("effective", self.store_path, "P-PRINT", "--date", "2026-10-15T00:00:00+08:00")
        self.assertEqual(within.stdout.strip(), "开头王宫（大宅）结尾")

    def test_resume_command_reports_pending(self) -> None:
        result = self.run_cli("resume", self.store_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("awaiting_signature", result.stdout)


if __name__ == "__main__":
    unittest.main()
