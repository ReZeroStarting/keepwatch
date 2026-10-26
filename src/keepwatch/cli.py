"""Command line interface. Every command maps to one function in ``core``."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__, core

COMMANDS = (
    "init", "send", "check", "claim", "renew", "release", "run",
    "done", "verify", "reap", "status", "doctor", "checkpoint", "pipeline",
)


def _default_root() -> str:
    return os.environ.get("KEEPWATCH_ROOT", ".")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keepwatch",
        description="Keep agent work alive when the process that started it is not.",
    )
    parser.add_argument("--root", default=_default_root(), help="work root (default: $KEEPWATCH_ROOT or .)")
    parser.add_argument("--json", action="store_true", help="machine readable output")
    parser.add_argument("--version", action="version", version=f"keepwatch {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create the layout for some agents")
    p_init.add_argument("agents", nargs="+", metavar="AGENT")

    p_send = sub.add_parser("send", help="hand a task to an agent")
    p_send.add_argument("--from", dest="sender", required=True)
    p_send.add_argument("--to", required=True)
    p_send.add_argument("--subject", required=True)
    p_send.add_argument("--body", default="")
    p_send.add_argument("--body-file")

    p_check = sub.add_parser("check", help="list tasks you can pick up")
    p_check.add_argument("--who", required=True)

    p_claim = sub.add_parser("claim", help="take a task (and hold a lease on it)")
    p_claim.add_argument("--who", required=True)
    p_claim.add_argument("--id", required=True)
    p_claim.add_argument("--ttl", type=int, default=core.DEFAULT_TTL_SECONDS)

    p_renew = sub.add_parser("renew", help="extend your own lease on a task")
    p_renew.add_argument("--who", required=True)
    p_renew.add_argument("--id", required=True)
    p_renew.add_argument("--ttl", type=int, default=core.DEFAULT_TTL_SECONDS)

    p_done = sub.add_parser("done", help="close a task (requires a passing verify)")
    p_done.add_argument("--who", required=True)
    p_done.add_argument("--id", required=True)
    p_done.add_argument("--note", default="")
    p_done.add_argument("--force", action="store_true", help="close without a verify record")

    p_verify = sub.add_parser("verify", help="record a check result against a task")
    p_verify.add_argument("--id", required=True)
    p_verify.add_argument("--name", required=True)
    p_verify.add_argument("--rc", type=int, required=True)

    p_run = sub.add_parser(
        "run",
        help="claim a task, run a command, record the exit code and close or release it",
        usage="keepwatch run --who AGENT [--id ID] [--ttl SECONDS] [--max-tries N] -- COMMAND ...",
    )
    p_run.add_argument("--who", required=True)
    p_run.add_argument("--id", help="task id (default: the oldest pending task)")
    p_run.add_argument("--ttl", type=int, default=core.DEFAULT_TTL_SECONDS)
    p_run.add_argument("--max-tries", type=int, default=3)
    p_run.add_argument("cmd", nargs=argparse.REMAINDER)

    sub.add_parser("reap", help="return expired leases to pending")

    p_status = sub.add_parser("status", help="what is stuck, who is dead, what is next")
    p_status.add_argument("--stale", type=int, default=core.DEFAULT_STALE_SECONDS)

    p_doctor = sub.add_parser("doctor", help="deterministic self-check (exit 0 when healthy)")
    p_doctor.add_argument("--quiet", action="store_true", help="print only failures and warnings")

    p_ckpt = sub.add_parser("checkpoint", help="write a resume note for a human or a fresh agent")
    p_ckpt.add_argument("--out", help="where to write it (default: <root>/CHECKPOINT.md)")

    p_gate = sub.add_parser("pipeline", help="open or close the gate for new work")
    gate_group = p_gate.add_mutually_exclusive_group()
    gate_group.add_argument("--on", action="store_true", help="accept new work")
    gate_group.add_argument("--off", action="store_true", help="stop accepting new work")
    p_gate.add_argument("--note", default="", help="why the gate was changed")

    return parser


def _emit(args: argparse.Namespace, payload: Any, human: list[str]) -> None:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for line in human:
            print(line)


def _short(text: str, width: int = 40) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def run(args: argparse.Namespace) -> int:
    store = core.Store(args.root)

    if args.command == "init":
        store.init(args.agents)
        _emit(args, {"root": str(store.root), "agents": args.agents},
              [f"initialised {store.root}", "agents: " + ", ".join(args.agents)])
        return core.EXIT_OK

    if args.command == "send":
        body = args.body
        if args.body_file:
            body = Path(args.body_file).read_text(encoding="utf-8")
        task = core.send(store, args.sender, args.to, args.subject, body)
        _emit(args, task, [f"sent {task['id']} -> {args.to}: {args.subject}"])
        return core.EXIT_OK

    if args.command == "check":
        core.heartbeat(store, args.who)
        tasks = core.check(store, args.who)
        human = [f"{len(tasks)} task(s) for {args.who}"]
        for t in tasks:
            age = int(core.age_seconds(t["created_at"]))
            human.append(f"  {t['id']}  {age:>6}s  from {t['from']:<12} {_short(t['subject'])}")
        _emit(args, {"who": args.who, "tasks": tasks}, human)
        return core.EXIT_OK

    if args.command == "claim":
        core.heartbeat(store, args.who)
        task = core.claim(store, args.who, args.id, args.ttl)
        lease = task["_lease"]
        _emit(args, task, [
            f"claimed {task['id']} (try {task['tries']}) lease until {lease['expires_at']}",
        ])
        return core.EXIT_OK

    if args.command == "renew":
        core.heartbeat(store, args.who)
        task = core.renew(store, args.who, args.id, args.ttl)
        _emit(args, task, [f"renewed {task['id']} until {task['_lease']['expires_at']}"])
        return core.EXIT_OK

    if args.command == "done":
        core.heartbeat(store, args.who)
        task = core.done(store, args.who, args.id, args.note, args.force)
        _emit(args, task, [f"closed {task['id']}"])
        return core.EXIT_OK

    if args.command == "verify":
        record = core.verify(store, args.id, args.name, args.rc)
        _emit(args, record, [f"recorded {args.name} rc={args.rc} for {args.id}"])
        return core.EXIT_OK

    if args.command == "run":
        return run_task(store, args)

    if args.command == "reap":
        tasks = core.reap(store)
        human = [f"reaped {len(tasks)} task(s)"]
        for t in tasks:
            human.append(f"  {t['id']}  back to pending (tries={t['tries']})")
        _emit(args, {"reaped": tasks}, human)
        return core.EXIT_OK

    if args.command == "status":
        state = core.status(store, args.stale)
        human = [f"root: {state['root']}"]
        for name, row in state["agents"].items():
            if row["heartbeat_age_s"] is None:
                hb, flag = "never seen", "unknown"
            elif row["alive"]:
                hb, flag = f"hb {row['heartbeat_age_s']}s", "alive"
            else:
                hb, flag = f"hb {row['heartbeat_age_s']}s", "STALE"
            human.append(
                f"  {name:<14} pending {row['pending']}  processing {row['processing']}"
                f"  done {row['done']}  {hb} ({flag})"
            )
        if state["next_steps"]:
            human.append("next:")
            human.extend(f"  - {line}" for line in state["next_steps"])
        else:
            human.append("next: nothing to do")
        _emit(args, state, human)
        return core.EXIT_OK if state["ok"] else core.EXIT_BLOCKED

    if args.command == "doctor":
        report = core.doctor(store)
        human = [f"root: {report['root']}"]
        for check in report["checks"]:
            if args.quiet and check["status"] == "ok":
                continue
            human.append(f"  [{check['status']:<4}] {check['check']:<10} {check['detail']}")
        human.append("healthy" if report["ok"] else "PROBLEMS FOUND")
        _emit(args, report, human)
        return core.EXIT_OK if report["ok"] else core.EXIT_ERROR

    if args.command == "checkpoint":
        path = core.checkpoint(store, Path(args.out) if args.out else None)
        _emit(args, {"path": str(path)}, [f"checkpoint written to {path}"])
        return core.EXIT_OK

    if args.command == "pipeline":
        if args.on or args.off:
            state = core.set_pipeline(store, enabled=bool(args.on), note=args.note)
            word = "open" if state["pipeline_enabled"] else "closed"
            _emit(args, state, [f"pipeline {word}" + (f" ({args.note})" if args.note else "")])
            return core.EXIT_OK
        enabled = core.pipeline_enabled(store)
        payload = {"pipeline_enabled": enabled}
        _emit(args, payload, [f"pipeline {'open' if enabled else 'closed'}"])
        return core.EXIT_OK

    raise core.KeepwatchError(f"unknown command: {args.command}")


def run_task(store: core.Store, args: argparse.Namespace) -> int:
    """Claim -> run -> verify -> close or release. The one command an agent needs."""
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise core.KeepwatchError(
            "no command given; use: keepwatch run --who AGENT -- COMMAND ..."
        )

    core.heartbeat(store, args.who)
    task_id = args.id
    if not task_id:
        task = core.oldest_pending(store, args.who)
        if task is None:
            raise core.NotFound(f"no pending task for {args.who}")
        task_id = task["id"]

    claimed = core.claim(store, args.who, task_id, args.ttl)
    label = " ".join(cmd)
    print(f"running {task_id} (try {claimed['tries']}): {label}", flush=True)

    # stdio is inherited on purpose: the agent's output must reach the human.
    rc = subprocess.run(cmd, check=False).returncode
    core.verify(store, task_id, label, rc)

    if rc == 0:
        core.done(store, args.who, task_id, note=label)
        print(f"closed {task_id} (rc=0)", flush=True)
        return core.EXIT_OK

    if claimed["tries"] >= args.max_tries:
        core.ledger_append(store, "suspect", id=task_id, tries=claimed["tries"], rc=rc)
        print(
            f"left {task_id} in processing: rc={rc}, tries={claimed['tries']} >= {args.max_tries}. "
            f"Check `status`, then `reap` if it should be retried anyway.",
            flush=True,
        )
        return rc if 0 < rc < 256 else core.EXIT_ERROR

    core.release(store, args.who, task_id, reason=f"rc={rc}")
    print(f"released {task_id} back to pending (rc={rc})", flush=True)
    return rc if 0 < rc < 256 else core.EXIT_ERROR


def normalise(argv: list[str]) -> list[str]:
    """Let ``--json`` sit anywhere, including after the sub-command.

    Agents write ``check --json`` far more often than ``--json check``, and argparse
    rejects the former because ``--json`` is a top-level flag. Move it in front of the
    sub-command when it appears after it, but never touch anything after ``--`` (that
    belongs to the wrapped command).
    """
    separator = argv.index("--") if "--" in argv else len(argv)
    head, tail = list(argv[:separator]), list(argv[separator:])
    if "--json" in head:
        json_at = head.index("--json")
        command_at = next((i for i, arg in enumerate(head) if arg in COMMANDS), None)
        if command_at is not None and json_at > command_at:
            head = ["--json"] + head[:json_at] + head[json_at + 1 :]
    return head + tail


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalise(sys.argv[1:] if argv is None else list(argv)))
    try:
        return run(args)
    except core.KeepwatchError as exc:
        print(f"keepwatch: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
