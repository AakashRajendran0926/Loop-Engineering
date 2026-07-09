# Implementation.md

## Agent Harness — Build Plan

**Prerequisite reading:** `Architecture.md` (all design decisions are recorded there; this document is the construction sequence).

---

## 1. Final Repository Tree

This is what a brownfield adopter's repo looks like **after** installation. The rule: all machinery inside `.claude/` (native, reversible), exactly one new root directory (`specs/`, the runtime artifacts), one config file, and a *merged* — never overwritten — `CLAUDE.md`.

```
their-repo/
├── .claude/
│   ├── settings.json                 # hook registrations, permissions (MERGED if exists)
│   ├── agents/
│   │   ├── context-researcher.md
│   │   ├── planner.md
│   │   ├── specialist-frontend.md
│   │   ├── specialist-backend.md
│   │   ├── specialist-database.md
│   │   └── reviewer.md
│   ├── skills/
│   │   ├── discovery-interview/SKILL.md
│   │   ├── contract-writing/SKILL.md
│   │   └── review-taxonomy/SKILL.md
│   ├── commands/
│   │   └── feature.md                # /feature — pipeline entry point
│   ├── hooks/
│   │   ├── commit-gate.py
│   │   ├── dispatch-gate.py
│   │   ├── retrieval-nudge.py
│   │   ├── state-updater.py
│   │   └── trace-emitter.py
│   └── scripts/
│       └── extract-deps.py           # deterministic dependency extractor
├── specs/                            # runtime artifacts, committed, human-reviewable
│   └── <feature-name>/
│       ├── discovery.md
│       ├── context-pack.md
│       ├── plan.md
│       ├── contracts/
│       ├── task-graph.json
│       ├── state.json
│       └── review.<task>.<n>.json
├── CLAUDE.md                         # merged: harness section between markers
└── harness.config.json               # retry caps, hop depth, Langfuse keys, graphify path
```

### `harness.config.json`

```json
{
  "graphify": { "binary": "graphify", "index_path": ".graphify/" },
  "retries": { "mechanical": 2, "contract": 1, "ambiguity": 0, "security": 0 },
  "dependency_depth": 1,
  "observability": {
    "provider": "langfuse",
    "host": "env:LANGFUSE_HOST",
    "public_key": "env:LANGFUSE_PUBLIC_KEY",
    "secret_key": "env:LANGFUSE_SECRET_KEY"
  },
  "gates": { "commit_requires_integration_review": true }
}
```

Secrets are environment references only — never literal keys in a committed file.

---

## 2. Build Phases

Build in this order. Each phase is independently demo-able, which matters for adoption trust.

### Phase 0 — Repo skeleton + installer core (the merge problem)

The installer (`npx loop-engineering-harness init` or a `install.sh` to start) is the hardest and highest-risk component. It must handle repos that already have `.claude/` content.

Merge rules, in order of danger:

1. **`CLAUDE.md`** — append a delimited section, never replace:
   ```
   <!-- harness:begin v1.0 -->
   ... harness orchestration instructions ...
   <!-- harness:end -->
   ```
   Upgrades replace only the delimited block. Uninstall removes it.
2. **`.claude/settings.json`** — deep-merge hook arrays. Existing hooks are preserved; harness hooks are appended with a `"_harness": true` marker so uninstall can filter them back out. Never reorder the adopter's existing entries. If a JSON parse fails, abort with a clear message — never write a best-guess settings file.
3. **`.claude/agents/`, `skills/`, `hooks/`, `commands/`, `scripts/`** — namespace-safe copy. If a filename collides (adopter already has `reviewer.md`), write `reviewer.harness.md` and warn, rather than clobbering.
4. **Detection report** — before writing anything, the installer prints what exists, what will be merged, what will be added, and requires confirmation. Brownfield trust is won in this ninety seconds.

Also ship `uninstall`: remove `_harness` hooks, delete harness files, strip the CLAUDE.md block, leave `specs/` (it's the adopter's work product).

**Acceptance:** install → uninstall on a repo with a pre-existing populated `.claude/` produces a byte-identical repo (minus `specs/`).

### Phase 1 — `extract-deps.py` (standalone value, zero agent dependencies)

Build the deterministic extractor first: it demos on day one without anyone trusting the full pipeline.

Spec:

```
extract-deps.py --plan specs/<f>/plan.md --out specs/<f>/task-graph.json
extract-deps.py --files src/auth/*        # standalone "what does this change touch?"
```

Logic: parse task footprints from `plan.md` frontmatter → for each footprint run `graphify query` to expand by `dependency_depth` hops (imports, importers, shared dependents) → pairwise-intersect expanded sets → emit edges. Contract consumption (task B consumes an interface task A produces, per `contracts/`) also creates an edge even with disjoint files.

`task-graph.json` schema:

```json
{
  "generated_from": { "plan_mtime": "...", "graphify_version": "..." },
  "tasks": [
    { "id": "db-orders-migration", "agent": "specialist-database",
      "footprint": ["db/migrations/", "db/schema.prisma"],
      "expanded_footprint": ["..."] }
  ],
  "edges": [
    { "from": "db-orders-migration", "to": "backend-orders-api",
      "reason": "shared_dependent: db/schema.prisma" }
  ]
}
```

**This is the one component with real unit tests** (fixture repos with known graphs). Test the edge logic exhaustively: overlap, shared dependent, contract edge, disjoint, depth sensitivity.

**Acceptance:** identical plan + codebase → byte-identical `task-graph.json` across runs.

### Phase 2 — Hooks (the enforcement layer)

All hooks: read JSON from stdin, act via exit code + stderr message. Keep each under ~150 lines; no shared framework beyond a tiny `harness_lib.py` for config/state IO.

**`commit-gate.py`** (`PreToolUse`, matcher: Bash) — if the command matches `git commit` / `gh pr create`: locate current feature's `specs/<f>/`, require an integration-scope `review.*.json` with `"status": "pass"` whose `changeset` matches the current diff hash. Otherwise exit 2 with: *"Commit blocked: no passing integration review for this changeset. Run the review phase."*

**`dispatch-gate.py`** (`PreToolUse`, matcher: Task) — parse the target task id from the dispatch; block (exit 2) if: upstream edges in `task-graph.json` not `done` in `state.json`; OR attempt counter ≥ cap for the pending failure class (message instructs orchestrator to escalate to the user); OR `task-graph.json` mtime < `plan.md` mtime (message instructs regenerating via `extract-deps.py`).

**`state-updater.py`** (`SubagentStop` + `PostToolUse` on Task) — transitions task status, increments attempt counters on failed reviews. `state.json` is the single writer's responsibility of this hook — agents never edit it.

**`retrieval-nudge.py`** (`PreToolUse`, matcher: Grep|Glob) — heuristic: broad pattern + no prior `graphify query` this task → exit 2 with the equivalent graph query suggested. Narrow, file-scoped searches pass through. This encodes the retrieval order **Graphifyy → skills → memory**.

**`trace-emitter.py`** (all events) — see Phase 4.

**Acceptance:** a scripted session proving (a) commit blocked without review, (b) dispatch blocked out of order, (c) dispatch blocked at retry cap, (d) stale-graph block.

### Phase 3 — Agents, skills, command (the judgment layer)

**`commands/feature.md`** — enters interview mode on the main thread, loads `discovery-interview` skill, ends by writing `discovery.md` and dispatching context-researcher.

Agent file conventions (every `agents/*.md`): frontmatter with `name`, `description`, minimal `tools` allowlist; body states **reads / writes / must-not** explicitly.

Key content requirements per agent:

- **context-researcher** — tools: Read, Bash(graphify only), Grep. Instruction: explore freely in your own window; return nothing but `context-pack.md` ≤ ~2k tokens (what exists, what depends on what, conventions observed, risk notes).
- **planner** — output contract: every task in `plan.md` carries an explicit file footprint; `contracts/` written before task list is final; **last action is invoking `extract-deps.py`**. Loads `contract-writing` skill.
- **specialists (×3)** — contract-bound: implement against `contracts/`, never against sibling in-progress work; graph-first retrieval; anything outside domain → report as finding, don't improvise across the seam.
- **reviewer** — dual mode: per-task (diff vs plan + contract) and integration (combined diff + contracts conformance + `graphify query` on touched files). Loads `review-taxonomy` skill; must emit valid `review.json` with `failure_class`; must diff own findings across attempts and mark `repeat_finding: true` (triggers early escalation).

**Acceptance:** golden-path run on a small fixture repo completing interview → commit gate open with zero manual file edits.

### Phase 4 — Observability wiring

`trace-emitter.py` registered on all lifecycle events. Mapping: `session_id` → trace id; `PreToolUse(Task)`/`SubagentStop` → subagent span open/close (no `SubagentStart` exists — approximate open from the orchestrator's Task dispatch); tool calls → child spans; review verdicts → Langfuse scores; failed reviews and escalations → dataset items with `failure_class` labels. Fail-open: if Langfuse is unreachable, log locally to `.claude/traces/` and never block the pipeline — observability must not become an availability dependency.

**Acceptance:** one full feature run renders in Langfuse as a single trace with per-subagent spans, scores from review verdicts, and failed attempts landed in a dataset.

### Phase 5 — Hardening + docs

Concurrency guard (advisory lock on `specs/<f>/state.json` for parallel dispatches), `PreCompact` hook to protect pipeline-critical context, README leading with the commit gate demo, and the detection-report installer UX polish.

---

## 3. Testing Strategy

| Layer | Method |
|---|---|
| `extract-deps.py` | Unit tests on fixture repos; determinism test (byte-identical output) |
| Hooks | Stdin-fixture tests: feed recorded hook JSON, assert exit codes/messages |
| Installer | Golden-repo tests: install→uninstall byte-identity; collision fixtures |
| Agents | Scenario evals via the flywheel itself: failed reviews become the regression dataset in Langfuse; re-run after any prompt/skill change |
| End-to-end | One fixture fullstack app; golden-path + each unhappy path (mechanical retry, ambiguity escalation, stale graph, cap hit, integration conflict) |

The agent layer is deliberately tested by the harness's own observability loop — the product tests itself, which is also the demo.

---

## 4. Definition of Done (v1)

A brownfield adopter can: run the installer on a repo with existing `.claude/` config and get a clean merge report; run `/feature`, be interviewed, and watch the artifact chain appear in `specs/`; see a commit physically blocked until integration review passes; hit a retry cap and be escalated with a specific question that updates `discovery.md`; open Langfuse and see the entire run as one labeled trace; and uninstall back to a byte-identical repo. Nothing else is v1.
