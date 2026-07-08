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

function parseArgs(argv) {
  const out = { _: [], yes: false, python: null, dryRun: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--yes' || a === '-y') out.yes = true;
    else if (a === '--dry-run') out.dryRun = true;
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
      'Flags:  --yes | -y   skip confirmation      --python <cmd>   force interpreter',
    ].join('\n'));
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
