# DevelopmentUpdates.md

## Loop Engineering — Design Delta & Creation Plan

**Status:** Approved design addendum — extends `Architecture.md` v1.0
**Companion documents:** `Architecture.md`, `Implementation.md`, `Usecase.md`
**Scope of this update:** the automation loop (continuous feature processing), context management as enforced invariants, the extended human gate (plan approval), and the divergence circuit breaker.

---

## 1. What Changed Since v1.0

The base design defined a single-feature pipeline. This update turns it into a **continuous loop workflow** that automates development across a feature queue, and answers the failure mode the loop introduces: **wrong implementations caused by context distractors and context overflow.**

Four decisions were made and locked:

| # | Decision | One-line rationale |
|---|---|---|
| D-1 | Disk is the source of truth; context windows are disposable caches | Anything reconstructable from `specs/` can be safely thrown away — the unlock for looping |
| D-2 | Human gate extends through **draft plan approval** (one synchronous session per feature) | Wrong implementations are born at decomposition, not at requirements — sign off where they're born |
| D-3 | Divergence is detected **mechanically by hooks reading disk**, never by agent self-assessment | A diverged context cannot notice its own divergence; the disk rats it out |
| D-4 | Token burndown is budgeted at plan approval and tracked live | Context bloat *is* token burn — cost control and divergence detection are the same instrument |

---

## 2. The Five Context Rules (Enforced Invariants, Not Guidelines)

These are the "trill-down" mechanics. Each rule names its enforcement point — if a rule has no enforcement point, it is not in this list.

**Rule 1 — One task, one fresh context.**
A specialist's dispatch prompt contains exactly four inputs: its task entry from `plan.md`, its `contracts/` slice, its **task-scoped slice** of the context pack, and (retry only) the latest findings. Never the full plan, never sibling tasks, never discovery, never history.
*Enforcement:* dispatch prompt template in the orchestrator's harness instructions + dispatch-gate sanity check on prompt size.

**Rule 2 — Progressive compression at every stage boundary.**
Each stage consumes a compressed artifact, never raw upstream output:

```
Codebase (millions of tokens)
  → context-pack.md (~2k)              [compression 1]
    → plan.md task entries (~200 ea)   [compression 2]
      → code + 5-line summary          [compression 3]
        → review.json (~100)           [compression 4]
```

The orchestrator lives only on the right side of the funnel: it never reads source code, raw Graphifyy output, or full diffs. The context pack is structured with **per-area sections keyed to tasks** so each specialist receives only its slice — feature-wide context inside a task-scoped window is itself a distractor.
*Enforcement:* artifact token-size checks (`PostToolUse`) against budgets in `harness.config.json`; a 9k-token context pack is a bug, not a bonus.

**Rule 3 — Retries are stateless and rehydrate from disk.**
A retry is a **brand-new subagent** receiving task + contract + findings — never the previous attempt's reasoning. Failed reasoning is the strongest distractor in the system: an agent that sees its own prior chain of thought defends its wrong approach instead of rethinking. It sees the failed code only as a diff to correct.
*Enforcement:* retry dispatch template; `state.json` counters (already on disk) make retries stateless by construction.

**Rule 4 — The loop unit is one feature = one session; sessions end on purpose.**
No eternal session chewing a backlog — cross-feature residue is the cross-feature distractor. Within a session, the `PreCompact` hook protects only the load-bearing minimum (current task id, phase, pointer to `specs/<f>/`); everything else compacts freely because it can be re-read from disk. The pipeline is made *indifferent* to compaction, not defended from it.
*Enforcement:* outer driver (see §4) + `PreCompact` hook registration.

**Rule 5 — Budget and measure, or the rules decay.**
Per-stage token budgets live in config; `trace-emitter.py` tracks actuals; the derived metric **tokens-per-passing-task** per specialist is the leak detector. When it creeps up, a compression stage is leaking and the trace shows which one. Context management becomes a monitored SLO, not vigilance.
*Enforcement:* trace-emitter + Langfuse dashboards; burndown breach feeds the circuit breaker (§5).

---

## 3. The Extended Human Gate — Interview Through Plan Approval

The single synchronous phase per feature, on the main thread:

```
/feature
  → interview: main flow + edge-case detection protocol (discovery-interview skill)
  → context-researcher runs (human waits ~minutes)
  → planner drafts plan.md + contracts/ + invokes extract-deps.py
  → orchestrator presents the DRAFT PLAN PACKAGE to the human:
        tasks + footprints · contracts · computed task-graph · token budget
  → human approves / edits / rejects
  → specs/<f>/approvals.json written: { plan_hash, budget, timestamp, approver }
```

**The autonomy boundary is a signed hash.** `dispatch-gate.py` blocks every specialist dispatch unless `approvals.json` exists **and its hash matches the current `plan.md`**. Any post-approval plan edit → hash mismatch → automation freezes → re-approval required. The gate cannot be talked around, and stale approvals cannot leak through.

What the human is actually signing: not "requirements sound right" but **"the decomposition is right"** — tasks, footprints, contracts, schedule, and cost. This is where the human and system work closely to surface what is really needed; after the signature, automation owns everything until a terminal state.

---

## 4. The Task Workflow Loop (Automation Layer)

```
                ┌──────────────────────────────────────────────┐
                │              FEATURE QUEUE                    │
                │   (tickets / issues / backlog items)          │
                └───────────────┬──────────────────────────────┘
                                ▼
                ┌──────────────────────────────────────────────┐
                │  OUTER DRIVER (script / claude -p headless)   │
                │  pulls next feature → starts FRESH session    │
                └───────────────┬──────────────────────────────┘
                                ▼
   HUMAN ──synchronous──► Interview + Plan Approval (§3)
                                ▼
                ┌──────────────────────────────────────────────┐
                │  AUTOMATED PIPELINE (per Architecture.md §9)  │
                │  dispatch per task-graph → specialists →      │
                │  per-task review → retries (stateless) →      │
                │  integration review → commit gate             │
                │        [circuit breaker armed throughout, §5] │
                └───────────────┬──────────────────────────────┘
                                ▼
              ┌────────────── TERMINAL STATE (state.json) ──────────────┐
              │                                                          │
        "committed"          "escalated"                "blocked"        │
              │           (specific question         (cap / breaker hit, │
              │            for the human)             human review)      │
              ▼                     ▼                        ▼            │
        next feature      HUMAN-ANSWER QUEUE          human resolves     │
                           (async — driver moves on)        │            │
                                    │                       │            │
                       answer appended to discovery.md ◄────┘            │
                                    ▼                                    │
                       feature RE-ENTERS queue; fresh session            │
                       resumes FROM ARTIFACTS (D-1 makes this work)      │
              └──────────────────────────────────────────────────────────┘
```

Key properties: exactly three terminal states, escalations **park** a feature instead of stalling the loop, and resumption is always a fresh session rehydrated from `specs/` — never a revived context. Humans are consumed at exactly two points (interview/approval, escalation resolution) and the second one is asynchronous.

---

## 5. Divergence Circuit Breaker

Triggers for human intervention — ambitious scope, redirects, goal diversion, runaway loops — detected mechanically:

| Signal | Source | Default threshold |
|---|---|---|
| Retry cap hit | `state.json` counters | per failure-class caps (existing) |
| Repeat finding | reviewer diffs own findings across attempts | 2 identical → escalate early (existing) |
| **Footprint violation** | `PostToolUse` on Edit/Write: file outside task's declared footprint | 1 = warn, 2 = halt |
| **Plan churn** | `plan.md` hash vs `approvals.json` | any mismatch = freeze |
| **Escalation count** | escalations this feature | >2 = "feature is ambitious" → stop, re-scope |
| **Token burndown breach** | trace-emitter cumulative vs approved budget | 80% = warn, 100% = halt |

The **footprint violation** is the sharpest goal-diversion detector: the approved plan *declared* what each task touches, so any write outside that set is, by definition, implementation wandering from the approved plan — divergence made measurable, ~5 lines of hook because footprints already sit in `task-graph.json`.

`divergence-monitor.py` aggregates signals into a `health` field in `state.json`; on trip, `dispatch-gate` refuses all dispatches and the orchestrator's only legal move is escalation. **The human is summoned by evidence, never by the agent's self-assessment.**

---

## 6. Creation Plan — Build Phases for the Loop Layer

Extends `Implementation.md` (base Phases 0–5 unchanged). Build in this order; each phase is independently demo-able.

### Phase L1 — Plan-approval gate  *(prerequisite for everything else)*
Build: `approvals.json` writer in the `/feature` flow; hash check added to `dispatch-gate.py`; draft-plan presentation format (tasks, footprints, contracts, graph, budget) in orchestrator instructions.
Accept: editing `plan.md` post-approval freezes all dispatches until re-approval.

### Phase L2 — Context invariants
Build: dispatch prompt templates (Rule 1, Rule 3 retry variant); per-area sectioning requirement in context-researcher's agent file; artifact size checks (`PostToolUse`) against `budgets` in config; `PreCompact` hook protecting the load-bearing minimum.
Accept: (a) oversized context pack flagged; (b) a retry demonstrably contains no prior-attempt reasoning (inspect the dispatched prompt in the trace); (c) forced compaction mid-pipeline → pipeline completes correctly from disk.

### Phase L3 — Divergence monitor + circuit breaker
Build: `divergence-monitor.py` (footprint-violation hook on Edit/Write, escalation counter, burndown breach ingest); `health` field in `state.json`; dispatch-gate integration; thresholds in config.
Accept: scripted scenario where a specialist writes outside its footprint twice → all dispatches blocked → escalation is the only legal move.

### Phase L4 — Outer driver + queue
Build: `harness-loop` driver (queue file or issue-tracker adapter → `claude -p` fresh session per feature); terminal-state protocol in `state.json`; human-answer queue; resume-from-artifacts flow (fresh session, rehydrate from `specs/<f>/`, counters/approval intact).
Accept: 3-feature queue where feature 2 escalates → driver completes 1 and 3, feature 2 parks → human answers → feature 2 resumes in a fresh session and commits.

### Phase L5 — Burndown + flywheel metrics
Build: per-task token estimates emitted by planner into the approval package; trace-emitter budget tracking; Langfuse dashboards — per-feature burndown, tokens-per-passing-task per specialist, estimate-vs-actual by task type.
Accept: one feature run shows live burndown; a deliberately leaky context slice shows up as a tokens-per-passing-task spike attributable to one compression stage.

### Config additions (`harness.config.json`)

```json
{
  "budgets": {
    "context_pack": 2000,
    "task_entry": 300,
    "review_findings": 500,
    "per_task_default": 15000
  },
  "circuit_breaker": {
    "footprint_violations": { "warn": 1, "halt": 2 },
    "max_escalations_per_feature": 2,
    "burndown": { "warn_pct": 80, "halt_pct": 100 }
  },
  "loop": {
    "queue": "specs/_queue.json",
    "answer_queue": "specs/_answers.json",
    "headless": true
  }
}
```

---

## 7. Definition of Done (Loop Layer)

The loop layer is done when: a queued feature runs end-to-end with the human involved only at interview + plan approval; a post-approval plan edit freezes automation; a footprint violation trips the breaker and summons the human with evidence; an escalated feature parks without stalling the queue and later resumes from artifacts in a fresh session; forced mid-pipeline compaction does not change the outcome; and Langfuse shows per-feature burndown plus tokens-per-passing-task trending per specialist. At that point, context management is no longer vigilance — it is an enforced, monitored property of the system.
