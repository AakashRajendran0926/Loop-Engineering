# Langfuse Dashboards (Phase L5)

`trace-emitter.py` streams one trace per session (trace id = `session_id`), with
subagent spans, tool child-spans, and review verdicts as scores. These three
dashboards turn that stream into the flywheel's instrument panel. Build them in
the Langfuse UI (Dashboards → New) over your project; the metric each needs is
noted. The same numbers are available offline via `agent-harness metrics`.

## 1. Per-feature burndown
**Question:** is a feature burning toward or through its approved budget?
- **Source:** score/observation `tokens_spent` accumulated per feature vs the
  `budget` recorded in `approvals.json` (the driver writes cumulative tokens into
  `divergence.json`; trace metadata carries `feature`).
- **Chart:** line — cumulative tokens over session time, with a horizontal marker
  at 80% (warn) and 100% (halt) of budget. A feature crossing 100% is exactly
  what trips the circuit breaker's burndown signal.

## 2. Tokens-per-passing-task, by specialist
**Question:** which compression stage is leaking? (Rule 5's leak detector.)
- **Source:** per subagent span, `output_tokens` ÷ passing tasks for that
  specialist type (`specialist-backend`, `-frontend`, `-database`).
- **Chart:** time series, one line per specialist type. A steady creep on one
  line localizes the leak to that specialist's retrieval/context slice — the
  trace then shows which span carries the extra tokens.

## 3. Estimate-vs-actual, by task type
**Question:** is the planner's cost estimate trustworthy?
- **Source:** planner-emitted per-task token estimate (in the approval package)
  vs actual tokens for that task's spans.
- **Chart:** scatter — estimate (x) vs actual (y), colored by failure class. Points
  far above the diagonal are underestimated task types; feed that back into the
  planner's estimates and the config `budgets.per_task_default`.

## Regression dataset (the flywheel closes here)
Every `"status": "fail"` review and every escalation lands as a dataset item
labeled with `failure_class`, attempts, and resolution. Before merging any change
to a skill, agent prompt, or hook, replay the dataset and compare scores — the
reviewer agent is the native evaluation producer, so "evaluate" never waits on
human labeling.
