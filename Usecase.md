# Usecase.md

## Agent Harness — Use Cases and Adoption Scenarios

**Companion documents:** `Architecture.md` (design), `Implementation.md` (build plan).

---

## 1. Who This Is For — and Who It Is Not For

**Primary user:** a fullstack developer working in an **existing, production codebase** who uses Claude Code and wants agentic workflows that are *enforced*, not aspirational — where "the reviewer must approve" is an exit code, not a sentence in a prompt that the model eventually rationalizes past.

**Secondary user:** a platform/enablement engineer standardizing how a team uses Claude Code — someone who will actually close the observability loop (review traces, maintain the regression dataset, tune skills and hooks).

**Explicitly not for (v1):** greenfield scaffolding seekers, GitHub Copilot / Antigravity users (v2, via `AGENTS.md`-floor adapters), and anyone wanting a fire-and-forget autonomous coder. This harness assumes a human in the loop at exactly two points: the discovery interview and escalations. That is a feature — ambiguity is routed to humans by design because retrying ambiguity only generates confident guesses.

---

## 2. UC-1 · The Golden Path: "Add order-cancellation to our app"

The canonical scenario the harness must prove in week one. Priya, a fullstack dev at a mid-size company, works in a 4-year-old TypeScript monorepo (Next.js + Node API + Postgres/Prisma).

**1. Entry.** Priya runs `/feature add order cancellation with refund handling`.

**2. Interview (main thread).** The orchestrator, driven by the `discovery-interview` skill, interviews her — not generically, but protocol-driven: edge cases (partial shipments? already-refunded orders?), non-functionals (must refunds be idempotent?), acceptance criteria. Output: `specs/order-cancellation/discovery.md`. Priya's involvement is now done unless the pipeline needs her.

**3. Context research (isolated window).** The context-researcher burns 40k+ tokens running `graphify query` over orders, payments, and refund modules and reading the relevant source. The orchestrator receives only `context-pack.md` (~2k tokens): what exists, what depends on what, house conventions, risk notes ("refund logic also called from admin panel — shared dependent").

**4. Plan + contracts.** The planner writes `plan.md` — five tasks, each with an explicit file footprint — and `contracts/` (cancellation API schema, migration shape, component props) *before* any implementation. Its final action invokes `extract-deps.py`.

**5. Computed schedule.** `task-graph.json` comes back: the DB migration and the "cancel button" UI-shell task have disjoint expanded footprints → **parallel**. The API task shares `schema.prisma` dependents with the migration → **sequential edge**. Nobody guessed this; Graphifyy computed it.

**6. Execution.** The dispatch gate releases tasks in graph order. Specialists implement against `contracts/`, graph-first retrieval enforced by the nudge hook. Each task gets a per-task review with a `review.json` verdict.

**7. Integration review.** All tasks green → the reviewer runs once more over `git diff main...feature/order-cancellation`, checking contracts conformance and querying Graphifyy on touched files for cross-task breakage.

**8. Gate opens.** Only now does `commit-gate.py` allow `git commit`. Priya reviews a PR backed by a complete artifact chain in `specs/` — the discovery answers, the plan, the contracts, every review verdict — instead of an unexplained 2,000-line diff.

**Success criteria:** zero manual pipeline babysitting between interview and escalation/PR; every artifact present; one coherent Langfuse trace.

---

## 3. Unhappy-Path Use Cases (where harnesses live or die)

### UC-2 · Mechanical failure → bounded retry

The backend specialist's task fails review: two type errors, one failing test. Reviewer emits `failure_class: "mechanical"`, findings attached. Orchestrator re-dispatches with findings; `state.json` counter → 1. Second attempt passes. Total cost: one retry, fully traced. **Cap:** mechanical allows 2 retries; the dispatch gate — not the orchestrator's goodwill — blocks a fourth attempt.

### UC-3 · Repeated finding → early escalation

Same scenario, but attempt 2 fails with the *identical* finding. The reviewer marks `repeat_finding: true`. Even though the counter has headroom, the orchestrator escalates immediately — two identical failures predict a third; the harness doesn't spend budget confirming it.

### UC-4 · Ambiguity → zero retries, structured human re-entry

Mid-implementation, the reviewer cannot determine intended behavior: should cancelling a partially-shipped order refund the shipped portion? `failure_class: "ambiguity"` → no retry. The orchestrator (main thread, so it *can* talk) asks Priya one specific question with the conflicting constraints laid out. Her answer is **appended to `discovery.md`** — it's new requirements information — the plan is patched, counters reset, pipeline resumes. The escalation produced an artifact update, not a chat apology.

### UC-5 · Cross-specialist conflict caught at integration

Backend renamed a response field per the contract; frontend, built in a parallel-safe task earlier, still reads the old name in one overlooked component (a file outside its declared footprint that Graphifyy flagged as a dependent). Per-task reviews both passed — the conflict is only visible in the combined diff. The integration review catches it via its Graphifyy pass over touched files; a scoped fix task is dispatched. The commit gate never opened on the broken state.

### UC-6 · Security finding → hard stop

The reviewer flags string-interpolated SQL in the refund path. `failure_class: "security"` → zero retries by the authoring specialist, hard stop, human review required. The rationale is policy, not capability: you don't want an agent iterating until the scanner goes quiet.

### UC-7 · Stale plan → blocked dispatch

Priya hand-edits `plan.md` to add a task. The next dispatch is blocked: `task-graph.json` older than `plan.md`. Exit message tells the orchestrator to re-run `extract-deps.py`. Schedules are never allowed to drift from plans.

---

## 4. Adoption Use Cases

### UC-8 · Brownfield installation into a repo with existing Claude config

Dev-lead Marco's repo already has a team `CLAUDE.md`, a populated `.claude/settings.json` with two custom hooks, and one custom agent. The installer prints a **detection report** — what exists, what merges, what gets added — and waits for confirmation. `CLAUDE.md` gains a delimited harness block below the team's content; settings hooks are appended with `_harness` markers, existing entries untouched and unreordered; the colliding agent name is written as `*.harness.md` with a warning. Marco diffs the result in a branch before merging. **Trust is won in this ninety seconds.**

### UC-9 · Trial by standalone tool before trusting the pipeline

A skeptical adopter runs only `extract-deps.py --files src/auth/*` to answer "what does this change touch?" — deterministic, useful, no agents involved. The harness earns credibility component-by-component.

### UC-10 · Reversal

The team decides against the harness. `uninstall` removes `_harness` hooks, harness files, and the delimited `CLAUDE.md` block; `specs/` remains (it's their work product). The repo is byte-identical to pre-install otherwise. Reversibility is an adoption feature, not an afterthought.

---

## 5. Operational Use Cases (the flywheel)

### UC-11 · Debugging a bad run

A feature took 3× the usual tokens. The platform engineer opens the Langfuse trace: one subagent span dominates — the frontend specialist did raw Grep exploration on a task where the retrieval nudge was mis-scoped. Fix the nudge matcher; the next run's trace confirms.

### UC-12 · The regression suite writes itself

Every failed `review.json` and every escalation lands in a Langfuse dataset, labeled with `failure_class`, attempts, and resolution. When anyone edits a skill, agent prompt, or hook, the team replays the dataset scenarios before merging the change. The evaluate step never waited on human labeling — the reviewer agent is the native producer.

### UC-13 · Tuning the parallelism/safety dial

On a tangled legacy module, depth-1 footprint expansion lets two tasks run parallel that the integration review then catches conflicting. The team raises `dependency_depth` to 2 for that area of the config — trading parallelism for fewer integration bounces — and watches the trace metrics to confirm the trade paid off.

---

## 6. Boundary Cases and Honest Limits

A hotfix that must ship *now* doesn't want an interview: the harness supports a documented bypass (`gates.commit_requires_integration_review: false` in a branch-scoped override), visible in the trace, so speed is a logged decision rather than a silent workaround. Very small changes ("fix this typo") shouldn't invoke the pipeline at all — the harness is for features, and the `/feature` command is the explicit opt-in; ordinary Claude Code usage in the same repo remains untouched. And the harness cannot rescue a codebase Graphifyy cannot parse — supported-language coverage is a stated prerequisite, checked by the installer's detection report.
