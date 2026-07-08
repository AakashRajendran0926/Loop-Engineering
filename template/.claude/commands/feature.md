---
description: Start a harness feature — runs the discovery interview on the main thread, then drives the context → plan → implement → review → gate pipeline.
argument-hint: <feature description>
---

# /feature $ARGUMENTS

You are the **orchestrator** (main thread). `/feature` is the single explicit
opt-in to the pipeline — ordinary Claude Code usage in this repo is untouched.
Tiny changes ("fix this typo") should NOT use this; the harness is for features.

Feature slug: derive a short kebab-case slug from "$ARGUMENTS" (e.g.
`order-cancellation`). All artifacts live in `specs/<slug>/`.

## 0 — Preflight: graphify must be initialized
Retrieval is graph-first. Check for `graphify-out/graph.json`:
- **Missing** → initialize now: run `/graphify .`. The skill self-installs
  (uv/pip) and builds the graph. Do this before dispatching the context-researcher
  — a subagent cannot build the graph itself (it can't spawn subagents).
- **Present** → continue.

## 1 — Discovery interview (main thread; you, not a subagent)
Load the `discovery-interview` skill and interview the user protocol-driven:
edge cases, non-functionals, acceptance criteria. Subagents can't talk to the
user, so this must happen here, now. Terminate by writing
`specs/<slug>/discovery.md`. The user is done until an escalation needs them.

## 2 — Context research (subagent)
Dispatch **context-researcher**. It returns only `context-pack.md` (~2k tokens).

## 3 — Plan + contracts (subagent)
Dispatch **planner**. It writes `contracts/` first, then `plan.md`, and its last
action runs `extract-deps.py` to produce `task-graph.json`.

## 4 — Dispatch per the computed schedule
Read `task-graph.json`. Dispatch specialists in edge order; run tasks with
**literally disjoint** footprints in parallel (same message, multiple Task calls).
`dispatch-gate.py` enforces order, retry caps, and graph freshness — you do not
override it with prose. After each specialist, dispatch **reviewer** (per-task).

Failure handling (the reviewer's `failure_class` decides; counters live in
`state.json`, maintained by the hook):
- `mechanical` → re-dispatch with findings (cap 2 retries).
- `contract` → re-dispatch once; a second failure means the contract is ambiguous
  → escalate.
- `ambiguity` / `security` → **zero retries**; escalate immediately.
- `repeat_finding: true` → escalate now even with retries left.

**Escalation = structured re-entry to interview mode.** Present the failure class,
the conflicting constraint, and attempts made; ask the user ONE specific question.
**Append the answer to `discovery.md`**, patch the plan if needed, let counters
reset, resume. An escalation must produce an artifact update, not a chat apology.

## 5 — Integration review
When every task is `done`, dispatch **reviewer** in integration mode over the
combined diff. Only a passing `review.integration.*.json` (with a matching
`changeset`) opens the commit gate.

## 6 — Commit
Attempt the commit / PR. `commit-gate.py` allows it only now. Present the PR
backed by the full artifact chain in `specs/<slug>/`, not a bare diff.
