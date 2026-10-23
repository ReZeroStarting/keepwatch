# Protocol v0.1

> 中文版：[docs/zh/PROTOCOL.md](zh/PROTOCOL.md)

**Goal:** any agent that can read and write files can join. Any human with `ls` and `cat`
can see the current state. Nothing here requires a daemon, a database, or a network.

This document describes **what v0.1 implements**. Features that are designed but not
built yet are listed in [§9](#9-not-implemented-yet) rather than implied above.

## 1. Layout

```
<ROOT>/
├── agents/
│   └── <agent-id>/
│       ├── pending/       # up for grabs
│       ├── processing/    # claimed, holds a lease
│       └── done/          # archived, never deleted
├── state/
│   └── <agent-id>.heartbeat.json
└── audit/
    └── <yyyy-mm>.jsonl    # append-only record of every transition
```

Rules:

- **Write only into the recipient's `pending/`.** Read and write your own three boxes.
- **Move, never delete.** Every archival step is a rename.
- `agent-id` must match `[a-z0-9_-]{1,32}`.
- `state/` and `agents/` must live on the same volume (renames must not cross devices).

## 2. Task format

One task = one JSON file named `<task-id>.json`.

```json
{
  "id": "20261023T160000-7f3a1c",
  "from": "reviewer",
  "to": "builder",
  "subject": "batch 3: add login page",
  "body": "...",
  "created_at": "2026-10-23T16:00:00.123Z",
  "state": "pending",
  "tries": 0,
  "prev_owners": [],
  "_lease": null
}
```

Fields added later in the lifecycle:

| Field | Written by | Meaning |
|---|---|---|
| `_lease` | `claim` / `renew` | present only while `state == "processing"` |
| `tries` | `claim` | how many times this task has been taken |
| `prev_owners` | `claim` (takeover) / `release` / `reap` | who held it before |
| `released_at` | `release` | put back without closing |
| `reaped_at` | `reap` | returned after a lease expired |
| `closed_at`, `closed_by`, `note` | `done` | how and by whom it was closed |

Timestamps are UTC with millisecond precision. Milliseconds matter: two tasks created
in the same second must still have a deterministic order when picking "the oldest one".

## 3. State machine

```
          send
           │
           ▼
       pending ──claim──► processing ──done──► done
           ▲                  │
           ├───── release ────┤   (failed run, explicit give-back)
           └─────── reap ─────┘   (lease expired)
```

- `claim` — take a task, start a lease
- `renew` — extend your own lease
- `release` — put it back to pending without closing it
- `reap` — return tasks whose lease expired (the dead-worker path)
- `done` — close it; requires a passing `verify` unless `--force`

## 4. Why claiming does not use rename as a lock

The first implementation used `os.replace(pending → processing)` as the mutex. **It failed
in real testing:**

> On one Windows filesystem, two threads calling `os.replace()` concurrently on the same
> source **both reported success** — even though the source file no longer existed for the
> second call.

The result was two workers "claiming" the same task, one of them silently overwriting the
other's lease.

Claiming is now guarded by an exclusive-create token:

- `<processing>/.<task-id>.claim` is created with `O_CREAT|O_EXCL`, which is atomic on both
  NTFS and POSIX (measured: 16 concurrent threads, exactly 1 winner)
- the token lives only for one state transition — a few milliseconds on the happy path
- a token older than 30 seconds is treated as stale and reclaimed
- tokens start with `.` and do not end in `.json`, so `list_tasks` never reads them

**Lesson:** filesystem atomicity guarantees are platform-specific. "Use a rename as a lock"
passes unit tests and fails silently under concurrency. The only way to know is to write
the concurrency test.

## 5. Lease

```json
"_lease": {
  "owner": "builder",
  "acquired_at": "2026-10-23T16:01:00.000Z",
  "expires_at": "2026-10-23T16:06:00.000Z"
}
```

- default TTL 300s (`--ttl`)
- `renew` works only for the current owner and does not increment `tries`
- `claim` on a live lease is refused with exit code **3**
- `claim` on an expired lease takes over: `tries += 1`, previous owner appended to
  `prev_owners`, and a `steal` event is recorded

## 6. Heartbeat

`state/<agent>.heartbeat.json`:

```json
{"agent": "builder", "at": "2026-10-23T16:02:11.000Z", "pid": 4242, "host": "..."}
```

**Every command that names an agent writes a heartbeat.** There is no separate "remember to
ping" step, because a heartbeat maintained by discipline is a heartbeat that stops.

## 7. What `status` reports

| Field | Meaning |
|---|---|
| `pending` / `processing` / `done` per agent | how much work is where |
| `heartbeat_age_s` | `null` means never seen; a number is seconds since the last command |
| `alive` | heartbeat within `--stale` (default 1200s) |
| `expired_leases` | task, holder, and how many seconds overdue |
| `next_steps` | human-readable list: what is waiting, what is stuck |
| `ok` | false when any lease is expired |

`status` exits **13** when `ok` is false, so `nightshift status || alert` works in cron.

## 8. Exit codes

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | bad arguments, IO error |
| 2 | task not found, or already taken by someone else |
| 3 | task held by another worker, or busy |
| 13 | blocked: no passing verify, or `status` found something that needs attention |

**Exit codes are part of the interface.** Agents branch on them instead of parsing prose.

## 9. Not implemented yet

Designed, deliberately out of scope for v0.1. Tracked in [ROADMAP](ROADMAP.md):

- message `type` (`task` / `reply` / `notice` / `ping`) and `reply_to`
- `depth` and `trace`, to stop two agents replying to each other forever
- `pipeline --on|--off` gate and exit code 14
- dependencies (`--after`) and a `hold` queue
- `checkpoint` — a one-command resume note for a human or a fresh agent
- `doctor` — deterministic self-check
- prioritised pools, broadcast delivery, hand-off between agents

## 10. Out of scope, permanently

- encryption and authentication (same machine, same user is the trust boundary)
- network transport (use A2A or MCP if you need that; this project stays local)
- scheduling (when to run a round is the host's job; this project only reports that
  nobody ran one)
