# Roadmap

> 中文版：[docs/zh/ROADMAP.md](zh/ROADMAP.md)

## Principles

1. **Protocol before implementation.** `docs/PROTOCOL.md` is the contract; code is one way
   to satisfy it.
2. **One kind of capability per release**, always with tests.
3. **Zero third-party dependencies, forever.** CI installs with `pip install --no-deps`.
4. No UI, no HTTP, no cloud.

## v0.1 — usable (✅ shipped)

- [x] package layout: `core` + `cli`
- [x] commands: `init` `send` `check` `claim` `renew` `release` `run` `done` `verify`
      `reap` `status`
- [x] `run`: claim → execute → record the real exit code → close on 0, release otherwise,
      mark `suspect` after `--max-tries`
- [x] heartbeat written by every command that names an agent
- [x] leases: claim, TTL, expiry, reclaim with `tries` / `prev_owners`
- [x] `done` refused without a `verify … rc=0` record (`--force` overrides)
- [x] exit codes 0/1/2/3/13, `--json` on every command
- [x] 35 tests: a 16-thread claim race, a killed-worker recovery, orphan files, bad ids
- [x] `examples/two_workers.py`: a worker dies, another picks the task up
- [x] released as v0.1.0 with a GitHub release

**Acceptance:** a stranger can `pip install -e .` and run the demo in three minutes.

## v0.2 — make failure visible (✅ shipped)

- [x] `doctor` — nine deterministic checks, exit 0/1
- [x] `checkpoint` — writes `CHECKPOINT.md`: what is stuck, what to run next
- [x] `pipeline --on|--off` — one gate; `send`/`claim` return exit 14 while closed
- [x] 51 tests (16 new): corrupt task files, duplicate ids, orphans, stale tokens, the gate
- [ ] dependencies (`--after`) and a `hold` queue
- [ ] crash-injection test: kill the process between claim and lease write
- [ ] one real overnight unattended run, recorded in `docs/field-notes.md`

**Acceptance:** kill the worker at any point; within one TTL the system says what died and
what is next.

## v0.3 — interoperate

- [ ] adapter: use agent-mailbox's MCP mailbox as the transport
- [ ] adapter: plain directory convention (any home-grown mailbox)
- [ ] `migrate` from older layouts

## v1.0 — trustworthy

- [ ] protocol frozen as `docs/PROTOCOL-v1.md`
- [ ] conformance test suite so a second implementation can be checked
- [ ] one external user running it in a real project for two weeks

## Release checklist

- [ ] PyPI name is free
- [ ] `LICENSE` and `pyproject.toml` metadata complete
- [ ] the README says what this is, and why it is needed, in the first ten seconds
- [ ] CI green: Linux 3.9 / 3.12 / 3.13, Windows 3.12 / 3.13
- [ ] `CHANGELOG.md` has an entry for this version
- [ ] `git tag vX.Y.Z` + GitHub release
- [ ] no `.bak`, no `__pycache__`, no local paths
