---
name: review-taxonomy
description: The failure classification the reviewer must apply. Use in the reviewer to assign a failure_class, which sets the retry policy the dispatch gate enforces. Also defines the repeat-finding early-stop rule.
---

# review-taxonomy

Your `failure_class` is not a label — it selects the retry policy the dispatch
gate enforces by exit code. Classify precisely; the wrong class either wastes
retries on an unretryable problem or hard-stops a fixable one.

## Decision tree (apply top to bottom; first match wins)

1. **security** — a vulnerability (injection, authz bypass, secret exposure,
   unsafe deserialization). → `fail`, **0 retries**, hard stop for human review.
   Rationale is policy, not capability: you do not let an agent iterate until the
   scanner goes quiet. Never downgrade a real security finding to unblock a run.

2. **ambiguity** — you cannot determine the *intended* behavior from discovery +
   contracts (e.g. "should cancelling a partially-shipped order refund the shipped
   portion?"). → `fail`, **0 retries**. Retrying ambiguity only manufactures
   confident guesses; it must go back to the human. Name the exact undecided
   question in a finding — the orchestrator will ask the user verbatim.

3. **contract** — the implementation does not match `contracts/` (wrong field
   name, wrong status code, missing endpoint). → `fail`, **1 retry**. A *second*
   contract failure means the contract itself is ambiguous → escalate as ambiguity.

4. **mechanical** — tests fail, type/lint errors, a missing migration; the fix is
   derivable from the error output. → `fail`, **2 retries** (3 attempts total).

If none apply and the work meets the acceptance criteria and stays in footprint:
→ `status: "pass"`, `failure_class: null`.

## Findings
Each finding: `{ id, severity, file, detail }`. `detail` must be specific enough
that the specialist can act without guessing (name the file, the symbol, the
expected vs actual). Severity `error` blocks a pass; `warn` does not.

## Repeat-finding early stop (mandatory)
Before writing your verdict, read the previous attempt's `review.<task>.*.json`.
If any finding is essentially identical to one you filed last attempt, set
`"repeat_finding": true`. Two identical failures predict a third — the harness
escalates immediately rather than spending the remaining retry budget confirming
the specialist doesn't understand the fix.

## Integration mode
Same taxonomy, but over the combined diff plus the graphify pass on touched files.
The seam bug you are hunting (a parallel task still reading a renamed field)
usually classes as `contract`. Record `changeset` on a pass — the commit gate
checks it.
