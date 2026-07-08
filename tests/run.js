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
  return runPy(path.join(HOOKS, name), [], ROOT,
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

  write(path.join(T, 'specs', 'feat', 'review.integration.1.json'),
    JSON.stringify({ task: 'integration', status: 'pass', changeset: 'sha1:deadbeef' }));
  r = hook('commit-gate.py', payload, T);
  ok('commit allowed with passing integration review', r.code === 0, 'exit ' + r.code + ' ' + r.stderr);

  // non-commit bash always passes
  r = hook('commit-gate.py', { tool_name: 'Bash', tool_input: { command: 'ls -la' } }, T);
  ok('non-commit Bash passes through', r.code === 0);
}

// ---------------------------------------------------------------------------
function testDispatchGate() {
  section('dispatch-gate.py — schedule enforcement');
  const T = freshDir(path.join(WORK, 'dispatch'));
  const featureDir = path.join(T, 'specs', 'feat');
  const graph = {
    tasks: [{ id: 'task-a' }, { id: 'task-b' }],
    edges: [{ from: 'task-a', to: 'task-b', reason: 'contract: x' }],
  };
  write(path.join(featureDir, 'task-graph.json'), JSON.stringify(graph));
  write(path.join(featureDir, 'state.json'), JSON.stringify({ tasks: { 'task-a': { status: 'pending' } } }));
  const dispatch = (id) => ({ hook_event_name: 'PreToolUse', tool_name: 'Task',
    tool_input: { description: 'implement task: ' + id, subagent_type: 'specialist-backend' } });

  let r = hook('dispatch-gate.py', dispatch('task-b'), T);
  ok('dispatch blocked out of order (upstream not done)', r.code === 2 && /unfinished upstream/.test(r.stderr));

  r = hook('dispatch-gate.py', dispatch('task-a'), T);
  ok('dispatch of ready task passes', r.code === 0);

  // staleness: make plan.md newer than task-graph.json
  write(path.join(featureDir, 'plan.md'), '# plan\n');
  const future = Date.now() / 1000 + 100;
  fs.utimesSync(path.join(featureDir, 'plan.md'), future, future);
  r = hook('dispatch-gate.py', dispatch('task-a'), T);
  ok('dispatch blocked when task-graph.json is stale', r.code === 2 && /older than plan\.md/.test(r.stderr));
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
try {
  testExtractDeps();
  testRetrievalNudge();
  testCommitGate();
  testDispatchGate();
  testInstaller();
} catch (err) {
  console.error('\nFATAL: ' + err.stack);
  process.exit(1);
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
