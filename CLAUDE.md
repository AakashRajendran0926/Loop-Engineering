# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This is the **packaged product** for the Loop Engineering agent harness — an
installer plus the payload it installs. It is *not* the harness operating on
itself. Keep two worlds separate:

- **`template/`** — the payload copied verbatim into an adopter's repo (`.claude/`
  hooks, agents, skills, commands, scripts; plus `harness.config.json`,
  `settings.harness.json`, `CLAUDE.harness.md`). This is Python + Markdown that
  runs inside the *adopter's* Claude Code, not here.
- **`src/` + `bin/`** — the Node installer, the autonomous loop driver, and
  metrics. Zero runtime dependencies (Node stdlib only). This is what `npx` runs.

Read `README.md` for the product overview and `Architecture.md` /
`Implementation.md` / `DevelopmentUpdates.md` / `HookDevelopment.md` for the
design record and its deltas (the specs are authoritative; the code implements them).

## Commands

```bash
npm test                       # node tests/run.js — the entire suite (~84 checks)
node tests/run.js              # same; prints "N passed, M failed", exits non-zero on failure

node bin/cli.js detect .       # dry-run install plan
node bin/cli.js init . --yes   # install into a repo (--python <cmd> to force interpreter)
node bin/cli.js uninstall .    # remove; keeps specs/
node bin/cli.js queue add "…" | loop [--once|--auto-approve|--dry-run] | metrics | requeue <id>
```

There is **no build step and no linter/formatter configured** — don't add lint
commands or expect a compile. Requires Node ≥ 16 and Python 3.8+ on PATH
(`python`/`python3`/`py`).

**Running one test:** `tests/run.js` is a single self-contained runner with no
filter flag. To run a subset, comment out calls in its invocation block (the
`testX()` list near the bottom, ~line 599). Each `testX()` is independent and
writes to `tests/.work/<name>/`. Python hooks are exercised by spawning the real
interpreter with recorded JSON on stdin; the loop driver is tested with an
**injectable mock runner** (no `claude -p` needed). Fixtures live in `tests/fixtures/`.

## The enforcement model (read before touching any hook)

Every hook reads JSON on stdin and enforces via **exit code**: `0` = allow, `2` =
block with the stderr message fed back to the model as steering. Hooks **fail
open** — a missing config, unparseable file, or absent tool must never crash and
wedge a session. `harness_lib.deny()/warn()/allow()` wrap this contract.

Policy lives in exit codes and on-disk artifacts under `specs/<feature>/`, never
in prompts. The gate stack (each a hook in `template/.claude/hooks/`):

| Enforces | Hook | Event |
|---|---|---|
| graph-first retrieval | `retrieval-nudge.py` | PreToolUse Grep/Glob |
| requirements/intention quality (R1–R4) | `requirements-gate.py` | PreToolUse Task |
| schedule: approval hash, freshness, breaker, order, caps, context size (G0–G6) | `dispatch-gate.py` | PreToolUse Task |
| footprint circuit breaker; artifact size | `divergence-monitor.py`, `artifact-size.py` | PostToolUse Edit/Write |
| integration-review gate; version gate (V1–V3) | `commit-gate.py`, `version-gate.py` | PreToolUse Bash |
| state reconciliation + derived breaker `health` | `state-updater.py` | SubagentStop, PostToolUse Task |
| compaction snapshot + resume | `precompact-guard.py`, `session-rehydrate.py` | PreCompact, SessionStart |
| dependency deltas; observability | `version-tracker.py`, `trace-emitter.py` | — |

Four shared Python libs (all under `template/.claude/hooks/`): `harness_lib`
(config/state/graphify detection, exit helpers), `loop_lib` (`recompute_health`,
footprint matching), `req_lib` (the R1–R4 requirement checks), `version_lib`
(manifest parsing, semver, snapshots). Hooks resolve the repo root from
`CLAUDE_PROJECT_DIR` or `cwd` (Claude Code runs hooks from the project root).

## Cross-file invariants that will bite you

- **Adding or renaming a hook touches four places:** create the file; register it
  in `template/settings.harness.json` (command uses the `__PYTHON__` placeholder,
  replaced at install, and a `"_harness": true` marker); add its basename to the
  uninstall filter regex in `src/merge.js` (`stripSettings`); and add default
  config to **both** `template/harness.config.json` and `harness_lib.DEFAULT_CONFIG`
  (these two must stay in sync — the JSON is what installs, the Python dict is the
  fail-open fallback). Then add a test.
- **Two different CLAUDE.md files.** This root file guides development *of* the
  repo. `template/CLAUDE.harness.md` is the operating manual installed *into*
  adopter repos as a delimited block — and its exact `<!-- harness:begin v1.0 -->`
  / `<!-- harness:end -->` marker lines must equal `BEGIN`/`END` in `src/merge.js`
  or the installer's merge/uninstall breaks.
- **Hash lock-steps.** `changeset.py` and `commit-gate.py` must fingerprint the
  diff identically; `approve-plan.py`'s `plan_hash` and `dispatch-gate.py` G1 must
  hash `plan.md` identically (`sha256(bytes)`); `version.py` and `version-gate.py`
  both go through `version_lib`. Change one side, change the other.
- **Installer round-trip is a hard invariant.** `install → uninstall` on a
  pre-populated `.claude/` must be **byte-identical** (minus `specs/`). It's driven
  by `.claude/.harness/manifest.json`; collisions become `*.harness.*`; `settings.json`
  and `CLAUDE.md` are re-serialized on strip (so an adopter's exotic whitespace
  isn't preserved — a documented limitation). The `testInstaller` case guards this.
- **`extract-deps.py` must be deterministic.** Identical plan + graph ⇒
  byte-identical `task-graph.json`. It keys `generated_from` on `plan_sha256` (not
  mtime) and sorts all output. Don't introduce timestamps or set iteration order.
- **`state.json` has a single writer** (`state-updater.py`). `health` is *derived*
  from disk via `loop_lib.recompute_health` — several hooks recompute it and
  converge; never hand-set it. Agents never edit `state.json` / `review.*.json`.
- **Windows reality.** File writes in hooks need `encoding="utf-8"` (a cp1252
  default once crashed compaction on a `←`); avoid non-ASCII in emitted text. The
  installer detects the interpreter; hook commands run `python`/`python3`/`py`.

## Testing philosophy

The agent-behavior layer (prompts in `agents/`, `skills/`) is validated by the
harness's own observability loop, not unit tests. What *is* unit-tested here:
`extract-deps` determinism + edge logic, every hook's exit codes via stdin
fixtures, the loop driver's queue/terminal-state/resume logic (mock runner), the
Version Controller cycle, and installer byte-identity. When you change a hook's
contract, update its `testX()` and keep the suite green before finishing.
