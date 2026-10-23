# Why this project exists

> 中文版：[docs/zh/COMPETITION.md](zh/COMPETITION.md)

This document answers one question: **what is this project for, that something else is not
already doing better?**

Short version: **"a mailbox between agents" is taken. "Reliability when nobody is around"
is still open — and the incumbent gave it away on purpose.**

## 1. The direct neighbour: agent-mailbox

- PyPI `agent-mailbox`, v0.8.2 (2026-10-10), Apache-2.0
- 0.5.0 → 0.8.2 in three weeks, still moving fast
- shape: local MCP mailbox + local web workbench + macOS app (Windows/Linux preview)

### What it does

1. project-scoped mailboxes, each agent connected over stdio MCP
2. explicit acceptance: reading a message is not accepting the task
3. pinned resource versions: the agent reads exact version ids
4. human review: a submission parks as "awaiting your decision"
5. works across hosts: Claude Code, Codex, DeepSeek Harness, Workbuddy, Doubao, ZCode, Hermes

### What it explicitly does not do

From their own README:

> *"Loading MCP tools does not itself wake an idle conversation. Ask the agent to check its
> mailbox, or configure a host-specific wake hook."*
>
> *"Notification is a separate connection… A successful notification does not mean the agent
> accepted or completed the task."*

In other words: **waking, liveness, reaping and resuming are all pushed onto the host.**

### Side by side

| | agent-mailbox | nightshift |
|---|---|---|
| transport | MCP over stdio | plain directories |
| task semantics | explicit accept | claim + lease |
| where the human is | in the loop, deciding | **not present** |
| liveness | none | heartbeat + threshold |
| stuck-task recovery | none | lease expiry + reap with a hand-over trail |
| resume after a restart | none | `run` retries; `status` shows what is left |
| dependencies | mcp, cryptography, … | none |

**Complementary, not competing.** A transport adapter for agent-mailbox is on the roadmap.

## 2. Neighbours

| Project | Shape | Relationship |
|---|---|---|
| [A2A](https://github.com/ai-boost/awesome-a2a) | HTTP/JSON-RPC interop | a network standard; does not address "nobody is working" |
| [Temporal](https://temporal.io) + [LangGraph plugin](https://temporal.click/blog/temporal-langgraph-plugin-durable-execution) | durable execution server | the industrial answer, but needs a server and SDK code. This project is the zero-dependency reduction, aimed at agents that only speak CLI |
| [agent-watchdog](https://pypi.org/project/agent-watchdog/) | in-process circuit breaker (loops, budget, timeout) | guards **one run in one process**; this project guards **across processes and hours** |
| [mailbox-mcp](https://github.com/EllGree/mailbox-mcp) | MCP wrapper over `.claude/inbox` | mail primitives only, phase 1 |
| [parallel-worktree](https://github.com/sh5623/parallel-worktree) | parallel worktrees + harvest | solves parallel writing, not lost messages |
| AutoGen / CrewAI / MetaGPT | in-process multi-agent orchestration | do not handle process boundaries or crashes |

## 3. The gap this project claims

1. **Naming silent failure** — distinguishing idle from dead from stuck from "done but never
   acknowledged". The four need different responses, and only one of them is urgent.
2. **Reclaim with a trail** — not a blind retry: `tries` and `prev_owners` travel with the
   task, so the next worker can see it has been attempted before.
3. **Recovery without a human** — a task whose lease expired comes back on its own.
   (A `checkpoint` resume note is planned, not built.)
4. **Guarantees live in the tool, not in the prompt** — a rule written in an agent's system
   prompt disappears when you switch agents. A rule enforced by the CLI does not.

## 4. Naming

Already taken: `agentbus`, `agent-mailbox`, `agent-watchdog`, `deadman`, `mailbox-mcp`.

Candidates (must be checked on both PyPI and GitHub before any release):

- `nightshift` (working name; "unattended work happens at night")
- `mailbox-ops`
- `silent-failure`
- `steward`

## 5. Risks

- If agent-mailbox adds wake-ups and leases, the gap narrows. Answer: stay small, stay
  embeddable, stay interoperable.
- One maintainer. Answer: protocol first, stable CLI, never build a UI.
