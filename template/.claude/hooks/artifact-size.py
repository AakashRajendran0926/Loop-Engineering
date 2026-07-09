#!/usr/bin/env python3
"""artifact-size.py — PostToolUse hook on Edit|Write (Context Rule 2, Phase L2).

Progressive compression is an enforced invariant, not a hope. Each stage of the
funnel must emit a COMPRESSED artifact; a 9k-token context pack is a bug, not a
bonus. This hook checks the size of the harness artifacts against the budgets in
harness.config.json and flags a breach (exit 2 → the message steers the author to
recompress). Non-artifact writes pass straight through.

Budgeted artifacts (harness.config.json → budgets, in approx tokens = chars/4):
  context-pack.md        -> context_pack       (default 2000)
  review.*.json          -> review_findings    (default 500)

Reads the file from disk after the write (so Edit/MultiEdit/Write are uniform).
Fail-open on any error — a size check must never wedge a session.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib as lib  # noqa: E402


def budget_key(basename):
    if basename == "context-pack.md":
        return "context_pack"
    if re.match(r"review\..*\.json$", basename):
        return "review_findings"
    return None


def main():
    event = lib.read_hook_input()
    if event.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        lib.allow()

    ti = event.get("tool_input", {}) or {}
    fpath = ti.get("file_path")
    if not fpath:
        lib.allow()

    key = budget_key(os.path.basename(fpath))
    if not key:
        lib.allow()  # not a budgeted artifact

    root = lib.project_dir(event)
    abs_path = fpath if os.path.isabs(fpath) else os.path.join(root, fpath)
    try:
        text = open(abs_path, "r", encoding="utf-8", errors="ignore").read()
    except OSError:
        lib.allow()

    budget = int(lib.load_config(root).get("budgets", {}).get(key, 0))
    if budget <= 0:
        lib.allow()
    approx = len(text) // 4
    if approx <= budget:
        lib.allow()

    lib.deny(
        "Artifact over budget (Loop Engineering, Context Rule 2): %s is ~%d tokens "
        "(budget %d for '%s'). Compress it — this artifact is a stage in the "
        "compression funnel, and an oversized one leaks distractors downstream. "
        "Keep only what the next stage needs; detail lives in the source, re-read "
        "on demand." % (os.path.basename(fpath), approx, budget, key)
    )


if __name__ == "__main__":
    main()
