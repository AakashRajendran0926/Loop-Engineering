# Loop Engineering — an agent harness for brownfield Claude Code

Turn Claude Code into a disciplined, observable, multi-agent development pipeline
you can drop into an **existing** codebase without disrupting it.

> **The core thesis:** policy lives in exit codes and artifacts on disk, not in
> prompts. Agents exercise judgment; hooks and scripts enforce the rules; files
> carry the state. Anything an agent could "forget" or rationalize past is instead
> enforced deterministically.

Built entirely on Claude Code's native primitives — subagents, skills, hooks,
slash commands — plus two integrations: **graphify** (deterministic code
knowledge graph) and **Langfuse** (trace-based observability).

Design record: [`Architecture.md`](Architecture.md) · build plan:
[`Implementation.md`](Implementation.md) · scenarios: [`Usecase.md`](Usecase.md).

---

## Install

```bash
npx agent-harness detect .     # dry run: show exactly what will change
npx agent-harness init .       # install (prints the plan, asks to confirm)
npx agent-harness uninstall .  # remove everything; keeps your specs/
```

Brownfield-safe by construction:

- **`CLAUDE.md`** gains a delimited `<!-- harness:begin -->…<!-- harness:end -->`
  block below your content — never a rewrite. Upgrades replace only the block.
- **`.claude/settings.json`** is deep-merged: your hooks are preserved and never
  reordered; harness hooks are appended with a `"_harness": true` marker.
  Invalid JSON aborts the install rather than guessing.
- **File collisions** (e.g. you already have `agents/reviewer.md`) are written as
  `reviewer.harness.md` with a warning — your file is never clobbered.
- **A manifest** (`.claude/.harness/manifest.json`) records exactly what was
  written, so `uninstall` restores a **byte-identical** repo (minus `specs/`,
  which is your work product).

---

## The signature feature: the commit gate

```
$ git commit -m "add order cancellation"
Commit blocked (Loop Engineering): no integration review for feature
'order-cancellation'. All tasks must pass, then run the reviewer's integration
pass over the combined diff before committing.
```

Review approval is an **exit code**, not a sentence in a prompt the model
eventually talks itself past. This is the line between a harness and a folder of
markdown personas.

---

## Using it: `/feature`

```
/feature add order cancellation with refund handling
```

1. **Discovery interview** (main thread) → `specs/<f>/discovery.md`
2. **context-researcher** (isolated window, graph-first) → `context-pack.md` (~2k tokens)
3. **planner** writes `contracts/` then `plan.md`, then runs `extract-deps.py` → `task-graph.json`
4. **Specialists** dispatched per the computed schedule — parallel only where file
   footprints are *literally disjoint*, sequential along every edge
5. **reviewer** per task, then one **integration review** over the combined diff
6. **commit gate opens** — PR backed by the full artifact chain in `specs/`

Ordinary Claude Code usage in the same repo is untouched. `/feature` is the only
opt-in; small edits should not use the pipeline.

---

## Retrieval is graph-first: graphify → skills → memory

The `retrieval-nudge.py` hook redirects broad, exploratory `Grep`/`Glob` toward a
dependency-aware `graphify query` first. Narrow, file-scoped searches pass
straight through. Its availability routing is the answer to *"search for context
with graphify, and if graphify isn't there, set it up first"*:

| State | What the hook does |
|---|---|
| Graph built (`graphify-out/graph.json`) | Steer to `graphify query "<derived question>"` |
| Installed, no graph | Instruct: `/graphify .` to build the graph (setup), then query |
| Not installed | Instruct: `/graphify .` — the skill **self-installs** (uv/pip), then builds |

**Loop-safe:** the hook blocks a session at most **once** (it drops a marker), so
it steers the first broad search but can never wedge a subagent. Configure via
`harness.config.json` → `retrieval_nudge.mode` (`block` | `warn` | `off`).

---

## What gets installed

```
.claude/
├── settings.json          # hook registrations (merged)
├── agents/                # context-researcher, planner, specialist-{db,backend,frontend}, reviewer
├── skills/                # discovery-interview, contract-writing, review-taxonomy
├── commands/feature.md    # /feature — pipeline entry point
├── hooks/                 # retrieval-nudge, commit-gate, dispatch-gate, state-updater, trace-emitter (+ harness_lib)
└── scripts/               # extract-deps.py (deterministic scheduler), changeset.py
specs/<feature>/           # runtime artifact chain (committed, human-reviewable)
CLAUDE.md                  # merged harness block
harness.config.json        # retry caps, hop depth, Langfuse keys, graphify path
```

### The hooks (deterministic enforcement)

| Hook | Event | Enforces |
|---|---|---|
| `retrieval-nudge.py` | PreToolUse · Grep\|Glob | Graph-first retrieval; auto-init routing |
| `commit-gate.py` | PreToolUse · Bash | No commit without a passing integration review for the current diff |
| `dispatch-gate.py` | PreToolUse · Task | Dispatch order, retry caps, stale-graph block |
| `state-updater.py` | SubagentStop, PostToolUse · Task | Reconciles `state.json` from review verdicts (single writer) |
| `trace-emitter.py` | all lifecycle events | Local trace + best-effort Langfuse (fail-open) |

### The standalone tool

Try the deterministic dependency extractor with zero agents involved:

```bash
python .claude/scripts/extract-deps.py --files src/auth/    # "what does this change touch?"
```

---

## Configuration — `harness.config.json`

```jsonc
{
  "graphify":  { "binary": "graphify", "index_path": "graphify-out/" },
  "retries":   { "mechanical": 2, "contract": 1, "ambiguity": 0, "security": 0 },
  "dependency_depth": 1,
  "retrieval_nudge": { "mode": "block", "auto_init": true },
  "observability": { "provider": "langfuse", "enabled": true,
                     "host": "env:LANGFUSE_HOST",
                     "public_key": "env:LANGFUSE_PUBLIC_KEY",
                     "secret_key": "env:LANGFUSE_SECRET_KEY" },
  "gates": { "commit_requires_integration_review": true }
}
```

Secrets are `env:` references only — never literal keys in a committed file. Set
`gates.commit_requires_integration_review: false` in a branch-scoped override for
a logged hotfix bypass.

---

## Testing

```bash
npm test        # node tests/run.js
```

Covers the three layers the build plan calls out: `extract-deps` determinism +
edge logic, hook exit codes via stdin fixtures, and installer install→uninstall
byte-identity on a pre-populated brownfield fixture.

---

## Requirements

- **Node ≥ 16** (installer only; zero runtime dependencies)
- **Python 3.8+** on PATH (`python`, `python3`, or `py`) for the hooks/scripts
- **graphify** for graph-first retrieval — `/graphify` self-installs it on first run
- **Langfuse** (optional) for observability; traces fall back to `.claude/traces/`

## Reversibility

`uninstall` removes the `_harness` hooks, the harness files, and the delimited
`CLAUDE.md` block, and leaves `specs/`. A pre-populated `.claude/` returns
byte-identical. Reversibility is an adoption feature, not an afterthought.

## License

MIT
