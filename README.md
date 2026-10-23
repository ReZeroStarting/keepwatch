# nightshift

> **Keep work alive when the process that started it is not.**
> A 300-line, zero-dependency ledger for agents that run unattended.

[![ci](https://github.com/ReZeroStarting/nightshift/actions/workflows/ci.yml/badge.svg)](https://github.com/ReZeroStarting/nightshift/actions/workflows/ci.yml)

---

## The problem

An unattended pipeline does not fail loudly. It goes quiet:

- a task sits in `pending/` and nobody claims it
- a worker dies mid-task and the task is stuck in `processing/` forever
- a worker finishes but never marks it done, so the next round re-dispatches it
- the agent says "all tests pass" and you have nothing to check that against

All four look identical from the outside: **nothing happened, and the logs are clean.**

## What it does

Three folders, one ledger, four verbs.

```
<root>/agents/<agent>/pending/      tasks a worker can pick up
<root>/agents/<agent>/processing/   tasks someone is holding (with a lease)
<root>/agents/<agent>/done/         closed tasks, never deleted
<root>/audit/YYYY-MM.jsonl          append-only record of everything
```

| Verb | Meaning |
|---|---|
| `check` | what can I pick up |
| `claim` | take one, and hold a lease on it |
| `done` | close it — **only if a passing `verify` record exists** |
| `verify` | record a check result (`--rc 0`) against a task |
| **`run`** | **claim → run a command → record its exit code → close or release** |

Plus `send`, `renew`, `release`, `reap`, `status`.

## The one command you actually need

```bash
nightshift run --who builder -- python -m pytest
```

That does the whole loop in one go:

1. claims the oldest pending task (or `--id <id>`)
2. runs the command with inherited stdio, so you still see the output
3. writes `verify ... --rc <real exit code>` into the ledger
4. `rc=0` → `done`; `rc!=0` → back to `pending` for another try
5. after `--max-tries` (default 3) failures → left in `processing` with a `suspect` record

If the process is killed mid-command, nothing is lost: the lease expires and `reap` puts the
task back. **You do not have to remember to write the ledger — the wrapper does it.**

## Why it matters

`claim` starts a lease. If the worker dies, the lease expires and `reap` puts the task
back with a trail (`tries`, `prev_owners`). No task is ever stranded, and no task is
silently done twice.

`done` refuses to close a task unless the ledger holds a `verify ... rc=0` for it.
That is the whole point: **the model can say "it passed"; the ledger is what proves it.**

```console
$ nightshift done --who builder --id 20261023T083729-8c4fae
nightshift: refusing to close 20261023T083729-8c4fae: no verify record with rc=0.
Run `verify --id ... --name <check> --rc 0` first, or pass --force.
$ echo $?
13
```

## Install

Python 3.9+, no dependencies.

```bash
pip install -e .
# or just run it from a checkout:
PYTHONPATH=src python -m nightshift.cli --help
```

## 60-second demo

```bash
nightshift --root /tmp/ns init builder reviewer

nightshift --root /tmp/ns send \
  --from reviewer --to builder --subject "batch 3: add login page"

nightshift --root /tmp/ns check --who builder
nightshift --root /tmp/ns claim --who builder --id <id> --ttl 60
```

Now pretend the worker dies. Wait for the lease, then:

```console
$ nightshift --root /tmp/ns status
root: /tmp/ns
  builder        pending 0  processing 1  done 0  hb 61s (alive)
  reviewer       pending 0  processing 0  done 0  never seen (unknown)
next:
  - builder: 20261023T083750-ef2c5d held by builder, 1s past its lease - run `reap`

$ nightshift --root /tmp/ns reap
reaped 1 task(s)
  20261023T083750-ef2c5d  back to pending (tries=1)

$ nightshift --root /tmp/ns claim --who builder --id 20261023T083750-ef2c5d
claimed 20261023T083750-ef2c5d (try 2) lease until 2026-10-23T08:42:31Z
```

The task came back with `tries=1` and the previous owner recorded. Close it, but only
after proof:

```bash
nightshift --root /tmp/ns verify --id <id> --name pytest --rc 0
nightshift --root /tmp/ns done   --who builder --id <id> --note "e2e green"
```

## Exit codes

Exit codes are the interface; agents read them instead of parsing prose.

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | bad arguments, IO error |
| 2 | task not found / already taken |
| 3 | task held by another worker (or busy) |
| 13 | blocked: no passing verify, or `status` found something that needs attention |

## Design notes

**Files are the source of truth.** No database, no daemon, no network. `ls` and `cat`
are the debugging tools.

**Exclusive create is the mutex, not rename.** On at least one Windows filesystem two
concurrent `os.replace()` calls on the same source *both report success*. Claiming is
therefore guarded by `O_CREAT|O_EXCL` on a short-lived token file. This is documented in
[`docs/PROTOCOL.md`](docs/PROTOCOL.md) because it cost a real bug to find.

**One worker per agent box.** Leases are per agent directory, so `builder` running two
processes is a configuration error, not a supported mode.

**`status` exits 13 when something needs attention** — so `nightshift status || alert`
works in cron.

## What it is not

- not a mailbox (see [docs/COMPETITION.md](docs/COMPETITION.md) for what exists)
- not an orchestrator: it never decides *what* to do next
- not an agent: it makes no model calls
- not durable execution: it will not replay your side effects

## Docs

| File | Contents |
|---|---|
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | layout, message schema, lease rules, exit codes (Chinese) |
| [docs/COMPETITION.md](docs/COMPETITION.md) | why this exists next to agent-mailbox / A2A / Temporal (Chinese) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | v0.1 → v1.0 and the release checklist (Chinese) |

Tests: `PYTHONPATH=src python -m unittest discover -s tests -v`

## License

MIT. See [LICENSE](LICENSE).
