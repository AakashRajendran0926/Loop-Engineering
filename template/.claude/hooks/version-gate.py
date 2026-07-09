#!/usr/bin/env python3
"""version-gate.py — PreToolUse hook on Bash (the Version Controller's commit gate).

Runs beside commit-gate.py. Where commit-gate enforces *review*, this enforces
*versioning*: a feature does not commit until all three responsibilities are
satisfied for it. Policy in an exit code, not a checklist someone remembers.

Blocks `git commit` / `gh pr create` when, for the active feature:
  V1 (dependency)   a MAJOR dependency bump is unacknowledged
  V2 (code)         no semver bump / CHANGELOG entry recorded for the feature
  V3 (development)  no development-version history captured

Each sub-check is individually toggleable in harness.config.json → versioning;
the whole gate is bypassable via gates.commit_requires_version:false (a logged,
branch-scoped decision). Non-feature commits pass through untouched.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib as lib  # noqa: E402
import version_lib as ver  # noqa: E402

COMMIT_RE = re.compile(r"\bgit\s+commit\b|\bgh\s+pr\s+create\b")


def main():
    event = lib.read_hook_input()
    if event.get("tool_name") != "Bash":
        lib.allow()
    if not COMMIT_RE.search((event.get("tool_input", {}) or {}).get("command", "")):
        lib.allow()

    root = lib.project_dir(event)
    config = lib.load_config(root)
    if not config.get("gates", {}).get("commit_requires_version", True):
        lib.allow()
    vcfg = config.get("versioning", {})
    if vcfg.get("enabled", True) is False:
        lib.allow()

    feature_dir = lib.current_feature_dir(root)
    if not feature_dir:
        lib.allow()
    feature = os.path.basename(feature_dir)

    # V1 — dependency: major bumps acknowledged
    if vcfg.get("require_major_dep_ack", True):
        deps = lib.read_json(os.path.join(feature_dir, "dependencies.json"), default={}) or {}
        if deps.get("has_major") and not deps.get("acknowledged"):
            majors = [c["name"] for c in deps.get("changes", []) if c.get("severity") == "major"]
            lib.deny(
                "Commit blocked (version): unacknowledged MAJOR dependency change in '%s' "
                "(%s). Review the bump, then: python .claude/scripts/version.py ack-deps %s"
                % (feature, ", ".join(majors), feature))

    # V2 — code: semver bump + changelog for this feature
    if vcfg.get("require_code_bump", True):
        vj = lib.read_json(os.path.join(feature_dir, "version.json"), default={}) or {}
        code_version = vj.get("code_version")
        changelog = os.path.join(root, "CHANGELOG.md")
        logged = False
        if code_version and os.path.exists(changelog):
            try:
                logged = code_version in open(changelog, "r", encoding="utf-8", errors="ignore").read()
            except OSError:
                logged = False
        if not (code_version and logged):
            lib.deny(
                "Commit blocked (version): feature '%s' has no recorded code version + "
                "CHANGELOG entry. Bump it: python .claude/scripts/version.py bump %s" % (feature, feature))

    # V3 — development: version history captured
    if vcfg.get("require_dev_history", True):
        hist = lib.read_json(os.path.join(feature_dir, "history.json"), default={}) or {}
        if not hist.get("entries"):
            lib.deny(
                "Commit blocked (version): no development-version history for '%s'. "
                "Snapshot it: python .claude/scripts/version.py snapshot %s integrated" % (feature, feature))

    lib.allow()


if __name__ == "__main__":
    main()
