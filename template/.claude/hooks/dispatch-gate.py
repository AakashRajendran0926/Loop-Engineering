#!/usr/bin/env python
"""dispatch-gate.py  —  PreToolUse hook (matcher: Task)

Enforces the computed schedule (Architecture.md §5, §7). Blocks a specialist
dispatch when:

  (a) an upstream edge in task-graph.json is not `done` in state.json,
  (b) the task's retry counter for its pending failure class is at cap, or
  (c) task-graph.json is stale (older than plan.md) — schedules may never drift.

The orchestrator does not get a vote: caps and order live in files, read here.
Non-harness Task dispatches (no feature, no task id) pass through untouched.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib as lib  # noqa: E402

TASK_RE = re.compile(r"(?:^|\n)\s*task(?:[_ -]?id)?\s*[:=]\s*([A-Za-z0-9._-]+)", re.I)


def extract_task_id(tool_input, task_ids):
    """Find the task id in the dispatch. Prefer an explicit `task: <id>` line in
    the prompt/description; else match a known task id appearing as a token."""
    blob = "\n".join(
        str(tool_input.get(k, "")) for k in ("description", "prompt", "subagent_type")
    )
    m = TASK_RE.search(blob)
    if m and m.group(1) in task_ids:
        return m.group(1)
    if m:
        return m.group(1)
    for tid in sorted(task_ids, key=len, reverse=True):
        if re.search(r"\b" + re.escape(tid) + r"\b", blob):
            return tid
    return None


def cap_for(config, failure_class):
    return config.get("retries", {}).get(failure_class, 0)


def main():
    hook_input = lib.read_hook_input()
    if hook_input.get("tool_name") != "Task":
        lib.allow()

    root = lib.project_dir(hook_input)
    feature_dir = lib.current_feature_dir(root)
    if not feature_dir:
        lib.allow()

    graph_path = os.path.join(feature_dir, "task-graph.json")
    plan_path = os.path.join(feature_dir, "plan.md")
    graph = lib.read_json(graph_path)
    if not graph:
        lib.allow()  # no schedule yet (e.g. dispatching the planner itself)

    # (c) staleness
    if os.path.exists(plan_path) and os.path.getmtime(graph_path) < os.path.getmtime(plan_path):
        lib.deny(
            "Dispatch blocked (Loop Engineering): task-graph.json is older than plan.md. "
            "The plan changed — regenerate the schedule before dispatching:\n\n"
            "    python .claude/scripts/extract-deps.py --plan %s --out %s"
            % (os.path.relpath(plan_path, root), os.path.relpath(graph_path, root))
        )

    tasks = {t["id"]: t for t in graph.get("tasks", [])}
    tid = extract_task_id(hook_input.get("tool_input", {}) or {}, set(tasks))
    if not tid or tid not in tasks:
        lib.allow()  # not a scheduled task dispatch

    state = lib.read_json(os.path.join(feature_dir, "state.json"), default={}) or {}
    task_state = (state.get("tasks", {}) or {}).get(tid, {})
    config = lib.load_config(root)

    # (a) upstream ordering
    pending = []
    for edge in graph.get("edges", []):
        if edge.get("to") == tid:
            up = edge.get("from")
            if (state.get("tasks", {}) or {}).get(up, {}).get("status") != "done":
                pending.append("%s (%s)" % (up, edge.get("reason", "dependency")))
    if pending:
        lib.deny(
            "Dispatch blocked (Loop Engineering): task '%s' has unfinished upstream "
            "dependencies:\n  - %s\nDispatch those first — the schedule is computed, "
            "not negotiable." % (tid, "\n  - ".join(pending))
        )

    # (b) retry cap for the pending failure class
    pending_class = task_state.get("pending_failure_class")
    attempts = task_state.get("attempts", 0)
    if pending_class:
        cap = cap_for(config, pending_class)
        if attempts > cap:
            lib.deny(
                "Dispatch blocked (Loop Engineering): task '%s' has hit the retry cap "
                "for failure class '%s' (%d attempt(s), cap %d). Do not re-dispatch — "
                "escalate to the user: present the failure class, the conflicting "
                "constraint, and one specific question. Append the answer to "
                "discovery.md and reset counters." % (tid, pending_class, attempts, cap)
            )

    lib.allow()


if __name__ == "__main__":
    main()
