#!/usr/bin/env python
"""commit-gate.py  —  PreToolUse hook (matcher: Bash)

The harness's signature feature (Architecture.md §6). Blocks `git commit` and
`gh pr create` unless a passing **integration**-scope review verdict exists whose
`changeset` matches the current pending diff. Review approval is an exit code,
not prose the model can rationalize past.

Fail-open on everything that isn't a real violation: no active feature, gate
disabled, or not a git command -> pass through. Ordinary Claude Code usage in
the same repo is never touched — only commits made while a harness feature is in
flight are gated.
"""

import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib as lib  # noqa: E402

COMMIT_RE = re.compile(r"\bgit\s+commit\b|\bgh\s+pr\s+create\b")


def latest_integration_review(feature_dir):
    """Newest review.integration.*.json (by attempt number, then mtime)."""
    files = glob.glob(os.path.join(feature_dir, "review.integration.*.json"))
    if not files:
        return None, None
    files.sort(key=lambda p: os.path.getmtime(p))
    path = files[-1]
    return path, lib.read_json(path, default={})


def main():
    hook_input = lib.read_hook_input()
    if hook_input.get("tool_name") != "Bash":
        lib.allow()

    command = (hook_input.get("tool_input", {}) or {}).get("command", "")
    if not COMMIT_RE.search(command):
        lib.allow()

    root = lib.project_dir(hook_input)
    config = lib.load_config(root)
    if not config.get("gates", {}).get("commit_requires_integration_review", True):
        lib.allow()  # documented branch-scoped bypass (Usecase §6)

    feature_dir = lib.current_feature_dir(root)
    if not feature_dir:
        lib.allow()  # no harness feature in flight -> not our business

    feature = os.path.basename(feature_dir)
    review_path, review = latest_integration_review(feature_dir)

    if not review_path:
        lib.deny(
            "Commit blocked (Loop Engineering): no integration review for feature "
            "'%s'. All tasks must pass, then run the reviewer's integration pass over "
            "the combined diff before committing." % feature
        )

    if review.get("status") != "pass":
        lib.deny(
            "Commit blocked (Loop Engineering): integration review for '%s' is '%s', "
            "not 'pass' (%s). Resolve the findings and re-review."
            % (feature, review.get("status", "missing"), os.path.basename(review_path))
        )

    current = lib.git_changeset(root)
    recorded = review.get("changeset")
    if current and recorded and current != recorded:
        lib.deny(
            "Commit blocked (Loop Engineering): the passing integration review for '%s' "
            "was for a different diff (review changeset %s, current %s). The tree changed "
            "since review — re-run the integration review over the current diff."
            % (feature, recorded, current)
        )

    lib.allow()


if __name__ == "__main__":
    main()
