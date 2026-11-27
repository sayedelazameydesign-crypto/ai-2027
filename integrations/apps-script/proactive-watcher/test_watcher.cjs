'use strict';

// Hermetic Apps Script API simulation: never call Google or use credentials.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, 'Code.gs'), 'utf8');

function sandbox(handler, lockOptions = {}) {
  const calls = [];
  const logs = [];
  const sleeps = [];
  const lockCalls = [];
  const context = vm.createContext({
    console: {log: (value) => logs.push(value)},
    LockService: {getScriptLock: () => ({
      tryLock: (timeout) => { lockCalls.push(['try', timeout]); return lockOptions.available !== false; },
      releaseLock: () => {
        lockCalls.push(['release']);
        if (lockOptions.releaseError) throw new Error('private lock details');
      },
    })},
    ScriptApp: {getOAuthToken: () => 'fake-token'},
    Utilities: {sleep: (ms) => sleeps.push(ms)},
    UrlFetchApp: {fetch: (url, options) => {
      calls.push({url, options});
      const result = handler(new URL(url), options);
      return {
        getResponseCode: () => result.status ?? 200,
        getContentText: () => result.text ?? JSON.stringify(result.body),
      };
    }},
  });
  vm.runInContext(source, context);
  return {context, calls, logs, sleeps, lockCalls};
}

function snapshot(context) {
  return JSON.parse(JSON.stringify(vm.runInContext(
    "watcherSnapshot_(new Date('2026-11-26T00:00:00Z'))", context,
  )));
}

function event(id, start, end, extra = {}) {
  return {id, start: {dateTime: start}, end: {dateTime: end}, ...extra};
}

test('manifest is scoped to reading and fetch, not sheets or write actions', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'appsscript.json')));
  assert.deepEqual(manifest.urlFetchWhitelist, [
    'https://gmail.googleapis.com/gmail/v1/users/me/messages',
    'https://www.googleapis.com/calendar/v3/calendars/primary/events',
  ]);
  assert.deepEqual(manifest.oauthScopes, [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar.events.readonly',
    'https://www.googleapis.com/auth/script.external_request',
  ]);
});

test('pages both providers and reports only aggregate timed conflicts', () => {
  const {context, calls} = sandbox((url, options) => {
    assert.equal(options.method, 'get');
    assert.equal(options.followRedirects, false);
    assert.equal(options.headers.Authorization, 'Bearer fake-token');
    if (url.hostname === 'gmail.googleapis.com') {
      assert.equal(url.searchParams.get('fields'), 'messages(id),nextPageToken');
      return url.searchParams.has('pageToken')
        ? {body: {messages: [{id: 'm2'}]}}
        : {body: {messages: [{id: 'm1'}], nextPageToken: 'page2'}};
    }
    assert.equal(url.hostname, 'www.googleapis.com');
    assert.equal(url.searchParams.get('singleEvents'), 'true');
    return {body: {items: [
      event('a', '2026-11-26T10:00:00+02:00', '2026-11-26T11:00:00+02:00'),
      event('b', '2026-11-26T08:30:00Z', '2026-11-26T09:30:00Z'),
      event('c', '2026-11-26T08:30:00Z', '2026-11-26T09:30:00Z', {transparency: 'transparent'}),
      event('d', '2026-11-26T09:30:00Z', '2026-11-26T10:30:00Z'),
      {id: 'all-day', start: {date: '2026-11-26'}, end: {date: '2026-11-27'}},
      {id: 'cancelled', status: 'cancelled'},
    ]}};
  });
  assert.deepEqual(snapshot(context), {
    status: 'ok', baseline: true, sources: 7, calendar_conflicts: 1,
    all_day_excluded: 1, writes: 0,
  });
  assert.equal(calls.length, 3);
});

test('failed calendar cycle produces no partial success or sensitive output', () => {
  const {context, logs, calls} = sandbox((url) => {
    if (url.hostname === 'gmail.googleapis.com') {
      return {body: {messages: [{id: 'private-id'}]}};
    }
    return {status: 403, text: 'private provider body must not appear'};
  });
  assert.throws(() => vm.runInContext('watcherPoll()', context), /Read-only watcher poll failed/);
  assert.deepEqual(logs, ['{"status":"failed","writes":0}']);
  assert.equal(calls.length, 2); // Permanent errors are not retried.
});

test('transient errors are bounded to three GET attempts and sanitised', () => {
  const {context, calls, logs, sleeps} = sandbox(() => ({status: 503, text: 'private-token'}));
  assert.throws(() => vm.runInContext('watcherPoll()', context), /Read-only watcher poll failed/);
  assert.equal(calls.length, 3);
  assert.deepEqual(sleeps, [250, 500]);
  assert.deepEqual(logs, ['{"status":"failed","writes":0}']);
});

test('pagination cycles, malformed times and overflow fail closed', () => {
  const cycling = sandbox(() => ({body: {messages: [], nextPageToken: 'same'}}));
  assert.throws(() => snapshot(cycling.context), /invalid pagination/);
  const overflow = sandbox(() => ({body: {messages: Array.from({length: 1001}, (_, i) => ({id: String(i)}))}}));
  assert.throws(() => snapshot(overflow.context), /item limit/);
  const invalid = sandbox((url) => url.hostname === 'gmail.googleapis.com'
    ? {body: {messages: []}}
    : {body: {items: [event('a', '2026-11-26T10:00:00', '2026-11-26T12:00:00Z')]}});
  assert.throws(() => snapshot(invalid.context), /invalid event time/);
});

test('overlapping poll skips before reading and never releases an unheld lock', () => {
  const {context, calls, logs, lockCalls} = sandbox(() => {
    throw new Error('must not fetch');
  }, {available: false});
  const result = JSON.parse(JSON.stringify(vm.runInContext('watcherPoll()', context)));
  assert.deepEqual(result, {status: 'skipped', reason: 'overlap', writes: 0});
  assert.deepEqual(logs, ['{"status":"skipped","reason":"overlap","writes":0}']);
  assert.deepEqual(lockCalls, [['try', 0]]);
  assert.equal(calls.length, 0);
});

test('poll releases lock on success and failure; release failure cannot leak or report success', () => {
  const success = sandbox((url) => ({body: url.hostname === 'gmail.googleapis.com'
    ? {messages: []} : {items: []}}));
  vm.runInContext('watcherPoll()', success.context);
  assert.deepEqual(success.lockCalls, [['try', 0], ['release']]);
  assert.equal(JSON.parse(success.logs[0]).status, 'ok');
  const failed = sandbox(() => ({status: 403}));
  assert.throws(() => vm.runInContext('watcherPoll()', failed.context), /Read-only watcher poll failed/);
  assert.deepEqual(failed.lockCalls, [['try', 0], ['release']]);
  const release = sandbox((url) => ({body: url.hostname === 'gmail.googleapis.com'
    ? {messages: []} : {items: []}}), {releaseError: true});
  assert.throws(() => vm.runInContext('watcherPoll()', release.context), /Read-only watcher poll failed/);
  assert.deepEqual(release.logs, ['{"status":"failed","writes":0}']);
});

test('strict event timestamps reject normalized invalid dates and unknown offsets', () => {
  for (const value of [
    '2026-02-29T10:00:00Z', '2026-04-31T10:00:00Z',
    '2026-11-26T24:00:00Z', '2026-11-26T10:00:00-00:00',
    '2026-11-26T10:00:00+01:60', '2026-11-26 10:00:00Z',
  ]) {
    const {context} = sandbox(() => ({}));
    context.inputTime = value;
    assert.throws(() => vm.runInContext('watcherTime_(inputTime)', context), /invalid event time/);
  }
  const {context} = sandbox(() => ({}));
  context.inputTime = '2024-02-29T10:00:00.123+02:00';
  assert.equal(vm.runInContext('watcherTime_(inputTime)', context), Date.parse('2024-02-29T08:00:00.123Z'));
});
