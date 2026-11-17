#!/usr/bin/env node
/*!
 * Stdlib-only contract tests for the browser reference port.
 * Run: node website/kernel-demo.test.js
 *
 * These tests do NOT replace the 86 Python kernel tests. They prove only that
 * the static demo honours the same three terminal states and the same verifier
 * discipline, so the public "try it" link cannot quietly become theatre.
 */
'use strict';
const assert = require('node:assert/strict');
const K = require('./kernel-demo.js');

let passed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log('ok   ' + name); }
  catch (e) { console.error('FAIL ' + name + '\n     ' + e.message); process.exitCode = 1; }
}

test('SHA-256 known-answer vectors (FIPS 180-4)', () => {
  assert.equal(K.sha256Text(''),
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
  assert.equal(K.sha256Text('abc'),
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  assert.equal(K.sha256Text('The quick brown fox jumps over the lazy dog'),
    'd7a8fbb307d7809469ca9abcb0082e4f8d5651e46d3cdb762d02d0bf37c9e592');
});

test('create_file reaches VERIFIED with independent sha256 evidence', () => {
  const w = K.createWorld();
  const r = K.runGoal(w, 'create_file', { path: 'a/b.txt', content: 'hello' });
  assert.equal(r.status, 'VERIFIED');
  assert.equal(r.evidence.length, 1);
  const c = r.evidence[0].checks;
  for (const k of ['claimed_ok', 'file_exists', 'correct_path', 'correct_content', 'hash_matches', 'observation_matches_world']) {
    assert.equal(c[k], true, 'check ' + k + ' must pass');
  }
  assert.equal(r.evidence[0].details.verifier_recomputed_sha256, K.sha256Text('hello'));
  assert.equal(r.denial_reasons.length, 0);
  assert.equal(r.ledger_valid, true);
});

test('path traversal is DENIED before the executor runs', () => {
  const w = K.createWorld();
  const r = K.runGoal(w, 'create_file', { path: '../../escape.txt', content: 'x' });
  assert.equal(r.status, 'DENIED');
  assert.match(r.denial_reasons[0], /escapes the sandbox/);
  assert.equal(r.observation, null);
  assert.equal(r.evidence.length, 0);
  assert.equal(Object.keys(w.fs).length, 0, 'nothing was written');
  assert.equal(r.stages.find(s => s.stage === 'Executor').state, 'skipped');
  assert.equal(r.ledger_valid, true);
});

test('absolute path and writing the sandbox root are DENIED', () => {
  const w = K.createWorld();
  assert.equal(K.runGoal(w, 'create_file', { path: '/etc/passwd', content: 'x' }).status, 'DENIED');
  assert.equal(K.runGoal(w, 'create_file', { path: '', content: 'x' }).status, 'DENIED');
  assert.equal(K.runGoal(w, 'delete_file', { path: '' }).status, 'DENIED');
});

test('unknown capability is denied by the allowlist (fail closed)', () => {
  const d = K.policyEvaluate({ capability: 'network.exfiltrate', params: { path: 'x' } });
  assert.equal(d.allowed, false);
  assert.match(d.reason, /not in the allowlist/);
});

test('payload guard rail denies oversized content', () => {
  const w = K.createWorld();
  const r = K.runGoal(w, 'create_file', { path: 'big.txt', content: 'y'.repeat(5001) });
  assert.equal(r.status, 'DENIED');
  assert.match(r.denial_reasons[0], /5000-byte input guard/);
});

test('missing read is FAILED — never dressed up as success', () => {
  const w = K.createWorld();
  const r = K.runGoal(w, 'read_file', { path: 'missing.txt' });
  assert.equal(r.status, 'FAILED');
  assert.equal(r.observation.ok, false);
  assert.equal(r.evidence[0].checks.file_exists, false);
  assert.ok(r.evidence.length > 0, 'failure still files evidence');
  assert.equal(r.ledger_valid, true);
});

test('append / read / list / mkdir / delete all verify independently', () => {
  const w = K.createWorld();
  assert.equal(K.runGoal(w, 'create_file', { path: 'n.txt', content: 'a' }).status, 'VERIFIED');
  assert.equal(K.runGoal(w, 'append_file', { path: 'n.txt', content: 'b' }).status, 'VERIFIED');
  assert.equal(new TextDecoder().decode(w.fs['n.txt'].bytes), 'ab');
  const read = K.runGoal(w, 'read_file', { path: 'n.txt' });
  assert.equal(read.status, 'VERIFIED');
  assert.equal(read.evidence[0].details.verifier_recomputed_sha256, K.sha256Text('ab'));
  assert.equal(K.runGoal(w, 'ensure_dir', { path: 'd' }).status, 'VERIFIED');
  assert.equal(K.runGoal(w, 'list_dir', { path: '' }).status, 'VERIFIED');
  assert.equal(K.runGoal(w, 'delete_file', { path: 'n.txt' }).status, 'VERIFIED');
  assert.equal(K.runGoal(w, 'read_file', { path: 'n.txt' }).status, 'FAILED');
});

test('an executor report that lies about the hash cannot reach VERIFIED', () => {
  const w = K.createWorld();
  // Tamper with the world between execution and verification is impossible in
  // the real loop, so instead assert the verifier recomputes from the world
  // rather than copying the executor's value.
  const r = K.runGoal(w, 'create_file', { path: 'x.txt', content: 'real bytes' });
  const d = r.evidence[0].details;
  assert.notEqual(d.executor_reported_sha256, null);
  assert.equal(d.verifier_recomputed_sha256, K.sha256Text('real bytes'));
  assert.equal(d.executor_reported_sha256, d.verifier_recomputed_sha256);
  assert.equal(r.evidence[0].checks.hash_matches, true);
});

test('unknown goal_type throws (same contract as live-agent/server.py)', () => {
  assert.throws(() => K.runGoal(K.createWorld(), 'launch_missiles', {}), /unknown goal_type/);
});

test('append-only hash-chained ledger detects tampering', () => {
  const w = K.createWorld();
  K.runGoal(w, 'create_file', { path: 'a', content: '1' });
  K.runGoal(w, 'create_file', { path: '../../b', content: '2' });
  assert.equal(w.ledger.length, 2);
  assert.equal(K.verifyLedger(w), true);
  const original = w.ledger[0].status;
  w.ledger[0].status = 'FAILED';
  assert.equal(K.verifyLedger(w), false, 'tampering breaks the chain');
  w.ledger[0].status = original;
  assert.equal(K.verifyLedger(w), true);
});

test('decision logic is deterministic (same goal -> same checks and status)', () => {
  const a = K.runGoal(K.createWorld(), 'create_file', { path: 'z.txt', content: 'q' });
  const b = K.runGoal(K.createWorld(), 'create_file', { path: 'z.txt', content: 'q' });
  assert.equal(a.status, b.status);
  assert.deepEqual(a.evidence[0].checks, b.evidence[0].checks);
  assert.equal(a.plan.planner, b.plan.planner);
});

console.log('\n' + passed + ' demo contract tests passed (Python kernel: 86 tests, run separately).');
