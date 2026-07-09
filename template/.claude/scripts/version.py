#!/usr/bin/env python3
"""version.py — the Version Controller CLI.

    python .claude/scripts/version.py baseline  <feature>            # capture pre-feature deps
    python .claude/scripts/version.py deps       <feature>            # recompute + show dep changes
    python .claude/scripts/version.py ack-deps   <feature> [--who X]  # acknowledge major bumps
    python .claude/scripts/version.py bump       <feature> [--level major|minor|patch] [--now TS]
    python .claude/scripts/version.py snapshot   <feature> <milestone> [--now TS]
    python .claude/scripts/version.py status     <feature>

Dependency baseline is captured at approval (approve-plan.py calls `baseline`);
the tracker hook diffs against it; `bump` writes VERSION + CHANGELOG; `snapshot`
records development history. Keep sha256/semver logic in version_lib so the hook
and this CLI never disagree.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import harness_lib as lib  # noqa: E402
import version_lib as ver  # noqa: E402

REPO = str(Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()))


def spec_of(feature):
    return os.path.join(REPO, "specs", feature)


def _cfg():
    return lib.load_config(REPO).get("versioning", {})


def cmd_baseline(a):
    spec = spec_of(a.feature)
    os.makedirs(spec, exist_ok=True)
    deps = ver.snapshot_deps(REPO, _cfg().get("manifests"))
    lib.write_json(os.path.join(spec, "dependencies.baseline.json"), deps)
    sys.stderr.write("baseline: %d dependencies captured for '%s'\n" % (len(deps), a.feature))
    return 0


def cmd_deps(a):
    spec = spec_of(a.feature)
    baseline = lib.read_json(os.path.join(spec, "dependencies.baseline.json"))
    if baseline is None:
        sys.stderr.write("no baseline — run: version.py baseline %s (at approval)\n" % a.feature)
        return 1
    changes = ver.diff_deps(baseline, ver.snapshot_deps(REPO, _cfg().get("manifests")))
    prev = lib.read_json(os.path.join(spec, "dependencies.json"), default={}) or {}
    lib.write_json(os.path.join(spec, "dependencies.json"), {
        "changes": changes, "has_major": ver.has_major(changes),
        "acknowledged": bool(prev.get("acknowledged")) and not ver.has_major(changes),
    })
    print(json.dumps(changes, indent=2))
    return 0


def cmd_ack_deps(a):
    spec = spec_of(a.feature)
    dp = os.path.join(spec, "dependencies.json")
    deps = lib.read_json(dp, default={}) or {}
    deps["acknowledged"] = True
    deps["acknowledged_by"] = a.who
    deps["acknowledged_at"] = a.now or datetime.now(timezone.utc).isoformat()
    lib.write_json(dp, deps)
    sys.stderr.write("acknowledged major dependency changes for '%s'\n" % a.feature)
    return 0


def cmd_bump(a):
    spec = spec_of(a.feature)
    level = a.level or ver.plan_version_impact(spec) or "minor"
    current = ver.read_root_version(REPO)
    new = ver.next_version(current, level)
    now = a.now or datetime.now(timezone.utc).isoformat()

    with open(os.path.join(REPO, "VERSION"), "w", encoding="utf-8") as fh:
        fh.write(new + "\n")

    deps = lib.read_json(os.path.join(spec, "dependencies.json"), default={}) or {}
    dep_line = "none"
    if deps.get("changes"):
        dep_line = ", ".join("%s %s->%s (%s)" % (c["name"], c["from"], c["to"], c["severity"])
                             for c in deps["changes"])
    entry = ("## %s — %s\n\n- Release level: **%s**\n- Feature: `%s`\n- Dependency changes: %s\n\n"
             % (new, now[:10] if now else "", level, a.feature, dep_line))
    clog = os.path.join(REPO, "CHANGELOG.md")
    existing = ""
    if os.path.exists(clog):
        with open(clog, "r", encoding="utf-8", errors="ignore") as fh:
            existing = fh.read()
    header = "# Changelog\n\n"
    body = existing[len(header):] if existing.startswith(header) else existing
    with open(clog, "w", encoding="utf-8") as fh:
        fh.write(header + entry + body)

    lib.write_json(os.path.join(spec, "version.json"),
                   {"code_version": new, "level": level, "previous": current, "at": now})
    sys.stderr.write("bumped %s -> %s (%s) and wrote CHANGELOG for '%s'\n" % (current, new, level, a.feature))
    return 0


def cmd_snapshot(a):
    dev = ver.record_snapshot(spec_of(a.feature), a.milestone,
                              now=a.now or datetime.now(timezone.utc).isoformat())
    sys.stderr.write("dev-version %d snapshot '%s' for '%s'\n" % (dev, a.milestone, a.feature))
    return 0


def cmd_status(a):
    spec = spec_of(a.feature)
    deps = lib.read_json(os.path.join(spec, "dependencies.json"), default={}) or {}
    vj = lib.read_json(os.path.join(spec, "version.json"), default={}) or {}
    hist = lib.read_json(os.path.join(spec, "history.json"), default={}) or {}
    print("Version status — %s" % a.feature)
    print("  code_version: %s (%s)" % (vj.get("code_version", "—"), vj.get("level", "—")))
    print("  dependency changes: %d (major=%s, acknowledged=%s)"
          % (len(deps.get("changes", [])), deps.get("has_major", False), deps.get("acknowledged", False)))
    print("  dev_version: %s (%d milestones)" % (hist.get("dev_version", "—"), len(hist.get("entries", []))))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("baseline", "deps", "status"):
        s = sub.add_parser(name); s.add_argument("feature")
    s = sub.add_parser("ack-deps"); s.add_argument("feature"); s.add_argument("--who", default="human"); s.add_argument("--now")
    s = sub.add_parser("bump"); s.add_argument("feature"); s.add_argument("--level", choices=["major", "minor", "patch"]); s.add_argument("--now")
    s = sub.add_parser("snapshot"); s.add_argument("feature"); s.add_argument("milestone"); s.add_argument("--now")
    a = ap.parse_args(argv)
    return {
        "baseline": cmd_baseline, "deps": cmd_deps, "ack-deps": cmd_ack_deps,
        "bump": cmd_bump, "snapshot": cmd_snapshot, "status": cmd_status,
    }[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
