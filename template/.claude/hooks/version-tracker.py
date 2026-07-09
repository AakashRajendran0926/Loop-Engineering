#!/usr/bin/env python3
"""version-tracker.py — PostToolUse hook on Edit|Write (dependency versioning).

When a specialist edits a dependency manifest (package.json, requirements.txt,
pyproject.toml, go.mod, Cargo.toml), recompute the change set for the active
feature against its baseline (captured at approval) and record it in
specs/<f>/dependencies.json. This is the producer; version-gate.py enforces that
a major bump is acknowledged before commit.

Records only — always exits 0 (a first out-of-footprint concern is the
divergence-monitor's job; this one just keeps the dependency ledger current). If
no baseline exists yet (feature not approved, or non-pipeline edit), it no-ops:
the baseline must be the pre-feature state, so it is captured at approval, never
guessed here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib as lib  # noqa: E402
import version_lib as ver  # noqa: E402


def main():
    event = lib.read_hook_input()
    if event.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        lib.allow()

    fpath = (event.get("tool_input", {}) or {}).get("file_path")
    if not fpath:
        lib.allow()

    root = lib.project_dir(event)
    config = lib.load_config(root)
    vcfg = config.get("versioning", {})
    if vcfg.get("enabled", True) is False:
        lib.allow()
    manifests = vcfg.get("manifests", ver.DEFAULT_MANIFESTS)
    if not ver.is_manifest(os.path.basename(fpath), manifests):
        lib.allow()

    feature_dir = lib.current_feature_dir(root)
    if not feature_dir:
        lib.allow()
    baseline = lib.read_json(os.path.join(feature_dir, "dependencies.baseline.json"))
    if not baseline:
        lib.allow()  # no pre-feature baseline captured -> nothing to diff against

    current = ver.snapshot_deps(root, manifests)
    changes = ver.diff_deps(baseline, current)
    prev = lib.read_json(os.path.join(feature_dir, "dependencies.json"), default={}) or {}
    lib.write_json(os.path.join(feature_dir, "dependencies.json"), {
        "changes": changes,
        "has_major": ver.has_major(changes),
        "acknowledged": bool(prev.get("acknowledged")) and not ver.has_major(changes),
    })

    if ver.has_major(changes):
        majors = [c["name"] for c in changes if c["severity"] == "major"]
        lib.warn("Dependency versioning: MAJOR change to %s. This must be acknowledged "
                 "before commit (version-gate) — run `python .claude/scripts/version.py "
                 "ack-deps <feature>` once reviewed." % ", ".join(majors))
    lib.allow()


if __name__ == "__main__":
    main()
