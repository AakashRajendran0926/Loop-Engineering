'use strict';
// L5 — flywheel metrics (DevelopmentUpdates.md §5-6, Rule 5).
//
// The derived metric that matters is **tokens-per-passing-task**: when it creeps
// up, a compression stage is leaking. This computes it per feature from disk
// (state.json + divergence.json + approvals.json) so it works with or without
// Langfuse. Langfuse dashboards (docs/langfuse-dashboards.md) chart the same
// numbers over time; this is the local, always-available view.

const fs = require('fs');
const path = require('path');

function readJSON(p, dflt) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (_) { return dflt; }
}

function computeMetrics(root) {
  root = path.resolve(root);
  const specs = path.join(root, 'specs');
  const out = [];
  if (!fs.existsSync(specs)) return out;

  for (const name of fs.readdirSync(specs).sort()) {
    if (name.startsWith('_')) continue; // queue/answer files
    const dir = path.join(specs, name);
    if (!fs.statSync(dir).isDirectory()) continue;

    const state = readJSON(path.join(dir, 'state.json'), { tasks: {} });
    const div = readJSON(path.join(dir, 'divergence.json'), {});
    const approvals = readJSON(path.join(dir, 'approvals.json'), {});

    const tasks = Object.values(state.tasks || {});
    const passing = tasks.filter((t) => t.status === 'done').length;
    const spent = div.tokens_spent || 0;
    const budget = approvals.budget || 0;

    out.push({
      feature: name,
      phase: state.phase || 'unknown',
      health: state.health || 'ok',
      tasks_total: tasks.length,
      tasks_passing: passing,
      tokens_spent: spent,
      budget: budget || null,
      burndown_pct: budget ? Math.round((100 * spent) / budget) : null,
      tokens_per_passing_task: passing ? Math.round(spent / passing) : null,
      footprint_violations: Object.values(div.footprint_violations || {}).reduce((a, b) => a + b, 0),
    });
  }
  return out;
}

function formatMetrics(rows) {
  if (!rows.length) return 'No features found under specs/.';
  const L = ['Loop Engineering — flywheel metrics', ''];
  for (const r of rows) {
    L.push(`• ${r.feature}  [${r.phase}${r.health === 'tripped' ? ', BREAKER TRIPPED' : ''}]`);
    L.push(`    tasks: ${r.tasks_passing}/${r.tasks_total} passing` +
      (r.footprint_violations ? `   footprint-violations: ${r.footprint_violations}` : ''));
    L.push(`    tokens: ${r.tokens_spent}` +
      (r.budget ? ` / ${r.budget} budget  (${r.burndown_pct}% burndown)` : ' (no budget set)'));
    if (r.tokens_per_passing_task != null) {
      L.push(`    tokens-per-passing-task: ${r.tokens_per_passing_task}  ← leak detector`);
    }
    L.push('');
  }
  return L.join('\n');
}

module.exports = { computeMetrics, formatMetrics };
