# HookDevelopment.md

## Feature Development Spec — `dispatch-gate` + `PreCompact` Protection

**Implements:** `Implementation.md` Phase 2 (dispatch-gate) · `DevelopmentUpdates.md` Phase L1 (approval hash), L2 (PreCompact), L3 (breaker integration)
**Ships as:** three hook scripts + one settings registration block

---

## 1. The Design in One Paragraph

`dispatch-gate.py` is the single choke point through which every pipeline subagent dispatch must pass — it enforces the approval hash (autonomy boundary), schedule freshness, dependency order, retry caps, and the circuit breaker, all by reading disk and exiting 2. The PreCompact protection is deliberately **two** hooks, not one: `precompact-guard.py` snapshots the load-bearing minimum to disk *before* compaction (it cannot and should not block compaction), and `session-rehydrate.py` injects that snapshot back into context on `SessionStart(source=compact|resume)`. Snapshot-then-rehydrate makes the pipeline *indifferent* to compaction rather than defended from it — and gives the loop layer its resume-from-artifacts mechanism for free, because recovering from compaction and resuming a parked feature are the same operation.

---

## 2. Files

```
.claude/hooks/
├── dispatch-gate.py        # PreToolUse on Task — gates G0..G5
├── precompact-guard.py     # PreCompact — writes specs/<f>/pipeline-context.md
└── session-rehydrate.py    # SessionStart (compact|resume) — injects snapshot via stdout
```

## 3. Registration — `.claude/settings.json`

Merge this into the adopter's existing hooks arrays (installer appends with a
`"_harness": true` marker; never reorder existing entries):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Task",
        "hooks": [
          { "type": "command",
            "command": "python3 .claude/hooks/dispatch-gate.py" }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "auto",
        "hooks": [
          { "type": "command",
            "command": "python3 .claude/hooks/precompact-guard.py" }
        ]
      },
      {
        "matcher": "manual",
        "hooks": [
          { "type": "command",
            "command": "python3 .claude/hooks/precompact-guard.py" }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          { "type": "command",
            "command": "python3 .claude/hooks/session-rehydrate.py" }
        ]
      },
      {
        "matcher": "resume",
        "hooks": [
          { "type": "command",
            "command": "python3 .claude/hooks/session-rehydrate.py" }
        ]
      }
    ]
  }
}
```

---

## 4. Contracts the Hooks Depend On

These conventions are load-bearing; the agent files and state-updater must honor them.

**Dispatch prompt header (orchestrator → Task tool).** Every pipeline dispatch prompt begins:

```
TASK-ID: backend-orders-api
FEATURE: order-cancellation
```

Prompts without the header are treated as non-pipeline usage and pass through ungated (G0) — the harness governs the pipeline, not all Claude Code use in the repo.

**`specs/_active.json`** — `{ "feature": "<name>" }`, maintained by state-updater when a pipeline starts/ends. Both compaction hooks key off it; absent file = nothing in flight = hooks no-op.

**`state.json` fields consumed here** (written only by `state-updater.py`, never by agents):

```json
{
  "phase": "execution",
  "current_task": "backend-orders-api",
  "health": "ok | tripped",
  "health_reasons": ["footprint_violation x2"],
  "tasks": {
    "backend-orders-api": {
      "status": "pending | in_progress | done | failed",
      "attempts": 1,
      "pending_failure_class": "mechanical"
    }
  }
}
```

**`approvals.json`** — `{ "plan_hash": "<sha256 of plan.md bytes>", "budget": 90000, "timestamp": "...", "approver": "..." }`. Written only by the `/feature` approval step.

---

## 5. Gate Semantics (dispatch-gate)

| Gate | Check | Block message instructs orchestrator to… |
|---|---|---|
| G0 | `TASK-ID`/`FEATURE` header present? | (pass through if absent — not a pipeline dispatch) |
| G1 | `approvals.json` hash == sha256(`plan.md`) | present revised plan to the user, obtain re-approval |
| G2 | `task-graph.json` exists and mtime ≥ `plan.md` | re-run `extract-deps.py` |
| G3 | `state.health != "tripped"` | escalate with evidence; only legal move |
| G4 | all upstream edges `done` | dispatch in task-graph order |
| G5 | attempts ≤ cap for `pending_failure_class`; ambiguity/security = 0 | stop retrying; escalate with latest findings |

Ordering is deliberate: cheapest and most fundamental first, so a frozen plan or tripped breaker short-circuits before any graph math. Every block message names the orchestrator's *legal next move* — a gate that says only "no" invites the orchestrator to improvise around it; a gate that says "no, do X instead" steers the loop.

Fail-closed policy: unreadable/corrupt `approvals.json`, `task-graph.json`, or `plan.md` → block. The one deliberate fail-open is G0: no header, no gating, so the harness never breaks ordinary Claude Code usage in the same repo.

---

## 6. PreCompact Semantics

What is protected (the load-bearing minimum, verbatim from Rule 4): **current feature (pointer to `specs/<f>/`), current task id, current pipeline phase** — plus breaker health, since a compaction must never launder a tripped breaker out of awareness. What is deliberately *not* protected: plan contents, review findings, diffs, prior reasoning — all reconstructable from `specs/`, and standing order #1 in the snapshot explicitly tells the post-compaction orchestrator to distrust compacted recollections and re-read disk.

Two invariants:

1. `precompact-guard.py` **always exits 0**. A broken guard must never break compaction; worst case is a missing snapshot, which `session-rehydrate.py` degrades into a manual-rehydration instruction.
2. Rehydration only fires on `source: compact|resume`. Fresh interactive sessions start clean **on purpose** — auto-injecting pipeline state into every new session would contaminate unrelated work, the exact distractor pattern this harness exists to kill.

---

## 7. Test Matrix

| # | Scenario (feed recorded hook JSON on stdin) | Expected |
|---|---|---|
| T1 | Task dispatch, no header | exit 0 (ungated) |
| T2 | Header present, no `approvals.json` | exit 2, re-approval message |
| T3 | `plan.md` edited after approval | exit 2, hash-mismatch freeze |
| T4 | `task-graph.json` older than `plan.md` | exit 2, regenerate message |
| T5 | `health: "tripped"` in state | exit 2, escalate-only message |
| T6 | upstream edge not `done` | exit 2, names unmet tasks |
| T7 | `mechanical`, attempts=3, cap=2 | exit 2, stop-retrying message |
| T8 | `ambiguity`, attempts=0 | exit 2, immediate escalation |
| T9 | all gates green | exit 0 |
| T10 | PreCompact with active feature | snapshot written, exit 0 |
| T11 | PreCompact, no `_active.json` | no write, exit 0 |
| T12 | PreCompact, unwritable specs dir | stderr warning, still exit 0 |
| T13 | SessionStart `source: compact`, snapshot exists | snapshot on stdout |
| T14 | SessionStart `source: startup` | no output |
| T15 | SessionStart `source: resume`, snapshot missing | manual-rehydration fallback text |

End-to-end acceptance (matches DevelopmentUpdates L2): force a compaction mid-pipeline → pipeline completes with the same commits/artifacts as an uncompacted run.

---

## 8. Known Follow-ups

The `pending_failure_class` and `current_task`/`phase` fields make **`state-updater.py` the next build item** — these hooks consume state they don't produce. G2's mtime check is a pragmatic v1; if adopters hit filesystem-timestamp edge cases (git checkouts normalize mtimes), upgrade to storing `plan_hash` inside `task-graph.json.generated_from` and comparing hashes, consistent with G1. And when `divergence-monitor.py` lands (Phase L3), it only needs to write `health`/`health_reasons` — G3 is already wired to obey it.
