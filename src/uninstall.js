'use strict';
// Reverses an install. With a manifest it is exact (byte-identical repo, minus
// specs/). Without one it falls back to a conservative heuristic. specs/ is the
// adopter's work product and is ALWAYS left in place.

const fs = require('fs');
const path = require('path');
const m = require('./merge');
const { MANIFEST_REL } = require('./install');

function tryRmEmptyDirsUp(startDir, stopDir) {
  let dir = startDir;
  while (dir.startsWith(stopDir) && dir !== stopDir) {
    try { fs.rmdirSync(dir); } catch (_) { break; } // non-empty -> stop
    dir = path.dirname(dir);
  }
}

function uninstall(target) {
  target = path.resolve(target);
  const manifestPath = path.join(target, MANIFEST_REL);
  const manifest = m.readJSON(manifestPath);
  const removed = [];

  // settings + CLAUDE.md first (independent of manifest presence)
  const setRes = m.stripSettings(path.join(target, '.claude', 'settings.json'));
  const claudeRes = m.stripClaudeMd(path.join(target, 'CLAUDE.md'));

  const toDelete = [];
  if (manifest) {
    for (const rel of manifest.filesAdded.concat(manifest.filesRenamed)) {
      if (rel === 'harness.config.json' && !manifest.configWritten) continue;
      toDelete.push(rel);
    }
  } else {
    // Heuristic: delete the files this template is known to ship.
    const { planClaudeFiles } = require('./detect');
    for (const f of planClaudeFiles(target)) {
      toDelete.push(path.relative(target, f.dest).replace(/\\/g, '/'));
    }
  }

  for (const rel of toDelete) {
    const abs = path.join(target, rel);
    if (fs.existsSync(abs)) {
      fs.rmSync(abs, { force: true });
      removed.push(rel);
      tryRmEmptyDirsUp(path.dirname(abs), target);
    }
  }

  // remove the harness state dir (markers, manifest) and clean empties
  fs.rmSync(path.join(target, '.claude', '.harness'), { recursive: true, force: true });
  tryRmEmptyDirsUp(path.join(target, '.claude', 'agents'), target);
  for (const d of ['agents', 'skills', 'commands', 'hooks', 'scripts']) {
    tryRmEmptyDirsUp(path.join(target, '.claude', d), target);
  }
  tryRmEmptyDirsUp(path.join(target, '.claude'), path.dirname(target));

  return { removed, settings: setRes.action, claudeMd: claudeRes.action, usedManifest: !!manifest };
}

module.exports = { uninstall };
