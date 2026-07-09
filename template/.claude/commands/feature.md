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

**Every pipeline dispatch carries a `FEATURE: <slug>` header line** (specialists
add `TASK-ID:` too — see §4). That header is what the **requirements gate** keys
on: before context research, planning, or any implementation, it checks that the
intention is unambiguous, the requirements are structured (Intent & Scope, edge
cases, non-functionals, and AC1/AC2… acceptance criteria — no TBD/??? markers),
the flow ran in order, and the plan covers exactly the acceptance criteria. If it
blocks, **re-enter interview mode** (discovery-interview skill), fix
`discovery.md` / `plan.md` with the user, and retry — these conditions route to a
human, never to a guess.

## 2 — Context research (subagent)
Dispatch **context-researcher** (prompt begins `FEATURE: <slug>`). It returns
only `context-pack.md` (~2k tokens), sectioned per work area (Rule 2).

## 3 — Plan + contracts (subagent)
Dispatch **planner** (prompt begins `FEATURE: <slug>`). It writes `contracts/`
first, then `plan.md` — each task tagged with the acceptance criteria it
`satisfies:` and a token estimate — and its last action runs `extract-deps.py` to
produce `task-graph.json`. Every AC from discovery must be covered by some task,
and no task may invent scope (an AC not in discovery); the requirements gate and
`approve-plan.py` both enforce this.

## 3.5 — Draft-plan approval (main thread; the autonomy boundary)
This is the second and final synchronous human point. Present the **draft plan
package** to the user: tasks + footprints, contracts, the computed task-graph
(what runs parallel vs sequential and why), and the token budget. The user
approves, edits, or rejects. What they are signing is not "requirements sound
right" but **"the decomposition is right."**

On approval, record the signature:

```
python .claude/scripts/approve-plan.py <slug> --budget <approved_tokens>
```

`dispatch-gate.py` (G1) refuses every specialist dispatch until
`specs/<slug>/approvals.json` exists **and its hash matches `plan.md`**. Any later
edit to `plan.md` breaks the hash and re-freezes automation — re-present and
re-approve. Do not hand-write `approvals.json`; use the script so the hash matches.

**Headless / autonomous mode.** When the outer driver runs you under
`agent-harness loop --auto-approve`, the environment variable
`HARNESS_AUTO_APPROVE=1` is set and there is no human to interview or sign the
plan. In that mode: proceed through discovery from the queue entry's description
(and any `discovery` block it carries), then — instead of waiting — approve the
plan yourself by running `approve-plan.py` with the planner's summed token
estimate as the budget. This is a logged decision (the approval records the
approver and timestamp), not a silent bypass; everything downstream — dispatch
order, retry caps, the circuit breaker — still enforces. If discovery is
genuinely ambiguous, do NOT self-approve: write the open question into
`discovery.md` and let the feature reach the `needs_approval` terminal state so
the driver parks it for a human.

## 4 — Dispatch per the computed schedule
Read `task-graph.json`. Dispatch specialists in edge order; run tasks with
**literally disjoint** footprints in parallel (same message, multiple Task calls).
`dispatch-gate.py` enforces approval, order, retry caps, graph freshness, the
circuit breaker, and a context-size sanity check — you do not override it.

**Context Rule 1 — every dispatch prompt is task-scoped.** Begin each Task prompt
with the header the gate keys on, and include ONLY that task's slice — never the
full plan, discovery, sibling tasks, or history:

```
TASK-ID: <task-id>
FEATURE: <slug>

<the task's entry from plan.md>
<the task's slice of contracts/>
<the task's section of context-pack.md>
```

**Rule 3 — retries are stateless.** A retry is a brand-new subagent given the same
task + contract slice PLUS the latest `review.<task>.<n>.json` findings and the
failed code *as a diff to correct* — never the prior attempt's reasoning. Seeing
its own failed chain of thought makes an agent defend a wrong approach.

After each specialist, dispatch **reviewer** (per-task). Failure handling (the
reviewer's `failure_class` decides; counters live in `state.json`, maintained by
the hook):
- `mechanical` → re-dispatch with findings (cap 2 retries).
- `contract` → re-dispatch once; a second failure means the contract is ambiguous
  → escalate.
- `ambiguity` / `security` → **zero retries**; escalate immediately.
- `repeat_finding: true` → escalate now even with retries left.

**The circuit breaker can trip mid-run.** If `state.json` `health` becomes
`"tripped"` (retry cap, repeat finding, too many escalations, or a footprint
violation caught by `divergence-monitor.py`), the gate refuses ALL dispatch. Your
only legal move is to escalate with the evidence in `state.json` /
`divergence.json` — the human is summoned by evidence, never by your own guess
that something feels off.

**Escalation = structured re-entry to interview mode.** Present the failure class,
the conflicting constraint, and attempts made; ask the user ONE specific question.
**Append the answer to `discovery.md`**, patch the plan (which requires
re-approval, §3.5), let counters reset, resume. An escalation must produce an
artifact update, not a chat apology.

## 5 — Integration review
When every task is `done`, dispatch **reviewer** in integration mode over the
combined diff. Only a passing `review.integration.*.json` (with a matching
`changeset`) opens the commit gate.

## 6 — Version the feature (Version Controller)
Before committing, close out versioning for the feature:
- **Dependencies:** if `specs/<slug>/dependencies.json` shows `has_major`, ensure
  the reviewer scrutinized it, then `python .claude/scripts/version.py ack-deps <slug>`.
- **Code:** `python .claude/scripts/version.py bump <slug>` — bumps `VERSION` per
  the plan's `VERSION-IMPACT` and writes the `CHANGELOG.md` entry.
- **Development:** `python .claude/scripts/version.py snapshot <slug> integrated`
  — records the artifact history at integration.

## 7 — Commit
Attempt the commit / PR. Two gates must open: `commit-gate.py` (a passing
integration review for this diff) **and** `version-gate.py` (major deps
acknowledged, code version + changelog recorded, development history captured).
Present the PR backed by the full artifact chain in `specs/<slug>/` — discovery,
plan, contracts, reviews, dependency deltas, version bump, and history — not a
bare diff.
