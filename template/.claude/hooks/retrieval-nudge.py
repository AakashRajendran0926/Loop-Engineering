#!/usr/bin/env python
"""retrieval-nudge.py  —  PreToolUse hook (matcher: Grep|Glob)

Encodes the harness retrieval order: **Graphify -> skills -> memory**
(Architecture.md §6). A broad, exploratory file scan is redirected to a
dependency-aware `graphify query` first. Narrow, file-scoped searches pass
straight through — the nudge steers discovery, it does not police every grep.

Graphify-availability routing (the "search for context with graphify, and if it
isn't available, auto-init then use the graphify skill" flow):

  * graph built            -> steer to `graphify query "<derived question>"`
  * installed, no graph    -> instruct: run `/graphify .` to build the graph,
                              then query it   (auto-init / setup)
  * not installed          -> instruct: run `/graphify .` — the skill self-installs
                              via uv/pip on first run, then builds the graph

Loop-safety: the hook blocks a given session at most once. It drops a marker on
first fire; any subsequent broad search in the same session passes through. So
the worst case is a single steering message — a subagent can never get wedged.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib as lib  # noqa: E402


def _abs(root, p):
    return p if os.path.isabs(p) else os.path.join(root, p)


def is_satisfied_or_already_nudged(root, hook_input):
    """Graph-first retrieval is satisfied if the session already ran a graphify
    query, OR we already nudged this session (one-shot — never trap a loop)."""
    if lib.graphify_used_this_session(hook_input.get("transcript_path")):
        return True
    return _nudge_marker(root, hook_input, write=False)


def _nudge_marker(root, hook_input, write):
    session = hook_input.get("session_id") or "default"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session))
    marker = os.path.join(root, ".claude", ".harness", "nudged-" + safe + ".flag")
    if write:
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write("1")
        except Exception:
            pass
        return True
    return os.path.exists(marker)


def is_broad_search(root, tool_name, ti):
    """True when the search is repo-wide exploration rather than a localized look.

    Deterministic and scope-based (not pattern-guessing): a search is *narrow*
    (passes through) when it is confined to a specific file, a specific
    subdirectory, or a literal filename glob. Everything else is broad.
    """
    path = ti.get("path") or ""
    # file-scoped -> always narrow
    if path and os.path.isfile(_abs(root, path)):
        return False
    # explicit subdirectory (anything but repo root / cwd) -> narrow
    if path and path not in (".", "./", "") and os.path.abspath(_abs(root, path)) != os.path.abspath(root):
        return False

    if tool_name == "Grep":
        glob = ti.get("glob") or ""
        # a literal filename glob (no wildcard) is a targeted lookup
        if glob and "*" not in glob:
            return False
        return True

    if tool_name == "Glob":
        pattern = ti.get("pattern") or ""
        # concrete directory prefix before the first wildcard -> targeted
        head = pattern.split("*", 1)[0]
        prefix = os.path.dirname(head)
        if prefix and os.path.isdir(_abs(root, prefix)):
            return False
        return True

    return False


def derived_question(ti):
    term = (ti.get("pattern") or ti.get("glob") or "").strip()
    if not term or term in ("**", "**/*", "*"):
        return "what are the main modules here and how do they depend on each other"
    return 'where is `%s` defined and used, and what depends on it' % term


def compose(root, tool_name, ti, gstate, auto_init):
    q = derived_question(ti)
    tail = ("\nThis is a one-time nudge for this session — if the graph can't answer, "
            "just re-run your %s and it will pass through." % tool_name)

    if gstate["graph_built"]:
        return ("Graph-first retrieval (Loop Engineering).\n"
                "A knowledge graph already exists at %s. Query it before a raw %s — "
                "it returns dependency-aware context (importers, shared dependents) "
                "that a text scan cannot:\n\n"
                '    graphify query "%s"\n' % (gstate["graph_json"], tool_name, q)) + tail

    if gstate["installed"]:
        return ("Graph-first retrieval (Loop Engineering).\n"
                "Graphify is installed but no graph has been built for this repo yet. "
                "Initialize it before broad file scanning:\n\n"
                "    /graphify .            # build the knowledge graph (one-time setup)\n"
                '    graphify query "%s"\n' % q) + tail

    # not installed -> route to the self-installing skill
    if auto_init:
        return ("Graph-first retrieval (Loop Engineering).\n"
                "Graphify is not available in this project. Auto-initialize it before "
                "broad file scanning — the /graphify skill self-installs (uv/pip) on "
                "first run, then builds the graph:\n\n"
                "    /graphify .            # install + build the knowledge graph\n"
                '    graphify query "%s"\n' % q) + tail
    return ("Graph-first retrieval (Loop Engineering): graphify is not installed. "
            "Consider `/graphify .` to enable dependency-aware context.") + tail


def main():
    hook_input = lib.read_hook_input()
    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Grep", "Glob"):
        lib.allow()

    root = lib.project_dir(hook_input)
    config = lib.load_config(root)
    nudge_cfg = config.get("retrieval_nudge", {})
    mode = nudge_cfg.get("mode", "block")
    if mode == "off":
        lib.allow()

    ti = hook_input.get("tool_input", {}) or {}
    if not is_broad_search(root, tool_name, ti):
        lib.allow()
    if is_satisfied_or_already_nudged(root, hook_input):
        lib.allow()

    gstate = lib.graphify_state(root, config)
    message = compose(root, tool_name, ti, gstate, nudge_cfg.get("auto_init", True))

    # Record the one-shot marker BEFORE emitting, so a retry always progresses.
    _nudge_marker(root, hook_input, write=True)

    if mode == "warn":
        lib.warn(message)
    lib.deny(message)


if __name__ == "__main__":
    main()
