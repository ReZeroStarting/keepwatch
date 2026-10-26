"""Crash injection: kill the process at the worst moment and prove nothing is lost.

The seam is ``KEEPWATCH_CRASH_AT`` — it makes the claimant die between moving the
file into ``processing/`` and writing the lease. That is the only window where an
orphan can exist, so it is the one worth testing for real (a subprocess that dies)
rather than mocking.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from keepwatch import core


class TestCrashInjection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = core.Store(self.root).init(["builder", "reviewer"])
        self.task = core.send(self.store, "reviewer", "builder", "survive a kill")

    def tearDown(self):
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def crash_claim(self) -> subprocess.CompletedProcess:
        env = dict(os.environ, KEEPWATCH_CRASH_AT="after_move")
        return subprocess.run(
            [
                sys.executable, "-m", "keepwatch.cli", "--root", str(self.root),
                "claim", "--who", "builder", "--id", self.task["id"],
            ],
            env=env, capture_output=True, text=True,
        )

    def token_path(self) -> Path:
        return next(self.store.box("builder", core.PROCESSING).glob(".*.claim"))

    def age_token(self, seconds: float) -> None:
        old = time.time() - seconds
        os.utime(self.token_path(), (old, old))

    # -- tests -----------------------------------------------------------
    def test_kill_between_move_and_lease_leaves_a_recoverable_orphan(self):
        proc = self.crash_claim()
        self.assertEqual(proc.returncode, 137, proc.stderr)

        pending = self.store.task_path("builder", core.PENDING, self.task["id"])
        processing = self.store.task_path("builder", core.PROCESSING, self.task["id"])
        self.assertFalse(pending.exists(), "the move happened before the crash")
        self.assertTrue(processing.exists(), "the task still exists exactly once")
        self.assertIsNone(
            json.loads(processing.read_text(encoding="utf-8"))["_lease"],
            "the crash happened before the lease was written",
        )

    def test_doctor_calls_the_orphan_a_warning_not_a_failure(self):
        self.crash_claim()
        report = core.doctor(self.store)
        self.assertTrue(report["ok"], "an orphan is recoverable, so it is not fatal")
        orphans = [c for c in report["checks"] if c["check"] == "orphans"][0]
        self.assertEqual(orphans["status"], "warn")

    def test_the_dead_claimer_token_blocks_recovery_until_it_goes_stale(self):
        self.crash_claim()
        # the token is still fresh: a second claim cannot proceed
        with self.assertRaises(core.Held):
            core.claim(self.store, "builder", self.task["id"])

        self.age_token(core.TOKEN_STALE_SECONDS + 1)
        self.assertEqual(len(core.reap(self.store)), 1)

    def test_after_recovery_the_task_can_be_claimed_again(self):
        self.crash_claim()
        self.age_token(core.TOKEN_STALE_SECONDS + 1)
        core.reap(self.store)

        back = core.check(self.store, "builder")
        self.assertEqual([t["id"] for t in back], [self.task["id"]])
        self.assertEqual(back[0]["tries"], 1, "the killed attempt is still counted")

        again = core.claim(self.store, "builder", self.task["id"], ttl=60)
        self.assertEqual(again["tries"], 2)
        # Nobody is in prev_owners: the crash happened before the lease existed, so
        # there was never a recorded holder. The attempt is still counted in `tries`.
        self.assertEqual(again["prev_owners"], [])
        self.assertEqual(
            list(self.store.box("builder", core.PROCESSING).glob(".*.claim")),
            [],
            "a normal claim cleans its token",
        )

    def test_no_task_is_duplicated_or_lost(self):
        self.crash_claim()
        self.age_token(core.TOKEN_STALE_SECONDS + 1)
        core.reap(self.store)

        for box in core.BOXES:
            ids = [t["id"] for t in core.list_tasks(self.store, "builder", box)]
            self.assertLessEqual(ids.count(self.task["id"]), 1, f"duplicate in {box}")
        total = sum(len(core.list_tasks(self.store, "builder", b)) for b in core.BOXES)
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
