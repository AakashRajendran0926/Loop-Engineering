#!/usr/bin/env node
'use strict';
// Loop Engineering harness installer.
//   agent-harness init [target] [--yes] [--python <cmd>]
//   agent-harness detect [target]
//   agent-harness uninstall [target] [--yes]

const path = require('path');
const readline = require('readline');
const { detect, formatReport } = require('../src/detect');
const { install } = require('../src/install');
const { uninstall } = require('../src/uninstall');
const loop = require('../src/loop');
const { computeMetrics, formatMetrics } = require('../src/metrics');

function parseArgs(argv) {
  const out = { _: [], yes: false, python: null, dryRun: false, once: false, autoApprove: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--yes' || a === '-y') out.yes = true;
    else if (a === '--dry-run') out.dryRun = true;
    else if (a === '--once') out.once = true;
    else if (a === '--auto-approve') out.autoApprove = true;
    else if (a === '--python') out.python = argv[++i];
    else if (a.startsWith('--python=')) out.python = a.slice(9);
    else out._.push(a);
  }
  return out;
}

function confirm(question) {
  return new Promise((resolve) => {
    if (!process.stdin.isTTY) return resolve(false);
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    rl.question(question, (ans) => { rl.close(); resolve(/^y(es)?$/i.test(ans.trim())); });
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const cmd = args._[0] || 'help';
  const target = path.resolve(args._[1] || '.');

  if (cmd === 'help' || cmd === '--help' || cmd === '-h') {
    console.log([
      'Loop Engineering — agent harness for brownfield Claude Code repos',
      '',
      'Usage:',
      '  agent-harness init [target]        Install into a repo (prints a plan first)',
      '  agent-harness detect [target]      Show the install plan, write nothing',
      '  agent-harness uninstall [target]   Remove the harness (keeps specs/)',
      '',
      '  agent-harness queue add "<desc>"   Add a feature to the work queue',
      '  agent-harness queue list           Show the queue and parked features',
      '  agent-harness loop                 Run the autonomous driver over the queue',
      '  agent-harness requeue <id>         Re-queue a parked feature after answering',
      '  agent-harness metrics              Per-feature burndown + tokens-per-task',
      '',
      'Flags:  --yes | -y   skip confirmation      --python <cmd>   force interpreter',
      '        --once       one feature then stop   --auto-approve  headless self-approval',
      '        --dry-run    plan the loop, run nothing',
    ].join('\n'));
    return 0;
  }

  if (cmd === 'queue') {
    const sub = args._[1];
    const root = path.resolve(args._[2] || '.');
    if (sub === 'add') {
      const desc = args._[2];
      if (!desc) { console.error('usage: agent-harness queue add "<description>"'); return 1; }
      const id = loop.addToQueue(process.cwd(), desc, { autoApprove: args.autoApprove, now: new Date().toISOString() });
      console.log(`queued '${id}'`);
      return 0;
    }
    if (sub === 'list' || !sub) {
      const q = loop.loadQueue(root);
      const ans = require('fs').existsSync(loop.answersPath(root))
        ? JSON.parse(require('fs').readFileSync(loop.answersPath(root), 'utf8')) : { parked: [] };
      console.log('Queue:');
      for (const f of q.features) console.log(`  [${f.status}] ${f.id} — ${f.description}`);
      if (ans.parked && ans.parked.length) {
        console.log('\nParked (need a human):');
        for (const p of ans.parked) console.log(`  ${p.id} (${p.terminal}): ${p.question}`);
      }
      return 0;
    }
    console.error(`unknown: queue ${sub}`); return 1;
  }

  if (cmd === 'requeue') {
    loop.requeue(process.cwd(), args._[1], new Date().toISOString());
    console.log(`re-queued '${args._[1]}'`);
    return 0;
  }

  if (cmd === 'loop') {
    const root = path.resolve(args._[1] || '.');
    if (args.dryRun) {
      const q = loop.loadQueue(root);
      const pending = q.features.filter((f) => f.status === 'pending');
      console.log(`dry-run: ${pending.length} pending feature(s): ${pending.map((f) => f.id).join(', ') || '(none)'}`);
      return 0;
    }
    const summary = await loop.processQueue(root, {
      once: args.once, autoApprove: args.autoApprove, log: (m) => console.log(m),
    });
    console.log(`\nDone. committed: ${summary.committed.length}, ` +
      `escalated: ${summary.escalated.length}, needs_approval: ${summary.needs_approval.length}, ` +
      `incomplete: ${summary.incomplete.length}`);
    if (summary.escalated.length || summary.needs_approval.length) {
      console.log('Parked features await a human — see: agent-harness queue list');
    }
    return 0;
  }

  if (cmd === 'metrics') {
    console.log(formatMetrics(computeMetrics(path.resolve(args._[1] || '.'))));
    return 0;
  }

  if (cmd === 'detect') {
    console.log(formatReport(detect(target, args.python)));
    return 0;
  }

  if (cmd === 'init' || cmd === 'install') {
    const report = detect(target, args.python);
    console.log(formatReport(report));
    console.log('');
    if (!report.settings.valid) { console.error('Aborted: fix settings.json first.'); return 1; }
    if (!args.yes) {
      const ok = await confirm('Proceed with install? [y/N] ');
      if (!ok) {
        console.log('Aborted. (Re-run with --yes for non-interactive install.)');
        return process.stdin.isTTY ? 0 : 1;
      }
    }
    const { manifest } = install(target, { python: args.python, now: new Date().toISOString() });
    console.log(`Installed. CLAUDE.md: ${manifest.claudeMd}; ` +
      `${manifest.filesAdded.length} files added` +
      (manifest.filesRenamed.length ? `, ${manifest.filesRenamed.length} collision(s) renamed to *.harness.*` : '') +
      `. Next: /feature <description>`);
    return 0;
  }

  if (cmd === 'uninstall') {
    if (!args.yes) {
      const ok = await confirm(`Uninstall the harness from ${target}? (specs/ is kept) [y/N] `);
      if (!ok) { console.log('Aborted.'); return process.stdin.isTTY ? 0 : 1; }
    }
    const res = uninstall(target);
    console.log(`Uninstalled. Removed ${res.removed.length} files; ` +
      `settings: ${res.settings}; CLAUDE.md: ${res.claudeMd}` +
      (res.usedManifest ? '' : ' (no manifest — heuristic uninstall)') + '. specs/ left in place.');
    return 0;
  }

  console.error(`Unknown command: ${cmd}. Try: agent-harness help`);
  return 1;
}

main().then((code) => process.exit(code || 0)).catch((err) => {
  console.error('Error: ' + err.message);
  process.exit(1);
});
