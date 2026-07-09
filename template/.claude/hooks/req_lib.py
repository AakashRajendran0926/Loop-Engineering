"""req_lib.py — the requirements/intention quality checker (the extended human gate).

Wrong implementations are born at the requirements and decomposition stages, not
at code. This module detects — mechanically, from disk, never by asking an agent
to grade itself (design principle D-3) — the four conditions that must summon a
human before automation proceeds:

  R1  ambiguous / unorganized intention   — Intent & Scope present, no unresolved
                                            markers (TBD / ??? / "clarify" / …)
  R2  vague / unstructured requirements    — required sections present, and >= N
                                            structured acceptance criteria (AC1, AC2…)
  R3  wrong flow                           — phase artifacts exist in order
                                            (discovery -> context-pack -> plan -> graph)
  R4  diverted from user requirements      — every acceptance criterion is covered
                                            by a task; no task cites an AC that is
                                            not in discovery.md (scope creep / drift)

Each check returns a list of human-readable problem strings; empty == clean.
"""

import os
import re

REQUIRED_SECTIONS = ("intent", "edge case", "non-functional", "acceptance criteria")

# Markers that mean the intention is not actually resolved yet.
UNCERTAINTY = (
    r"\bTBD\b", r"\bTODO\b", r"\bXXX\b", r"\?\?\?",
    "to be decided", "to be determined", "not sure", "unclear",
    "figure out later", "clarify with", "decide later", "revisit later",
)

AC_RE = re.compile(r"\bAC\d+\b")
TASK_BLOCK_RE = re.compile(r"```task\s*\n(.*?)```", re.S)


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return None


def _headers(text):
    return "\n".join(l.strip().lower() for l in text.splitlines() if l.lstrip().startswith("#"))


def acceptance_ids(spec_dir):
    text = _read(os.path.join(spec_dir, "discovery.md")) or ""
    return set(AC_RE.findall(text))


def _satisfies(block):
    m = re.search(r"^\s*satisfies:\s*(.*)$", block, re.M)
    ids = set()
    if m:
        ids |= set(AC_RE.findall(m.group(1)))
    # also allow a dash-list under `satisfies:`
    for line in block.splitlines():
        if re.match(r"^\s*-\s*AC\d+", line):
            ids |= set(AC_RE.findall(line))
    return ids


def plan_task_coverage(spec_dir):
    """Union of AC ids referenced across all plan.md task blocks."""
    text = _read(os.path.join(spec_dir, "plan.md")) or ""
    covered = set()
    for block in TASK_BLOCK_RE.findall(text):
        covered |= _satisfies(block)
    return covered


def check_discovery(spec_dir, cfg=None):
    cfg = cfg or {}
    disc = os.path.join(spec_dir, "discovery.md")
    text = _read(disc)
    if text is None:
        return ["R1: discovery.md is missing — run the interview before any pipeline work."]

    problems = []
    heads = _headers(text)
    for sec in REQUIRED_SECTIONS:
        if sec not in heads:
            problems.append("R2: discovery.md has no '%s' section — requirements are unstructured." % sec)

    min_ac = int(cfg.get("min_acceptance_criteria", 1))
    n_ac = len(set(AC_RE.findall(text)))
    if n_ac < min_ac:
        problems.append(
            "R2: fewer than %d structured acceptance criteria (label them AC1, AC2, …) "
            "in discovery.md — vague success criteria produce ambiguity failures downstream." % min_ac)

    for pat in UNCERTAINTY:
        if re.search(pat, text, re.I):
            problems.append(
                "R1: unresolved marker matching '%s' in discovery.md — the intention is still "
                "ambiguous. Resolve it with the user, don't let automation guess." % pat)
            break
    return problems


def check_flow(spec_dir, is_specialist):
    """R3 — phases must have run in order before an implementation dispatch."""
    if not is_specialist:
        return []
    problems = []
    for fname, phase in (("context-pack.md", "context research"),
                         ("plan.md", "planning"),
                         ("task-graph.json", "scheduling")):
        if not os.path.exists(os.path.join(spec_dir, fname)):
            problems.append(
                "R3: wrong flow — dispatching implementation but %s (%s phase) is missing. "
                "Phases run discovery -> context -> plan -> schedule -> implement, in order."
                % (fname, phase))
    return problems


def check_coverage(spec_dir, cfg=None):
    """R4 — plan must cover exactly the acceptance criteria: no gaps, no creep."""
    cfg = cfg or {}
    if not cfg.get("require_coverage", True):
        return []
    acs = acceptance_ids(spec_dir)
    if not acs:
        return []  # missing ACs already reported by check_discovery
    covered = plan_task_coverage(spec_dir)
    problems = []
    uncovered = acs - covered
    if uncovered:
        problems.append(
            "R4: acceptance criteria not covered by any task: %s. The plan under-delivers "
            "or has diverged from what the user asked for." % ", ".join(sorted(uncovered)))
    unknown = covered - acs
    if unknown:
        problems.append(
            "R4: tasks claim acceptance criteria absent from discovery.md: %s. This is scope "
            "creep / diversion — the plan is building something not requested." % ", ".join(sorted(unknown)))
    return problems


def check_all(spec_dir, is_specialist, cfg=None):
    problems = check_discovery(spec_dir, cfg)
    problems += check_flow(spec_dir, is_specialist)
    if is_specialist or os.path.exists(os.path.join(spec_dir, "plan.md")):
        problems += check_coverage(spec_dir, cfg)
    return problems
