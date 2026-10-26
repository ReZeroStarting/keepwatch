"""keepwatch core.

Three ideas, nothing more:

1. A task is a JSON file that lives in exactly one of three folders:
   ``pending`` -> ``processing`` -> ``done``.
2. Whoever is working on a task holds a *lease*. If that worker dies, the lease
   expires and ``reap`` puts the task back. The task keeps a hand-over trail.
3. A task is only ``done`` when a ``verify`` record with ``rc == 0`` exists.
   The model can say "it passed"; the ledger is what proves it.

Everything is files. No database, no daemon, no third-party dependency.
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
PENDING = "pending"
PROCESSING = "processing"
DONE = "done"
BOXES = (PENDING, PROCESSING, DONE)

AGENT_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
DEFAULT_TTL_SECONDS = 300
DEFAULT_STALE_SECONDS = 1200
# A claim holds its token for milliseconds. Five seconds is already generous, and
# it bounds how long a hard-killed claimer can block the task.
TOKEN_STALE_SECONDS = 5.0

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_FOUND = 2
EXIT_HELD = 3
EXIT_BLOCKED = 13
EXIT_STOPPED = 14


class KeepwatchError(Exception):
    """Base error. ``code`` is part of the CLI contract."""

    code = EXIT_ERROR


class NotFound(KeepwatchError):
    code = EXIT_NOT_FOUND


class Held(KeepwatchError):
    code = EXIT_HELD


class Blocked(KeepwatchError):
    code = EXIT_BLOCKED


class Stopped(KeepwatchError):
    """The pipeline gate is closed: no new work is accepted."""

    code = EXIT_STOPPED


# --------------------------------------------------------------------------
# time helpers (always UTC, always aware)
# --------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(dt: datetime | None = None) -> str:
    # Milliseconds, not seconds: two tasks created in the same second must still
    # have a deterministic order when we pick "the oldest pending one".
    return (dt or utcnow()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_stamp(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def age_seconds(text: str) -> float:
    return (utcnow() - parse_stamp(text)).total_seconds()


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------
class Store:
    """The directory layout. Construct once, pass around."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    # -- paths ----------------------------------------------------------
    def agent_dir(self, agent: str) -> Path:
        if not AGENT_RE.match(agent):
            raise KeepwatchError(f"bad agent id: {agent!r} (want [a-z0-9_-]{{1,32}})")
        return self.root / "agents" / agent

    def box(self, agent: str, box: str) -> Path:
        return self.agent_dir(agent) / box

    def task_path(self, agent: str, box: str, task_id: str) -> Path:
        return self.box(agent, box) / f"{task_id}.json"

    def heartbeat_path(self, agent: str) -> Path:
        return self.root / "state" / f"{agent}.heartbeat.json"

    def audit_path(self) -> Path:
        return self.root / "audit" / f"{utcnow():%Y-%m}.jsonl"

    # -- lifecycle ------------------------------------------------------
    def init(self, agents: Iterable[str]) -> Store:
        for agent in agents:
            self.agent_dir(agent)
            for box in BOXES:
                self.box(agent, box).mkdir(parents=True, exist_ok=True)
        (self.root / "state").mkdir(parents=True, exist_ok=True)
        (self.root / "audit").mkdir(parents=True, exist_ok=True)
        return self

    def require(self) -> None:
        if not (self.root / "agents").is_dir():
            raise KeepwatchError(f"{self.root} is not a keepwatch root; run `init` first")

    def agents(self) -> list[str]:
        base = self.root / "agents"
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.iterdir() if p.is_dir())


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------
def _read(path: Path) -> dict[str, Any]:
    for attempt in range(6):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise NotFound(f"task not found: {path.name}") from None
        except PermissionError:
            # Windows may refuse a read while another process renames the file.
            if attempt == 5:
                raise Held(f"task is busy (locked by another process): {path.name}") from None
            time.sleep(0.005)
        except json.JSONDecodeError as exc:
            raise KeepwatchError(f"corrupt task file {path.name}: {exc}") from exc
    raise Held(f"task is busy: {path.name}")


def _replace(src: Path, dst: Path, retries: int = 10, delay: float = 0.005) -> None:
    """os.replace with a short retry loop.

    On Windows a rename fails with PermissionError (WinError 32) while another
    process has the file open, even for a moment. Retrying turns a spurious
    failure into a correct outcome: whoever wins the retry race owns the file.
    """
    last: Exception | None = None
    for _ in range(retries):
        try:
            os.replace(src, dst)
            return
        except FileNotFoundError:
            raise
        except PermissionError as exc:
            last = exc
            time.sleep(delay)
    assert last is not None
    raise last


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write to a uniquely named temp file, then rename. No half files, no shared temps."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _move(src: Path, dst: Path) -> None:
    """Atomic move. If src disappeared, someone else won the race."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        _replace(src, dst)
    except FileNotFoundError:
        raise NotFound(f"task already taken: {dst.name}") from None
    except PermissionError:
        raise Held(f"task is busy (locked by another process): {dst.name}") from None


@contextmanager
def _exclusive(path: Path, stale_after: float = TOKEN_STALE_SECONDS):
    """A mutex built on ``O_CREAT | O_EXCL``.

    Do **not** use rename as a lock. On at least one Windows filesystem two
    concurrent ``os.replace()`` calls on the same source both report success,
    which silently lets two workers claim one task. Exclusive create was the one
    primitive that stayed atomic under contention, so it is the one we use.
    """
    acquired = False
    for _ in range(4):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > stale_after:
                    path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            raise Held(f"task is busy (someone is working on it): {path.name}") from None
    if not acquired:
        raise Held(f"task is busy: {path.name}")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _crash_point(name: str) -> None:
    """Test seam: die like SIGKILL at a named moment, leaving no cleanup behind.

    Only active when KEEPWATCH_CRASH_AT names this point. It exists so the crash
    behaviour can be tested for real instead of being mocked.
    """
    if os.environ.get("KEEPWATCH_CRASH_AT") == name:
        os._exit(137)


def heartbeat(store: Store, agent: str) -> dict[str, Any]:
    payload = {"agent": agent, "at": stamp(), "pid": os.getpid(), "host": socket.gethostname()}
    _write_atomic(store.heartbeat_path(agent), payload)
    return payload


def ledger_append(store: Store, event: str, **fields: Any) -> dict[str, Any]:
    record = {"at": stamp(), "event": event, "schema": SCHEMA_VERSION, **fields}
    path = store.audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def ledger_records(store: Store) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    audit = store.root / "audit"
    if not audit.is_dir():
        return records
    for path in sorted(audit.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# --------------------------------------------------------------------------
# pipeline gate
# --------------------------------------------------------------------------
def gate_path(store: Store) -> Path:
    return store.root / "state" / "gate.json"


def pipeline_enabled(store: Store) -> bool:
    path = gate_path(store)
    if not path.exists():
        return True
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")).get("pipeline_enabled", True))
    except (json.JSONDecodeError, OSError, AttributeError):
        return True


def set_pipeline(store: Store, enabled: bool, note: str = "") -> dict[str, Any]:
    store.require()
    payload = {"pipeline_enabled": enabled, "note": note, "at": stamp()}
    _write_atomic(gate_path(store), payload)
    ledger_append(store, "gate", enabled=enabled, note=note)
    return payload


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def new_task_id() -> str:
    return f"{utcnow():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


def send(store: Store, sender: str, to: str, subject: str, body: str = "") -> dict[str, Any]:
    store.require()
    if not pipeline_enabled(store):
        raise Stopped("pipeline is stopped; run `keepwatch pipeline --on` to accept new work")
    if to not in store.agents():
        raise KeepwatchError(f"unknown agent: {to!r}")
    task = {
        "id": new_task_id(),
        "from": sender,
        "to": to,
        "subject": subject,
        "body": body,
        "created_at": stamp(),
        "state": PENDING,
        "tries": 0,
        "prev_owners": [],
        "_lease": None,
    }
    _write_atomic(store.task_path(to, PENDING, task["id"]), task)
    ledger_append(store, "send", id=task["id"], from_=sender, to=to, subject=subject)
    return task


def list_tasks(store: Store, agent: str, box: str = PENDING) -> list[dict[str, Any]]:
    store.require()
    folder = store.box(agent, box)
    if not folder.is_dir():
        return []
    tasks = []
    for path in sorted(folder.glob("*.json")):
        try:
            tasks.append(_read(path))
        except KeepwatchError:
            continue
    return tasks


def check(store: Store, agent: str) -> list[dict[str, Any]]:
    """What can I pick up right now?"""
    return list_tasks(store, agent, PENDING)


def claim(store: Store, agent: str, task_id: str, ttl: int = DEFAULT_TTL_SECONDS) -> dict[str, Any]:
    store.require()
    if not pipeline_enabled(store):
        raise Stopped("pipeline is stopped; no task may be claimed")
    token = store.box(agent, PROCESSING) / f".{task_id}.claim"
    with _exclusive(token):
        return _claim_locked(store, agent, task_id, ttl)


def _claim_locked(store: Store, agent: str, task_id: str, ttl: int) -> dict[str, Any]:
    now = utcnow()
    pending = store.task_path(agent, PENDING, task_id)
    processing = store.task_path(agent, PROCESSING, task_id)

    if pending.exists():
        task = _read(pending)
        # Record the attempt before the move. If this process dies between the
        # move and the lease write, the file still says someone took it once.
        task["tries"] = int(task.get("tries", 0)) + 1
        _write_atomic(pending, task)
        _move(pending, processing)
        _crash_point("after_move")  # the worst moment: moved, but no lease yet
        task["state"] = PROCESSING
        task["_lease"] = {
            "owner": agent,
            "acquired_at": stamp(now),
            "expires_at": stamp(now + timedelta(seconds=ttl)),
        }
        _write_atomic(processing, task)
        ledger_append(store, "claim", id=task_id, by=agent, tries=task["tries"], ttl=ttl)
        return task

    if processing.exists():
        task = _read(processing)
        lease = task.get("_lease") or {}
        owner = lease.get("owner")
        expires = lease.get("expires_at")
        if not expires:
            # The file is in processing but has no lease yet: another process is
            # between "moved the file" and "wrote the lease". Never steal that.
            raise Held(f"task {task_id} is being claimed right now (no lease yet)")
        if parse_stamp(expires) > now:
            # Strict on purpose: a second claim means two workers would run the
            # same task. Use `renew` to extend your own lease instead.
            raise Held(f"task {task_id} is already claimed (owner={owner}, until {expires})")
        # Lease expired: take it over, and leave a trail.
        task.setdefault("prev_owners", []).append(owner)
        task["tries"] = int(task.get("tries", 0)) + 1
        task["_lease"] = {
            "owner": agent,
            "acquired_at": stamp(now),
            "expires_at": stamp(now + timedelta(seconds=ttl)),
        }
        _write_atomic(processing, task)
        ledger_append(store, "steal", id=task_id, by=agent, from_=owner, tries=task["tries"])
        return task

    raise NotFound(f"no such task in {agent}: {task_id}")


def renew(store: Store, agent: str, task_id: str, ttl: int = DEFAULT_TTL_SECONDS) -> dict[str, Any]:
    """Extend your own lease. Only the current owner may call this."""
    store.require()
    path = store.task_path(agent, PROCESSING, task_id)
    with _exclusive(store.box(agent, PROCESSING) / f".{task_id}.claim"):
        task = _read(path)
        lease = task.get("_lease") or {}
        if lease.get("owner") != agent:
            raise Held(f"task {task_id} is not yours (owner={lease.get('owner')})")
        lease["expires_at"] = stamp(utcnow() + timedelta(seconds=ttl))
        task["_lease"] = lease
        _write_atomic(path, task)
    ledger_append(store, "renew", id=task_id, by=agent, ttl=ttl)
    return task


def release(store: Store, agent: str, task_id: str, reason: str = "") -> dict[str, Any]:
    """Put a task back to pending without closing it (failed run, or giving up)."""
    store.require()
    path = store.task_path(agent, PROCESSING, task_id)
    token = store.box(agent, PROCESSING) / f".{task_id}.claim"
    with _exclusive(token):
        task = _read(path)
        lease = task.get("_lease") or {}
        if lease.get("owner") not in (None, agent):
            raise Held(f"task {task_id} is held by {lease.get('owner')}")
        task["state"] = PENDING
        task["prev_owners"] = list(task.get("prev_owners", [])) + [lease.get("owner")]
        task["_lease"] = None
        task["released_at"] = stamp()
        _write_atomic(path, task)
        _move(path, store.task_path(agent, PENDING, task_id))
    ledger_append(store, "release", id=task_id, by=agent, reason=reason)
    return task


def oldest_pending(store: Store, agent: str) -> dict[str, Any] | None:
    tasks = list_tasks(store, agent, PENDING)
    if not tasks:
        return None
    return min(tasks, key=lambda t: (t.get("created_at", ""), t["id"]))


def verify(store: Store, task_id: str, name: str, rc: int) -> dict[str, Any]:
    """Record a verification result. The task is not done until this returns 0."""
    store.require()
    return ledger_append(store, "verify", id=task_id, name=name, rc=int(rc))


def has_ok_verify(store: Store, task_id: str) -> bool:
    return any(
        r.get("event") == "verify" and r.get("id") == task_id and r.get("rc") == 0
        for r in ledger_records(store)
    )


def done(
    store: Store,
    agent: str,
    task_id: str,
    note: str = "",
    force: bool = False,
) -> dict[str, Any]:
    store.require()
    processing = store.task_path(agent, PROCESSING, task_id)
    with _exclusive(store.box(agent, PROCESSING) / f".{task_id}.claim"):
        task = _read(processing)
        lease = task.get("_lease") or {}
        if lease.get("owner") not in (None, agent):
            raise Held(f"task {task_id} is held by {lease.get('owner')}")

        if not force and not has_ok_verify(store, task_id):
            raise Blocked(
                f"refusing to close {task_id}: no verify record with rc=0. "
                f"Run `verify --id {task_id} --name <check> --rc 0` first, or pass --force."
            )

        task["state"] = DONE
        task["_lease"] = None
        task["closed_at"] = stamp()
        task["closed_by"] = agent
        task["note"] = note
        _write_atomic(processing, task)
        _move(processing, store.task_path(agent, DONE, task_id))
    ledger_append(store, "done", id=task_id, by=agent, note=note)
    return task


def reap(store: Store, stale_after: int = 0) -> list[dict[str, Any]]:
    """Return expired leases to pending. This is what runs after a worker dies."""
    store.require()
    now = utcnow()
    reaped: list[dict[str, Any]] = []
    for agent in store.agents():
        for task in list_tasks(store, agent, PROCESSING):
            lease = task.get("_lease") or {}
            path = store.task_path(agent, PROCESSING, task["id"])
            expires = lease.get("expires_at")
            if expires:
                deadline = parse_stamp(expires)
            else:
                # Orphan: crashed between moving the file and writing the lease.
                deadline = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if deadline + timedelta(seconds=stale_after) > now:
                continue
            task_id = task["id"]
            token = store.box(agent, PROCESSING) / f".{task_id}.claim"
            try:
                with _exclusive(token):
                    task["state"] = PENDING
                    task["reaped_at"] = stamp(now)
                    owner = lease.get("owner")
                    if owner:  # an orphan never had a lease: nobody to record
                        task["prev_owners"] = list(task.get("prev_owners", [])) + [owner]
                    task["_lease"] = None
                    _write_atomic(store.task_path(agent, PROCESSING, task_id), task)
                    _move(
                        store.task_path(agent, PROCESSING, task_id),
                        store.task_path(agent, PENDING, task_id),
                    )
            except Held:
                continue  # someone is mid-claim; leave it for the next sweep
            ledger_append(store, "reap", id=task_id, agent=agent, owner=lease.get("owner"))
            reaped.append(task)
    return reaped


def status(store: Store, stale_after: int = DEFAULT_STALE_SECONDS) -> dict[str, Any]:
    """Everything a human needs to answer: what is stuck, who is dead, what is next."""
    store.require()
    now = utcnow()
    agents: dict[str, Any] = {}
    expired: list[dict[str, Any]] = []
    next_steps: list[str] = []

    for agent in store.agents():
        hb_path = store.heartbeat_path(agent)
        hb_age = None
        if hb_path.exists():
            try:
                hb_age = int(age_seconds(json.loads(hb_path.read_text(encoding="utf-8"))["at"]))
            except (KeyError, ValueError, json.JSONDecodeError):
                hb_age = None
        alive = hb_age is not None and hb_age <= stale_after

        pending = list_tasks(store, agent, PENDING)
        processing = list_tasks(store, agent, PROCESSING)
        finished = list_tasks(store, agent, DONE)
        agents[agent] = {
            "pending": len(pending),
            "processing": len(processing),
            "done": len(finished),
            "heartbeat_age_s": hb_age,
            "alive": alive,
        }

        for task in processing:
            lease = task.get("_lease") or {}
            expires = lease.get("expires_at")
            if expires:
                deadline = parse_stamp(expires)
            else:
                deadline = datetime.fromtimestamp(
                    store.task_path(agent, PROCESSING, task["id"]).stat().st_mtime,
                    tz=timezone.utc,
                )
            if deadline <= now:
                expired.append(
                    {
                        "agent": agent,
                        "id": task["id"],
                        "held_by": lease.get("owner"),
                        "overdue_s": int((now - deadline).total_seconds()),
                    }
                )

        if pending and not processing and not alive:
            next_steps.append(
                f"{agent}: {len(pending)} task(s) waiting, and no live heartbeat - is the worker dead?"
            )
        elif pending and alive:
            next_steps.append(f"{agent}: {len(pending)} task(s) waiting to be claimed")

    for item in expired:
        next_steps.append(
            f"{item['agent']}: {item['id']} held by {item['held_by'] or 'nobody'}, "
            f"{item['overdue_s']}s past its lease - run `reap`"
        )

    return {
        "root": str(store.root),
        "agents": agents,
        "expired_leases": expired,
        "next_steps": next_steps,
        "pipeline_enabled": pipeline_enabled(store),
        "ok": not expired,
    }


# --------------------------------------------------------------------------
# doctor: deterministic self-check, exit 0 when nothing is broken
# --------------------------------------------------------------------------
def doctor(store: Store) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    if not (store.root / "agents").is_dir():
        add("layout", "fail", f"{store.root} has no agents/ directory; run `init` first")
        return {"root": str(store.root), "checks": checks, "ok": False}

    agents = store.agents()
    add("layout", "ok", f"{len(agents)} agent(s)")

    missing_boxes = []
    for agent in agents:
        for box in BOXES:
            if not store.box(agent, box).is_dir():
                missing_boxes.append(f"{agent}/{box}")
    if missing_boxes:
        add("boxes", "fail", "missing: " + ", ".join(missing_boxes))
    else:
        add("boxes", "ok", "pending/processing/done present for every agent")

    seen: dict[str, str] = {}
    duplicates: list[str] = []
    corrupt: list[str] = []
    for agent in agents:
        for box in BOXES:
            for path in sorted(store.box(agent, box).glob("*.json")):
                try:
                    task = _read(path)
                except KeepwatchError:
                    corrupt.append(str(path.relative_to(store.root)))
                    continue
                if task["id"] in seen:
                    duplicates.append(f"{task['id']} in {seen[task['id']]} and {box}")
                seen[task["id"]] = box
    add("tasks", "fail" if corrupt or duplicates else "ok",
        "; ".join(corrupt + duplicates) if (corrupt or duplicates) else f"{len(seen)} task(s) parse")

    orphans = []
    expired = 0
    now = utcnow()
    for agent in agents:
        for task in list_tasks(store, agent, PROCESSING):
            lease = task.get("_lease") or {}
            if not lease.get("expires_at"):
                orphans.append(task["id"])
            elif parse_stamp(lease["expires_at"]) <= now:
                expired += 1
    add("orphans", "warn" if orphans else "ok",
        f"{len(orphans)} processing file(s) without a lease - run `reap`" if orphans else "none")
    add("leases", "warn" if expired else "ok",
        f"{expired} expired lease(s) - run `reap`" if expired else "all leases live or absent")

    stale_tokens = []
    for agent in agents:
        for token in store.box(agent, PROCESSING).glob(".*.claim"):
            if time.time() - token.stat().st_mtime > TOKEN_STALE_SECONDS:
                stale_tokens.append(token.name)
    add("tokens", "warn" if stale_tokens else "ok",
        f"{len(stale_tokens)} stale claim token(s) - a claimer died mid-claim" if stale_tokens else "none")

    bad_lines = 0
    total = 0
    audit = store.root / "audit"
    if audit.is_dir():
        for path in audit.glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                total += 1
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
    add("ledger", "fail" if bad_lines else "ok",
        f"{bad_lines} unparseable line(s) out of {total}" if bad_lines else f"{total} record(s) parse")

    bad_hb = 0
    for agent in agents:
        path = store.heartbeat_path(agent)
        if path.exists():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                bad_hb += 1
    add("heartbeats", "fail" if bad_hb else "ok",
        f"{bad_hb} unparseable heartbeat file(s)" if bad_hb else "heartbeat files parse")

    add("gate", "ok", "open" if pipeline_enabled(store) else "closed")

    ok = all(c["status"] != "fail" for c in checks)
    return {"root": str(store.root), "checks": checks, "ok": ok}


# --------------------------------------------------------------------------
# checkpoint: a resume note a human or a fresh agent can pick up
# --------------------------------------------------------------------------
def checkpoint(store: Store, out: Path | None = None) -> Path:
    store.require()
    now = utcnow()
    lines = [
        "# checkpoint",
        "",
        f"- root: `{store.root}`",
        f"- written: {stamp(now)}",
        f"- pipeline: {'open' if pipeline_enabled(store) else 'CLOSED'}",
        "",
    ]

    for agent in store.agents():
        pending = list_tasks(store, agent, PENDING)
        processing = list_tasks(store, agent, PROCESSING)
        finished = list_tasks(store, agent, DONE)
        hb_path = store.heartbeat_path(agent)
        hb = "never seen"
        if hb_path.exists():
            try:
                hb = f"{int(age_seconds(json.loads(hb_path.read_text(encoding='utf-8'))['at']))}s ago"
            except (KeyError, ValueError, json.JSONDecodeError):
                hb = "unreadable"
        lines += [
            f"## {agent}",
            "",
            f"- heartbeat: {hb}",
            f"- pending {len(pending)} / processing {len(processing)} / done {len(finished)}",
        ]
        for task in processing:
            lease = task.get("_lease") or {}
            expires = lease.get("expires_at")
            overdue = ""
            if expires and parse_stamp(expires) <= now:
                overdue = f" (EXPIRED {int((now - parse_stamp(expires)).total_seconds())}s ago)"
            lines.append(
                f"  - in progress: `{task['id']}` held by {lease.get('owner')}, "
                f"lease until {expires}{overdue}"
            )
        for task in pending[:10]:
            lines.append(f"  - waiting: `{task['id']}` {task.get('subject', '')}")
        if len(pending) > 10:
            lines.append(f"  - … and {len(pending) - 10} more")
        lines.append("")

    recent = ledger_records(store)[-10:]
    if recent:
        lines += ["## last 10 ledger records", ""]
        for record in recent:
            extra = {k: v for k, v in record.items() if k not in ("at", "event", "schema")}
            lines.append(f"- {record.get('at')} **{record.get('event')}** {extra}")
        lines.append("")

    lines += [
        "## resume",
        "",
        "```",
        "keepwatch status                      # what is stuck",
        "keepwatch reap                        # return expired leases",
        "keepwatch run --who <agent> -- <cmd>  # claim, run, record the rc, close",
        "```",
        "",
    ]

    target = out or (store.root / "CHECKPOINT.md")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
