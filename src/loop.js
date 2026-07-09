'use strict';
// L4 — the outer driver (DevelopmentUpdates.md §4).
//
// Pulls features off a queue and runs each in a FRESH headless session
// (`claude -p`), one feature = one session (Rule 4). It never revives a context:
// resuming a parked feature is a brand-new session rehydrated from specs/ by the
// session-rehydrate hook — recover-from-compaction and resume-from-parking are
// deliberately the same mechanism.
//
// Exactly three terminal states are read back from specs/<f>/state.json:
//   committed  -> feature done, move on
//   escalated  -> breaker tripped / task escalated: PARK (a question for the
//                 human) and keep the queue moving — escalations do not stall it
//   needs_approval -> plan drafted but not signed: PARK for the human gate
//
// The `runner` is injectable so the queue logic is unit-testable without
// spawning a model. The default runner shells out to `claude -p`.

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

function readJSON(p, dflt) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (_) { return dflt; }
}
function writeJSON(p, obj) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(obj, null, 2) + '\n');
}
function slug(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'feature';
}

function queuePath(root) { return path.join(root, 'specs', '_queue.json'); }
function answersPath(root) { return path.join(root, 'specs', '_answers.json'); }

function loadQueue(root) {
  return readJSON(queuePath(root), { features: [] });
}
function addToQueue(root, description, opts) {
  opts = opts || {};
  const q = loadQueue(root);
  const id = opts.id || slug(description);
  if (q.features.some((f) => f.id === id)) throw new Error(`feature '${id}' already queued`);
  q.features.push({
    id, description, status: 'pending',
    auto_approve: !!opts.autoApprove, queuedAt: opts.now || null,
  });
  writeJSON(queuePath(root), q);
  return id;
}

// ---- terminal-state detection (reads only disk) --------------------------
function classify(root, id) {
  const spec = path.join(root, 'specs', id);
  const state = readJSON(path.join(spec, 'state.json'), null);
  const plan = fs.existsSync(path.join(spec, 'plan.md'));
  const approved = fs.existsSync(path.join(spec, 'approvals.json'));

  if (state && state.phase === 'committed') return { terminal: 'committed' };
  if (state && (state.health === 'tripped' ||
      Object.values(state.tasks || {}).some((t) => t.status === 'escalate'))) {
    return { terminal: 'escalated', reasons: state.health_reasons || ['task escalated'] };
  }
  if (plan && !approved) return { terminal: 'needs_approval', reasons: ['awaiting human plan approval'] };
  return { terminal: 'incomplete', reasons: ['session ended without a terminal state'] };
}

function park(root, feature, cls) {
  const ans = readJSON(answersPath(root), { parked: [] });
  ans.parked = ans.parked.filter((p) => p.id !== feature.id);
  ans.parked.push({
    id: feature.id, terminal: cls.terminal,
    reasons: cls.reasons || [], description: feature.description,
    question: `Resolve '${feature.id}' (${cls.terminal}): ${(cls.reasons || []).join('; ')}. ` +
      `Append the answer to specs/${feature.id}/discovery.md, then re-queue.`,
  });
  writeJSON(answersPath(root), ans);
}

// ---- token burndown producer (L3) ----------------------------------------
// The driver is the one place with a real token count (from `claude -p` usage),
// so it is where burndown is fed. recompute_health reads divergence.json.tokens_spent.
function recordTokens(root, id, usage) {
  if (!usage) return;
  const spec = path.join(root, 'specs', id);
  const dp = path.join(spec, 'divergence.json');
  const div = readJSON(dp, {});
  const spent = (usage.input_tokens || 0) + (usage.output_tokens || 0);
  div.tokens_spent = (div.tokens_spent || 0) + spent;
  writeJSON(dp, div);
  return div.tokens_spent;
}

// ---- the default (real) runner: a fresh `claude -p` session --------------
function claudeRunner(feature, ctx) {
  const resume = fs.existsSync(path.join(ctx.root, 'specs', feature.id, 'plan.md'));
  const prompt = resume
    ? `Resume feature '${feature.id}'. A session is being rehydrated from ` +
      `specs/${feature.id}/. Read state.json, then continue the pipeline per ` +
      `task-graph.json to a terminal state.`
    : `/feature ${feature.description}`;
  const auto = ctx.autoApprove || feature.auto_approve;
  const bin = ctx.claudeBin || 'claude';
  const args = ['-p', prompt, '--output-format', 'json'];
  if (ctx.permissionMode) args.push('--permission-mode', ctx.permissionMode);
  const r = spawnSync(bin, args, {
    cwd: ctx.root, encoding: 'utf8', timeout: ctx.timeoutMs || 1800000,
    env: Object.assign({}, process.env, auto ? { HARNESS_AUTO_APPROVE: '1' } : {}),
  });
  if (r.error) throw new Error(`failed to spawn '${bin}': ${r.error.message}. Is Claude Code installed and on PATH?`);
  let usage = null;
  try { usage = (JSON.parse(r.stdout) || {}).usage || null; } catch (_) { /* non-JSON tail */ }
  return { usage, raw: r.stdout };
}

// ---- the loop -------------------------------------------------------------
async function processQueue(root, opts) {
  opts = opts || {};
  root = path.resolve(root);
  const runner = opts.runner || claudeRunner;
  const ctx = {
    root, autoApprove: !!opts.autoApprove, claudeBin: opts.claudeBin,
    permissionMode: opts.permissionMode, timeoutMs: opts.timeoutMs,
  };
  const summary = { committed: [], escalated: [], needs_approval: [], incomplete: [] };

  while (true) {
    const q = loadQueue(root);
    const feature = q.features.find((f) => f.status === 'pending');
    if (!feature) break;

    if (opts.log) opts.log(`▶ ${feature.id}: starting fresh session`);
    let usage = null;
    try {
      const res = await runner(feature, ctx);
      usage = res && res.usage;
    } catch (err) {
      feature.status = 'parked';
      feature.error = err.message;
      writeJSON(queuePath(root), q);
      park(root, feature, { terminal: 'incomplete', reasons: [err.message] });
      summary.incomplete.push(feature.id);
      if (opts.once) break; else continue;
    }
    const total = recordTokens(root, feature.id, usage);

    const cls = classify(root, feature.id);
    if (cls.terminal === 'committed') {
      feature.status = 'done';
      summary.committed.push(feature.id);
    } else {
      feature.status = 'parked';
      park(root, feature, cls);
      summary[cls.terminal].push(feature.id);
    }
    feature.lastTokens = total || undefined;
    writeJSON(queuePath(root), q); // persist after each feature (crash-safe)
    if (opts.log) opts.log(`  ${feature.id} -> ${cls.terminal}${total ? ` (${total} tok)` : ''}`);
    if (opts.once) break;
  }
  return summary;
}

// Re-queue a parked feature (after a human appended the answer to discovery.md).
function requeue(root, id, now) {
  const q = loadQueue(root);
  const f = q.features.find((x) => x.id === id);
  if (!f) throw new Error(`no queued feature '${id}'`);
  f.status = 'pending';
  f.requeuedAt = now || null;
  writeJSON(queuePath(root), q);
  const ans = readJSON(answersPath(root), { parked: [] });
  ans.parked = ans.parked.filter((p) => p.id !== id);
  writeJSON(answersPath(root), ans);
}

module.exports = {
  processQueue, addToQueue, loadQueue, requeue, classify, claudeRunner,
  queuePath, answersPath, slug,
};
