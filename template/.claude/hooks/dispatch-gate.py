#!/usr/bin/env python3
"""
dispatch-gate.py — PreToolUse hook on the Task tool.

The load-bearing enforcement point of the harness. Blocks any subagent
dispatch that violates policy, using exit code 2 (stderr is fed back to
the orchestrator as the reason).

Checks, in order (cheapest first):
  G0  Harness applies?      Only gate pipeline dispatches (TASK-ID present).
  G1  Plan approved?        specs/<f>/approvals.json exists, hash matches plan.md.
  G2  Graph fresh?          task-graph.json newer than plan.md.
  G3  Breaker healthy?      state.json health != "tripped".
  G4  Edges satisfied?      all upstream tasks in task-graph.json are "done".
  G5  Retries under cap?    attempt counter < cap for pending failure class.
  G6  Context bounded?      dispatch prompt within budget (Context Rule 1).

On allow, records specs/<f>/.current.json (the task now in flight) so the
divergence-monitor knows which footprint to police for the writes that follow.

Dispatch prompt convention (enforced by G0): the orchestrator's Task prompt
must begin with a header block:

    TASK-ID: backend-orders-api
    FEATURE: order-cancellation

Exit codes:
  0 = allow dispatch
  2 = block dispatch (stderr message tells the orchestrator its legal move)
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Prefer the project dir Claude Code passes; fall back to cwd (hooks run from the
# repo root, so both agree in practice — the env var just hardens odd launches).
REPO = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
CONFIG_PATH = REPO / "harness.config.json"

DEFAULT_CAPS = {"mechanical": 2, "contract": 1, "ambiguity": 0, "security": 0}
# Context Rule 1: a specialist gets task + contract slice + (retry) findings —
# never the full plan/discovery/history. A bloated dispatch prompt is that leak.
DEFAULT_PROMPT_TOKEN_MAX = 8000


def block(msg: str) -> None:
    print(f"[dispatch-gate] BLOCKED: {msg}", file=sys.stderr)
    sys.exit(2)


def load_json(path: Path, what: str, required: bool = True):
    if not path.exists():
        if required:
            block(f"{what} not found at {path}. It must exist before dispatch.")
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        block(f"{what} at {path} is not valid JSON. Fix or regenerate it.")


def main() -> None:
    event = json.load(sys.stdin)

    if event.get("tool_name") != "Task":
        sys.exit(0)  # not ours

    prompt = (event.get("tool_input") or {}).get("prompt", "") or ""

    # ---- G0: does the harness govern this dispatch? -------------------
    m_task = re.search(r"^TASK-ID:\s*(\S+)", prompt, re.MULTILINE)
    m_feat = re.search(r"^FEATURE:\s*(\S+)", prompt, re.MULTILINE)
    if not m_task or not m_feat:
        # Non-pipeline subagent use (e.g. context-researcher during planning,
        # or ad-hoc usage) is allowed through untouched.
        sys.exit(0)

    task_id, feature = m_task.group(1), m_feat.group(1)
    spec = REPO / "specs" / feature
    if not spec.is_dir():
        block(f"specs/{feature}/ does not exist. Run /feature to start the pipeline.")

    cfg = load_json(CONFIG_PATH, "harness.config.json", required=False) or {}
    caps = {**DEFAULT_CAPS, **cfg.get("retries", {})}

    # ---- G6: context is bounded (Rule 1 sanity check) ------------------
    # Cheap and early: a dispatch prompt carrying the whole plan/history is the
    # context-distractor the loop layer exists to prevent. Approx tokens = chars/4.
    prompt_token_max = int(cfg.get("budgets", {}).get("dispatch_prompt_max", DEFAULT_PROMPT_TOKEN_MAX))
    approx_tokens = len(prompt) // 4
    if approx_tokens > prompt_token_max:
        block(
            f"dispatch prompt for '{task_id}' is ~{approx_tokens} tokens "
            f"(budget {prompt_token_max}). Context Rule 1: a specialist gets only "
            "its task entry, its contracts/ slice, and (on retry) the latest "
            "findings — never the full plan, discovery, or history. Rebuild the "
            "prompt from disk artifacts, task-scoped."
        )

    plan = spec / "plan.md"
    graph_p = spec / "task-graph.json"
    approvals_p = spec / "approvals.json"
    state_p = spec / "state.json"

    if not plan.exists():
        block(f"specs/{feature}/plan.md missing. Planning phase incomplete.")

    # ---- G1: plan approval + hash match (the autonomy boundary) -------
    approvals = load_json(approvals_p, "approvals.json")
    plan_hash = hashlib.sha256(plan.read_bytes()).hexdigest()
    if approvals.get("plan_hash") != plan_hash:
        block(
            "plan.md has changed since human approval (hash mismatch). "
            "Automation is frozen. Present the revised draft plan to the user "
            "and obtain re-approval (rewrite approvals.json) before any dispatch."
        )

    # ---- G2: task-graph freshness --------------------------------------
    if not graph_p.exists():
        block("task-graph.json missing. Run .claude/scripts/extract-deps.py first.")
    if graph_p.stat().st_mtime < plan.stat().st_mtime:
        block(
            "task-graph.json is older than plan.md (stale schedule). "
            "Regenerate it with extract-deps.py before dispatching."
        )
    graph = load_json(graph_p, "task-graph.json")

    known = {t["id"] for t in graph.get("tasks", [])}
    if task_id not in known:
        block(f"task '{task_id}' is not in task-graph.json. Dispatch only planned tasks.")

    state = load_json(state_p, "state.json", required=False) or {"tasks": {}, "health": "ok"}

    # ---- G3: circuit breaker -------------------------------------------
    if state.get("health") == "tripped":
        reasons = ", ".join(state.get("health_reasons", [])) or "unspecified"
        block(
            f"circuit breaker is TRIPPED ({reasons}). All dispatches refused. "
            "Your only legal move is to escalate to the user with the evidence "
            "in state.json, then resume after human resolution."
        )

    # ---- G4: dependency edges satisfied --------------------------------
    unmet = [
        e["from"]
        for e in graph.get("edges", [])
        if e.get("to") == task_id
        and state["tasks"].get(e["from"], {}).get("status") != "done"
    ]
    if unmet:
        block(
            f"upstream tasks not done for '{task_id}': {', '.join(sorted(set(unmet)))}. "
            "Dispatch tasks in task-graph order."
        )

    # ---- G5: retry cap ---------------------------------------------------
    t = state["tasks"].get(task_id, {})
    attempts = int(t.get("attempts", 0))
    fclass = t.get("pending_failure_class")  # set by state-updater from review.json
    if fclass is not None:
        cap = int(caps.get(fclass, 0))
        if attempts > cap:  # attempts counts completed tries; > cap means budget spent
            block(
                f"retry cap reached for '{task_id}' "
                f"(class={fclass}, attempts={attempts}, cap={cap} retries). "
                "Do NOT re-dispatch. Escalate to the user with the latest "
                f"review findings in specs/{feature}/."
            )
        if fclass in ("ambiguity", "security"):
            block(
                f"failure class '{fclass}' permits zero retries for '{task_id}'. "
                "Escalate to the user immediately."
            )

    # ---- record the in-flight task for the divergence-monitor ----------
    # A pointer file, not state.json (state.json stays single-writer:
    # state-updater). The Edit/Write hook reads this to know whose footprint
    # to police for the writes this dispatch is about to make.
    try:
        (spec / ".current.json").write_text(
            json.dumps({"task_id": task_id, "feature": feature}), encoding="utf-8")
    except OSError:
        pass  # advisory only — never fail a legal dispatch on a write hiccup

    sys.exit(0)  # all gates passed — dispatch allowed


if __name__ == "__main__":
    main()
