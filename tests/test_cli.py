"""End-to-end CLI tests: the exit codes are part of the contract."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from nightshift import core
from nightshift.cli import main


def call(*argv: str) -> tuple[int, str]:
    """Run the CLI, return (exit code, stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(list(argv))
    return code, out.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = str(Path(self._tmp.name))
        code, _ = call("--root", self.root, "init", "builder", "reviewer")
        self.assertEqual(code, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def _only_pending_id(self) -> str:
        folder = Path(self.root) / "agents" / "builder" / "pending"
        return sorted(p.name for p in folder.glob("*.json"))[0][: -len(".json")]

    def test_full_happy_path(self):
        code, out = call("--root", self.root, "send", "--from", "reviewer",
                         "--to", "builder", "--subject", "batch 1")
        self.assertEqual(code, 0)
        task_id = self._only_pending_id()

        code, out = call("--root", self.root, "check", "--who", "builder")
        self.assertEqual(code, 0)
        self.assertIn(task_id, out)

        self.assertEqual(call("--root", self.root, "claim", "--who", "builder", "--id", task_id)[0], 0)

        # Closing without proof is refused.
        code, _ = call("--root", self.root, "done", "--who", "builder", "--id", task_id)
        self.assertEqual(code, core.EXIT_BLOCKED)

        self.assertEqual(call("--root", self.root, "verify", "--id", task_id, "--name", "e2e", "--rc", "0")[0], 0)
        self.assertEqual(call("--root", self.root, "done", "--who", "builder", "--id", task_id)[0], 0)

        code, out = call("--root", self.root, "--json", "status")
        self.assertEqual(code, 0)
        state = json.loads(out)
        self.assertEqual(state["agents"]["builder"]["done"], 1)

    def test_missing_task_gives_exit_code_2(self):
        code, _ = call("--root", self.root, "claim", "--who", "builder", "--id", "nope")
        self.assertEqual(code, core.EXIT_NOT_FOUND)

    def test_unknown_agent_gives_a_clear_failure(self):
        code, _ = call("--root", self.root, "send", "--from", "reviewer", "--to", "ghost", "--subject", "x")
        self.assertEqual(code, core.EXIT_ERROR)

    def test_reap_returns_a_dead_workers_task(self):
        call("--root", self.root, "send", "--from", "reviewer", "--to", "builder", "--subject", "b1")
        task_id = self._only_pending_id()
        call("--root", self.root, "claim", "--who", "builder", "--id", task_id, "--ttl", "0")
        code, out = call("--root", self.root, "--json", "reap")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["reaped"]), 1)
        code, _ = call("--root", self.root, "check", "--who", "builder")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
