#!/usr/bin/env python3
"""
precompact-guard.py — PreCompact hook.

Design intent (Rule 4, DevelopmentUpdates.md): we do NOT fight compaction —
we make the pipeline indifferent to it. This hook's job is to guarantee that
the load-bearing minimum survives on DISK before the context window is
compressed:

    - current feature        (pointer to specs/<f>/)
    - current task id
    - current pipeline phase
    - attempt counters / breaker health (already in state.json)

It writes a tiny snapshot file, specs/<f>/pipeline-context.md, that the
SessionStart(source=compact) rehydration hook injects back into context
right after compaction. Everything else in the window is allowed to
compact freely, because it can be re-read from specs/ on demand.

PreCompact hooks cannot block compaction — and we don't want to. Exit 0 always.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path.cwd()
ACTIVE_POINTER = REPO / "specs" / "_active.json"  # {"feature": "...", maintained by state-updater}


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)  # never break compaction

    if not ACTIVE_POINTER.exists():
        sys.exit(0)  # no pipeline in flight — nothing to protect

    try:
        feature = json.loads(ACTIVE_POINTER.read_text(encoding="utf-8")).get("feature")
    except (json.JSONDecodeError, OSError):
        sys.exit(0)
    if not feature:
        sys.exit(0)

    spec = REPO / "specs" / feature
    state_p = spec / "state.json"
    state = {}
    if state_p.exists():
        try:
            state = json.loads(state_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}

    current_task = state.get("current_task", "unknown")
    phase = state.get("phase", "unknown")
    health = state.get("health", "ok")

    snapshot = f"""# PIPELINE CONTEXT SNAPSHOT (load-bearing minimum)
_Written by precompact-guard at {datetime.now(timezone.utc).isoformat()} \
(trigger: {event.get('trigger', 'unknown')})_

- **FEATURE:** {feature}
- **ARTIFACTS:** specs/{feature}/  <- re-read plan.md, task-graph.json,
  state.json, contracts/ from here instead of relying on compacted memory.
- **CURRENT TASK:** {current_task}
- **PIPELINE PHASE:** {phase}
- **BREAKER HEALTH:** {health}

## Standing orders after compaction
1. Treat this snapshot + specs/{feature}/ as the ONLY source of truth.
   Do not trust compacted recollections of plan details or review findings.
2. Resume by re-reading state.json, then continue dispatching per
   task-graph.json. dispatch-gate.py will enforce order, approval hash,
   retry caps, and breaker state regardless.
3. Never read source code or raw diffs into this (orchestrator) context —
   that work belongs to subagents (compression funnel, Rule 2).
"""
    try:
        spec.mkdir(parents=True, exist_ok=True)
        (spec / "pipeline-context.md").write_text(snapshot, encoding="utf-8")
    except OSError as e:
        print(f"[precompact-guard] snapshot write failed: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
