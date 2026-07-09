#!/usr/bin/env python3
"""approve-plan.py — write specs/<feature>/approvals.json (the autonomy boundary).

This is the signature the human applies to the DRAFT PLAN PACKAGE (tasks,
footprints, contracts, computed task-graph, token budget). After it exists and
its hash matches plan.md, dispatch-gate G1 lets automation run — and any later
edit to plan.md breaks the hash and re-freezes the pipeline until re-approval.

    python .claude/scripts/approve-plan.py <feature> [--budget N] [--approver NAME] [--force]

The plan_hash MUST be computed exactly as dispatch-gate.py computes it:
    sha256(plan.md bytes)   — keep these two in lock-step.

Approval also runs the requirements gate (req_lib): it REFUSES to sign a plan
built on ambiguous intent, vague requirements, a broken flow, or one that has
diverged from the acceptance criteria (uncovered ACs / scope creep). --force
overrides and records the override in approvals.json, so the bypass is logged,
never silent.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import harness_lib as lib  # noqa: E402
import req_lib as req  # noqa: E402
import version_lib as ver  # noqa: E402

REPO = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("feature")
    ap.add_argument("--budget", type=int, default=None,
                    help="approved token budget for this feature (burndown ceiling)")
    ap.add_argument("--approver", default=os.environ.get("USER") or os.environ.get("USERNAME") or "human")
    ap.add_argument("--now", default=None, help="override timestamp (tests)")
    ap.add_argument("--force", action="store_true",
                    help="sign despite requirements-gate problems (logged as an override)")
    args = ap.parse_args(argv)

    spec = REPO / "specs" / args.feature
    plan = spec / "plan.md"
    if not plan.exists():
        sys.stderr.write("approve-plan: %s not found — nothing to approve.\n" % plan)
        return 1

    cfg = lib.load_config(str(REPO)).get("requirements", {})
    problems = req.check_all(str(spec), is_specialist=True, cfg=cfg)
    if problems and not args.force:
        sys.stderr.write(
            "approve-plan: REFUSED — the plan is not ready to sign:\n  - "
            + "\n  - ".join(problems)
            + "\nResolve these with the user (re-interview / re-plan), or pass --force "
              "to sign anyway (the override is logged in approvals.json).\n")
        return 2

    approvals = {
        "plan_hash": hashlib.sha256(plan.read_bytes()).hexdigest(),
        "budget": args.budget,
        "approver": args.approver,
        "timestamp": args.now or datetime.now(timezone.utc).isoformat(),
    }
    if problems and args.force:
        approvals["forced_override"] = problems
    (spec / "approvals.json").write_text(
        json.dumps(approvals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Version Controller: capture the pre-implementation dependency baseline and
    # the first development-version snapshot. Both must happen at approval — the
    # baseline is the pre-feature state, and dev history starts the moment the
    # decomposition is signed.
    try:
        deps = ver.snapshot_deps(str(REPO), lib.load_config(str(REPO)).get("versioning", {}).get("manifests"))
        lib.write_json(str(spec / "dependencies.baseline.json"), deps)
        ver.record_snapshot(str(spec), "approved", now=approvals["timestamp"])
    except Exception as e:  # versioning must never block an otherwise-valid approval
        sys.stderr.write("approve-plan: version baseline/snapshot skipped (%s)\n" % e)

    sys.stderr.write("approved '%s' (plan_hash %s…, budget %s)\n"
                     % (args.feature, approvals["plan_hash"][:12], approvals["budget"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
