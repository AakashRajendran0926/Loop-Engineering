<!-- harness:begin v1.0 -->
## Loop Engineering — Agent Harness

This repo has the Loop Engineering harness installed. Policy lives in exit codes
and on-disk artifacts, not in prose — hooks and scripts enforce the rules below;
you exercise judgment within them.

**Retrieval order — always graphify → skills → memory.** Before broad file
exploration, query the knowledge graph: `graphify query "<question>"`. If
`graphify-out/graph.json` does not exist, initialize with `/graphify .` (it
self-installs and builds the graph). A hook nudges broad `Grep`/`Glob` toward the
graph; narrow file-scoped reads pass through.

**Features run through `/feature`.** It opens a discovery interview on the main
thread, then drives context → plan+contracts → dispatch → review → integration
review → commit. Ordinary Claude Code usage in this repo is untouched — small
edits do not need the pipeline.

**The pipeline is enforced, not advised:**
- Dispatch order, retry caps, and graph freshness are enforced by `dispatch-gate.py`.
- `git commit` / `gh pr create` is blocked by `commit-gate.py` until a passing
  integration review exists for the current diff.
- Task status and retry counters live in `specs/<feature>/state.json`, written
  only by `state-updater.py` — never edit it by hand.
- Escalations are structured re-entry to the interview: append the answer to
  `discovery.md`, don't just apologize in chat.

Artifacts for each feature live in `specs/<feature>/` (discovery, context-pack,
plan, contracts, task-graph, state, review verdicts). Config is `harness.config.json`.
<!-- harness:end -->
