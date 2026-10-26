# Changelog

## 0.2.2 — `--json` works after the sub-command

### Fixed
- `keepwatch check --json` (flag after the command) was rejected by argparse with a usage
  error, which looked exactly like "no tasks". Agents write it that way far more often than
  `--json check`. The flag is now accepted in both positions; anything after a literal `--`
  is left alone so a wrapped command keeps its own flags.

Found by a 6h45m soak run, where the harness's own `check --json` call silently returned
nothing for the whole duration.

60 tests.

## 0.2.1 — crash injection

### Fixed
- **An attempt was lost when a claimer died mid-claim.** `tries` is now written to the
  pending file *before* the move into `processing/`, so a process killed between the move
  and the lease write leaves a task that still records the attempt.
- **`reap` wrote `null` into `prev_owners`** when recovering an orphan (a task that never
  had a lease, so there was no previous owner to record).

### Changed
- Claim tokens count as stale after **5 s** instead of 30 s. A claim holds its token for
  milliseconds, so this only bounds how long a hard-killed claimer can block the task.
- New test seam `KEEPWATCH_CRASH_AT=after_move`: the claimant dies exactly between the
  move and the lease write. 5 tests drive it through a real subprocess.

## 0.2.0 — make failure visible

### Added
- `doctor` — nine deterministic checks (layout, boxes, task parsing, duplicate ids across
  boxes, orphaned processing files, expired leases, stale claim tokens, ledger parsing,
  heartbeat parsing). Exit 0 when healthy, 1 when something is broken; `--quiet` hides
  passing checks.
- `checkpoint` — writes `CHECKPOINT.md`: per-agent counts, heartbeat age, tasks in progress
  with an `EXPIRED` marker, the front of each queue, the last ten ledger records, and the
  commands to resume. `--out` to choose the path.
- `pipeline --on|--off [--note]` — a gate in `state/gate.json`. While closed, `send` and
  `claim` refuse with **exit 14**. Nothing is deleted; the work waits.
- `status` now reports `pipeline_enabled`.

### Changed
- 35 → 51 tests.
- `docs/PROTOCOL.md` §9 documents the gate, `doctor` and `checkpoint`; exit code 14 added.

## 0.1.1 — renamed to keepwatch

The project was called `nightshift` in 0.1.0. That name is taken on PyPI by an unrelated 2021
tool for plotting NMR spectra, so the project, the package, the CLI and the repository were
renamed. Behaviour is unchanged.

### Changed
- package `nightshift` → `keepwatch`; CLI `nightshift` → `keepwatch`
- env var `NIGHTSHIFT_ROOT` → `KEEPWATCH_ROOT`
- repository `ReZeroStarting/nightshift` → `ReZeroStarting/keepwatch` (GitHub redirects the
  old URL)
- docs rewritten in English, with the Chinese originals under `docs/zh/`

## 0.1.0 — first working version

First release. Not published to PyPI yet; install from the repository.

### Added
- `init` — create the three boxes (`pending` / `processing` / `done`) per agent
- `send` — hand a task to another agent
- `check` — list tasks that can be picked up
- `claim` — take a task and start a lease (`--ttl`, default 300s)
- `renew` — extend your own lease
- `done` — close a task, **refused unless a `verify ... --rc 0` record exists** (`--force` to override)
- `verify` — append a check result to the ledger
- `reap` — return expired leases (and orphaned files) to `pending`, keeping `tries` and `prev_owners`
- `run` — claim, execute a command, record its real exit code, then close (rc=0) or release
  (rc!=0); stops after `--max-tries` and leaves a `suspect` record
- `release` — put a claimed task back to pending without closing it
- `status` — per-agent counts, heartbeat age, and a `next:` list of what needs attention
- append-only audit ledger at `audit/YYYY-MM.jsonl`
- `--json` on every command, and semantic exit codes (0/1/2/3/13)
- 28 tests, including a 16-thread claim race and a "worker died" recovery path

### Notable
- Claiming is guarded by `O_CREAT|O_EXCL`, **not** by rename. On one Windows
  filesystem two concurrent `os.replace()` calls on the same source both reported
  success, which let two workers claim one task. See `docs/PROTOCOL.md`.
- Tool output is English on purpose (portable across console code pages). Docs are in
  English, with the original Chinese under `docs/zh/`.
- Docs describe what the code does. Capabilities that are only designed (message types,
  depth/trace, gates, checkpoint, dependencies) are listed as "not implemented yet".
