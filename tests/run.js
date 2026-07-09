#!/usr/bin/env node
'use strict';
// Self-contained test runner (no test framework). Covers the three layers the
// build plan calls out: extract-deps (determinism + edge logic), hooks (stdin
// fixtures -> exit codes/messages), installer (install->uninstall byte-identity).

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const m = require('../src/merge');
const { install } = require('../src/install');
const { uninstall } = require('../src/uninstall');

const ROOT = path.join(__dirname, '..');
const TEMPLATE = path.join(ROOT, 'template');
const HOOKS = path.join(TEMPLATE, '.claude', 'hooks');
const SCRIPTS = path.join(TEMPLATE, '.claude', 'scripts');
const WORK = path.join(__dirname, '.work');
const PY = m.detectPython();

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '  -- ' + extra : '')); }
}
function section(t) { console.log('\n' + t); }
function freshDir(p) { fs.rmSync(p, { recursive: true, force: true }); fs.mkdirSync(p, { recursive: true }); return p; }
function write(p, s) { fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, s); }

function runPy(script, args, cwd, env, input) {
  const [bin, ...pre] = PY.split(' ');
  const r = spawnSync(bin, [...pre, script, ...(args || [])], {
    cwd: cwd || ROOT, input: input || '', encoding: 'utf8',
    env: Object.assign({}, process.env, env || {}),
  });
  return { code: r.status, stdout: r.stdout || '', stderr: r.stderr || '' };
}
function hook(name, payload, projectDir) {
  // Run from the project dir (as Claude Code does) AND set the env var, so both
  // env-aware hooks and Path.cwd()-based hooks resolve the same root.
  return runPy(path.join(HOOKS, name), [], projectDir,
    { CLAUDE_PROJECT_DIR: projectDir }, JSON.stringify(payload));
}

// ---------------------------------------------------------------------------
function testExtractDeps() {
  section('extract-deps.py — determinism + edge logic');
  const basic = path.join(__dirname, 'fixtures', 'plan-basic');
  const out1 = path.join(WORK, 'tg1.json'), out2 = path.join(WORK, 'tg2.json');
  const ed = path.join(SCRIPTS, 'extract-deps.py');

  runPy(ed, ['--plan', 'plan.md', '--out', out1, '--graphify-out', 'graphify-out'], basic);
  runPy(ed, ['--plan', 'plan.md', '--out', out2, '--graphify-out', 'graphify-out'], basic);
  ok('byte-identical across runs', fs.readFileSync(out1).equals(fs.readFileSync(out2)));

  const g = JSON.parse(fs.readFileSync(out1, 'utf8'));
  const edge = (f, t) => g.edges.find((e) => e.from === f && e.to === t);
  ok('contract edge db -> backend', !!edge('db-orders-migration', 'backend-orders-api'));
  ok('contract edge backend -> frontend', !!edge('backend-orders-api', 'frontend-cancel-button'));
  ok('db and frontend are parallel-safe (no edge)',
    !edge('db-orders-migration', 'frontend-cancel-button') &&
    !edge('frontend-cancel-button', 'db-orders-migration'));

  // depth sensitivity: shared dependent is 2 hops from task-a
  const depth = path.join(__dirname, 'fixtures', 'plan-depth');
  const d1 = path.join(WORK, 'd1.json'), d2 = path.join(WORK, 'd2.json');
  runPy(ed, ['--plan', 'plan.md', '--out', d1, '--graphify-out', 'graphify-out', '--depth', '1'], depth);
  runPy(ed, ['--plan', 'plan.md', '--out', d2, '--graphify-out', 'graphify-out', '--depth', '2'], depth);
  const e1 = JSON.parse(fs.readFileSync(d1, 'utf8')).edges;
  const e2 = JSON.parse(fs.readFileSync(d2, 'utf8')).edges;
  ok('depth 1: tasks disjoint (no edge)', e1.length === 0, JSON.stringify(e1));
  ok('depth 2: shared_dependent edge appears', e2.length === 1 && /shared_dependent/.test(e2[0].reason), JSON.stringify(e2));
}

// ---------------------------------------------------------------------------
function testRetrievalNudge() {
  section('retrieval-nudge.py — graphify-first + auto-init routing');
  const T = freshDir(path.join(WORK, 'nudge'));
  fs.mkdirSync(path.join(T, 'src'), { recursive: true });
  write(path.join(T, 'src', 'orders.ts'), 'export const x = 1;\n');

  const broad = { session_id: 's1', hook_event_name: 'PreToolUse', tool_name: 'Grep',
    tool_input: { pattern: 'refund' }, transcript_path: path.join(T, 'no-transcript.jsonl') };

  // (1) graph present -> steer to graphify query
  write(path.join(T, 'graphify-out', 'graph.json'), '{"nodes":[],"edges":[]}');
  let r = hook('retrieval-nudge.py', broad, T);
  ok('broad search blocked when graph exists', r.code === 2);
  ok('message steers to `graphify query`', /graphify query/.test(r.stderr));

  // (2) one-shot: same session second broad search passes through
  r = hook('retrieval-nudge.py', broad, T);
  ok('second broad search in same session passes (one-shot marker)', r.code === 0);

  // (3) file-scoped search always passes
  const scoped = Object.assign({}, broad, { session_id: 's2',
    tool_input: { pattern: 'refund', path: 'src/orders.ts' } });
  r = hook('retrieval-nudge.py', scoped, T);
  ok('file-scoped search passes through', r.code === 0);

  // (4) graphify not available -> auto-init routing to /graphify
  const T2 = freshDir(path.join(WORK, 'nudge2'));
  write(path.join(T2, 'harness.config.json'),
    JSON.stringify({ graphify: { binary: 'graphify_nonexistent_zzz', index_path: 'graphify-out/' } }));
  const broad2 = Object.assign({}, broad, { session_id: 's3', transcript_path: path.join(T2, 'nope.jsonl') });
  r = hook('retrieval-nudge.py', broad2, T2);
  ok('broad search blocked when graphify unavailable', r.code === 2);
  ok('message routes to `/graphify` auto-init', /\/graphify\s+\./.test(r.stderr) && /self-installs/.test(r.stderr));
}

// ---------------------------------------------------------------------------
function testCommitGate() {
  section('commit-gate.py — review gate');
  const T = freshDir(path.join(WORK, 'commit'));
  write(path.join(T, 'specs', 'feat', 'state.json'), JSON.stringify({ feature: 'feat', tasks: {} }));
  const payload = { hook_event_name: 'PreToolUse', tool_name: 'Bash',
    tool_input: { command: 'git commit -m "wip"' } };

  let r = hook('commit-gate.py', payload, T);
  ok('commit blocked with no integration review', r.code === 2 && /no integration review/.test(r.stderr));

  // stamp the review with the ACTUAL changeset (via changeset.py) so the gate's
  // diff check matches regardless of whether the temp dir is under a git tree.
  const cs = runPy(path.join(SCRIPTS, 'changeset.py'), [T], ROOT, { CLAUDE_PROJECT_DIR: T }).stdout.trim();
  write(path.join(T, 'specs', 'feat', 'review.integration.1.json'),
    JSON.stringify({ task: 'integration', status: 'pass', changeset: cs }));
  r = hook('commit-gate.py', payload, T);
  ok('commit allowed with passing integration review', r.code === 0, 'exit ' + r.code + ' ' + r.stderr);

  // non-commit bash always passes
  r = hook('commit-gate.py', { tool_name: 'Bash', tool_input: { command: 'ls -la' } }, T);
  ok('non-commit Bash passes through', r.code === 0);
}

// ---------------------------------------------------------------------------
function runScript(name, args, cwd, projectDir) {
  return runPy(path.join(SCRIPTS, name), args, cwd || ROOT, { CLAUDE_PROJECT_DIR: projectDir });
}

// Build a fully valid, approved feature dir; individual tests then break ONE
// precondition to prove the corresponding gate.
function buildFeature(T, opts) {
  opts = opts || {};
  const spec = path.join(T, 'specs', 'feat');
  write(path.join(spec, 'plan.md'), opts.plan || '# plan v1\n');
  const graph = opts.graph || {
    tasks: [{ id: 'task-a', footprint: ['src/a.ts'] }, { id: 'task-b', footprint: ['src/b.ts'] }],
    edges: [{ from: 'task-a', to: 'task-b', reason: 'contract: x' }],
  };
  write(path.join(spec, 'task-graph.json'), JSON.stringify(graph));
  write(path.join(spec, 'state.json'), JSON.stringify(opts.state || { tasks: {}, health: 'ok' }));
  // --force: these fixtures exercise the *dispatch* gate, not requirements, so
  // bypass the requirements check (which would otherwise refuse a bare plan).
  if (opts.approve !== false) runScript('approve-plan.py', ['feat', '--now', 'TEST', '--force'], ROOT, T);
  return spec;
}

function dispatch(id, extra) {
  return { hook_event_name: 'PreToolUse', tool_name: 'Task',
    tool_input: { subagent_type: 'specialist-backend',
      prompt: `TASK-ID: ${id}\nFEATURE: feat\n\n${extra || 'implement it'}` } };
}

function testDispatchGate() {
  section('dispatch-gate.py — approval, order, breaker, context (V1.0 loop layer)');

  // G0: no TASK-ID header -> not governed
  let T = freshDir(path.join(WORK, 'dg0'));
  buildFeature(T);
  let r = hook('dispatch-gate.py', { tool_name: 'Task', tool_input: { prompt: 'just do a thing' } }, T);
  ok('G0: ungoverned dispatch (no TASK-ID) passes through', r.code === 0);

  // G1: approvals missing -> blocked
  T = freshDir(path.join(WORK, 'dg1a'));
  buildFeature(T, { approve: false });
  r = hook('dispatch-gate.py', dispatch('task-a'), T);
  ok('G1: dispatch blocked without approvals.json', r.code === 2 && /approvals\.json not found/.test(r.stderr));

  // G1: plan edited after approval -> hash mismatch -> frozen
  T = freshDir(path.join(WORK, 'dg1b'));
  buildFeature(T);
  write(path.join(T, 'specs', 'feat', 'plan.md'), '# plan v2 (edited after approval)\n');
  r = hook('dispatch-gate.py', dispatch('task-a'), T);
  ok('G1: post-approval plan edit freezes automation (hash mismatch)', r.code === 2 && /hash mismatch/.test(r.stderr));

  // approve-plan hash equals what the gate computes (sha256 of plan bytes)
  T = freshDir(path.join(WORK, 'dghash'));
  const spec = buildFeature(T);
  const planBytes = fs.readFileSync(path.join(spec, 'plan.md'));
  const expected = require('crypto').createHash('sha256').update(planBytes).digest('hex');
  const approvals = JSON.parse(fs.readFileSync(path.join(spec, 'approvals.json'), 'utf8'));
  ok('approve-plan writes a gate-identical plan_hash', approvals.plan_hash === expected);

  // Happy path: ready task passes AND records .current.json
  r = hook('dispatch-gate.py', dispatch('task-a'), T);
  ok('ready task passes all gates', r.code === 0, 'exit ' + r.code + ' ' + r.stderr);
  const cur = JSON.parse(fs.readFileSync(path.join(spec, '.current.json'), 'utf8'));
  ok('.current.json records the in-flight task', cur.task_id === 'task-a' && cur.feature === 'feat');

  // G2: stale graph (plan mtime bumped, content unchanged so hash still matches)
  T = freshDir(path.join(WORK, 'dg2'));
  const s2 = buildFeature(T);
  const future = Date.now() / 1000 + 100;
  fs.utimesSync(path.join(s2, 'plan.md'), future, future);
  r = hook('dispatch-gate.py', dispatch('task-a'), T);
  ok('G2: stale task-graph.json blocks dispatch', r.code === 2 && /older than plan\.md/.test(r.stderr));

  // G3: circuit breaker tripped -> all dispatch refused
  T = freshDir(path.join(WORK, 'dg3'));
  buildFeature(T, { state: { tasks: {}, health: 'tripped', health_reasons: ['footprint_violations:2>=halt 2'] } });
  r = hook('dispatch-gate.py', dispatch('task-a'), T);
  ok('G3: tripped breaker refuses dispatch', r.code === 2 && /TRIPPED/.test(r.stderr));

  // G4: upstream not done
  T = freshDir(path.join(WORK, 'dg4'));
  buildFeature(T, { state: { tasks: { 'task-a': { status: 'pending' } }, health: 'ok' } });
  r = hook('dispatch-gate.py', dispatch('task-b'), T);
  ok('G4: out-of-order dispatch blocked (upstream not done)', r.code === 2 && /upstream tasks not done/.test(r.stderr));

  // G5: zero-retry class (security) -> escalate, no retry
  T = freshDir(path.join(WORK, 'dg5'));
  buildFeature(T, { state: { tasks: { 'task-a': { status: 'needs_retry', attempts: 1, pending_failure_class: 'security' } }, health: 'ok' } });
  r = hook('dispatch-gate.py', dispatch('task-a'), T);
  ok('G5: security failure blocks re-dispatch (escalate)', r.code === 2 && /security/.test(r.stderr) && /[Ee]scalate/.test(r.stderr));

  // T7: mechanical attempts=3 over cap=2 -> stop retrying
  T = freshDir(path.join(WORK, 'dg7'));
  buildFeature(T, { state: { tasks: { 'task-a': { status: 'needs_retry', attempts: 3, pending_failure_class: 'mechanical' } }, health: 'ok' } });
  r = hook('dispatch-gate.py', dispatch('task-a'), T);
  ok('T7: mechanical over retry cap blocked', r.code === 2 && /retry cap reached/.test(r.stderr));

  // T8: ambiguity, attempts=0 -> immediate escalation (zero retries)
  T = freshDir(path.join(WORK, 'dg8'));
  buildFeature(T, { state: { tasks: { 'task-a': { status: 'needs_retry', attempts: 0, pending_failure_class: 'ambiguity' } }, health: 'ok' } });
  r = hook('dispatch-gate.py', dispatch('task-a'), T);
  ok('T8: ambiguity permits zero retries (immediate escalation)', r.code === 2 && /zero retries/.test(r.stderr));

  // G6: oversized dispatch prompt (Context Rule 1)
  T = freshDir(path.join(WORK, 'dg6'));
  buildFeature(T);
  r = hook('dispatch-gate.py', dispatch('task-a', 'x'.repeat(40000)), T);
  ok('G6: oversized dispatch prompt blocked (Context Rule 1)', r.code === 2 && /Context Rule 1/.test(r.stderr));
}

function testDivergenceMonitor() {
  section('divergence-monitor.py — footprint-violation circuit breaker');
  const T = freshDir(path.join(WORK, 'div'));
  const spec = buildFeature(T);
  write(path.join(spec, '.current.json'), JSON.stringify({ task_id: 'task-a', feature: 'feat' }));
  const edit = (file) => ({ hook_event_name: 'PostToolUse', tool_name: 'Write',
    tool_input: { file_path: file, content: 'x' } });

  let r = hook('divergence-monitor.py', edit('src/a.ts'), T);
  ok('write inside footprint passes', r.code === 0 && !/violation/i.test(r.stderr));

  r = hook('divergence-monitor.py', edit('src/evil.ts'), T);
  ok('first out-of-footprint write warns (not halt)', r.code === 0 && /Footprint violation/.test(r.stderr));

  r = hook('divergence-monitor.py', edit('src/evil2.ts'), T);
  ok('second violation trips the breaker (exit 2)', r.code === 2 && /TRIPPED/.test(r.stderr));
  const st = JSON.parse(fs.readFileSync(path.join(spec, 'state.json'), 'utf8'));
  ok('state.health tripped with footprint reason', st.health === 'tripped' && st.health_reasons.some((x) => /footprint/.test(x)));
}

function testStateHealth() {
  section('state-updater.py — derives breaker health from review verdicts');
  const T = freshDir(path.join(WORK, 'health'));
  const spec = path.join(T, 'specs', 'feat');
  write(path.join(spec, 'task-graph.json'), JSON.stringify({ tasks: [{ id: 't1' }] }));
  write(path.join(spec, 'state.json'), JSON.stringify({ tasks: {} }));
  // identical finding twice -> repeat_finding -> escalate -> health tripped
  const finding = [{ file: 'a.ts', detail: 'boom' }];
  write(path.join(spec, 'review.t1.1.json'), JSON.stringify({ task: 't1', status: 'fail', failure_class: 'mechanical', findings: finding }));
  write(path.join(spec, 'review.t1.2.json'), JSON.stringify({ task: 't1', status: 'fail', failure_class: 'mechanical', findings: finding }));
  const r = hook('state-updater.py', { hook_event_name: 'SubagentStop' }, T);
  ok('state-updater exits 0', r.code === 0);
  const st = JSON.parse(fs.readFileSync(path.join(spec, 'state.json'), 'utf8'));
  ok('repeated finding escalates the task', st.tasks.t1.status === 'escalate');
  ok('breaker health tripped from the disk signal', st.health === 'tripped' && st.health_reasons.some((x) => /repeat_finding/.test(x)));
}

// ---------------------------------------------------------------------------
function snapshot(dir, ignore) {
  const out = {};
  (function rec(d) {
    for (const name of fs.readdirSync(d)) {
      const p = path.join(d, name);
      const rel = path.relative(dir, p).replace(/\\/g, '/');
      if (ignore.some((ig) => rel === ig || rel.startsWith(ig + '/'))) continue;
      if (fs.statSync(p).isDirectory()) rec(p);
      else out[rel] = fs.readFileSync(p, 'utf8');
    }
  })(dir);
  return out;
}

function testCompactionCycle() {
  section('precompact-guard + session-rehydrate — compaction indifference / resume');
  const T = freshDir(path.join(WORK, 'compact'));
  const spec = path.join(T, 'specs', 'feat');
  write(path.join(spec, 'task-graph.json'), JSON.stringify({ tasks: [{ id: 'task-a' }] }));
  write(path.join(spec, 'state.json'), JSON.stringify({ tasks: {} }));
  write(path.join(spec, '.current.json'), JSON.stringify({ task_id: 'task-a', feature: 'feat' }));

  hook('state-updater.py', { hook_event_name: 'SubagentStop' }, T);
  ok('state-updater writes specs/_active.json',
    fs.existsSync(path.join(T, 'specs', '_active.json')) &&
    JSON.parse(fs.readFileSync(path.join(T, 'specs', '_active.json'), 'utf8')).feature === 'feat');
  const st = JSON.parse(fs.readFileSync(path.join(spec, 'state.json'), 'utf8'));
  ok('state-updater mirrors current_task from .current.json', st.current_task === 'task-a');

  let r = hook('precompact-guard.py', { hook_event_name: 'PreCompact', trigger: 'auto' }, T);
  ok('precompact-guard exits 0 and snapshots', r.code === 0 && fs.existsSync(path.join(spec, 'pipeline-context.md')));
  const snap = fs.readFileSync(path.join(spec, 'pipeline-context.md'), 'utf8');
  ok('snapshot captures feature + current task', /FEATURE:\**\s*feat/.test(snap) && /task-a/.test(snap));

  r = hook('session-rehydrate.py', { hook_event_name: 'SessionStart', source: 'compact' }, T);
  ok('rehydrate injects snapshot after compaction', r.code === 0 && /FEATURE:\**\s*feat/.test(r.stdout));

  r = hook('session-rehydrate.py', { hook_event_name: 'SessionStart', source: 'startup' }, T);
  ok('normal startup does NOT rehydrate (clean session)', r.code === 0 && !/FEATURE:\**\s*feat/.test(r.stdout));

  const st2 = JSON.parse(fs.readFileSync(path.join(spec, 'state.json'), 'utf8'));
  ok('state-updater derives pipeline phase', st2.phase === 'execution', 'phase=' + st2.phase);

  // T11: PreCompact with no _active.json -> no snapshot, exit 0
  const T2 = freshDir(path.join(WORK, 'compact-noactive'));
  fs.mkdirSync(path.join(T2, 'specs', 'feat'), { recursive: true });
  r = hook('precompact-guard.py', { hook_event_name: 'PreCompact', trigger: 'auto' }, T2);
  ok('T11: PreCompact no-op without _active.json', r.code === 0 && !fs.existsSync(path.join(T2, 'specs', 'feat', 'pipeline-context.md')));

  // T15: resume with active feature but snapshot missing -> manual fallback text
  const T3 = freshDir(path.join(WORK, 'resume-nosnap'));
  write(path.join(T3, 'specs', '_active.json'), JSON.stringify({ feature: 'feat' }));
  fs.mkdirSync(path.join(T3, 'specs', 'feat'), { recursive: true });
  r = hook('session-rehydrate.py', { hook_event_name: 'SessionStart', source: 'resume' }, T3);
  ok('T15: resume with no snapshot falls back to manual-rehydration text', r.code === 0 && /rehydrate manually|No snapshot/.test(r.stdout));
}

const GOOD_DISCOVERY = [
  '# Discovery', '',
  '## Intent & Scope', 'In scope: order cancellation. Out of scope: partial refunds.', '',
  '## Edge Cases', '- Q: already cancelled? A: return 409.', '',
  '## Non-functionals', '- refunds are idempotent', '',
  '## Acceptance Criteria',
  '- AC1: cancelling refunds the unshipped portion',
  '- AC2: a repeated cancel does not double-refund', '',
].join('\n');

function buildReqFeature(T, opts) {
  opts = opts || {};
  const spec = path.join(T, 'specs', 'feat');
  write(path.join(spec, 'discovery.md'), opts.discovery !== undefined ? opts.discovery : GOOD_DISCOVERY);
  if (opts.contextPack !== false) write(path.join(spec, 'context-pack.md'), '## Area: backend\nstuff\n');
  if (opts.plan !== false) write(path.join(spec, 'plan.md'), opts.plan !== undefined ? opts.plan :
    '```task\nid: t1\nagent: specialist-backend\nfootprint: [api/orders.ts]\nsatisfies: [AC1, AC2]\n```\n');
  if (opts.graph !== false) write(path.join(spec, 'task-graph.json'), JSON.stringify({ tasks: [{ id: 't1', footprint: ['api/orders.ts'] }], edges: [] }));
  return spec;
}

function reqDispatch(opts) {
  opts = opts || {};
  let prompt = 'FEATURE: feat\n';
  if (opts.taskId) prompt += `TASK-ID: ${opts.taskId}\n`;
  prompt += '\nwork';
  return { hook_event_name: 'PreToolUse', tool_name: 'Task', tool_input: { prompt } };
}

function testRequirementsGate() {
  section('requirements-gate.py — extended human gate (R1 intent · R2 structure · R3 flow · R4 coverage)');

  // clean specialist dispatch -> passes
  let T = freshDir(path.join(WORK, 'req-ok'));
  buildReqFeature(T);
  let r = hook('requirements-gate.py', reqDispatch({ taskId: 't1' }), T);
  ok('clean, well-structured, fully-covered plan passes', r.code === 0, r.stderr);

  // no FEATURE header -> ungated
  r = hook('requirements-gate.py', { tool_name: 'Task', tool_input: { prompt: 'do a thing' } }, T);
  ok('non-pipeline dispatch (no FEATURE) passes through', r.code === 0);

  // R1: ambiguous intention (unresolved marker)
  T = freshDir(path.join(WORK, 'req-r1'));
  buildReqFeature(T, { discovery: GOOD_DISCOVERY + '\n## Open\n- refund window? TBD\n' });
  r = hook('requirements-gate.py', reqDispatch({ taskId: 't1' }), T);
  ok('R1: unresolved marker (TBD) blocks -> human', r.code === 2 && /R1/.test(r.stderr));

  // R2: vague/unstructured (no acceptance criteria section/IDs)
  T = freshDir(path.join(WORK, 'req-r2'));
  buildReqFeature(T, { discovery: '# Discovery\n## Intent & Scope\nsomething\n## Edge Cases\n-\n## Non-functionals\n-\n' });
  r = hook('requirements-gate.py', reqDispatch({ taskId: 't1' }), T);
  ok('R2: missing acceptance criteria blocks -> human', r.code === 2 && /R2/.test(r.stderr));

  // R3: wrong flow (specialist dispatched before context-pack exists)
  T = freshDir(path.join(WORK, 'req-r3'));
  buildReqFeature(T, { contextPack: false });
  r = hook('requirements-gate.py', reqDispatch({ taskId: 't1' }), T);
  ok('R3: implementation before context-pack blocks (wrong flow)', r.code === 2 && /R3/.test(r.stderr));

  // R4: diverged — acceptance criterion not covered by any task
  T = freshDir(path.join(WORK, 'req-r4a'));
  buildReqFeature(T, { plan: '```task\nid: t1\nagent: specialist-backend\nfootprint: [api/orders.ts]\nsatisfies: [AC1]\n```\n' });
  r = hook('requirements-gate.py', reqDispatch({ taskId: 't1' }), T);
  ok('R4: uncovered acceptance criterion blocks (under-delivery)', r.code === 2 && /R4/.test(r.stderr) && /AC2/.test(r.stderr));

  // R4: scope creep — task cites an AC not in discovery
  T = freshDir(path.join(WORK, 'req-r4b'));
  buildReqFeature(T, { plan: '```task\nid: t1\nagent: specialist-backend\nfootprint: [api/orders.ts]\nsatisfies: [AC1, AC2, AC9]\n```\n' });
  r = hook('requirements-gate.py', reqDispatch({ taskId: 't1' }), T);
  ok('R4: task citing an unknown AC blocks (scope creep)', r.code === 2 && /R4/.test(r.stderr) && /AC9/.test(r.stderr));

  // planner-phase dispatch (FEATURE, no TASK-ID) with good discovery but no plan yet -> allowed
  T = freshDir(path.join(WORK, 'req-plan'));
  buildReqFeature(T, { plan: false, graph: false });
  r = hook('requirements-gate.py', reqDispatch({}), T);
  ok('planner dispatch allowed once discovery is solid (pre-plan)', r.code === 0, r.stderr);

  // approve-plan refuses a diverged plan, --force overrides (logged)
  T = freshDir(path.join(WORK, 'req-approve'));
  const spec = buildReqFeature(T, { plan: '```task\nid: t1\nagent: specialist-backend\nfootprint: [api/orders.ts]\nsatisfies: [AC1]\n```\n' });
  r = runScript('approve-plan.py', ['feat', '--now', 'T'], ROOT, T);
  ok('approve-plan REFUSES a plan that under-covers requirements', r.code === 2 && /REFUSED/.test(r.stderr));
  ok('  ...and writes no approvals.json', !fs.existsSync(path.join(spec, 'approvals.json')));
  r = runScript('approve-plan.py', ['feat', '--now', 'T', '--force'], ROOT, T);
  ok('approve-plan --force signs but logs the override', r.code === 0 &&
    fs.existsSync(path.join(spec, 'approvals.json')) &&
    Array.isArray(JSON.parse(fs.readFileSync(path.join(spec, 'approvals.json'), 'utf8')).forced_override));
}

function testArtifactSize() {
  section('artifact-size.py — compression-funnel budget enforcement (Rule 2 / L2)');
  const T = freshDir(path.join(WORK, 'artifact'));
  const spec = path.join(T, 'specs', 'feat');
  const edit = (rel) => ({ hook_event_name: 'PostToolUse', tool_name: 'Write',
    tool_input: { file_path: rel } });

  write(path.join(spec, 'context-pack.md'), 'x'.repeat(4000)); // ~1000 tok < 2000
  let r = hook('artifact-size.py', edit('specs/feat/context-pack.md'), T);
  ok('in-budget context pack passes', r.code === 0);

  write(path.join(spec, 'context-pack.md'), 'x'.repeat(12000)); // ~3000 tok > 2000
  r = hook('artifact-size.py', edit('specs/feat/context-pack.md'), T);
  ok('oversized context pack flagged (exit 2)', r.code === 2 && /over budget/.test(r.stderr));

  r = hook('artifact-size.py', edit('src/app.ts'), T);
  ok('non-artifact write ignored', r.code === 0);
}

function testLoopDriver() {
  section('loop.js — autonomous outer driver (queue, terminal states, resume) (L4/L3)');
  const T = freshDir(path.join(WORK, 'loop'));
  const { addToQueue, processQueue, loadQueue, requeue, answersPath } = require('../src/loop');

  addToQueue(T, 'feature one', { id: 'feat-a', now: 'T' });
  addToQueue(T, 'feature two', { id: 'feat-b', now: 'T' });
  addToQueue(T, 'feature three', { id: 'feat-c', now: 'T' });

  // Mock runner: simulates a fresh session by writing the terminal state to disk.
  const scripted = {
    'feat-a': { phase: 'committed', tasks: { t1: { status: 'done' } } },
    'feat-b': { phase: 'execution', health: 'tripped', health_reasons: ['footprint_violations:2>=halt 2'], tasks: {} },
    'feat-c': { phase: 'execution', tasks: { t1: { status: 'done' } } }, // will be overridden below
  };
  const runner = (feature) => {
    const spec = path.join(T, 'specs', feature.id);
    fs.mkdirSync(spec, { recursive: true });
    // feat-c: no plan yet -> needs_approval terminal (plan drafted, unsigned)
    if (feature.id === 'feat-c') write(path.join(spec, 'plan.md'), '# plan\n');
    write(path.join(spec, 'state.json'), JSON.stringify(scripted[feature.id]));
    return { usage: { input_tokens: 1000, output_tokens: 500 } };
  };

  return processQueue(T, { runner, log: () => {} }).then((summary) => {
    ok('committed feature marked done', summary.committed.includes('feat-a'));
    ok('tripped feature escalated + parked', summary.escalated.includes('feat-b'));
    ok('unsigned plan -> needs_approval parked', summary.needs_approval.includes('feat-c'));
    const ans = JSON.parse(fs.readFileSync(answersPath(T), 'utf8'));
    ok('parked features recorded with a question', ans.parked.length === 2 && ans.parked.every((p) => p.question));
    const q = loadQueue(T);
    ok('queue persisted per-feature (a=done, b/c=parked)',
      q.features.find((f) => f.id === 'feat-a').status === 'done' &&
      q.features.find((f) => f.id === 'feat-b').status === 'parked');

    // L3 burndown: driver fed tokens_spent into divergence.json
    const div = JSON.parse(fs.readFileSync(path.join(T, 'specs', 'feat-a', 'divergence.json'), 'utf8'));
    ok('driver records tokens_spent (burndown producer)', div.tokens_spent === 1500);

    // resume-from-artifacts: requeue a parked feature, then re-run it to commit
    requeue(T, 'feat-b', 'T2');
    scripted['feat-b'] = { phase: 'committed', tasks: { t1: { status: 'done' } } };
    return processQueue(T, { runner, log: () => {} }).then((s2) => {
      ok('re-queued parked feature resumes and commits', s2.committed.includes('feat-b'));

      // L5 metrics over the resulting specs/
      const { computeMetrics } = require('../src/metrics');
      const m2 = computeMetrics(T);
      const a = m2.find((x) => x.feature === 'feat-a');
      ok('metrics compute tokens-per-passing-task', a && a.tokens_per_passing_task === 1500 && a.tasks_passing === 1);
    });
  });
}

function testVersionController() {
  section('Version Controller — dependency + code + development versioning (gated)');
  const T = freshDir(path.join(WORK, 'version'));
  const spec = path.join(T, 'specs', 'feat');
  write(path.join(spec, 'state.json'), JSON.stringify({ feature: 'feat', tasks: {} }));
  write(path.join(T, 'package.json'), JSON.stringify({ dependencies: { 'left-pad': '1.2.3' } }));

  const v = (args) => runScript('version.py', args, ROOT, T);
  const commit = { hook_event_name: 'PreToolUse', tool_name: 'Bash', tool_input: { command: 'git commit -m x' } };

  // baseline captured (pre-feature deps)
  v(['baseline', 'feat']);
  ok('dependency baseline captured', fs.existsSync(path.join(spec, 'dependencies.baseline.json')));

  // a MAJOR dependency bump, detected by the tracker
  write(path.join(T, 'package.json'), JSON.stringify({ dependencies: { 'left-pad': '2.0.0' } }));
  let r = hook('version-tracker.py', { hook_event_name: 'PostToolUse', tool_name: 'Write', tool_input: { file_path: 'package.json' } }, T);
  const deps = JSON.parse(fs.readFileSync(path.join(spec, 'dependencies.json'), 'utf8'));
  ok('tracker classifies the major dependency bump',
    deps.has_major === true && deps.changes.some((c) => /left-pad/.test(c.name) && c.severity === 'major'));

  // V1: version gate blocks an unacknowledged major bump
  r = hook('version-gate.py', commit, T);
  ok('V1: commit blocked on unacknowledged major dependency', r.code === 2 && /MAJOR dependency/.test(r.stderr));

  v(['ack-deps', 'feat', '--now', 'T']);
  r = hook('version-gate.py', commit, T);
  ok('V2: after ack, commit blocked on missing code version/changelog', r.code === 2 && /code version/.test(r.stderr));

  // code versioning: bump + changelog (plan declares VERSION-IMPACT: major)
  write(path.join(spec, 'plan.md'), '# plan\nVERSION-IMPACT: major\n');
  v(['bump', 'feat', '--now', '2026-01-01T00:00:00Z']);
  ok('bump writes VERSION 1.0.0', fs.readFileSync(path.join(T, 'VERSION'), 'utf8').trim() === '1.0.0');
  ok('bump writes CHANGELOG entry', /1\.0\.0/.test(fs.readFileSync(path.join(T, 'CHANGELOG.md'), 'utf8')));

  r = hook('version-gate.py', commit, T);
  ok('V3: still blocked without development history', r.code === 2 && /development-version history/.test(r.stderr));

  // development versioning: snapshot
  v(['snapshot', 'feat', 'integrated', '--now', 'T']);
  const hist = JSON.parse(fs.readFileSync(path.join(spec, 'history.json'), 'utf8'));
  ok('snapshot records development history with artifact hashes',
    hist.entries.length === 1 && hist.entries[0].milestone === 'integrated' && hist.entries[0].artifacts['plan.md']);

  r = hook('version-gate.py', commit, T);
  ok('all three version gates open -> commit allowed', r.code === 0, 'exit ' + r.code + ' ' + r.stderr);

  // config bypass is honored (logged decision)
  const T2 = freshDir(path.join(WORK, 'version-off'));
  write(path.join(T2, 'specs', 'feat', 'state.json'), JSON.stringify({ tasks: {} }));
  write(path.join(T2, 'harness.config.json'), JSON.stringify({ gates: { commit_requires_version: false } }));
  r = hook('version-gate.py', commit, T2);
  ok('gates.commit_requires_version:false bypasses the gate', r.code === 0);
}

function testInstaller() {
  section('installer — brownfield merge + install/uninstall byte-identity');
  const T = freshDir(path.join(WORK, 'brownfield'));
  // pre-existing populated .claude + team CLAUDE.md + colliding agent
  write(path.join(T, 'CLAUDE.md'), '# Team Rules\n\nUse tabs.\n');
  write(path.join(T, '.claude', 'settings.json'),
    JSON.stringify({ hooks: { PreToolUse: [{ matcher: 'Bash',
      hooks: [{ type: 'command', command: 'echo team-hook' }] }] } }, null, 2) + '\n');
  write(path.join(T, '.claude', 'agents', 'reviewer.md'), '# my own reviewer\n');
  write(path.join(T, 'src', 'app.ts'), 'console.log(1)\n');
  write(path.join(T, 'specs', 'old', 'discovery.md'), '# prior work\n');

  const before = snapshot(T, ['.claude/.harness']);

  const { manifest } = install(T, { yes: true, python: 'python3', now: 'TEST' });
  ok('collision renamed to reviewer.harness.md', fs.existsSync(path.join(T, '.claude', 'agents', 'reviewer.harness.md')));
  ok('adopter reviewer.md untouched', fs.readFileSync(path.join(T, '.claude', 'agents', 'reviewer.md'), 'utf8') === '# my own reviewer\n');
  const settings = JSON.parse(fs.readFileSync(path.join(T, '.claude', 'settings.json'), 'utf8'));
  const teamPreserved = settings.hooks.PreToolUse.some((g) => g.hooks.some((h) => h.command === 'echo team-hook'));
  const harnessAdded = settings.hooks.PreToolUse.some((g) => g.hooks.some((h) => h._harness === true));
  ok('team hook preserved after merge', teamPreserved);
  ok('harness hooks appended with _harness marker', harnessAdded);
  ok('CLAUDE.md gained the delimited block', fs.readFileSync(path.join(T, 'CLAUDE.md'), 'utf8').includes(m.BEGIN));
  ok('manifest records added files', manifest.filesAdded.length > 0);

  uninstall(T);
  const after = snapshot(T, ['.claude/.harness']);
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  let identical = true, firstDiff = '';
  for (const k of keys) {
    if (before[k] !== after[k]) { identical = false; firstDiff = firstDiff || k; }
  }
  ok('install -> uninstall is byte-identical (minus specs/ retained)', identical, 'first diff: ' + firstDiff);
  ok('specs/ retained through uninstall', fs.existsSync(path.join(T, 'specs', 'old', 'discovery.md')));
}

// ---------------------------------------------------------------------------
freshDir(WORK);
console.log('Loop Engineering — test suite  (python: ' + PY + ')');
(async () => {
  try {
    testExtractDeps();
    testRetrievalNudge();
    testCommitGate();
    testDispatchGate();
    testDivergenceMonitor();
    testStateHealth();
    testCompactionCycle();
    testRequirementsGate();
    testArtifactSize();
    testVersionController();
    await testLoopDriver();
    testInstaller();
  } catch (err) {
    console.error('\nFATAL: ' + err.stack);
    process.exit(1);
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
