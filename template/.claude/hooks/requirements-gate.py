#!/usr/bin/env python3
"""requirements-gate.py — PreToolUse hook on Task (the EXTENDED human gate).

Runs alongside dispatch-gate. Where dispatch-gate enforces the *schedule*
(approval hash, order, caps, breaker), this gate enforces *intention and
requirement quality* — it refuses to let automation proceed on ambiguous
intentions, vague/unstructured requirements, a wrong flow, or a plan that has
diverted from what the user asked for. On a block, the only legal move is to
re-enter interview mode with the user: these conditions route to a human by
design, because retrying them just manufactures confident wrong guesses.

Gated when the dispatch prompt carries `FEATURE: <slug>`:
  * any pipeline dispatch     -> R1 (intent), R2 (structured requirements)
  * a specialist (TASK-ID set) -> also R3 (flow order), R4 (requirement coverage)

Non-pipeline Task use (no FEATURE header) passes through untouched.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_lib as lib  # noqa: E402
import req_lib as req  # noqa: E402


def main():
    event = lib.read_hook_input()
    if event.get("tool_name") != "Task":
        lib.allow()

    prompt = (event.get("tool_input", {}) or {}).get("prompt", "") or ""
    m_feat = re.search(r"^FEATURE:\s*(\S+)", prompt, re.MULTILINE)
    if not m_feat:
        lib.allow()  # not a governed pipeline dispatch

    root = lib.project_dir(event)
    cfg = lib.load_config(root).get("requirements", {})
    if cfg.get("enabled", True) is False:
        lib.allow()

    feature = m_feat.group(1)
    spec_dir = os.path.join(root, "specs", feature)
    is_specialist = bool(re.search(r"^TASK-ID:\s*\S+", prompt, re.MULTILINE))

    problems = req.check_all(spec_dir, is_specialist, cfg)
    if not problems:
        lib.allow()

    lib.deny(
        "Human gate — requirements/intention not ready (Loop Engineering):\n"
        "  - " + "\n  - ".join(problems) + "\n\n"
        "These route to the USER, not a retry. Re-enter interview mode "
        "(discovery-interview skill): resolve the ambiguity, structure the "
        "acceptance criteria (AC1, AC2, …), and make the plan cover exactly what "
        "was asked. Update discovery.md / plan.md, then re-approve. Do not dispatch "
        "around this — a wrong requirement built perfectly is still wrong."
    )


if __name__ == "__main__":
    main()
