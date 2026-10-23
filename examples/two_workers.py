"""A runnable story: a worker dies mid-task, and the work survives it.

    PYTHONPATH=src python examples/two_workers.py
"""

import tempfile

from nightshift import core


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = core.Store(tmp).init(["reviewer", "builder"])

        task = core.send(store, "reviewer", "builder", "batch 3: add login page")
        print(f"1. reviewer handed over {task['id']}")

        core.heartbeat(store, "builder")
        core.claim(store, "builder", task["id"], ttl=0)  # 0s lease = dies at once
        print("2. builder claimed it, then the process was killed")

        state = core.status(store)
        print(f"3. status: ok={state['ok']}")
        for line in state["next_steps"]:
            print(f"   - {line}")

        reaped = core.reap(store)
        print(f"4. reap returned {len(reaped)} task(s) to pending")

        core.heartbeat(store, "builder")
        again = core.claim(store, "builder", task["id"], ttl=60)
        print(f"5. claimed again: try {again['tries']}, prev owners {again['prev_owners']}")

        try:
            core.done(store, "builder", task["id"])
        except core.Blocked as exc:
            print(f"6. done refused: {exc}")

        core.verify(store, task["id"], "e2e", 0)
        core.done(store, "builder", task["id"], note="login page shipped")
        print("7. verify rc=0 recorded, task closed")

        final = core.status(store)
        print(f"8. done={final['agents']['builder']['done']}, ok={final['ok']}")


if __name__ == "__main__":
    main()
