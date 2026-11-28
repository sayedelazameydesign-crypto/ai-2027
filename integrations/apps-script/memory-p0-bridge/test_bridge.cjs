/**
 * Hermetic offline tests for integrations/apps-script/memory-p0-bridge/Code.gs.
 *
 * Zero npm dependencies; runs on Node's built-in `node:test`, `node:vm`, and `node:crypto`.
 */
'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const DIR = __dirname;
const CODE = fs.readFileSync(path.join(DIR, 'Code.gs'), 'utf8');
const MANIFEST = JSON.parse(fs.readFileSync(path.join(DIR, 'appsscript.json'), 'utf8'));

function makeFakeSheet(name, initialRows) {
  const rows = initialRows.map((r) => r.map((v) => String(v ?? '')));
  const formats = [];
  return {
    name,
    rows,
    formats,
    getLastRow: () => rows.length,
    getLastColumn: () => (rows.length > 0 ? Math.max(...rows.map((r) => r.length)) : 0),
    getRange: (row, col, numRows, numCols) => ({
      setNumberFormat: (fmt) => {
        formats.push({ row, col, numRows, numCols, fmt });
      },
      setValues: (matrix) => {
        for (let r = 0; r < numRows; r++) {
          const targetIdx = row - 1 + r;
          while (rows.length <= targetIdx) rows.push([]);
          for (let c = 0; c < numCols; c++) {
            rows[targetIdx][col - 1 + c] = String(matrix[r][c] ?? '');
          }
        }
      },
      getDisplayValues: () => {
        const out = [];
        for (let r = 0; r < numRows; r++) {
          const src = rows[row - 1 + r] || [];
          const line = [];
          for (let c = 0; c < numCols; c++) {
            line.push(String(src[col - 1 + c] ?? ''));
          }
          out.push(line);
        }
        return out;
      }
    })
  };
}

function makeMemorySpreadsheet(overrides = {}) {
  const defaultTabs = {
    pa_source_records: [Array.from({ length: 23 }, (_, i) => `src_${i}`)],
    pa_tasks: [Array.from({ length: 22 }, (_, i) => `task_${i}`)],
    pa_approvals: [[
      'id', 'created_at', 'updated_at', 'version', 'source_id', 'confidence',
      'epistemic', 'sensitivity', 'consent', 'retention', 'review_at',
      'deleted_at', 'taint', 'checksum', 'action', 'payload_hash', 'summary',
      'status', 'issued_at', 'expires_at', 'consumed_at', 'single_use'
    ]],
    pa_delegation_grants: [Array.from({ length: 23 }, (_, i) => `grant_${i}`)],
    pa_audit_logs_202611: [Array.from({ length: 25 }, (_, i) => `audit_${i}`)],
    pa_system_state: [[
      'id', 'created_at', 'updated_at', 'version', 'source_id', 'confidence',
      'epistemic', 'sensitivity', 'consent', 'retention', 'review_at', 'deleted_at',
      'taint', 'checksum', 'key', 'value', 'schema_version', 'last_migration'
    ]],
    pa_projects: [Array.from({ length: 22 }, (_, i) => `proj_${i}`)]
  };
  const merged = { ...defaultTabs, ...overrides };
  const sheets = {};
  for (const [k, v] of Object.entries(merged)) {
    if (v !== null) sheets[k] = makeFakeSheet(k, v);
  }
  return {
    sheets,
    getSheetByName: (name) => sheets[name] || null
  };
}

function makeHarness({
  props = {},
  spreadsheet = makeMemorySpreadsheet(),
  httpHandler = () => ({ status: 200, body: {} }),
  lockAcquired = true
} = {}) {
  const store = { ...props };
  const logs = [];
  const calls = [];
  const triggers = [];
  let lockReleased = false;
  let uuidCounter = 0;

  const sandbox = {
    Date,
    Math,
    JSON,
    Number,
    String,
    Boolean,
    Array,
    Object,
    RegExp,
    Error,
    parseInt,
    isNaN,
    isFinite,
    Infinity,
    NaN,
    encodeURIComponent,
    Logger: { log: (line) => logs.push(String(line)) },
    LockService: {
      getScriptLock: () => ({
        tryLock: () => lockAcquired,
        releaseLock: () => { lockReleased = true; }
      })
    },
    PropertiesService: {
      getScriptProperties: () => ({
        getProperty: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
        setProperty: (k, v) => { store[k] = String(v); }
      })
    },
    SpreadsheetApp: {
      openById: (id) => {
        if (!spreadsheet) throw new Error(`Spreadsheet ${id} unreachable`);
        return spreadsheet;
      },
      flush: () => {}
    },
    ScriptApp: {
      getOAuthToken: () => 'fake-oauth-token-secret-123',
      getProjectTriggers: () => [...triggers],
      deleteTrigger: (trig) => {
        const idx = triggers.indexOf(trig);
        if (idx !== -1) triggers.splice(idx, 1);
      },
      newTrigger: (handler) => ({
        timeBased: () => ({
          everyMinutes: (mins) => ({
            create: () => {
              const t = { handler, mins };
              triggers.push(t);
              return t;
            }
          })
        })
      })
    },
    Utilities: {
      DigestAlgorithm: { SHA_256: 'SHA_256' },
      Charset: { UTF_8: 'UTF_8' },
      computeDigest: (_alg, value, charset) => {
        assert.equal(charset, 'UTF_8', 'computeDigest must pass UTF_8 charset for Arabic safety');
        const buf = crypto.createHash('sha256').update(String(value), 'utf8').digest();
        // Simulate Apps Script's signed byte[] (-128..127)
        return Array.from(buf, (b) => (b > 127 ? b - 256 : b));
      },
      getUuid: () => `00000000-0000-4000-8000-${String(++uuidCounter).padStart(12, '0')}`,
      sleep: () => {}
    },
    UrlFetchApp: {
      fetch: (url, opts = {}) => {
        const allowed = MANIFEST.urlFetchWhitelist.some((prefix) => url.startsWith(prefix));
        assert.ok(allowed, `URL blocked by manifest urlFetchWhitelist: ${url}`);
        calls.push({ url, opts });
        const reply = httpHandler(url, opts) || { status: 200, body: {} };
        const status = reply.status ?? 200;
        const text = typeof reply.body === 'string' ? reply.body : JSON.stringify(reply.body ?? {});
        return {
          getResponseCode: () => status,
          getContentText: () => text
        };
      }
    }
  };

  vm.createContext(sandbox);
  vm.runInContext(CODE, sandbox, { filename: 'Code.gs' });
  return { sandbox, store, logs, calls, triggers, spreadsheet, wasLockReleased: () => lockReleased };
}

test('manifest enforces minimal scopes and strict 3-endpoint urlFetchWhitelist', () => {
  assert.equal(MANIFEST.runtimeVersion, 'V8');
  assert.equal(MANIFEST.timeZone, 'Africa/Cairo');
  assert.deepEqual(
    [...MANIFEST.oauthScopes].sort(),
    [
      'https://www.googleapis.com/auth/calendar.events.readonly',
      'https://www.googleapis.com/auth/gmail.readonly',
      'https://www.googleapis.com/auth/script.external_request',
      'https://www.googleapis.com/auth/script.scriptapp',
      'https://www.googleapis.com/auth/spreadsheets'
    ]
  );
  for (const forbidden of ['gmail.send', 'gmail.modify', 'calendar.events"', 'drive"']) {
    assert.ok(!JSON.stringify(MANIFEST.oauthScopes).includes(forbidden));
  }
  assert.deepEqual(
    [...MANIFEST.urlFetchWhitelist].sort(),
    [
      'https://api.telegram.org/',
      'https://gmail.googleapis.com/gmail/v1/users/me/messages',
      'https://www.googleapis.com/calendar/v3/calendars/primary/events'
    ]
  );
});

test('watcherPoll keeps read-only baseline behavior with zero writes', () => {
  const h = makeHarness({
    httpHandler: (url) => {
      if (url.includes('gmail.googleapis.com')) {
        return { status: 200, body: { messages: [{ id: 'm1' }, { id: 'm2' }] } };
      }
      return {
        status: 200,
        body: {
          items: [
            { start: { dateTime: '2026-11-28T10:00:00Z' }, end: { dateTime: '2026-11-28T11:00:00Z' } },
            { start: { dateTime: '2026-11-28T10:30:00Z' }, end: { dateTime: '2026-11-28T11:30:00Z' } },
            { start: { date: '2026-11-29' }, end: { date: '2026-11-30' } }
          ]
        }
      };
    }
  });
  const res = h.sandbox.watcherPoll();
  assert.equal(res.status, 'ok');
  assert.equal(res.writes, 0);
  assert.equal(res.sources.gmail_unread, 2);
  assert.equal(res.sources.calendar_events_48h, 2);
  assert.equal(res.calendar_conflicts, 1);
  assert.equal(res.all_day_excluded, 1);
  assert.equal(Object.keys(h.store).length, 0);
  assert.ok(h.wasLockReleased());
});

test('bridgeDoctor reports boolean presence only and never logs secret values', () => {
  const secretToken = '999999999:SUPER_SECRET_BOT_TOKEN_DO_NOT_LEAK';
  const secretChat = '-100987654321';
  const h = makeHarness({
    props: {
      PA_MEMORY_SHEET_ID: '1LPceG_K9jPaGOEFVf5wymR4eYyc3Jh4Bpgg9VatNf2I',
      PA_APPROVAL_BOT_TOKEN: secretToken,
      PA_APPROVAL_CHAT_ID: secretChat,
      PA_APPROVAL_ALLOWED_SENDERS: '111222333'
    }
  });
  const doc = h.sandbox.bridgeDoctor();
  assert.equal(doc.status, 'ok');
  assert.equal(doc.properties_present.PA_APPROVAL_BOT_TOKEN, true);
  assert.equal(doc.memory_sheet.reachable, true);
  const serialized = JSON.stringify(doc) + '\n' + h.logs.join('\n');
  assert.ok(!serialized.includes(secretToken));
  assert.ok(!serialized.includes(secretChat));
});

test('bindDefaultMemorySheet refuses to bind if required pa_* tabs are missing', () => {
  const incomplete = makeMemorySpreadsheet({ pa_approvals: null });
  const hBad = makeHarness({ spreadsheet: incomplete });
  assert.throws(() => hBad.sandbox.bindDefaultMemorySheet(), /missing tabs: pa_approvals/);
  assert.equal(hBad.store.PA_MEMORY_SHEET_ID, undefined);

  const hGood = makeHarness();
  const bound = hGood.sandbox.bindDefaultMemorySheet();
  assert.equal(bound.status, 'bound');
  assert.equal(hGood.store.PA_MEMORY_SHEET_ID, '1LPceG_K9jPaGOEFVf5wymR4eYyc3Jh4Bpgg9VatNf2I');
});

test('memoryWatcherCycle redacts sensitive data, deduplicates, chains SHA-256 audit, and verifies read-back', () => {
  const h = makeHarness({
    props: { PA_MEMORY_SHEET_ID: '1LPceG_K9jPaGOEFVf5wymR4eYyc3Jh4Bpgg9VatNf2I' },
    httpHandler: (url) => {
      if (url.includes('/messages?')) {
        return { status: 200, body: { messages: [{ id: 'msg-1' }] } };
      }
      if (url.includes('/messages/msg-1')) {
        return {
          status: 200,
          body: {
            threadId: 'th-1',
            snippet: 'الرقم القومي 29901011234567 وكلمة السر password=TopSecret99',
            payload: {
              headers: [
                { name: 'From', value: 'client@example.com' },
                { name: 'Subject', value: 'عاجل: فاتورة مستحقة' },
                { name: 'Date', value: 'Sat, 28 Nov 2026 09:00:00 +0000' }
              ]
            }
          }
        };
      }
      return {
        status: 200,
        body: {
          items: [{
            id: 'cal-1',
            summary: 'اجتماع مراجعة التصميم',
            start: { dateTime: '2026-11-28T14:00:00Z' },
            end: { dateTime: '2026-11-28T15:00:00Z' },
            htmlLink: 'https://calendar.google.com/event?eid=1',
            attendees: [{ email: 'a@b.com' }]
          }]
        }
      };
    }
  });

  const first = h.sandbox.memoryWatcherCycle();
  assert.equal(first.status, 'ok');
  assert.equal(first.sources_ingested, 2);
  assert.equal(first.tasks_created, 2);
  assert.match(first.audit_self_hash, /^[0-9a-f]{64}$/);

  const srcRows = h.spreadsheet.sheets.pa_source_records.rows;
  assert.equal(srcRows.length, 3);
  assert.equal(srcRows[1].length, 23);
  assert.equal(srcRows[1][7], 'confidential');
  assert.equal(srcRows[1][8], 'not_granted');
  assert.equal(srcRows[1][12], 'tainted');
  assert.ok(!srcRows[1][19].includes('29901011234567'));
  assert.ok(!srcRows[1][19].includes('TopSecret99'));
  assert.ok(srcRows[1][19].includes('[REDACTED_SENSITIVE]'));

  const taskRows = h.spreadsheet.sheets.pa_tasks.rows;
  assert.equal(taskRows.length, 3);
  assert.equal(taskRows[1].length, 22);
  assert.equal(taskRows[1][6], 'inferred');
  assert.equal(taskRows[1][8], 'not_granted');

  // Second cycle with identical upstream items must ingest 0 duplicates and chain prev_hash -> self_hash
  const second = h.sandbox.memoryWatcherCycle();
  assert.equal(second.sources_ingested, 0);
  assert.equal(second.tasks_created, 0);

  const auditRows = h.spreadsheet.sheets.pa_audit_logs_202611.rows;
  assert.equal(auditRows.length, 3);
  assert.equal(auditRows[1][24], first.audit_self_hash);
  assert.equal(auditRows[2][23], first.audit_self_hash); // prev_hash chains to row 1 self_hash
  assert.equal(auditRows[2][24], second.audit_self_hash);
});

test('approvalBotCycle fails closed when unconfigured and enforces allowlist + grant/deny transitions', () => {
  const unconf = makeHarness({ props: { PA_MEMORY_SHEET_ID: 'sheet-1' } });
  assert.throws(() => unconf.sandbox.approvalBotCycle(), /fail-closed/);

  const pendingRow = [
    '01APPRTEST0000000000000001', '2026-11-28T08:00:00.000Z', '2026-11-28T08:00:00.000Z', '1',
    '', '1', 'fact', 'personal', 'not_granted', 'keep', '', '', 'clean', 'seedhash',
    'gmail.send_draft', 'deadbeef', 'إرسال مسودة عرض سعر 29901011234567', 'pending',
    '2026-11-28T08:00:00.000Z', '2099-12-31T23:59:59.000Z', '', 'true'
  ];
  const ss = makeMemorySpreadsheet({
    pa_approvals: [
      [
        'id', 'created_at', 'updated_at', 'version', 'source_id', 'confidence',
        'epistemic', 'sensitivity', 'consent', 'retention', 'review_at',
        'deleted_at', 'taint', 'checksum', 'action', 'payload_hash', 'summary',
        'status', 'issued_at', 'expires_at', 'consumed_at', 'single_use'
      ],
      pendingRow
    ]
  });

  const sentMessages = [];
  const callbackAnswers = [];
  let updatesBatch = [
    {
      update_id: 100,
      callback_query: {
        id: 'cb-stranger',
        from: { id: 999999 },
        data: 'appr:01APPRTEST0000000000000001:grant'
      }
    },
    {
      update_id: 101,
      callback_query: {
        id: 'cb-owner',
        from: { id: 777111 },
        data: 'appr:01APPRTEST0000000000000001:grant'
      }
    }
  ];

  const h = makeHarness({
    spreadsheet: ss,
    props: {
      PA_MEMORY_SHEET_ID: '1LPceG_K9jPaGOEFVf5wymR4eYyc3Jh4Bpgg9VatNf2I',
      PA_APPROVAL_BOT_TOKEN: '123456:FAKE_TOKEN',
      PA_APPROVAL_CHAT_ID: '777111',
      PA_APPROVAL_ALLOWED_SENDERS: '777111',
      PA_APPROVAL_VOTE_BASE_URL: 'https://vote.example.workers.dev'
    },
    httpHandler: (url, opts) => {
      if (url.endsWith('/sendMessage')) {
        const body = JSON.parse(opts.payload);
        sentMessages.push(body);
        return { status: 200, body: { ok: true, result: { message_id: 42 } } };
      }
      if (url.includes('/getUpdates')) {
        const current = updatesBatch;
        updatesBatch = [];
        return { status: 200, body: { ok: true, result: current } };
      }
      if (url.endsWith('/answerCallbackQuery')) {
        callbackAnswers.push(JSON.parse(opts.payload));
        return { status: 200, body: { ok: true, result: true } };
      }
      throw new Error(`Unexpected URL: ${url}`);
    }
  });

  const res = h.sandbox.approvalBotCycle();
  assert.equal(res.cards_sent, 1);
  assert.equal(res.callbacks_handled, 2);
  assert.equal(res.granted, 1);
  assert.equal(res.denied, 1);

  assert.equal(sentMessages.length, 1);
  assert.ok(!sentMessages[0].text.includes('29901011234567'));
  assert.ok(sentMessages[0].text.includes('[REDACTED_SENSITIVE]'));
  assert.ok(sentMessages[0].text.includes('https://vote.example.workers.dev?approval=01APPRTEST0000000000000001'));

  assert.equal(callbackAnswers.length, 2);
  assert.equal(callbackAnswers[0].text, 'غير مصرح لك بالتصويت.');
  assert.equal(callbackAnswers[1].text, '✅ تم التسجيل: موافقة ممنوحة.');

  const updatedRow = ss.sheets.pa_approvals.rows[1];
  assert.equal(updatedRow[17], 'granted');
  assert.equal(updatedRow[3], '2');
  assert.match(updatedRow[13], /^[0-9a-f]{64}$/);

  const savedState = JSON.parse(h.store.PA_BOT_STATE_JSON);
  assert.equal(savedState.offset, 102);
  assert.deepEqual(savedState.sent, ['01APPRTEST0000000000000001']);
});

test('installTrigger30m removes stale triggers before installing single 30m trigger', () => {
  const h = makeHarness();
  h.sandbox.installTrigger30m();
  const second = h.sandbox.installTrigger30m();
  assert.equal(second.removed_previous, 1);
  assert.equal(h.triggers.length, 1);
  assert.equal(h.triggers[0].handler, 'runAllGovernedCycles');
  assert.equal(h.triggers[0].mins, 30);
});

test('sanitizeError_ strips Telegram bot tokens, Bearer tokens, and email addresses', () => {
  const h = makeHarness();
  const scrubbed = h.sandbox.sanitizeError_(
    new Error('Failed POST https://api.telegram.org/bot123456:ABC-DEF_secret99/sendMessage Bearer ya29.secret user@domain.com')
  );
  assert.ok(!scrubbed.includes('ABC-DEF_secret99'));
  assert.ok(!scrubbed.includes('ya29.secret'));
  assert.ok(!scrubbed.includes('user@domain.com'));
  assert.ok(scrubbed.includes('bot[REDACTED]'));
});
