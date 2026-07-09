# Loop Engineering — an agent harness for brownfield Claude Code

Turn Claude Code into a disciplined, observable, **continuously-looping** multi-agent
development pipeline you can drop into an existing codebase without disrupting it.

> **The thesis:** policy lives in exit codes and artifacts on disk, not in prompts.
> Agents exercise judgment; hooks and scripts enforce the rules; files carry the
> state. Anything an agent could forget or rationalize past is enforced deterministically.

Built entirely on Claude Code's native primitives — subagents, skills, hooks, slash
commands — plus **graphify** (deterministic code knowledge graph) and **Langfuse**
(trace observability). Design record: [Architecture.md](Architecture.md),
[Implementation.md](Implementation.md), [Usecase.md](Usecase.md),
[DevelopmentUpdates.md](DevelopmentUpdates.md), [HookDevelopment.md](HookDevelopment.md).

---

## Install

```bash
npx agent-harness detect .      # dry run — show exactly what will change
npx agent-harness init .        # install (prints a plan, asks to confirm)
npx agent-harness uninstall .   # remove everything; keeps your specs/
```

Brownfield-safe by construction:

- **`CLAUDE.md`** gains a delimited `<!-- harness:begin -->…<!-- harness:end -->`
  block below your content — never a rewrite. Upgrades replace only the block.
- **`.claude/settings.json`** is deep-merged: your hooks are preserved and never
  reordered; harness hooks are appended with a `"_harness": true` marker. Invalid
  JSON aborts the install rather than guessing.
- **File collisions** (e.g. you already have `agents/reviewer.md`) are written as
  `reviewer.harness.md` with a warning — your file is never clobbered.
- **A manifest** records exactly what was written, so `uninstall` restores a
  **byte-identical** repo (minus `specs/`, which is your work product).

---

## The gate stack — the whole point

Every gate is a hook that reads disk and exits non-zero to block. The harness
summons a human at bad **inputs**, bad **flow**, goal **drift**, failed **outputs**,
and unversioned **change** — each decided mechanically, never by an agent grading
itself.

| Gate | Fires on | Blocks unless… |
|---|---|---|
| `retrieval-nudge.py` | Grep/Glob | broad search goes graph-first (`graphify query`); auto-inits graphify if absent |
| `requirements-gate.py` | Task | intent is unambiguous, requirements structured (AC1, AC2…), flow in order, plan covers exactly the acceptance criteria |
| `dispatch-gate.py` | Task | G0–G6: approval-hash matches, graph fresh, breaker healthy, upstream done, under retry cap, prompt within context budget |
| `divergence-monitor.py` | Edit/Write | writes stay inside the task's declared footprint (else trips the breaker) |
| `artifact-size.py` | Edit/Write | context-pack / review stay within their compression budget |
| `commit-gate.py` | Bash | a passing **integration review** exists for the current diff |
| `version-gate.py` | Bash | major deps acknowledged, code version + changelog recorded, dev history captured |

Supporting hooks: `state-updater.py` (single writer of `state.json` + derived
breaker `health`), `precompact-guard.py`/`session-rehydrate.py` (compaction
indifference + resume), `trace-emitter.py` (Langfuse + local traces),
`version-tracker.py` (dependency deltas).

---

## Using it — `/feature`

```
/feature add order cancellation with refund handling
```

1. **Discovery interview** (main thread, human) → `discovery.md` with `AC1, AC2…` acceptance criteria
2. **context-researcher** (isolated window, graph-first) → `context-pack.md` (~2k tokens, sectioned per work area)
3. **planner** → `contracts/` then `plan.md` (footprints, `satisfies:` coverage tags, `VERSION-IMPACT`, token estimates) → runs `extract-deps.py` → `task-graph.json`
4. **Plan approval** (human, or `--auto-approve`) → `approvals.json` signs the decomposition; automation is frozen until it matches `plan.md`'s hash
5. **Specialists** dispatched per the computed schedule — parallel only where footprints are *literally disjoint*, sequential along edges; task-scoped prompts (Rule 1), stateless retries (Rule 3)
6. **reviewer** per task, then one **integration review** over the combined diff
7. **Version** the feature (bump + changelog + snapshot)
8. **Commit** — the commit gate *and* version gate open; the PR is backed by the full artifact chain in `specs/`, not a bare diff

`/feature` is the only opt-in — ordinary Claude Code usage in the repo is untouched.

---

## Retrieval is graph-first: graphify → skills → memory

`retrieval-nudge.py` redirects broad `Grep`/`Glob` toward a dependency-aware
`graphify query`. Narrow, file-scoped searches pass through. If no graph exists it
routes to `/graphify .` (which self-installs and builds one). It blocks a session
at most **once**, so it steers the first broad search but can never wedge a subagent.

---

## The human gate — summoned by evidence, before work goes wrong

Four conditions freeze automation until a person resolves them
(`requirements-gate.py`, and `approve-plan.py` refuses to sign a failing plan):

| Condition | Detected as |
|---|---|
| Ambiguous / unorganized intention | `discovery.md` missing Intent & Scope, or unresolved markers (TBD / ??? / "clarify") |
| Vague / unstructured requirements | required sections absent, or no structured acceptance criteria (AC1, AC2…) |
| Wrong flow | implementation dispatched before its phase artifacts exist (discovery → context-pack → plan → task-graph) |
| Diverted from user requirements | an acceptance criterion covered by no task (under-delivery), or a task citing an AC absent from discovery (scope creep) |

A block is not a retry — the message says *re-enter interview mode*. `--force` on
`approve-plan.py` overrides but is recorded in `approvals.json` as a logged decision.

---

## Version Controller — dependency, code, and development versioning

A feature does not commit until it is versioned (`version-tracker.py` +
`version-gate.py` + [`version.py`](template/.claude/scripts/version.py)):

- **Dependency** — the pre-feature dependency set is baselined at approval; every
  manifest edit (`package.json`, `requirements.txt`, `pyproject.toml`, `go.mod`,
  `Cargo.toml`) is diffed and classified major/minor/patch into
  `dependencies.json`. A **major** bump must be acknowledged after review.
- **Code** — `version.py bump` derives a semver bump from the plan's
  `VERSION-IMPACT`, updates `VERSION`, and writes a `CHANGELOG.md` entry.
- **Development** — `version.py snapshot` hashes the spec artifacts into
  `history.json` at each milestone (`approved` → `integrated` → …) so a feature's
  evolution is auditable, not overwritten.

```bash
python .claude/scripts/version.py status <feature>
```

---

## Autonomous workflow — the outer loop

The driver processes a **feature queue**, running each in a fresh headless
`claude -p` session to one of three terminal states read back from `state.json`.
Escalations **park** (with a question for a human) instead of stalling the queue;
a parked feature resumes from artifacts in a brand-new session — recover-from-
compaction and resume-from-parking are the same mechanism.

```bash
agent-harness queue add "add order cancellation with refunds"
agent-harness queue add "nightly CSV export" --auto-approve   # headless self-approve
agent-harness loop                    # drive the queue to terminal states
agent-harness queue list              # done / parked (+ the questions)
#   ...human appends the answer to specs/<f>/discovery.md...
agent-harness requeue <feature-id>    # resume in a fresh session
agent-harness metrics                 # per-feature burndown + tokens-per-passing-task
```

The human gate (interview + plan approval) is deliberate — ambiguity is routed to
people, not retried into confident guesses. `--auto-approve` is a **logged**
opt-out for trusted/CI features; every downstream gate still enforces. Token
burndown is fed from each session's real usage into the circuit breaker (halts at
100% of the approved budget). Dashboards: [docs/langfuse-dashboards.md](docs/langfuse-dashboards.md).

---

## The artifact chain

The pipeline's real data structure is `specs/<feature>/` — agents are transient,
artifacts are the pipeline:

```
specs/<feature>/
├── discovery.md            # interview output; AC1, AC2… ; appended on escalation
├── context-pack.md         # ~2k-token brief, sectioned per work area
├── plan.md                 # tasks + footprints + satisfies: + VERSION-IMPACT + est_tokens
├── contracts/              # interface agreements, written before any specialist runs
├── task-graph.json         # computed schedule (extract-deps.py)
├── approvals.json          # the signed autonomy boundary (plan hash + budget)
├── state.json              # per-task status, attempts, phase, breaker health (hook-written)
├── review.<task>.<n>.json  # reviewer verdicts, one per attempt
├── dependencies.json       # dependency deltas vs baseline (+ .baseline.json)
├── version.json            # code semver bump for this feature
├── history.json            # development-version snapshots per milestone
├── divergence.json         # footprint violations + token burndown
└── pipeline-context.md     # PreCompact snapshot (load-bearing minimum)
specs/_queue.json · _answers.json · _active.json   # the loop driver's ledgers
```

---

## What gets installed

```
.claude/
├── settings.json          # hook registrations (merged)
├── agents/                # context-researcher, planner, specialist-{db,backend,frontend}, reviewer
├── skills/                # discovery-interview, contract-writing, review-taxonomy
├── commands/feature.md    # /feature — pipeline entry point
├── hooks/                 # 12 hooks + harness_lib, loop_lib, req_lib, version_lib
└── scripts/               # extract-deps.py, approve-plan.py, changeset.py, version.py
CLAUDE.md                  # merged harness block
harness.config.json        # every knob (below)
```

---

## Configuration — `harness.config.json`

```jsonc
{
  "graphify":   { "binary": "graphify", "index_path": "graphify-out/" },
  "retries":    { "mechanical": 2, "contract": 1, "ambiguity": 0, "security": 0 },
  "dependency_depth": 1,
  "retrieval_nudge": { "mode": "block", "auto_init": true },
  "budgets":    { "context_pack": 2000, "review_findings": 500, "dispatch_prompt_max": 8000 },
  "circuit_breaker": { "footprint_violations": { "warn": 1, "halt": 2 },
                       "max_escalations_per_feature": 2, "burndown": { "warn_pct": 80, "halt_pct": 100 } },
  "requirements": { "enabled": true, "min_acceptance_criteria": 1, "require_coverage": true },
  "versioning":  { "enabled": true, "require_major_dep_ack": true, "require_code_bump": true, "require_dev_history": true },
  "loop":       { "queue": "specs/_queue.json", "answer_queue": "specs/_answers.json", "auto_approve": false },
  "observability": { "provider": "langfuse", "enabled": true, "host": "env:LANGFUSE_HOST",
                     "public_key": "env:LANGFUSE_PUBLIC_KEY", "secret_key": "env:LANGFUSE_SECRET_KEY" },
  "gates": { "commit_requires_integration_review": true, "commit_requires_version": true }
}
```

Secrets are `env:` references only — never literal keys on disk. Any gate is a
config toggle; bypasses are logged decisions, not silent workarounds.

---

## Observability & the flywheel

`trace-emitter.py` streams one Langfuse trace per session (trace id = `session_id`),
with subagent spans, tool child-spans, and review verdicts as scores. Every failed
review and escalation lands in a regression dataset labeled with `failure_class`.
If Langfuse is unreachable it falls back to `.claude/traces/` — observability never
becomes an availability dependency. Offline view: `agent-harness metrics`.

## Standalone tool

Try the deterministic dependency extractor with zero agents:

```bash
python .claude/scripts/extract-deps.py --files src/auth/    # "what does this change touch?"
```

## Testing

```bash
npm test        # node tests/run.js — 84 checks across extract-deps determinism,
                # every hook (stdin fixtures), the loop driver (mock runner),
                # metrics, the Version Controller, and installer byte-identity
```

## Requirements

- **Node ≥ 16** (installer + loop driver; zero runtime dependencies)
- **Python 3.8+** on PATH (`python`, `python3`, or `py`) for hooks/scripts
- **graphify** for graph-first retrieval — `/graphify` self-installs it on first run
- **Claude Code** (for the `claude -p` headless loop) and, optionally, **Langfuse**

## Reversibility

`uninstall` removes the `_harness` hooks, the harness files, and the delimited
`CLAUDE.md` block, and leaves `specs/`. A pre-populated `.claude/` returns
byte-identical. Reversibility is an adoption feature, not an afterthought.

## License

MIT
