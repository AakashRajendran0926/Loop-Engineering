# Architecture.md

## Agent Harness for Brownfield Claude Code Development

**Version:** 1.0 (v1 scope: Claude Code only)
**Status:** Design finalized — ready for implementation

---

## 1. Purpose and Positioning

This harness turns Claude Code into a disciplined, observable, multi-agent development pipeline that can be adopted into an **existing (brownfield) codebase** without disrupting it. It is not a template repo and not a prompt pack. It is a set of enforced workflows built entirely on Claude Code's native primitives — subagents, skills, hooks, slash commands — plus two external integrations: **Graphifyy** (deterministic AST-based code knowledge graph, via CLI/MCP) and **Langfuse** (trace-based observability).

The core thesis: **policy lives in exit codes and artifacts on disk, not in prompts.** Agents exercise judgment; hooks and scripts enforce rules; files carry state. Anything an agent could "forget" or rationalize its way around is instead enforced deterministically.

### Design principles

1. **Agents do judgment. Scripts do computation.** Never let an LLM do what a deterministic script can (dependency math, retry counting, gate checks).
2. **Files, not messages, are the handoff medium.** Subagent return values get compacted; artifacts on disk are lossless and universally readable.
3. **The harness decides, not the orchestrator.** Retry caps, commit gates, and dispatch order are enforced by hooks reading state files — the orchestrator cannot override them with prose.
4. **Stay inside native directories.** All machinery lives in `.claude/`; the only new root directory is `specs/` (runtime artifacts). Adoption must be reversible.
5. **Every failure is training data.** Retries, escalations, and review verdicts are captured as labeled traces, feeding the evaluation flywheel.

### Explicit non-goals (v1)

Multi-framework support (GitHub Copilot, Antigravity) is deferred to v2 via `AGENTS.md`-floor adapters. Parallel specialist execution beyond disjoint-footprint tasks is deferred. Greenfield scaffolding is out of scope — this harness installs into repos that already exist.

---

## 2. Execution Model — What Claude Code Actually Provides

The design respects three hard constraints of Claude Code's subagent model:

**Subagents are fire-and-forget.** They run in isolated context windows, cannot hold interactive conversations with the user, and return a single result. Therefore, anything requiring user interaction (discovery interviews, escalations) runs on the **main orchestrator thread**.

**Subagents cannot spawn subagents.** There is no nesting. The pipeline is a flat sequence of dispatches conducted by the main thread, scheduled from an on-disk task graph — not a hierarchy.

**Context isolation is the primary resource.** Subagents burn tokens in their own windows and return compact summaries. The harness exploits this deliberately: the context-researcher may read 50k tokens of Graphifyy output and source code, but the orchestrator only ever sees a ~2k-token context pack.

---

## 3. The Agent Roster

| Agent | Type | Role | Reads | Writes |
|---|---|---|---|---|
| Orchestrator | Main thread | Interviews user, dispatches per task graph, escalates failures | Everything in `specs/` | Dispatch decisions (via Task tool) |
| context-researcher | Subagent | Expands Graphifyy queries + source reading into a compact brief | Codebase, `graphify query`, `discovery.md` | `context-pack.md` |
| planner | Subagent | Decomposes feature into tasks with file footprints; defines contracts | `discovery.md`, `context-pack.md` | `plan.md`, `contracts/`, invokes `extract-deps.py` |
| specialist-database | Subagent | Migrations, schema, data layer | `plan.md`, `contracts/`, graph-first retrieval | Code |
| specialist-backend | Subagent | Services, APIs, business logic | `plan.md`, `contracts/`, graph-first retrieval | Code |
| specialist-frontend | Subagent | UI, components, client state | `plan.md`, `contracts/`, graph-first retrieval | Code |
| reviewer | Subagent | Per-task review with failure taxonomy; final integration review | Diff, `plan.md`, `contracts/`, graph queries | `review.*.json` |

Specialists are deliberately narrow ("monotonic"): one domain, one contract, no cross-domain improvisation. A specialist that needs something outside its domain reports it as a finding rather than reaching across the seam — seams are owned by contracts and the integration review, not by individual specialists.

### Discovery is a phase, not an agent

The discovery interview runs on the main thread, entered via the `/feature` slash command and governed by the `discovery-interview` skill (edge cases, non-functionals, acceptance criteria protocol). It terminates by writing `specs/<feature>/discovery.md`. Subagents begin only after the human is no longer needed synchronously.

---

## 4. The Artifact Chain

The pipeline's real data structure is the `specs/<feature>/` directory. Agents are transient; artifacts are the pipeline.

```
specs/feature-x/
├── discovery.md          # Interview output: requirements, edge cases, acceptance criteria.
│                         # Appended on every escalation (escalations produce artifact updates).
├── context-pack.md       # Compressed codebase brief from the context-researcher (~2k tokens).
├── plan.md               # Task decomposition. Each task carries an explicit FILE FOOTPRINT.
├── contracts/            # Interface agreements written BEFORE any specialist runs:
│   ├── api.yaml          #   API schemas
│   ├── migration.md      #   DB migration shape
│   └── components.md     #   Component props / client contracts
├── task-graph.json       # Computed dependency graph (see §5). Tasks, footprints, edges.
├── state.json            # Runtime state: per-task status + per-specialist attempt counters.
│                         # Updated by hooks, never by orchestrator prose.
└── review.<task>.<n>.json  # Reviewer verdicts, one per attempt (see §7 schema).
```

**Contract-first planning** is the seam-integrity mechanism: `plan.md` and `contracts/` fix the interfaces before implementation, so specialists implement against agreements, never against each other's in-progress work.

---

## 5. Dependency-Ordered Scheduling (Hybrid Sequential/Parallel)

Execution order is neither fixed (DB→BE→FE) nor guessed. It is **computed**:

**Step 1 — Judgment (planner):** maps each task in `plan.md` to a file footprint ("task 3 touches `api/orders.ts` and `db/migrations/`").

**Step 2 — Computation (`extract-deps.py`):** expands each footprint through Graphifyy (imports, importers, shared dependents — default depth 1 hop, configurable), intersects expanded sets, and emits `task-graph.json`:

- Overlapping footprints, shared dependents, or contract consumption → **sequential edge**
- Fully disjoint expanded footprints → **parallel-safe**

**The hard parallelism rule:** two tasks may run concurrently only if their file footprints are *literally disjoint* — not "conceptually independent." Parallel subagents share one working tree; overlapping writes are silent corruption. When in doubt, the edge goes in: a false "dependent" costs minutes, a false "independent" costs a corrupted merge.

**Staleness protection:** the dispatch gate refuses to schedule if `task-graph.json` is missing or older than `plan.md`. Plan edited → graph must regenerate → hook enforces it.

Determinism dividend: same plan + same codebase → same schedule, every run. The edge logic is unit-testable — impossible if an agent computed it.

---

## 6. Hook Layer — Deterministic Enforcement

All hooks receive JSON on stdin (including `session_id`) and enforce via exit codes.

| Hook script | Event | Enforces |
|---|---|---|
| `commit-gate.py` | `PreToolUse` on Bash | Blocks `git commit` / `gh pr create` unless a passing **integration** review verdict exists for the current changeset |
| `dispatch-gate.py` | `PreToolUse` on Task | Blocks dispatch if (a) upstream edges in `task-graph.json` aren't done in `state.json`, (b) the task's retry counter is at cap, or (c) `task-graph.json` is stale vs `plan.md` |
| `retrieval-nudge.py` | `PreToolUse` on Grep/Glob | Redirects broad exploratory searches to `graphify query` first (retrieval order: **Graphifyy → skills → memory**) |
| `trace-emitter.py` | All lifecycle events | Maps hook events to OTel spans → Langfuse (see §8) |
| state updater | `SubagentStop` / `PostToolUse` on Task | Increments attempt counters and task status in `state.json` |

Relevant lifecycle events: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop`, `PreCompact`, `SessionEnd`. There is no `SubagentStart`; subagent span-open is approximated from the orchestrator's `PreToolUse` on the Task tool, span-close from `SubagentStop`.

The commit gate is the harness's signature feature: review approval is enforced by an exit code, not by prompt discipline. This is the line between a harness and a folder of markdown personas.

---

## 7. Failure Model — Taxonomy, Finite Retries, Escalation

### Failure taxonomy (classified by the reviewer)

| Class | Meaning | Retry policy |
|---|---|---|
| `mechanical` | Tests fail, lint/type errors, missing migration — fix derivable from the error | 2 retries (3 attempts) |
| `contract` | Implementation doesn't match `contracts/` | 1 retry; second failure signals an ambiguous contract → human problem |
| `ambiguity` | Intended behavior undeterminable from spec | 0 retries — retrying ambiguity generates confident guesses |
| `security` | Vulnerability finding | 0 retries by the authoring specialist; hard stop for human review |

### `review.json` schema

```json
{
  "task": "backend-orders-api",
  "attempt": 2,
  "status": "fail",
  "failure_class": "mechanical",
  "findings": [
    { "id": "F1", "severity": "error", "file": "api/orders.ts", "detail": "..." }
  ],
  "changeset": "<git ref or diff hash>"
}
```

### Enforcement mechanics

Retry counters live in `state.json` on disk — never in the orchestrator's context, where compaction could silently erase them and make a finite loop infinite. The dispatch gate reads the counter and **blocks the Task dispatch** at cap; the orchestrator does not get a vote.

**Repeated-finding early stop:** the reviewer diffs its own findings across `review.<task>.1.json`, `review.<task>.2.json`. The same finding twice means the specialist doesn't understand the fix — escalate immediately even if the counter has headroom. Two identical failures predict a third.

### Escalation = structured re-entry to interview mode

Escalation is native because discovery already lives on the main thread. The orchestrator presents the failure class, the conflicting constraint, attempts made, and asks one specific question. The answer is **appended to `discovery.md`** (it is new requirements information), the plan is patched if needed, counters reset, pipeline resumes. Escalation that produces an artifact update is a feature; escalation that produces a chat apology is a failure mode.

### Integration review — the cross-specialist gate

Individually approved work can still conflict at seams (renamed column vs. old response shape; incompatible edits to a shared utility). After all tasks complete, the reviewer runs once more over the **whole feature diff** (`git diff main...feature-branch`), checking exactly three inputs: (1) the combined diff, (2) `contracts/` conformance, (3) `graphify query` on touched files — did anyone change something other touched code depends on? Only a passing integration verdict opens the commit gate.

---

## 8. Observability — The Flywheel Has a Native Producer

Every hook event carries `session_id` → the Langfuse trace ID. `trace-emitter.py` (~150 lines of Python) maps:

- `PreToolUse`/`SubagentStop` on Task → subagent spans
- Individual tool calls → child spans
- Review verdicts → Langfuse **scores** attached to the trace
- Every `"status": "fail"` review → a dataset item in the regression set
- Escalations → labeled traces (failure class, attempts, resolution)

This closes the **trace → evaluate → dataset → fix → regression test** loop with a structural advantage: the reviewer agent is a native evaluation producer, so the "evaluate" step doesn't wait on human labeling. Retries are not just cost — captured properly, they are the regression suite writing itself. The optimization targets are the harness's own components: agents, skills, instructions, hooks, and retrieval behavior.

---

## 9. End-to-End Pipeline (Final)

```
/feature invoked
   │
   ▼
[MAIN THREAD] Discovery interview (discovery-interview skill)
   │  → specs/<f>/discovery.md
   ▼
[SUBAGENT] context-researcher (Graphifyy + source, isolated window)
   │  → context-pack.md (~2k tokens)
   ▼
[SUBAGENT] planner
   │  → plan.md (tasks + footprints) + contracts/
   │  → invokes extract-deps.py → task-graph.json
   ▼
[MAIN THREAD] Orchestrator dispatches per task-graph.json
   │     parallel where footprints disjoint · sequential along edges
   │     (dispatch-gate.py enforces order, caps, staleness)
   ▼
[SUBAGENTS] Specialists (contract-bound, graph-first retrieval)
   │
   ▼
[SUBAGENT] reviewer per task → review.<task>.<n>.json
   │     fail → classified retry (state.json counters) or escalation
   ▼
[SUBAGENT] reviewer integration pass over combined diff
   │  → passing verdict
   ▼
commit-gate.py opens → git commit / PR
   │
   └── trace-emitter.py streams everything to Langfuse throughout
```

---

## 10. Architectural Decision Record (summary)

| # | Decision | Rationale |
|---|---|---|
| ADR-1 | Brownfield-first, Claude Code only (v1) | Brownfield is where developers live; Claude Code is the only target where subagents, hooks, skills are real primitives |
| ADR-2 | Discovery on main thread, not a subagent | Subagents cannot interact with users |
| ADR-3 | Disk-based artifact pipeline (`specs/`) | Subagent messages get compacted; files are lossless |
| ADR-4 | Contract-first planning | Kills the cross-specialist seam problem at the source |
| ADR-5 | Hook-enforced commit gate | Review must be a gate, not advice |
| ADR-6 | Finite retries with failure taxonomy, counters on disk | Prevents infinite loops surviving context compaction |
| ADR-7 | Hybrid scheduler from computed task-graph.json | Dependency order must be computed, not assumed; disjoint-footprint rule for parallelism |
| ADR-8 | `extract-deps.py` as a script, not an agent | Graph intersection is deterministic math; agents doing it are slower and sometimes wrong |
| ADR-9 | Machinery in `.claude/`, artifacts in `specs/`, merged `CLAUDE.md` | Reversible, non-invasive brownfield adoption |
| ADR-10 | Langfuse via hook-driven OTel emitter | Flywheel with a native evaluation producer (the reviewer) |
