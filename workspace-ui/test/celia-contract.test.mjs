// Contract tests for the CELIA.SH 2027 workspace-ui rebrand.
// These are real, source-reading checks (no mocks): they fail if the security
// contract is weakened or if the honesty matrix starts lying about capabilities.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const workspaceUi = path.resolve(here, '..');
const repoRoot = path.resolve(here, '..', '..');

const read = (p) => readFileSync(p, 'utf8');

const ALLOWED_STATUSES = new Set(['implemented', 'partial', 'planned', 'unavailable', 'blocked']);

test('vite.config.ts preserves the loopback-only security contract', () => {
  const vite = read(path.join(workspaceUi, 'vite.config.ts'));
  assert.ok(vite.includes("host: '127.0.0.1'"), 'dev server must bind to 127.0.0.1');
  assert.ok(vite.includes('trustedLocalGateway'), 'trustedLocalGateway plugin must remain');
  assert.ok(
    vite.includes("allowedHosts: ['127.0.0.1', 'localhost']"),
    'allowedHosts must restrict to loopback',
  );
  assert.ok(!vite.includes("host: '0.0.0.0'"), 'must never bind to 0.0.0.0');
  assert.ok(!vite.includes("host: \"0.0.0.0\""), 'must never bind to 0.0.0.0 (double quotes)');
  // The guard that throws when the server is not local-only must still be present.
  assert.ok(
    vite.includes('credential proxy is local-only'),
    'local-only credential proxy guard must remain',
  );
});

test('capabilities.json is a valid, honest matrix', () => {
  const raw = read(path.join(workspaceUi, 'src', 'capabilities.json'));
  const matrix = JSON.parse(raw);
  assert.ok(Array.isArray(matrix.capabilities), 'capabilities must be an array');
  assert.ok(Array.isArray(matrix.skills), 'skills must be an array');
  assert.ok(Array.isArray(matrix.projects), 'projects must be an array');

  const all = [...matrix.capabilities, ...matrix.skills, ...matrix.projects];
  assert.ok(all.length > 0, 'matrix must not be empty');

  const seen = new Set();
  for (const entry of all) {
    assert.ok(typeof entry.id === 'string' && entry.id.length > 0, `entry ${entry.id} needs an id`);
    assert.ok(ALLOWED_STATUSES.has(entry.status), `entry ${entry.id} has invalid status ${entry.status}`);
    seen.add(entry.status);
    // Honesty rule: planned / unavailable / blocked must NOT carry an action
    // that would imply a completed, connected run.
    if (entry.status === 'planned' || entry.status === 'unavailable' || entry.status === 'blocked') {
      assert.ok(entry.action === null || entry.action === undefined, `entry ${entry.id} (${entry.status}) must not define an action`);
    }
  }
  // The matrix must actually exercise more than one honesty tier.
  assert.ok(seen.has('implemented'), 'matrix should contain at least one implemented entry');
  assert.ok(seen.has('planned') || seen.has('blocked'), 'matrix should contain at least one non-implemented entry');
});

test('App.tsx exposes the six unified sections and CELIA.SH branding', () => {
  const app = read(path.join(workspaceUi, 'src', 'App.tsx'));
  for (const label of ['المهام', 'المشاريع', 'المكتبة', 'المهارات', 'الموصلات', 'الإعدادات والحوكمة']) {
    assert.ok(app.includes(label), `sidebar must include section "${label}"`);
  }
  assert.ok(app.includes('CELIA'), 'brand wordmark CELIA.SH must be present');
  assert.ok(app.includes('Celia'), 'assistant name Celia must be present');
  // The local gateway / operations console link must remain reachable.
  assert.ok(app.includes('/ops/'), 'operations console link (/ops/) must remain');
});

test('architecture doc exists and carries the honesty matrix marker', () => {
  const docPath = path.join(repoRoot, 'docs', 'CELIA-SH-2027-ARCHITECTURE.md');
  assert.ok(existsSync(docPath), 'docs/CELIA-SH-2027-ARCHITECTURE.md must exist');
  const doc = read(docPath);
  assert.ok(doc.includes('implemented'), 'doc must describe the implemented tier');
  assert.ok(doc.includes('planned'), 'doc must describe the planned tier');
  assert.ok(doc.includes('celia-contract.test.mjs'), 'doc must reference the contract test');
});
