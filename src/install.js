'use strict';
// Applies the plan from detect.js. Writes a manifest so uninstall is exact.

const fs = require('fs');
const path = require('path');
const m = require('./merge');
const { detect, planClaudeFiles, templateDir } = require('./detect');

const MANIFEST_REL = path.join('.claude', '.harness', 'manifest.json');

function install(target, opts) {
  opts = opts || {};
  target = path.resolve(target);
  const python = opts.python || m.detectPython();
  const report = detect(target, python);

  if (!report.settings.valid) {
    throw new Error('.claude/settings.json is not valid JSON — aborting (no files written).');
  }

  const manifest = {
    version: '1.0', python, installedAt: opts.now || null,
    filesAdded: [], filesRenamed: [], configWritten: false,
    claudeMd: null, settingsEvents: [],
  };

  // 1) .claude tree — collision-safe copy
  for (const f of planClaudeFiles(target)) {
    const res = m.copyFileSafe(f.src, f.dest);
    const rel = path.relative(target, res.dest).replace(/\\/g, '/');
    if (res.status === 'added') manifest.filesAdded.push(rel);
    else if (res.status === 'renamed') {
      manifest.filesRenamed.push(path.relative(target, res.renamedTo).replace(/\\/g, '/'));
    }
  }

  // 2) harness.config.json — add only if absent (never clobber adopter's tuning)
  const cfgDest = path.join(target, 'harness.config.json');
  if (!fs.existsSync(cfgDest)) {
    fs.copyFileSync(path.join(templateDir(), 'harness.config.json'), cfgDest);
    manifest.configWritten = true;
    manifest.filesAdded.push('harness.config.json');
  }

  // 3) CLAUDE.md — delimited block
  const block = m.read(path.join(templateDir(), 'CLAUDE.harness.md'));
  manifest.claudeMd = m.mergeClaudeMd(path.join(target, 'CLAUDE.md'), block).action;

  // 4) settings.json — deep-merge hooks
  const payload = JSON.parse(m.read(path.join(templateDir(), 'settings.harness.json')));
  const res = m.mergeSettings(path.join(target, '.claude', 'settings.json'), payload.hooks, python);
  manifest.settingsEvents = res.appended;

  m.writeJSON(path.join(target, MANIFEST_REL), manifest);
  return { report, manifest };
}

module.exports = { install, MANIFEST_REL };
