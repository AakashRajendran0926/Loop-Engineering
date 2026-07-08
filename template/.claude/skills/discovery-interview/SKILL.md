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

4. **Acceptance criteria.** Concrete, checkable statements ("cancelling a shipped
   order refunds only the unshipped portion and is idempotent"). These become the
   reviewer's rubric — vague criteria produce `ambiguity` failures downstream.

## Rules
- Ask, don't assume. One unresolved ambiguity here is worth ten confident wrong
  guesses later. If the user says "you decide", record the decision explicitly as
  a chosen default so the reviewer can check it.
- Keep it tight — a handful of high-leverage questions, not an interrogation.

## Output — `specs/<feature>/discovery.md`
Sections: **Intent & Scope**, **Edge Cases** (Q→A), **Non-functionals**,
**Acceptance Criteria** (numbered, checkable).

## On escalation re-entry
When the orchestrator escalates, you are re-entered with a specific question and
the conflicting constraint. Get the answer, then **append** a dated
`## Escalation: <topic>` section to `discovery.md` — never rewrite prior content.
The append is the new requirements information the plan is patched from.
