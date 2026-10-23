"""`nightshift run` is the one command an agent should have to remember."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from nightshift import core
from nightshift.cli import main

PY = sys.executable


def call(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(list(argv))
    return code, out.getvalue()


def ok_cmd() -> list[str]:
    return [PY, "-c", "raise SystemExit(0)"]


def fail_cmd(code: int = 3) -> list[str]:
    return [PY, "-c", f"raise SystemExit({code})"]


class TestRun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = str(Path(self._tmp.name))
        call("--root", self.root, "init", "builder", "reviewer")
        call("--root", self.root, "send", "--from", "reviewer", "--to", "builder", "--subject", "b1")

    def tearDown(self):
        self._tmp.cleanup()

    def task_id(self) -> str:
        folder = Path(self.root) / "agents" / "builder" / "pending"
        return sorted(p.name for p in folder.glob("*.json"))[0][: -len(".json")]

    def counts(self) -> dict:
        store = core.Store(self.root)
        return {
            box: len(core.list_tasks(store, "builder", box))
            for box in core.BOXES
        }

    def test_passing_command_closes_the_task(self):
        code, out = call("--root", self.root, "run", "--who", "builder", "--", *ok_cmd())
        self.assertEqual(code, 0)
        self.assertIn("closed", out)
        self.assertEqual(self.counts(), {"pending": 0, "processing": 0, "done": 1})

        verify = [r for r in core.ledger_records(core.Store(self.root)) if r["event"] == "verify"]
        self.assertEqual(verify[-1]["rc"], 0)

    def test_failing_command_puts_the_task_back(self):
        code, out = call("--root", self.root, "run", "--who", "builder", "--", *fail_cmd(3))
        self.assertEqual(code, 3)
        self.assertIn("released", out)
        self.assertEqual(self.counts(), {"pending": 1, "processing": 0, "done": 0})

        task = core.check(core.Store(self.root), "builder")[0]
        self.assertEqual(task["tries"], 1)
        self.assertIsNone(task["_lease"])

    def test_retries_stop_at_max_tries(self):
        code, out = call("--root", self.root, "run", "--who", "builder", "--max-tries", "2", "--", *fail_cmd(1))
        self.assertEqual(code, 1)
        self.assertIn("released", out)  # try 1: goes back for another attempt

        code, out = call("--root", self.root, "run", "--who", "builder", "--max-tries", "2", "--", *fail_cmd(1))
        self.assertEqual(code, 1)
        self.assertIn("left", out)  # try 2: stop, a human has to look
        self.assertEqual(self.counts(), {"pending": 0, "processing": 1, "done": 0})

        suspect = [r for r in core.ledger_records(core.Store(self.root)) if r["event"] == "suspect"]
        self.assertEqual(suspect[-1]["tries"], 2)

    def test_without_id_it_takes_the_oldest_task(self):
        first = self.task_id()
        call("--root", self.root, "send", "--from", "reviewer", "--to", "builder", "--subject", "b2")
        code, out = call("--root", self.root, "run", "--who", "builder", "--", *ok_cmd())
        self.assertEqual(code, 0)
        self.assertIn(first, out)

    def test_no_pending_task_is_exit_code_2(self):
        call("--root", self.root, "run", "--who", "builder", "--", *ok_cmd())
        code, _ = call("--root", self.root, "run", "--who", "builder", "--", *ok_cmd())
        self.assertEqual(code, core.EXIT_NOT_FOUND)

    def test_empty_command_is_rejected(self):
        code, _ = call("--root", self.root, "run", "--who", "builder")
        self.assertEqual(code, core.EXIT_ERROR)


if __name__ == "__main__":
    unittest.main()
