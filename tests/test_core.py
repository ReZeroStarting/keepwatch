"""Behaviour tests for the core. The interesting ones simulate a worker dying."""

import tempfile
import threading
import unittest
from pathlib import Path

from keepwatch import core


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = core.Store(self.root).init(["builder", "reviewer"])

    def tearDown(self):
        self._tmp.cleanup()

    def hand_over(self, subject="do it"):
        return core.send(self.store, "reviewer", "builder", subject, "body")


class TestLayout(Base):
    def test_init_creates_three_boxes_per_agent(self):
        for agent in ("builder", "reviewer"):
            for box in core.BOXES:
                self.assertTrue((self.root / "agents" / agent / box).is_dir())

    def test_bad_agent_id_is_rejected(self):
        with self.assertRaises(core.keepwatchError):
            self.store.agent_dir("Not Valid")

    def test_unknown_root_fails_loudly(self):
        with self.assertRaises(core.keepwatchError):
            core.status(core.Store(self.root / "nope"))


class TestTaskFlow(Base):
    def test_send_lands_in_pending_and_check_sees_it(self):
        task = self.hand_over()
        self.assertTrue((self.root / "agents" / "builder" / "pending" / f"{task['id']}.json").exists())
        tasks = core.check(self.store, "builder")
        self.assertEqual([t["id"] for t in tasks], [task["id"]])
        self.assertEqual(tasks[0]["subject"], "do it")

    def test_send_to_unknown_agent_raises(self):
        with self.assertRaises(core.keepwatchError):
            core.send(self.store, "reviewer", "ghost", "x")

    def test_claim_moves_task_and_starts_one_try(self):
        task = self.hand_over()
        claimed = core.claim(self.store, "builder", task["id"], ttl=60)
        self.assertEqual(claimed["state"], core.PROCESSING)
        self.assertEqual(claimed["tries"], 1)
        self.assertEqual(claimed["_lease"]["owner"], "builder")
        self.assertEqual(core.check(self.store, "builder"), [])

    def test_second_claim_is_refused_while_lease_is_alive(self):
        task = self.hand_over()
        core.claim(self.store, "builder", task["id"], ttl=60)
        with self.assertRaises(core.Held):
            core.claim(self.store, "builder", task["id"], ttl=60)

    def test_claim_of_missing_task_is_not_found(self):
        with self.assertRaises(core.NotFound):
            core.claim(self.store, "builder", "does-not-exist")

    def test_renew_extends_the_lease(self):
        task = self.hand_over()
        first = core.claim(self.store, "builder", task["id"], ttl=60)
        later = core.renew(self.store, "builder", task["id"], ttl=600)
        self.assertGreater(
            core.parse_stamp(later["_lease"]["expires_at"]),
            core.parse_stamp(first["_lease"]["expires_at"]),
        )

    def test_renew_refused_when_you_are_not_the_owner(self):
        task = self.hand_over()
        core.claim(self.store, "builder", task["id"], ttl=60)
        path = self.store.task_path("builder", core.PROCESSING, task["id"])
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_lease"]["owner"] = "someone-else"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(core.Held):
            core.renew(self.store, "builder", task["id"])


class TestDone(Base):
    def test_done_is_refused_without_a_verify_record(self):
        task = self.hand_over()
        core.claim(self.store, "builder", task["id"])
        with self.assertRaises(core.Blocked):
            core.done(self.store, "builder", task["id"])

    def test_a_failing_verify_does_not_unlock_done(self):
        task = self.hand_over()
        core.claim(self.store, "builder", task["id"])
        core.verify(self.store, task["id"], "unit", 1)
        with self.assertRaises(core.Blocked):
            core.done(self.store, "builder", task["id"])

    def test_done_succeeds_after_a_passing_verify(self):
        task = self.hand_over()
        core.claim(self.store, "builder", task["id"])
        core.verify(self.store, task["id"], "unit", 0)
        closed = core.done(self.store, "builder", task["id"], note="finished at 03:10")
        self.assertEqual(closed["state"], core.DONE)
        self.assertEqual(closed["note"], "finished at 03:10")
        self.assertTrue((self.root / "agents" / "builder" / "done" / f"{task['id']}.json").exists())
        self.assertFalse((self.root / "agents" / "builder" / "processing" / f"{task['id']}.json").exists())

    def test_force_closes_without_verify(self):
        task = self.hand_over()
        core.claim(self.store, "builder", task["id"])
        closed = core.done(self.store, "builder", task["id"], force=True)
        self.assertEqual(closed["state"], core.DONE)


class TestDeadWorker(Base):
    def test_expired_lease_shows_up_then_is_reaped_with_a_trail(self):
        task = self.hand_over()
        core.claim(self.store, "builder", task["id"], ttl=0)  # dies immediately

        state = core.status(self.store)
        self.assertEqual(len(state["expired_leases"]), 1)
        self.assertEqual(state["expired_leases"][0]["id"], task["id"])
        self.assertFalse(state["ok"])
        self.assertTrue(state["next_steps"])

        reaped = core.reap(self.store)
        self.assertEqual([t["id"] for t in reaped], [task["id"]])

        pending = core.check(self.store, "builder")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["tries"], 1)
        self.assertIn("builder", pending[0]["prev_owners"])
        self.assertIsNone(pending[0]["_lease"])

    def test_reap_leaves_a_live_lease_alone(self):
        task = self.hand_over()
        core.claim(self.store, "builder", task["id"], ttl=600)
        self.assertEqual(core.reap(self.store), [])
        self.assertEqual(len(core.list_tasks(self.store, "builder", core.PROCESSING)), 1)

    def test_reclaiming_after_reap_counts_as_second_try(self):
        task = self.hand_over()
        core.claim(self.store, "builder", task["id"], ttl=0)
        core.reap(self.store)
        again = core.claim(self.store, "builder", task["id"], ttl=60)
        self.assertEqual(again["tries"], 2)

    def test_orphan_without_lease_is_recovered_by_reap(self):
        """A crash between "moved the file" and "wrote the lease" must not strand a task."""
        import json
        import os
        import time as clock

        task = self.hand_over()
        orphan = self.store.task_path("builder", core.PROCESSING, task["id"])
        payload = dict(task)
        payload["_lease"] = None
        orphan.write_text(json.dumps(payload), encoding="utf-8")
        (self.root / "agents" / "builder" / "pending" / f"{task['id']}.json").unlink()

        old = clock.time() - 3600
        os.utime(orphan, (old, old))

        self.assertEqual(len(core.status(self.store)["expired_leases"]), 1)
        self.assertEqual(len(core.reap(self.store)), 1)
        self.assertEqual(len(core.check(self.store, "builder")), 1)


class TestStatus(Base):
    def test_missing_heartbeat_reads_as_dead(self):
        self.hand_over()
        state = core.status(self.store)
        self.assertFalse(state["agents"]["builder"]["alive"])
        self.assertIsNone(state["agents"]["builder"]["heartbeat_age_s"])

    def test_heartbeat_makes_an_agent_alive(self):
        self.hand_over()
        core.heartbeat(self.store, "builder")
        self.assertTrue(core.status(self.store)["agents"]["builder"]["alive"])

    def test_quiet_pipeline_is_reported_as_ok(self):
        self.assertTrue(core.status(self.store)["ok"])


class TestRace(Base):
    def test_exactly_one_thread_wins_the_claim(self):
        task = self.hand_over()
        barrier = threading.Barrier(2)
        outcomes = []
        lock = threading.Lock()

        def worker():
            barrier.wait()
            try:
                core.claim(self.store, "builder", task["id"], ttl=60)
                result = "won"
            except (core.Held, core.NotFound):
                result = "lost"
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(outcomes), ["lost", "won"])
        self.assertEqual(len(core.list_tasks(self.store, "builder", core.PROCESSING)), 1)


if __name__ == "__main__":
    unittest.main()
