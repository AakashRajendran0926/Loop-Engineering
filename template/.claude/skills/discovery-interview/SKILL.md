---
name: discovery-interview
description: Protocol-driven feature discovery. Use on the main thread at the start of /feature (and on every escalation re-entry) to interview the user for edge cases, non-functionals, and acceptance criteria, then write discovery.md.
---

# discovery-interview

Discovery is a **phase on the main thread**, not a subagent — subagents cannot
talk to the user. Your job is to extract the requirements a plan can be built on
and the acceptance criteria a review can be judged against. Interview
protocol-driven, not generically.

## Protocol — cover all four, in order

1. **Intent & scope.** One sentence: what changes for the user? What is explicitly
   out of scope? Name the modules you expect to touch (confirm against graphify).

2. **Edge cases.** Push hard here — this is where ambiguity that later stalls the
   pipeline is cheapest to resolve. Ask the awkward ones concretely:
   - boundary states ("an already-refunded order?", "a partial shipment?")
   - concurrency ("two cancels race?"), empty/limit cases, failure/rollback paths.

3. **Non-functionals.** Idempotency, performance budgets, security/authz
   constraints, observability/audit requirements, backward compatibility.

4. **Acceptance criteria.** Concrete, checkable statements, each labelled with a
   stable id — **AC1, AC2, AC3, …** — one per line:
   ```
   - AC1: cancelling a shipped order refunds only the unshipped portion
   - AC2: refunds are idempotent (a repeated cancel does not double-refund)
   ```
   These ids are load-bearing: the planner tags each task with the ACs it
   `satisfies:`, and the requirements gate refuses to proceed unless every AC is
   covered by a task and no task invents an AC that isn't here (scope creep). They
   are also the reviewer's rubric — vague criteria produce `ambiguity` failures.

## Rules
- Ask, don't assume. One unresolved ambiguity here is worth ten confident wrong
  guesses later. If the user says "you decide", record the decision explicitly as
  a chosen default so the reviewer can check it.
- **Leave no unresolved markers.** `TBD`, `TODO`, `???`, "clarify with…", "not
  sure", "decide later" in discovery.md will freeze the pipeline at the
  requirements gate — resolve them with the user now, or record an explicit
  chosen default. An unresolved marker is an unfinished interview.
- Keep it tight — a handful of high-leverage questions, not an interrogation.

## Output — `specs/<feature>/discovery.md`
Sections (all four are required; the requirements gate checks for them):
**Intent & Scope** (explicit in-scope AND out-of-scope), **Edge Cases** (Q→A),
**Non-functionals**, **Acceptance Criteria** (AC1, AC2, … — checkable, one per line).

## On escalation re-entry
When the orchestrator escalates, you are re-entered with a specific question and
the conflicting constraint. Get the answer, then **append** a dated
`## Escalation: <topic>` section to `discovery.md` — never rewrite prior content.
The append is the new requirements information the plan is patched from.
