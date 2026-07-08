'use strict';
// Builds the pre-flight detection report: what exists, what will be added,
// merged, or renamed. Pure inspection — writes nothing.

const fs = require('fs');
const path = require('path');
const m = require('./merge');

function templateDir() { return path.join(__dirname, '..', 'template'); }

// The set of files the harness ships into the adopter's .claude/ tree.
function planClaudeFiles(target) {
  const src = path.join(templateDir(), '.claude');
  return m.walk(src).map((abs) => {
    const rel = path.relative(templateDir(), abs); // e.g. .claude/hooks/x.py
    return { src: abs, rel, dest: path.join(target, rel) };
  });
}

function classify(dest, src) {
  if (!fs.existsSync(dest)) return { status: 'add' };
  const a = fs.readFileSync(dest), b = fs.readFileSync(src);
  if (a.equals(b)) return { status: 'identical' };
  const ext = path.extname(dest);
  return { status: 'collision', renamedTo: dest.slice(0, -ext.length || undefined) + '.harness' + ext };
}

function detect(target, python) {
  const report = { target, python: python || m.detectPython(), files: [] };

  for (const f of planClaudeFiles(target)) {
    report.files.push(Object.assign({ rel: f.rel.replace(/\\/g, '/') }, classify(f.dest, f.src)));
  }

  const cfgDest = path.join(target, 'harness.config.json');
  report.config = { exists: fs.existsSync(cfgDest), action: fs.existsSync(cfgDest) ? 'keep' : 'add' };

  const claudeDest = path.join(target, 'CLAUDE.md');
  const claudeRaw = m.read(claudeDest);
  report.claudeMd = {
    exists: claudeRaw !== null,
    action: claudeRaw === null ? 'create'
      : claudeRaw.includes(m.BEGIN) ? 'upgrade-block' : 'append-block',
  };

  const setDest = path.join(target, '.claude', 'settings.json');
  const setRaw = m.read(setDest);
  let valid = true;
  if (setRaw !== null && setRaw.trim() !== '') { try { JSON.parse(setRaw); } catch (_) { valid = false; } }
  report.settings = { exists: setRaw !== null, valid, action: 'merge-hooks' };

  return report;
}

function formatReport(r) {
  const L = [];
  L.push('Loop Engineering — installation plan');
  L.push('  target: ' + r.target);
  L.push('  python: ' + r.python);
  L.push('');
  const adds = r.files.filter((f) => f.status === 'add');
  const same = r.files.filter((f) => f.status === 'identical');
  const cols = r.files.filter((f) => f.status === 'collision');
  L.push(`  files to add:      ${adds.length}`);
  if (same.length) L.push(`  already present:   ${same.length} (identical, skipped)`);
  for (const c of cols) L.push(`  ! COLLISION:       ${c.rel}  ->  ${path.basename(c.renamedTo)} (yours kept)`);
  L.push('');
  L.push(`  harness.config.json: ${r.config.action}`);
  L.push(`  CLAUDE.md:           ${r.claudeMd.action}`);
  if (!r.settings.valid) {
    L.push('  .claude/settings.json: INVALID JSON — install will abort. Fix it first.');
  } else {
    L.push(`  .claude/settings.json: ${r.settings.action} (existing hooks preserved, ` +
      `harness hooks appended with _harness markers)`);
  }
  return L.join('\n');
}

module.exports = { detect, formatReport, planClaudeFiles, templateDir };
