# Changelog

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
