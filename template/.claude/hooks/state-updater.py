#!/usr/bin/env python
"""state-updater.py  —  SubagentStop + PostToolUse(Task) hook

The single writer of specs/<f>/state.json (Architecture.md §6). Agents never
edit state; this hook reconciles it from the review verdicts on disk each time a
subagent finishes. Reconciler pattern — it does not need to know which subagent
just ran; it re-derives the whole state from the review.*.json files + the task
graph. Idempotent, and always exits 0 (it updates, it never gates).

Per-task status:  pending -> needs_retry -> done  (or -> escalate)
Counters live here, on disk, so context compaction can never turn a finite retry
loop into an infinite one.
"""

import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib as lib  # noqa: E402

REVIEW_RE = re.compile(r"review\.(?P<task>.+)\.(?P<n>\d+)\.json$")


def reviews_by_task(feature_dir):
    out = {}
    for path in glob.glob(os.path.join(feature_dir, "review.*.json")):
        m = REVIEW_RE.search(os.path.basename(path))
        if not m:
            continue
        out.setdefault(m.group("task"), []).append((int(m.group("n")), path))
    for task in out:
        out[task].sort(key=lambda x: x[0])
    return out


def finding_keys(review):
    keys = set()
    for f in review.get("findings", []) or []:
        keys.add((f.get("file", ""), f.get("detail", "") or f.get("id", "")))
    return keys


def main():
    hook_input = lib.read_hook_input()
    root = lib.project_dir(hook_input)
    feature_dir = lib.current_feature_dir(root)
    if not feature_dir:
        lib.allow()

    state_path = os.path.join(feature_dir, "state.json")
    state = lib.read_json(state_path, default={}) or {}
    state.setdefault("feature", os.path.basename(feature_dir))
    state.setdefault("tasks", {})

    graph = lib.read_json(os.path.join(feature_dir, "task-graph.json"), default={}) or {}
    for t in graph.get("tasks", []):
        state["tasks"].setdefault(t["id"], {"status": "pending", "attempts": 0})

    by_task = reviews_by_task(feature_dir)
    for task, entries in by_task.items():
        latest_n, latest_path = entries[-1]
        latest = lib.read_json(latest_path, default={}) or {}
        ts = state["tasks"].setdefault(task, {"status": "pending", "attempts": 0})
        ts["attempts"] = latest_n
        ts["last_review"] = os.path.basename(latest_path)

        if latest.get("status") == "pass":
            ts["status"] = "done"
            ts.pop("pending_failure_class", None)
            continue

        ts["status"] = "needs_retry"
        ts["pending_failure_class"] = latest.get("failure_class", "mechanical")

        # Repeated-finding early stop: reviewer flag, or identical findings twice.
        repeat = bool(latest.get("repeat_finding"))
        if not repeat and len(entries) >= 2:
            prev = lib.read_json(entries[-2][1], default={}) or {}
            repeat = bool(finding_keys(latest) & finding_keys(prev))
        if repeat:
            ts["status"] = "escalate"
            ts["repeat_finding"] = True

    lib.write_json(state_path, state)
    lib.allow()


if __name__ == "__main__":
    main()
