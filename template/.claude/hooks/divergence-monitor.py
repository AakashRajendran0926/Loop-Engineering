#!/usr/bin/env python3
"""divergence-monitor.py — PostToolUse hook on Edit|Write (DevelopmentUpdates.md §5).

The footprint-violation detector: the approved plan *declared* which files each
task may touch, so any write outside that set is, by definition, implementation
wandering from the approved plan — divergence made measurable. It is the sharpest
goal-diversion signal precisely because footprints already live on disk in
task-graph.json; no agent judgment is involved (design principle D-3).

Thresholds (harness.config.json → circuit_breaker.footprint_violations):
  warn (default 1)  -> record + warn on stderr, let the run continue
  halt (default 2)  -> record + trip the breaker in state.json; exit 2 so the
                       subagent sees it immediately. dispatch-gate G3 then refuses
                       every further dispatch until a human resolves it.

Always safe: if there is no task in flight (.current.json absent) the edit is not
a pipeline edit and passes untouched. Harness/runtime paths are never policed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib as lib  # noqa: E402
import loop_lib as loop  # noqa: E402

IGNORE_PREFIXES = ("specs/", ".claude/", "graphify-out/", ".git/")


def rel_to_repo(root, file_path):
    if not file_path:
        return None
    ap = file_path if os.path.isabs(file_path) else os.path.join(root, file_path)
    rel = os.path.relpath(os.path.abspath(ap), os.path.abspath(root))
    return rel.replace("\\", "/")


def main():
    event = lib.read_hook_input()
    if event.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        lib.allow()

    root = lib.project_dir(event)
    ti = event.get("tool_input", {}) or {}
    rel = rel_to_repo(root, ti.get("file_path"))
    if not rel:
        lib.allow()

    feature_dir = lib.current_feature_dir(root)
    if not feature_dir:
        lib.allow()
    cur = loop.read_current(feature_dir)
    if not cur or not cur.get("task_id"):
        lib.allow()  # no task in flight -> not a policed pipeline edit

    # never police harness/runtime artifacts
    if any(rel == p.rstrip("/") or rel.startswith(p) for p in IGNORE_PREFIXES):
        lib.allow()

    task_id = cur["task_id"]
    graph = lib.read_json(os.path.join(feature_dir, "task-graph.json"), default={}) or {}
    task = next((t for t in graph.get("tasks", []) if t.get("id") == task_id), None)
    if task is None:
        lib.allow()  # unknown task -> don't invent a violation

    footprint = task.get("footprint", [])
    if loop.in_footprint(rel, footprint):
        lib.allow()  # inside the declared footprint — all good

    # ---- violation ----------------------------------------------------
    config = lib.load_config(root)
    cb = (config.get("circuit_breaker", {}) or {}).get("footprint_violations", {}) or {}
    warn_at, halt_at = int(cb.get("warn", 1)), int(cb.get("halt", 2))

    div = lib.read_json(loop.divergence_path(feature_dir), default={}) or {}
    fpv = div.setdefault("footprint_violations", {})
    fpv[task_id] = int(fpv.get(task_id, 0)) + 1
    div.setdefault("events", []).append({"task": task_id, "file": rel})
    lib.write_json(loop.divergence_path(feature_dir), div)

    # recompute health from disk and persist (health is derived; writers converge)
    state = lib.read_json(os.path.join(feature_dir, "state.json"), default={}) or {}
    state.setdefault("tasks", {})
    loop.recompute_health(state, feature_dir, config)
    lib.write_json(os.path.join(feature_dir, "state.json"), state)

    total = sum(fpv.values())
    msg = ("Footprint violation: task '%s' wrote '%s', outside its declared "
           "footprint %s. Violations this feature: %d." % (task_id, rel, footprint, total))
    if total >= halt_at:
        lib.deny(msg + " Circuit breaker TRIPPED — all further dispatch is refused. "
                       "Escalate to the user with the evidence in state.json / divergence.json.")
    if total >= warn_at:
        lib.warn(msg + " (warning; breaker trips at %d)" % halt_at)
    lib.allow()


if __name__ == "__main__":
    main()
