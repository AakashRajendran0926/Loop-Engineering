---
name: contract-writing
description: How to write the interface contracts that let specialists work in parallel without colliding. Use in the planner, before finalizing the task list — contracts are written before any implementation.
---

# contract-writing

Contracts are the harness's answer to the cross-specialist seam problem. They fix
every interface **before** implementation, so a frontend task builds against the
agreed API shape rather than the backend's half-finished code. A contract is a
promise a reviewer can check a diff against.

## Principle
Write the contract for a seam **only where two tasks meet**. Internal
implementation detail is not a contract. If exactly one task touches something,
it needs no contract — footprint ownership is enough.

## The three usual contracts (write what the feature needs, name each interface)

`contracts/api.yaml` — endpoint shapes the backend *produces* and the frontend
*consumes*. Method, path, request body, response body, error responses. Field
names here are law: the classic seam bug is a frontend reading `refundId` when the
contract (and backend) say `refund_id`.

```yaml
cancel-api:            # <- the interface name tasks reference in produces/consumes
  POST /orders/{id}/cancel:
    request:  { reason: string }
    response: { status: "cancelled", refund_id: string, refunded_amount: number }
    errors:   { 409: "already cancelled", 422: "not cancellable" }
```

`contracts/migration.md` — the DB shape the data task *produces*: tables/columns
added or changed, nullability, indexes, reversibility. Downstream API tasks
`consume` it.

`contracts/components.md` — component props / client-state contracts the frontend
owns and any shared UI seam.

## Naming interfaces
Give each seam a short id (`cancel-api`, `migration`) and use it in the plan's
`produces:` / `consumes:` lists. `extract-deps.py` turns a produce→consume pair
into a sequential edge even when the two tasks share no files — that is how the
schedule knows the frontend waits on the backend.

## Checklist before finalizing the plan
- Every cross-task seam has a named contract entry.
- Every task that depends on a seam lists it in `consumes:`.
- No contract describes a purely internal detail (over-specifying creates false
  edges and needless serialization).
