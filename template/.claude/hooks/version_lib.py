"""version_lib.py — the Version Controller core (dependency + code + development).

Three responsibilities, one deterministic engine (scripts do computation; files
carry state — same ethos as the rest of the harness):

  * Dependency versioning — parse manifests, diff current vs the feature's
    baseline, classify each change major/minor/patch, flag major bumps.
  * Code versioning — semver bump derived from a declared impact level, with a
    CHANGELOG.md entry.
  * Development versioning — hash-snapshot the spec artifacts at each milestone
    so a feature's evolution (approved -> integrated -> committed) is auditable.

Everything here is pure/deterministic and fails open on parse errors — a manifest
we can't read is skipped, never crashes a hook.
"""

import hashlib
import json
import os
import re

_here = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, _here)
import harness_lib as lib  # noqa: E402

DEFAULT_MANIFESTS = ["package.json", "requirements.txt", "pyproject.toml", "go.mod", "Cargo.toml"]
_SEMVER = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


# --------------------------------------------------------------------------- #
# semver
# --------------------------------------------------------------------------- #
def parse_semver(v):
    if not v:
        return None
    m = _SEMVER.search(str(v))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def classify(old, new):
    a, b = parse_semver(old), parse_semver(new)
    if a is None or b is None:
        return "unknown"
    if a[0] != b[0]:
        return "major"
    if a[1] != b[1]:
        return "minor"
    if a[2] != b[2]:
        return "patch"
    return "none"


def next_version(current, level):
    a = parse_semver(current) or (0, 0, 0)
    if level == "major":
        return "%d.0.0" % (a[0] + 1)
    if level == "patch":
        return "%d.%d.%d" % (a[0], a[1], a[2] + 1)
    return "%d.%d.0" % (a[0], a[1] + 1)  # minor (default)


# --------------------------------------------------------------------------- #
# manifest parsing (best-effort, dependency-free)
# --------------------------------------------------------------------------- #
def _p_package_json(text):
    out = {}
    try:
        data = json.loads(text)
    except Exception:
        return out
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        for name, ver in (data.get(key) or {}).items():
            out[name] = ver
    return out


def _p_requirements(text):
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9._-]+)\s*(?:==|~=|>=|<=|>|<)\s*([0-9][^\s;,]*)", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _p_pyproject(text):
    out = {}
    # PEP 621 / poetry — best effort, no TOML lib
    for m in re.finditer(r'"([A-Za-z0-9._-]+)\s*(?:==|>=|~=)\s*([0-9][^"\s,]*)"', text):
        out[m.group(1)] = m.group(2)
    for m in re.finditer(r'^\s*([A-Za-z0-9._-]+)\s*=\s*"[\^~]?([0-9][^"\s]*)"', text, re.M):
        out.setdefault(m.group(1), m.group(2))
    return out


def _p_gomod(text):
    out = {}
    for m in re.finditer(r'^\s*([^\s]+/[^\s]+)\s+v(\d+\.\d+\.\d+)', text, re.M):
        out[m.group(1)] = m.group(2)
    return out


def _p_cargo(text):
    out = {}
    for m in re.finditer(r'^\s*([A-Za-z0-9._-]+)\s*=\s*"([0-9][^"]*)"', text, re.M):
        out[m.group(1)] = m.group(2)
    for m in re.finditer(r'^\s*([A-Za-z0-9._-]+)\s*=\s*\{[^}]*version\s*=\s*"([0-9][^"]*)"', text, re.M):
        out[m.group(1)] = m.group(2)
    return out


_PARSERS = {
    "package.json": _p_package_json,
    "requirements.txt": _p_requirements,
    "pyproject.toml": _p_pyproject,
    "go.mod": _p_gomod,
    "Cargo.toml": _p_cargo,
}


def is_manifest(basename, manifests=None):
    return basename in (manifests or DEFAULT_MANIFESTS)


def snapshot_deps(root, manifests=None):
    """Aggregate declared dependency versions across all manifests found in root."""
    manifests = manifests or DEFAULT_MANIFESTS
    deps = {}
    for name in manifests:
        parser = _PARSERS.get(name)
        if not parser:
            continue
        path = os.path.join(root, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        for dep, ver in parser(text).items():
            deps["%s:%s" % (name, dep)] = ver
    return deps


def diff_deps(baseline, current):
    """List of {name, from, to, kind, severity}; kind in added|removed|bumped."""
    changes = []
    for key in sorted(set(baseline) | set(current)):
        old, new = baseline.get(key), current.get(key)
        if old == new:
            continue
        if old is None:
            changes.append({"name": key, "from": None, "to": new, "kind": "added", "severity": "minor"})
        elif new is None:
            changes.append({"name": key, "from": old, "to": None, "kind": "removed", "severity": "major"})
        else:
            sev = classify(old, new)
            changes.append({"name": key, "from": old, "to": new, "kind": "bumped",
                            "severity": sev if sev != "none" else "patch"})
    return changes


def has_major(changes):
    return any(c["severity"] == "major" for c in changes)


# --------------------------------------------------------------------------- #
# development versioning — artifact snapshots
# --------------------------------------------------------------------------- #
SNAPSHOT_TARGETS = ["discovery.md", "plan.md", "task-graph.json", "approvals.json"]


def _sha1(path):
    try:
        with open(path, "rb") as fh:
            return "sha1:" + hashlib.sha1(fh.read()).hexdigest()[:16]
    except OSError:
        return None


def snapshot_artifacts(spec_dir):
    hashes = {}
    for rel in SNAPSHOT_TARGETS:
        h = _sha1(os.path.join(spec_dir, rel))
        if h:
            hashes[rel] = h
    cdir = os.path.join(spec_dir, "contracts")
    if os.path.isdir(cdir):
        for name in sorted(os.listdir(cdir)):
            h = _sha1(os.path.join(cdir, name))
            if h:
                hashes["contracts/" + name] = h
    for name in sorted(os.listdir(spec_dir)) if os.path.isdir(spec_dir) else []:
        if re.match(r"review\..*\.json$", name):
            h = _sha1(os.path.join(spec_dir, name))
            if h:
                hashes[name] = h
    return hashes


def record_snapshot(spec_dir, milestone, now=None):
    """Append a development-version entry to history.json; returns dev_version."""
    hp = os.path.join(spec_dir, "history.json")
    hist = lib.read_json(hp, default={}) or {}
    entries = hist.setdefault("entries", [])
    dev_version = len(entries) + 1
    entries.append({
        "dev_version": dev_version,
        "milestone": milestone,
        "at": now,
        "artifacts": snapshot_artifacts(spec_dir),
    })
    hist["dev_version"] = dev_version
    lib.write_json(hp, hist)
    return dev_version


# --------------------------------------------------------------------------- #
# code versioning — VERSION + CHANGELOG
# --------------------------------------------------------------------------- #
def read_root_version(root):
    p = os.path.join(root, "VERSION")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def plan_version_impact(spec_dir):
    """Level declared by the planner as `VERSION-IMPACT: major|minor|patch` in plan.md."""
    try:
        with open(os.path.join(spec_dir, "plan.md"), "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return None
    m = re.search(r"VERSION-IMPACT:\s*(major|minor|patch)", text, re.I)
    return m.group(1).lower() if m else None
