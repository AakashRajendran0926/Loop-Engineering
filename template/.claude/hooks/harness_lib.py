"""harness_lib.py — tiny shared IO layer for the Loop Engineering hooks.

Design rule (Architecture.md §1): agents do judgment, scripts do computation.
This module is pure computation + IO. It never blocks; callers decide policy
via exit codes. Everything here fails *open* — a missing config, an unparseable
state file, or an absent graphify install must never crash a hook and wedge the
user's session. A crashed hook is worse than an un-enforced one.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# Config defaults. harness.config.json (repo root) overrides these shallowly.
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "graphify": {"binary": "graphify", "index_path": "graphify-out/"},
    "retries": {"mechanical": 2, "contract": 1, "ambiguity": 0, "security": 0},
    "dependency_depth": 1,
    "retrieval_nudge": {
        # "block" -> exit 2 (deny + steer). "warn" -> exit 0 with a note. "off".
        "mode": "block",
        # broad searches over more than this many chars of pattern still pass if
        # they are scoped to a single existing file (see is_broad_search).
        "auto_init": True,
    },
    "observability": {"provider": "langfuse", "enabled": True},
    "gates": {"commit_requires_integration_review": True},
}


def read_hook_input():
    """Parse the JSON object Claude Code writes to a hook's stdin.

    Returns {} on any error so a malformed payload degrades to pass-through
    rather than a stack trace in the user's terminal.
    """
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def project_dir(hook_input=None):
    """Resolve the adopter repo root.

    CLAUDE_PROJECT_DIR is set by Claude Code for every hook; fall back to the
    hook payload's cwd, then the process cwd.
    """
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return os.path.abspath(env)
    if hook_input and hook_input.get("cwd"):
        return os.path.abspath(hook_input["cwd"])
    return os.path.abspath(os.getcwd())


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(root):
    """Read harness.config.json merged over DEFAULT_CONFIG. Fail-open to defaults."""
    path = os.path.join(root, "harness.config.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return _deep_merge(DEFAULT_CONFIG, json.load(fh))
    except Exception:
        return dict(DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Feature / spec artifact chain
# ---------------------------------------------------------------------------
def specs_root(root):
    return os.path.join(root, "specs")


def current_feature_dir(root):
    """The active feature = the specs/<f>/ dir with the most recently touched
    state.json (falling back to directory mtime). Returns None if no features.

    The orchestrator only works one feature at a time; recency is a good-enough
    and fully deterministic-per-filesystem selector without extra bookkeeping.
    """
    sroot = specs_root(root)
    if not os.path.isdir(sroot):
        return None
    best, best_mtime = None, -1.0
    for name in os.listdir(sroot):
        d = os.path.join(sroot, name)
        if not os.path.isdir(d):
            continue
        marker = os.path.join(d, "state.json")
        try:
            mtime = os.path.getmtime(marker if os.path.exists(marker) else d)
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = d, mtime
    return best


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Graphify state — the heart of the retrieval nudge
# ---------------------------------------------------------------------------
def graphify_state(root, config=None):
    """Return the graphify readiness of this repo.

    {
      "installed":   bool,  # binary on PATH or a saved interpreter recorded
      "graph_built": bool,  # graphify-out/graph.json exists (queryable now)
      "index_path":  str,   # resolved absolute index dir
      "graph_json":  str,   # resolved absolute graph.json path
    }
    Cheap by design: only shutil.which + os.path.exists. No subprocess, so it is
    safe to call on every Grep/Glob PreToolUse without adding latency.
    """
    config = config or load_config(root)
    binary = config.get("graphify", {}).get("binary", "graphify")
    index_rel = config.get("graphify", {}).get("index_path", "graphify-out/")
    index_path = os.path.join(root, index_rel)
    graph_json = os.path.join(index_path, "graph.json")

    saved_interp = os.path.join(index_path, ".graphify_python")
    installed = bool(shutil.which(binary)) or os.path.exists(saved_interp)

    return {
        "installed": installed,
        "graph_built": os.path.exists(graph_json),
        "index_path": index_path,
        "graph_json": graph_json,
    }


def graphify_used_this_session(transcript_path):
    """Best-effort scan of the session transcript for prior graph-first retrieval.

    True if any earlier tool call ran `graphify query|path|explain` or read from
    graphify-out/. Used so the nudge fires once and then gets out of the way —
    it steers the *first* broad search of a task, not every subsequent one.

    Reads at most the tail of the transcript to bound cost.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return False
    needles = ("graphify query", "graphify path", "graphify explain", "graphify-out")
    try:
        # Transcripts are JSONL and can be large; read the last ~512 KB only.
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as fh:
            if size > 512 * 1024:
                fh.seek(size - 512 * 1024)
                fh.readline()  # discard partial line
            blob = fh.read()
        return any(n in blob for n in needles)
    except Exception:
        return False


def git_changeset(root):
    """A stable fingerprint of the current pending diff (staged + unstaged vs HEAD).

    The commit gate compares this to the `changeset` recorded in the integration
    review verdict — a review only opens the gate for the diff it actually saw.
    The reviewer agent records its changeset with this same helper (see
    scripts/changeset.py) so the two always agree. Returns None outside a repo.
    """
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root, capture_output=True, text=True, timeout=15)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return None  # not a git repo -> no diff to fingerprint
        parts = []
        for args in (["git", "diff", "HEAD"], ["git", "diff", "--cached"]):
            r = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=15)
            parts.append(r.stdout or "")
        blob = "\n".join(parts)
        return "sha1:" + hashlib.sha1(blob.encode("utf-8", "ignore")).hexdigest()[:16]
    except Exception:
        return None


def deny(message):
    """Emit a PreToolUse block: stderr message + exit code 2.

    Exit 2 is the Claude Code contract for "deny this tool call and feed stderr
    back to the model" — the message becomes steering, not a user-facing error.
    """
    sys.stderr.write(message.rstrip() + "\n")
    sys.exit(2)


def warn(message):
    """Non-blocking note: surfaced to the model, tool still runs (exit 0)."""
    sys.stderr.write(message.rstrip() + "\n")
    sys.exit(0)


def allow():
    sys.exit(0)
