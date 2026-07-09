#!/usr/bin/env python3
"""
session-rehydrate.py — SessionStart hook (matchers: "compact", "resume").

The second half of the compaction-indifference design. precompact-guard.py
snapshots the load-bearing minimum to disk; this hook injects it back into
the fresh/compacted context. Stdout from a SessionStart hook is added to
Claude's context, which is exactly the channel we want.

Also serves the loop layer (DevelopmentUpdates.md §4): when the outer driver
resumes a parked feature in a brand-new session, this same hook rehydrates
it from artifacts — resume-from-artifacts and recover-from-compaction are
deliberately the same mechanism.
"""

import json
import sys
from pathlib import Path

REPO = Path.cwd()
ACTIVE_POINTER = REPO / "specs" / "_active.json"


def main() -> None:
    # Snapshot content may contain non-ASCII; force UTF-8 stdout so injection
    # never dies on a Windows cp1252 console.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    if event.get("source") not in ("compact", "resume"):
        sys.exit(0)  # fresh interactive sessions start clean on purpose

    if not ACTIVE_POINTER.exists():
        sys.exit(0)

    try:
        feature = json.loads(ACTIVE_POINTER.read_text(encoding="utf-8")).get("feature")
    except (json.JSONDecodeError, OSError):
        sys.exit(0)
    if not feature:
        sys.exit(0)

    snap = REPO / "specs" / feature / "pipeline-context.md"
    if snap.exists():
        # stdout -> injected into context
        print(snap.read_text(encoding="utf-8"))
    else:
        print(
            f"# PIPELINE REHYDRATION\n"
            f"Active feature: {feature}. No snapshot found — rehydrate manually by "
            f"reading specs/{feature}/state.json, plan.md, and task-graph.json, "
            f"then continue per task-graph order."
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
