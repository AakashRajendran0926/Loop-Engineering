"""loop_lib.py — shared helpers for the V1.0 loop layer (DevelopmentUpdates.md).

Two things live here so the divergence-monitor and the state-updater agree
byte-for-byte on what "healthy" means:

  * footprint matching (is this file inside a task's declared footprint?)
  * recompute_health() — derive state.json's `health` from disk signals ONLY.

Design principle D-3: divergence is detected mechanically from disk, never by an
agent's self-assessment. `health` is therefore a pure function of on-disk
artifacts (review verdicts in state.json + footprint counts in divergence.json +
the approved budget). Any hook can recompute it and land on the same answer, so
it does not matter who writes it last — they converge.
"""

import fnmatch
import os

_here = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, _here)
import harness_lib as lib  # noqa: E402

DEFAULT_CAPS = {"mechanical": 2, "contract": 1, "ambiguity": 0, "security": 0}


def _norm(p):
    return str(p).replace("\\", "/").strip()


def in_footprint(rel_path, footprint):
    """True if rel_path is covered by any footprint entry (dir/, file, or glob).
    Mirrors extract-deps.py matching so the gate and the plan speak one language."""
    rel = _norm(rel_path)
    for entry in footprint or []:
        e = _norm(entry)
        if not e:
            continue
        if e.endswith("/") and rel.startswith(e):
            return True
        if any(c in e for c in "*?[") and fnmatch.fnmatch(rel, e):
            return True
        if rel == e or rel.startswith(e + "/"):
            return True
    return False


def read_current(spec_dir):
    """The task the dispatch-gate marked in flight (or None)."""
    return lib.read_json(os.path.join(spec_dir, ".current.json"))


def divergence_path(spec_dir):
    return os.path.join(spec_dir, "divergence.json")


def recompute_health(state, spec_dir, config):
    """Derive and set state['health'] / state['health_reasons'] from disk signals.

    Mutates and returns `state`; the CALLER writes state.json (so there is one
    write per hook invocation). Every reason added is a HALT-level condition —
    health flips to 'tripped' iff at least one is present, which is what
    dispatch-gate G3 refuses on. Warn-level signals (e.g. a first footprint
    violation) intentionally do not trip; they wait for the halt threshold.
    """
    caps = {**DEFAULT_CAPS, **config.get("retries", {})}
    cb = config.get("circuit_breaker", {})
    max_esc = int(cb.get("max_escalations_per_feature", 2))
    halt_fp = int((cb.get("footprint_violations", {}) or {}).get("halt", 2))
    halt_pct = int((cb.get("burndown", {}) or {}).get("halt_pct", 100))

    reasons = []
    tasks = state.get("tasks", {}) or {}

    for tid, t in sorted(tasks.items()):
        fc = t.get("pending_failure_class")
        if fc:
            cap = int(caps.get(fc, 0))
            if int(t.get("attempts", 0)) > cap:
                reasons.append("retry_cap:%s(class=%s,attempts=%s,cap=%s)"
                               % (tid, fc, t.get("attempts", 0), cap))
            if fc in ("ambiguity", "security"):
                reasons.append("zero_retry_class:%s(%s)" % (tid, fc))
        if t.get("status") == "escalate" or t.get("repeat_finding"):
            reasons.append("repeat_finding:%s" % tid)

    escalations = sum(1 for t in tasks.values() if t.get("status") == "escalate")
    if escalations > max_esc:
        reasons.append("too_many_escalations:%d>%d (feature likely over-scoped)"
                       % (escalations, max_esc))

    div = lib.read_json(divergence_path(spec_dir), default={}) or {}
    fp_total = sum((div.get("footprint_violations", {}) or {}).values())
    if fp_total >= halt_fp:
        reasons.append("footprint_violations:%d>=halt %d" % (fp_total, halt_fp))

    # Token burndown vs the approved budget, if both are on disk.
    spent = div.get("tokens_spent")
    approvals = lib.read_json(os.path.join(spec_dir, "approvals.json"), default={}) or {}
    budget = approvals.get("budget")
    if isinstance(spent, (int, float)) and isinstance(budget, (int, float)) and budget > 0:
        pct = 100.0 * spent / budget
        if pct >= halt_pct:
            reasons.append("burndown:%d%%>=halt %d%%" % (int(pct), halt_pct))

    state["health"] = "tripped" if reasons else "ok"
    state["health_reasons"] = reasons
    return state
