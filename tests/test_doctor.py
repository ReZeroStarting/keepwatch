"""doctor, checkpoint and the pipeline gate."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from keepwatch import core
from keepwatch.cli import main


def call(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(list(argv))
    return code, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = core.Store(self.root).init(["builder", "reviewer"])
        self.arg_root = str(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def hand_over(self, subject: str = "work") -> dict:
        return core.send(self.store, "reviewer", "builder", subject, "body")


class TestDoctor(Base):
    def test_clean_root_is_healthy(self):
        report = core.doctor(self.store)
        self.assertTrue(report["ok"])
        self.assertTrue(all(c["status"] != "fail" for c in report["checks"]))

    def test_cli_exits_zero_when_healthy(self):
        code, out = call("--root", self.arg_root, "doctor")
        self.assertEqual(code, 0)
        self.assertIn("healthy", out)

    def test_corrupt_task_file_is_a_failure(self):
        self.hand_over()
        bad = self.store.box("builder", core.PENDING) / "broken.json"
        bad.write_text("{not json", encoding="utf-8")

        report = core.doctor(self.store)
        self.assertFalse(report["ok"])
        tasks = [c for c in report["checks"] if c["check"] == "tasks"][0]
        self.assertEqual(tasks["status"], "fail")

        code, out = call("--root", self.arg_root, "doctor")
        self.assertEqual(code, 1)
        self.assertIn("PROBLEMS FOUND", out)

    def test_same_id_in_two_boxes_is_a_failure(self):
        task = self.hand_over()
        source = self.store.task_path("builder", core.PENDING, task["id"])
        twin = self.store.task_path("builder", core.DONE, task["id"])
        twin.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        report = core.doctor(self.store)
        self.assertFalse(report["ok"])

    def test_orphan_in_processing_is_a_warning_not_a_failure(self):
        task = self.hand_over()
        path = self.store.task_path("builder", core.PROCESSING, task["id"])
        path.write_text(json.dumps({**task, "_lease": None}), encoding="utf-8")
        self.store.task_path("builder", core.PENDING, task["id"]).unlink()

        report = core.doctor(self.store)
        self.assertTrue(report["ok"])  # warn only
        orphans = [c for c in report["checks"] if c["check"] == "orphans"][0]
        self.assertEqual(orphans["status"], "warn")

    def test_expired_lease_is_a_warning(self):
        task = self.hand_over()
        core.claim(self.store, "builder", task["id"], ttl=0)
        report = core.doctor(self.store)
        self.assertTrue(report["ok"])
        leases = [c for c in report["checks"] if c["check"] == "leases"][0]
        self.assertEqual(leases["status"], "warn")

    def test_stale_claim_token_is_a_warning(self):
        import os
        import time as clock

        token = self.store.box("builder", core.PROCESSING) / ".20260101T000000-abcdef.claim"
        token.write_text("123", encoding="utf-8")
        old = clock.time() - 60
        os.utime(token, (old, old))
        report = core.doctor(self.store)
        checks = [c for c in report["checks"] if c["check"] == "tokens"][0]
        self.assertEqual(checks["status"], "warn")

    def test_missing_layout_is_a_failure(self):
        report = core.doctor(core.Store(self.root / "nowhere"))
        self.assertFalse(report["ok"])


class TestPipelineGate(Base):
    def test_gate_is_open_by_default(self):
        self.assertTrue(core.pipeline_enabled(self.store))

    def test_closing_the_gate_blocks_new_work(self):
        core.set_pipeline(self.store, False, "night stop")
        self.assertFalse(core.pipeline_enabled(self.store))

        with self.assertRaises(core.Stopped):
            self.hand_over()

        task = {"id": "x"}
        with self.assertRaises(core.Stopped):
            core.claim(self.store, "builder", task["id"])

    def test_cli_returns_14_when_stopped(self):
        call("--root", self.arg_root, "pipeline", "--off", "--note", "maintenance")
        code, _ = call("--root", self.arg_root, "send", "--from", "reviewer", "--to", "builder", "--subject", "x")
        self.assertEqual(code, core.EXIT_STOPPED)

    def test_reopening_lets_work_through(self):
        call("--root", self.arg_root, "pipeline", "--off")
        call("--root", self.arg_root, "pipeline", "--on")
        code, _ = call("--root", self.arg_root, "send", "--from", "reviewer", "--to", "builder", "--subject", "x")
        self.assertEqual(code, 0)

    def test_status_reports_the_gate(self):
        core.set_pipeline(self.store, False)
        self.assertFalse(core.status(self.store)["pipeline_enabled"])


class TestCheckpoint(Base):
    def test_writes_a_resume_note(self):
        task = self.hand_over("batch 3: login page")
        path = core.checkpoint(self.store)
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("checkpoint", text)
        self.assertIn("resume", text)
        self.assertIn(task["id"], text)
        self.assertIn("builder", text)

    def test_mentions_a_stuck_task(self):
        task = self.hand_over()
        core.claim(self.store, "builder", task["id"], ttl=0)
        text = core.checkpoint(self.store).read_text(encoding="utf-8")
        self.assertIn("EXPIRED", text)

    def test_cli_writes_to_a_chosen_path(self):
        out = self.root / "resume.md"
        code, _ = call("--root", self.arg_root, "checkpoint", "--out", str(out))
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
