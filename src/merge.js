'use strict';
// Low-level merge primitives shared by install/uninstall/detect.
// Zero runtime dependencies — Node stdlib only.

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const BEGIN = '<!-- harness:begin v1.0 -->';
const END = '<!-- harness:end -->';

function read(p) {
  try { return fs.readFileSync(p, 'utf8'); } catch (_) { return null; }
}
function readJSON(p) {
  const raw = read(p);
  if (raw === null) return undefined;
  return JSON.parse(raw); // caller decides how to handle a throw (we abort)
}
function writeJSON(p, obj) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(obj, null, 2) + '\n');
}

// Detect a Python interpreter for the hook commands. Preference order lets the
// same payload work on Windows (python / py) and unix (python3).
function detectPython(explicit) {
  const candidates = explicit ? [explicit] : ['python3', 'python', 'py -3'];
  for (const c of candidates) {
    try {
      execSync(`${c} --version`, { stdio: 'ignore' });
      return c;
    } catch (_) { /* try next */ }
  }
  // Fall back to python3; the hooks fail-open, and the adopter can edit config.
  return 'python3';
}

// ---- CLAUDE.md ------------------------------------------------------------
// Append a delimited block; upgrades replace only the block; never touch the
// adopter's own content.
function mergeClaudeMd(targetPath, blockWithMarkers) {
  const existing = read(targetPath);
  if (existing === null) {
    fs.writeFileSync(targetPath, blockWithMarkers.trimEnd() + '\n');
    return { action: 'created' };
  }
  const b = existing.indexOf(BEGIN);
  const e = existing.indexOf(END);
  if (b !== -1 && e !== -1 && e > b) {
    const before = existing.slice(0, b);
    const after = existing.slice(e + END.length);
    const next = before + blockWithMarkers.trim() + after;
    fs.writeFileSync(targetPath, next);
    return { action: 'upgraded' };
  }
  const sep = existing.endsWith('\n') ? '\n' : '\n\n';
  fs.writeFileSync(targetPath, existing + sep + blockWithMarkers.trimEnd() + '\n');
  return { action: 'appended' };
}

function stripClaudeMd(targetPath) {
  const existing = read(targetPath);
  if (existing === null) return { action: 'absent' };
  const b = existing.indexOf(BEGIN);
  const e = existing.indexOf(END);
  if (b === -1 || e === -1 || e < b) return { action: 'no-block' };
  let before = existing.slice(0, b);
  let after = existing.slice(e + END.length);
  let next = (before.replace(/\s+$/, '') + '\n' + after.replace(/^\s+/, '')).trim();
  // If nothing but the block remained, remove the file entirely (byte-identity).
  if (next === '') { fs.rmSync(targetPath, { force: true }); return { action: 'removed-file' }; }
  fs.writeFileSync(targetPath, next + '\n');
  return { action: 'stripped' };
}

// ---- settings.json --------------------------------------------------------
// Deep-merge hook arrays. Existing entries are preserved and never reordered;
// harness entries are appended, each already carrying "_harness": true.
// A parse failure ABORTS — we never write a best-guess settings file.
function mergeSettings(targetPath, payloadHooks, python) {
  let settings = {};
  const raw = read(targetPath);
  if (raw !== null && raw.trim() !== '') {
    try { settings = JSON.parse(raw); }
    catch (err) {
      throw new Error(
        `Refusing to merge: ${targetPath} is not valid JSON (${err.message}). ` +
        `Fix or remove it, then re-run.`);
    }
  }
  settings.hooks = settings.hooks || {};
  const appended = [];
  for (const event of Object.keys(payloadHooks)) {
    settings.hooks[event] = settings.hooks[event] || [];
    for (const group of payloadHooks[event]) {
      const g = JSON.parse(JSON.stringify(group));
      for (const h of g.hooks) {
        if (typeof h.command === 'string') h.command = h.command.replace(/__PYTHON__/g, python);
      }
      settings.hooks[event].push(g);
      appended.push(event);
    }
  }
  writeJSON(targetPath, settings);
  return { appended };
}

// Remove every hook entry marked _harness; drop empty event arrays; if the whole
// file reduces to {} remove it so the tree is byte-identical to pre-install.
function stripSettings(targetPath) {
  const raw = read(targetPath);
  if (raw === null) return { action: 'absent' };
  let settings;
  try { settings = JSON.parse(raw); } catch (_) { return { action: 'unparseable-skip' }; }
  if (!settings.hooks) return { action: 'no-hooks' };
  for (const event of Object.keys(settings.hooks)) {
    const groups = settings.hooks[event];
    if (!Array.isArray(groups)) continue;
    const kept = [];
    for (const group of groups) {
      const hooks = (group.hooks || []).filter((h) => h._harness !== true &&
        !(typeof h.command === 'string' && /\.claude[\\/]hooks[\\/]/.test(h.command) &&
          /(retrieval-nudge|commit-gate|dispatch-gate|state-updater|trace-emitter)\.py/.test(h.command)));
      if (hooks.length) kept.push(Object.assign({}, group, { hooks }));
    }
    if (kept.length) settings.hooks[event] = kept;
    else delete settings.hooks[event];
  }
  if (Object.keys(settings.hooks).length === 0) delete settings.hooks;
  if (Object.keys(settings).length === 0) {
    fs.rmSync(targetPath, { force: true });
    return { action: 'removed-file' };
  }
  writeJSON(targetPath, settings);
  return { action: 'stripped' };
}

// ---- collision-safe file copy --------------------------------------------
// Returns { dest, status: 'added'|'identical'|'renamed', renamedTo? }
function copyFileSafe(src, dest) {
  const content = fs.readFileSync(src);
  if (fs.existsSync(dest)) {
    const cur = fs.readFileSync(dest);
    if (cur.equals(content)) return { dest, status: 'identical' };
    const ext = path.extname(dest);
    const renamed = dest.slice(0, -ext.length || undefined) + '.harness' + ext;
    fs.mkdirSync(path.dirname(renamed), { recursive: true });
    fs.writeFileSync(renamed, content);
    return { dest, status: 'renamed', renamedTo: renamed };
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, content);
  return { dest, status: 'added' };
}

const SKIP = new Set(['__pycache__', '.pytest_cache', '.DS_Store']);
function walk(dir) {
  const out = [];
  for (const name of fs.readdirSync(dir)) {
    if (SKIP.has(name) || name.endsWith('.pyc')) continue; // never ship build caches
    const p = path.join(dir, name);
    if (fs.statSync(p).isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

module.exports = {
  BEGIN, END, read, readJSON, writeJSON, detectPython,
  mergeClaudeMd, stripClaudeMd, mergeSettings, stripSettings,
  copyFileSafe, walk,
};
