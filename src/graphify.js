'use strict';
// Best-effort installer for graphify (PyPI package `graphifyy`, imported as
// `graphify`). Retrieval in the harness is graph-first, so we install the CLI at
// `init` time rather than waiting for the first `/graphify` to self-install it.
// Non-fatal by design: a failure here never blocks the harness install — the
// `/graphify` skill still self-installs on first use.

const { spawnSync } = require('child_process');
const m = require('./merge');

const PKG = 'graphifyy';

function has(cmd) {
  const probe = process.platform === 'win32' ? 'where' : 'which';
  const r = spawnSync(probe, [cmd], { stdio: 'ignore' });
  return r.status === 0;
}

function importable(python) {
  const r = spawnSync(python, ['-c', 'import graphify'], { stdio: 'ignore' });
  return r.status === 0;
}

// Returns { status, via, message }.
//   status: 'already' | 'installed' | 'failed' | 'skipped'
function ensureGraphify(opts) {
  opts = opts || {};
  const python = opts.python || m.detectPython();
  const run = opts.run || ((cmd, cmdArgs) => spawnSync(cmd, cmdArgs, { stdio: 'inherit' }));

  if (python && importable(python)) {
    return { status: 'already', via: python, message: 'graphify already installed' };
  }

  // Prefer isolated tool installs (uv → pipx), fall back to pip into the
  // detected interpreter — mirrors what the /graphify skill does on first run.
  const attempts = [];
  if (has('uv')) attempts.push({ via: 'uv', cmd: 'uv', args: ['tool', 'install', '--upgrade', PKG] });
  if (has('pipx')) attempts.push({ via: 'pipx', cmd: 'pipx', args: ['install', PKG] });
  if (python) attempts.push({ via: 'pip', cmd: python, args: ['-m', 'pip', 'install', '--upgrade', PKG] });

  if (attempts.length === 0) {
    return { status: 'skipped', via: null, message: 'no uv/pipx/python found — skipped graphify install' };
  }

  for (const a of attempts) {
    const r = run(a.cmd, a.args);
    if (r && r.status === 0) {
      return { status: 'installed', via: a.via, message: `graphify installed via ${a.via}` };
    }
  }
  return { status: 'failed', via: null, message: 'graphify install failed — /graphify will self-install on first use' };
}

module.exports = { ensureGraphify, PKG };
