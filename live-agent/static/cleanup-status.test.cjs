#!/usr/bin/env node
'use strict';

const assert = require('node:assert/strict');
const { assessCleanup } = require('./cleanup-status.js');

const cases = [
  [{ status: 'complete' }, 'VERIFIED', 'complete', true, true],
  [{ status: 'not_applicable' }, 'VERIFIED', 'not_applicable', true, true],
  [{ status: 'partial' }, 'VERIFIED', 'partial', false, false],
  [{ status: 'failed' }, 'VERIFIED', 'failed', false, false],
  [undefined, 'VERIFIED', 'unreported', false, false],
  [{ status: 'future-unknown' }, 'VERIFIED', 'unreported', false, false],
  [{ status: 'complete' }, 'DENIED', 'complete', true, false],
];

for (const [cleanup, runStatus, expectedStatus, accepted, fullSuccess] of cases) {
  const actual = assessCleanup(cleanup, runStatus);
  assert.equal(actual.status, expectedStatus);
  assert.equal(actual.accepted, accepted);
  assert.equal(actual.fullSuccess, fullSuccess);
}

console.log(`${cases.length} cleanup contract cases passed`);
