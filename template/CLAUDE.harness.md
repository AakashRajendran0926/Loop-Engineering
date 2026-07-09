<!-- harness:begin v1.0 -->
## Loop Engineering — Agent Harness

This repo runs the Loop Engineering harness. **Policy lives in exit codes and
on-disk artifacts, not in prose** — hooks and scripts enforce the rules below; you
exercise judgment within them. Files in `specs/<feature>/` are the pipeline;
`harness.config.json` holds every knob. Any gate that blocks tells you the legal
next move — do that, don't route around it.

### Retrieval order — always graphify → skills → memory
Before broad file exploration, query the knowledge graph: `graphify query "<q>"`.
If `graphify-out/graph.json` is missing, initialize with `/graphify .` (it
self-installs and builds it). A hook nudges broad `Grep`/`Glob` toward the graph
once per session; narrow, file-scoped reads pass through.

### Features run through `/feature` — everything else is untouched
Small edits do not use the pipeline. `/feature` opens a discovery interview, then
drives: context → plan + contracts → **plan approval** → dispatch → review →
integration review → **version** → commit. Humans are needed at exactly two
points: the interview/approval and escalations.

### The enforced gates (you cannot talk past these)
- **Requirements gate** — refuses to proceed on an ambiguous intention, vague or
  unstructured requirements (need Intent & Scope, edge cases, non-functionals, and
  `AC1, AC2…` acceptance criteria — no TBD/??? markers), a wrong flow, or a plan
  that has diverged from the acceptance criteria (an AC covered by no task, or a
  task inventing scope). On block, **re-enter interview mode** — these go to the
  user, never to a guess.
- **Plan-approval boundary** — `dispatch-gate.py` refuses every dispatch until
  `specs/<f>/approvals.json` matches `plan.md`'s hash. Sign with
  `python .claude/scripts/approve-plan.py <feature> --budget <tokens>`. Editing the
  plan after approval re-freezes automation. In headless mode (`HARNESS_AUTO_APPROVE=1`)
  self-approve after drafting — a logged decision — unless discovery is ambiguous.
- **Dispatch gate** — enforces approval, task-graph freshness, dependency order,
  retry caps, the circuit breaker, and a context-size limit (Rule 1: a dispatch
  prompt carries only the task's slice — never the full plan, discovery, or history).
- **Circuit breaker** — `state.json` `health` becomes `tripped` on retry caps,
  repeat findings, too many escalations, footprint violations, or token-burndown
  breach. Then all dispatch is refused and your only move is to escalate with the
  evidence in `state.json` / `divergence.json`.
- **Commit gate** — `git commit` / `gh pr create` is blocked until a passing
  **integration review** exists for the current diff.
- **Version gate** — commit is also blocked until the feature is versioned:
  major dependency bumps acknowledged (`version.py ack-deps`), a code semver bump +
  `CHANGELOG.md` entry (`version.py bump`), and a development snapshot
  (`version.py snapshot <feature> integrated`).

### Rules you must follow
- **State is hook-owned.** Never hand-edit `state.json`, `review.*.json`,
  `approvals.json`, or the version/dependency/history files.
- **Task-scoped dispatch (Rule 1).** Begin each pipeline Task prompt with
  `FEATURE: <slug>` (specialists add `TASK-ID: <id>`), and include only that task's
  entry, its contracts slice, and — on retry — the latest findings.
- **Stateless retries (Rule 3).** A retry is a fresh subagent given the findings and
  the failed code as a diff to correct — never its own prior reasoning.
- **Contracts before code.** Specialists implement against `contracts/`, stay inside
  their declared footprint, and report anything cross-seam as a finding.
- **Escalation is structured re-entry.** Present the failure/condition and one
  specific question; append the answer to `discovery.md`; re-approve if the plan
  changed. An escalation produces an artifact update, not a chat apology.
- **Compaction is fine.** After a compact, trust `specs/<feature>/` and the injected
  snapshot — re-read from disk rather than compacted memory.

Artifacts per feature live in `specs/<feature>/` (discovery, context-pack, plan,
contracts, task-graph, approvals, state, reviews, dependencies, version, history).
The autonomous driver processes `specs/_queue.json`; parked features await a human
in `specs/_answers.json`.
<!-- harness:end -->
