/**
 * AI-2027 — Google Apps Script Memory P0 & Zero-Interface Approval Bridge
 * Target project: Celia Fashion Design (1UNRaWAvGNYI6lYnJ1nlQt3PyMsG3eZkI--WASZrhMPoBzB6ZqiBXG-5z)
 * Target sheet  : My Personal Agent — Memory (P0) (1LPceG_K9jPaGOEFVf5wymR4eYyc3Jh4Bpgg9VatNf2I)
 *
 * Governance invariants (AD-002, AD-004, AD-005):
 *   1. MAX_SPEND_USD = 0: runs entirely inside free Google Apps Script + Sheets + Telegram Bot API.
 *   2. Fail-closed: missing ScriptProperties, unverified tabs, empty PA_APPROVAL_ALLOWED_SENDERS,
 *      expired approvals, or overlapping executions refuse action cleanly.
 *   3. Privacy & ssGateway: Gmail reads metadata headers (From, Subject, Date) + snippet only;
 *      sensitive patterns (14-digit national ID, cards, IBAN, password/otp/pin/secret/token) are
 *      redacted before any write or Telegram card; secret values are never logged.
 *   4. Evidence & Tamper-evident chain: every write to pa_source_records (A:W), pa_tasks (A:V),
 *      pa_approvals (A:V), pa_system_state (A:R), and pa_audit_logs_202611 (A:Y) uses canonical
 *      SHA-256 matching src/watchers/proactive_watcher.py and src/watchers/approval_bot.py,
 *      with read-back verification on audit rows.
 */

var BRIDGE_CONFIG = {
  SCRIPT_ID: '1UNRaWAvGNYI6lYnJ1nlQt3PyMsG3eZkI--WASZrhMPoBzB6ZqiBXG-5z',
  DEFAULT_MEMORY_SHEET_ID: '1LPceG_K9jPaGOEFVf5wymR4eYyc3Jh4Bpgg9VatNf2I',
  AUDIT_TAB_DEFAULT: 'pa_audit_logs_202611',
  GMAIL_QUERY: 'is:unread -category:promotions -category:social',
  GMAIL_MAX_RESULTS: 25,
  GMAIL_PAGE_SIZE: 50,
  CALENDAR_LOOKAHEAD_HOURS: 48,
  CALENDAR_PAGE_SIZE: 100,
  MAX_PAGES: 10,
  FETCH_MAX_ATTEMPTS: 3,
  FETCH_BACKOFF_MS: 500,
  EXCERPT_MAX_CHARS: 280,
  AGENT_ACTOR_ID: 'agent:apps_script_bridge',
  BOT_STATE_PROPERTY: 'PA_BOT_STATE_JSON',
  REQUIRED_TABS: [
    'pa_source_records',
    'pa_tasks',
    'pa_approvals',
    'pa_delegation_grants',
    'pa_audit_logs_202611',
    'pa_system_state',
    'pa_projects'
  ]
};

var APPROVAL_COLUMNS = [
  'id', 'created_at', 'updated_at', 'version', 'source_id', 'confidence',
  'epistemic', 'sensitivity', 'consent', 'retention', 'review_at',
  'deleted_at', 'taint', 'checksum', 'action', 'payload_hash', 'summary',
  'status', 'issued_at', 'expires_at', 'consumed_at', 'single_use'
];

var COL_UPDATED_AT = 2;
var COL_VERSION = 3;
var COL_CHECKSUM = 13;
var COL_STATUS = 17;
var COL_CONSUMED_AT = 20;

var STATE_PENDING = 'pending';
var STATE_GRANTED = 'granted';
var STATE_DENIED = 'denied';
var STATE_EXPIRED = 'expired';

var CALLBACK_RE = /^appr:([0-9A-Za-z]+):(grant|deny)$/;

var DENY_ANSWERS = {
  invalid_callback: 'طلب غير صالح.',
  sender_not_allowed: 'غير مصرح لك بالتصويت.',
  unknown_approval: 'الطلب غير موجود.',
  not_pending: 'تمت معالجة هذا الطلب مسبقاً.',
  expired: 'انتهت صلاحية الطلب.',
  consumed: 'استُهلك هذا الطلب (single-use).'
};

var REDACT_PATTERNS = [
  /\b\d{14}\b/g,
  /\b(?:\d[ -]*?){13,16}\b/g,
  /(?:password|passwd|otp|pin|secret|token)\s*[:=]\s*\S+/gi,
  /\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b/g
];

var TASK_HINTS_RE = /(مطلوب|موعد|اجتماع|تسليم|فاتورة|دفع|رد على|عاجل|urgent|deadline|action required|invoice|meeting|follow[- ]?up|review)/i;

// ============================================================================
// 1. Read-Only Connectivity Probe (keeps exact parity with watcherPoll baseline)
// ============================================================================

/**
 * Read-only probe: verifies Gmail & Calendar OAuth + UrlFetch connectivity
 * with zero writes to Gmail, Calendar, Sheets, or PropertiesService.
 */
function watcherPoll() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(0)) {
    var skipped = {
      ts: new Date().toISOString(),
      status: 'skipped',
      reason: 'previous_invocation_still_running',
      writes: 0
    };
    Logger.log(JSON.stringify(skipped));
    return skipped;
  }

  try {
    var now = new Date();
    var windowEnd = new Date(now.getTime() + BRIDGE_CONFIG.CALENDAR_LOOKAHEAD_HOURS * 3600 * 1000);
    var gmail = countUnreadMetadata_(BRIDGE_CONFIG.GMAIL_QUERY);
    var calendar = inspectCalendarWindow_(now, windowEnd);

    var summary = {
      ts: now.toISOString(),
      status: 'ok',
      baseline: true,
      sources: {
        gmail_unread: gmail.count,
        gmail_truncated: gmail.truncated,
        calendar_events_48h: calendar.timedCount,
        calendar_truncated: calendar.truncated
      },
      calendar_conflicts: calendar.conflicts,
      all_day_excluded: calendar.allDayCount,
      writes: 0
    };
    Logger.log(JSON.stringify(summary));
    return summary;
  } catch (err) {
    var failure = {
      ts: new Date().toISOString(),
      status: 'error',
      error_type: (err && err.name) ? String(err.name) : 'Error',
      message: sanitizeError_(err),
      writes: 0
    };
    Logger.log(JSON.stringify(failure));
    throw err;
  } finally {
    lock.releaseLock();
  }
}

// ============================================================================
// 2. Setup, Doctor & Trigger Helpers (Names-only diagnostics, Fail-Closed)
// ============================================================================

/**
 * Binds DEFAULT_MEMORY_SHEET_ID into ScriptProperties ONLY after verifying
 * that every required pa_* worksheet tab exists in the target spreadsheet.
 */
function bindDefaultMemorySheet() {
  var sheetId = BRIDGE_CONFIG.DEFAULT_MEMORY_SHEET_ID;
  var ss = SpreadsheetApp.openById(sheetId);
  var check = verifyRequiredTabs_(ss);
  if (!check.ok) {
    throw new Error('Refusing to bind PA_MEMORY_SHEET_ID; missing tabs: ' + check.missing.join(','));
  }
  PropertiesService.getScriptProperties().setProperty('PA_MEMORY_SHEET_ID', sheetId);
  var report = {
    ts: utcNowIso_(),
    status: 'bound',
    script_id: BRIDGE_CONFIG.SCRIPT_ID,
    memory_sheet_id: sheetId,
    verified_tabs: check.present.length
  };
  Logger.log(JSON.stringify(report));
  return report;
}

/**
 * Read-only health report. NEVER prints secret values, lengths, or prefixes.
 */
function bridgeDoctor() {
  var props = PropertiesService.getScriptProperties();
  var sheetId = String(props.getProperty('PA_MEMORY_SHEET_ID') || '').trim();
  var botToken = String(props.getProperty('PA_APPROVAL_BOT_TOKEN') || '').trim();
  var chatId = String(props.getProperty('PA_APPROVAL_CHAT_ID') || '').trim();
  var sendersRaw = String(props.getProperty('PA_APPROVAL_ALLOWED_SENDERS') || '').trim();
  var senders = parseAllowedSenders_(sendersRaw);

  var report = {
    ts: utcNowIso_(),
    status: 'ok',
    script_id: BRIDGE_CONFIG.SCRIPT_ID,
    posture: {
      max_spend_usd: 0,
      default_verdict: 'deny',
      secret_values_logged: false
    },
    properties_present: {
      PA_MEMORY_SHEET_ID: Boolean(sheetId),
      PA_APPROVAL_BOT_TOKEN: Boolean(botToken),
      PA_APPROVAL_CHAT_ID: Boolean(chatId),
      PA_APPROVAL_ALLOWED_SENDERS: senders.length > 0,
      PA_APPROVAL_VOTE_BASE_URL: Boolean(String(props.getProperty('PA_APPROVAL_VOTE_BASE_URL') || '').trim())
    },
    allowed_senders_count: senders.length,
    memory_sheet: {
      configured: Boolean(sheetId),
      reachable: false,
      missing_tabs: [],
      audit_tail_present: false
    }
  };

  if (sheetId) {
    try {
      var ss = SpreadsheetApp.openById(sheetId);
      var tabCheck = verifyRequiredTabs_(ss);
      report.memory_sheet.reachable = true;
      report.memory_sheet.missing_tabs = tabCheck.missing;
      if (!tabCheck.ok) {
        report.status = 'degraded';
      } else {
        var auditSheet = ss.getSheetByName(BRIDGE_CONFIG.AUDIT_TAB_DEFAULT);
        var auditRows = readAllRowsRaw_(auditSheet);
        if (auditRows.length >= 2) {
          var tail = auditRows[auditRows.length - 1][24] || '';
          report.memory_sheet.audit_tail_present = /^[0-9a-f]{64}$/.test(tail);
        }
      }
    } catch (err) {
      report.status = 'degraded';
      report.memory_sheet.error = sanitizeError_(err);
    }
  } else {
    report.status = 'unconfigured';
  }

  Logger.log(JSON.stringify(report));
  return report;
}

/**
 * Installs a single idempotent 30-minute trigger for runAllGovernedCycles
 * after removing any stale bridge triggers to prevent duplicate runs.
 */
function installTrigger30m() {
  var removed = removeTriggers_();
  ScriptApp.newTrigger('runAllGovernedCycles')
    .timeBased()
    .everyMinutes(30)
    .create();
  var result = {
    ts: utcNowIso_(),
    status: 'installed',
    handler: 'runAllGovernedCycles',
    cadence_minutes: 30,
    removed_previous: removed
  };
  Logger.log(JSON.stringify(result));
  return result;
}

/**
 * Removes all triggers owned by this project for clean teardown/re-install.
 */
function removeTriggers() {
  var removed = removeTriggers_();
  var result = { ts: utcNowIso_(), status: 'removed', removed_count: removed };
  Logger.log(JSON.stringify(result));
  return result;
}

function removeTriggers_() {
  var triggers = ScriptApp.getProjectTriggers();
  var removed = 0;
  for (var i = 0; i < triggers.length; i++) {
    ScriptApp.deleteTrigger(triggers[i]);
    removed++;
  }
  return removed;
}

// ============================================================================
// 3. Governed Memory Watcher Cycle (Parity with src/watchers/proactive_watcher.py)
// ============================================================================

/**
 * Runs one governed ingestion cycle into Memory P0 (pa_source_records, pa_tasks,
 * pa_audit_logs_202611, pa_system_state) using ssGateway redaction and SHA-256
 * hash chaining. Fail-closed if PA_MEMORY_SHEET_ID is unset or tabs are missing.
 */
function memoryWatcherCycle() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(0)) {
    var skipped = {
      ts: utcNowIso_(),
      status: 'skipped',
      reason: 'previous_invocation_still_running'
    };
    Logger.log(JSON.stringify(skipped));
    return skipped;
  }

  try {
    return memoryWatcherCycleUnlocked_();
  } finally {
    lock.releaseLock();
  }
}

function memoryWatcherCycleUnlocked_() {
  var props = PropertiesService.getScriptProperties();
  var sheetId = String(props.getProperty('PA_MEMORY_SHEET_ID') || '').trim();
  if (!sheetId) {
    throw new Error('PA_MEMORY_SHEET_ID is not set in ScriptProperties — fail-closed (run bindDefaultMemorySheet first)');
  }

  var ss = SpreadsheetApp.openById(sheetId);
  var tabCheck = verifyRequiredTabs_(ss);
  if (!tabCheck.ok) {
    throw new Error('Memory sheet missing required tabs: ' + tabCheck.missing.join(','));
  }

  var srcSheet = ss.getSheetByName('pa_source_records');
  var taskSheet = ss.getSheetByName('pa_tasks');
  var existingExternalIds = readColumnSet_(srcSheet, 15);
  var existingTaskDedupe = readColumnSet_(taskSheet, 21);

  var ingestedSources = [];
  var createdTasks = [];

  var gmailItems = fetchUnreadGmailMetadata_(BRIDGE_CONFIG.GMAIL_QUERY, BRIDGE_CONFIG.GMAIL_MAX_RESULTS);
  for (var i = 0; i < gmailItems.length; i++) {
    var msg = gmailItems[i];
    var extId = 'gmail:' + msg.id;
    if (existingExternalIds[extId]) continue;

    var rawText = String(msg.subject || '') + ' — ' + String(msg.snippet || '');
    var red = sanitizeAndRedact_(rawText);
    var srcId = ingestSourceRecord_(srcSheet, {
      connector: 'gmail',
      external_id: extId,
      uri: 'https://mail.google.com/mail/u/0/#all/' + msg.id,
      captured_at: utcNowIso_(),
      excerpt: red.text,
      sensitivity: red.sensitivity,
      metadata: {
        from: sanitizeAndRedact_(String(msg.from || '')).text.slice(0, 120),
        date: String(msg.date || '').slice(0, 64),
        thread_id: String(msg.threadId || '')
      }
    });
    existingExternalIds[extId] = true;
    ingestedSources.push(srcId);

    if (TASK_HINTS_RE.test(red.text)) {
      var titleRed = sanitizeAndRedact_('[بريد] ' + String(msg.subject || '').slice(0, 100));
      var taskId = createCandidateTask_(taskSheet, existingTaskDedupe, {
        source_id: srcId,
        title: titleRed.text,
        description: red.text,
        due_at: '',
        priority: /عاجل|urgent/i.test(red.text) ? 'p1' : 'p2',
        sensitivity: red.sensitivity,
        dedupe_basis: extId
      });
      if (taskId) createdTasks.push(taskId);
    }
  }

  var now = new Date();
  var windowEnd = new Date(now.getTime() + BRIDGE_CONFIG.CALENDAR_LOOKAHEAD_HOURS * 3600 * 1000);
  var calEvents = fetchCalendarWindowDetails_(now, windowEnd);
  for (var j = 0; j < calEvents.length; j++) {
    var ev = calEvents[j];
    var startRaw = String(ev.startIso || '');
    var calExtId = 'gcal:' + ev.id + ':' + startRaw;
    if (existingExternalIds[calExtId]) continue;

    var summaryRed = sanitizeAndRedact_(String(ev.summary || '(حدث بدون عنوان)'));
    var calSrcId = ingestSourceRecord_(srcSheet, {
      connector: 'google_calendar',
      external_id: calExtId,
      uri: String(ev.htmlLink || ''),
      captured_at: utcNowIso_(),
      excerpt: summaryRed.text + ' @ ' + startRaw,
      sensitivity: summaryRed.sensitivity,
      metadata: {
        start: startRaw,
        end: String(ev.endIso || ''),
        attendees_count: Number(ev.attendeesCount || 0)
      }
    });
    existingExternalIds[calExtId] = true;
    ingestedSources.push(calSrcId);

    var calTaskId = createCandidateTask_(taskSheet, existingTaskDedupe, {
      source_id: calSrcId,
      title: ('تحضير لحدث: ' + summaryRed.text).slice(0, 120),
      description: 'موعد قادم في التقويم عند ' + startRaw,
      due_at: startRaw,
      priority: 'p2',
      sensitivity: summaryRed.sensitivity,
      dedupe_basis: calExtId
    });
    if (calTaskId) createdTasks.push(calTaskId);
  }

  var auditHash = appendAuditLog_(ss, {
    unit: 'watcher',
    action: 'proactive.poll',
    outcome: 'ok',
    subjects: ingestedSources.concat(createdTasks),
    payload: {
      sources_ingested: ingestedSources.length,
      tasks_created: createdTasks.length,
      runtime: 'apps_script_v8'
    },
    policy_id: 'P1-WATCHER-INGEST'
  });

  upsertSystemState_(ss, 'apps_script_bridge_cursor', {
    script_id: BRIDGE_CONFIG.SCRIPT_ID,
    last_cycle_at: utcNowIso_(),
    sources_ingested: ingestedSources.length,
    tasks_created: createdTasks.length,
    audit_self_hash: auditHash,
    status: 'active'
  });

  var report = {
    ts: utcNowIso_(),
    status: 'ok',
    sources_ingested: ingestedSources.length,
    tasks_created: createdTasks.length,
    audit_self_hash: auditHash
  };
  Logger.log(JSON.stringify(report));
  return report;
}

// ============================================================================
// 4. Governed Zero-Interface Approval Bot Cycle (Parity with approval_bot.py)
// ============================================================================

/**
 * Delivers pending non-expired pa_approvals cards to Telegram and processes
 * owner callback decisions fail-closed (AD-004).
 */
function approvalBotCycle() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(0)) {
    var skipped = {
      ts: utcNowIso_(),
      status: 'skipped',
      reason: 'previous_invocation_still_running'
    };
    Logger.log(JSON.stringify(skipped));
    return skipped;
  }

  try {
    return approvalBotCycleUnlocked_();
  } finally {
    lock.releaseLock();
  }
}

function approvalBotCycleUnlocked_() {
  var props = PropertiesService.getScriptProperties();
  var sheetId = String(props.getProperty('PA_MEMORY_SHEET_ID') || '').trim();
  var botToken = String(props.getProperty('PA_APPROVAL_BOT_TOKEN') || '').trim();
  var chatId = String(props.getProperty('PA_APPROVAL_CHAT_ID') || '').trim();
  var sendersRaw = String(props.getProperty('PA_APPROVAL_ALLOWED_SENDERS') || '').trim();
  var voteBaseUrl = String(props.getProperty('PA_APPROVAL_VOTE_BASE_URL') || '').trim();

  var missing = [];
  if (!sheetId) missing.push('PA_MEMORY_SHEET_ID');
  if (!botToken) missing.push('PA_APPROVAL_BOT_TOKEN');
  if (!chatId) missing.push('PA_APPROVAL_CHAT_ID');
  var allowedSenders = parseAllowedSenders_(sendersRaw);
  if (allowedSenders.length === 0) missing.push('PA_APPROVAL_ALLOWED_SENDERS');

  if (missing.length > 0) {
    throw new Error('approval bot unconfigured (missing ' + missing.join(', ') + ') — fail-closed');
  }

  var allowedMap = {};
  for (var a = 0; a < allowedSenders.length; a++) {
    allowedMap[allowedSenders[a]] = true;
  }

  var ss = SpreadsheetApp.openById(sheetId);
  var tabCheck = verifyRequiredTabs_(ss);
  if (!tabCheck.ok) {
    throw new Error('Memory sheet missing required tabs: ' + tabCheck.missing.join(','));
  }

  var apprSheet = ss.getSheetByName('pa_approvals');
  var state = loadBotState_(props);
  var sentSet = {};
  for (var s = 0; s < state.sent.length; s++) {
    sentSet[String(state.sent[s])] = true;
  }

  var now = new Date();
  var allRows = readAllRowsRaw_(apprSheet);
  var newlySent = [];

  for (var r = 1; r < allRows.length; r++) {
    var ap = approvalFromRow_(allRows[r]);
    if (!ap.id || ap.status !== STATE_PENDING) continue;
    if (isExpired_(ap.expires_at, now)) continue;
    if (sentSet[ap.id]) continue;

    var card = buildApprovalCard_(ap, voteBaseUrl);
    var messageId = telegramSendCard_(botToken, chatId, card);
    sentSet[ap.id] = true;
    newlySent.push({ id: ap.id, message_id: messageId });
  }

  if (newlySent.length > 0) {
    var sentSubjects = [];
    for (var ns = 0; ns < newlySent.length; ns++) sentSubjects.push(newlySent[ns].id);
    appendAuditLog_(ss, {
      unit: 'approval',
      action: 'approval.cards_sent',
      outcome: 'allowed',
      subjects: sentSubjects,
      payload: { sent: newlySent.length },
      policy_id: 'P1-APPROVAL-BOT'
    });
    // Persist sent card IDs immediately before polling callbacks so a transient
    // getUpdates failure never re-sends already delivered cards while keeping old offset.
    saveBotState_(props, Object.keys(sentSet).sort(), state.offset);
  }

  var updates = telegramFetchUpdates_(botToken, state.offset);
  var nextOffset = state.offset;
  var handled = [];

  for (var u = 0; u < updates.length; u++) {
    var upd = updates[u];
    var nextCandidate = Number(upd.update_id || 0) + 1;
    if (upd.callback_query) {
      var decision = handleApprovalCallback_(ss, apprSheet, botToken, allowedMap, upd.callback_query, new Date());
      handled.push(decision);
    }
    nextOffset = nextCandidate;
  }

  saveBotState_(props, Object.keys(sentSet).sort(), nextOffset);

  var grantedCount = 0;
  var deniedCount = 0;
  for (var h = 0; h < handled.length; h++) {
    if (handled[h].state === STATE_GRANTED) grantedCount++;
    if (!handled[h].ok) deniedCount++;
  }

  var report = {
    ts: utcNowIso_(),
    status: 'ok',
    cards_sent: newlySent.length,
    callbacks_handled: handled.length,
    granted: grantedCount,
    denied: deniedCount
  };
  Logger.log(JSON.stringify(report));
  return report;
}

/**
 * Unified 30-minute trigger entrypoint: runs memoryWatcherCycle, and runs
 * approvalBotCycle when Telegram credentials + allowlist are configured.
 */
function runAllGovernedCycles() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(0)) {
    var skipped = {
      ts: utcNowIso_(),
      status: 'skipped',
      reason: 'previous_invocation_still_running'
    };
    Logger.log(JSON.stringify(skipped));
    return skipped;
  }

  try {
    var watcherResult = memoryWatcherCycleUnlocked_();
    var props = PropertiesService.getScriptProperties();
    var hasTelegram = Boolean(
      String(props.getProperty('PA_APPROVAL_BOT_TOKEN') || '').trim() &&
      String(props.getProperty('PA_APPROVAL_CHAT_ID') || '').trim() &&
      parseAllowedSenders_(props.getProperty('PA_APPROVAL_ALLOWED_SENDERS') || '').length > 0
    );
    var approvalResult = hasTelegram
      ? approvalBotCycleUnlocked_()
      : { status: 'skipped_unconfigured', reason: 'telegram_properties_absent' };

    var combined = {
      ts: utcNowIso_(),
      status: 'ok',
      watcher: watcherResult,
      approval_bot: approvalResult
    };
    Logger.log(JSON.stringify(combined));
    return combined;
  } finally {
    lock.releaseLock();
  }
}

// ============================================================================
// 5. Approval Callback & Card Logic (Exact Parity with approval_bot.py)
// ============================================================================

function handleApprovalCallback_(ss, apprSheet, botToken, allowedMap, query, now) {
  var callbackData = String((query && (query.data !== undefined ? query.data : query.callback_data)) || '');
  var sender = String((query && query.from && query.from.id) || '');
  var queryId = String((query && query.id) || '');

  var match = CALLBACK_RE.exec(callbackData);
  if (!match) {
    return denyCallback_(ss, botToken, null, 'invalid_callback', sender, callbackData, queryId);
  }
  var approvalId = match[1];
  var verb = match[2];

  if (!allowedMap[sender]) {
    return denyCallback_(ss, botToken, approvalId, 'sender_not_allowed', sender, callbackData, queryId);
  }

  var found = findApprovalRow_(apprSheet, approvalId);
  if (!found) {
    return denyCallback_(ss, botToken, approvalId, 'unknown_approval', sender, callbackData, queryId);
  }

  var sheetRow = found.sheetRow;
  var row = found.row;
  var ap = approvalFromRow_(row);

  if (ap.status !== STATE_PENDING) {
    return denyCallback_(ss, botToken, approvalId, 'not_pending', sender, callbackData, queryId);
  }

  var nowIso = formatIsoMs_(now);
  if (isExpired_(ap.expires_at, now)) {
    persistApprovalState_(apprSheet, sheetRow, row, ap, STATE_EXPIRED, nowIso);
    return auditApprovalDecision_(ss, botToken, ap, STATE_EXPIRED, 'expired', sender, callbackData, queryId);
  }

  var singleUse = String(ap.single_use || '').toLowerCase() === 'true';
  if (singleUse && String(ap.consumed_at || '').trim() !== '') {
    return denyCallback_(ss, botToken, approvalId, 'consumed', sender, callbackData, queryId);
  }

  var nextState = verb === 'grant' ? STATE_GRANTED : STATE_DENIED;
  persistApprovalState_(apprSheet, sheetRow, row, ap, nextState, nowIso);
  return auditApprovalDecision_(ss, botToken, ap, nextState, 'user_decision', sender, callbackData, queryId);
}

function persistApprovalState_(apprSheet, sheetRow, row, ap, state, nowIso) {
  row[COL_UPDATED_AT] = nowIso;
  var parsedVer = parseInt(row[COL_VERSION] || '1', 10);
  row[COL_VERSION] = isNaN(parsedVer) ? '2' : String(parsedVer + 1);
  row[COL_STATUS] = state;
  row[COL_CONSUMED_AT] = nowIso;
  var updated = approvalFromRow_(row);
  row[COL_CHECKSUM] = approvalChecksum_(updated);
  writeRowAtRaw_(apprSheet, sheetRow, row);
  var refreshed = approvalFromRow_(row);
  var keys = Object.keys(refreshed);
  for (var i = 0; i < keys.length; i++) {
    ap[keys[i]] = refreshed[keys[i]];
  }
}

function auditApprovalDecision_(ss, botToken, ap, state, reason, sender, callbackData, queryId) {
  var policy = state === STATE_GRANTED ? 'POL-004:DELEGATION-GRANTED' : 'POL-004:DELEGATION-DENIED';
  appendAuditLog_(ss, {
    unit: 'approval',
    action: 'approval.' + state,
    outcome: state,
    subjects: [ap.id],
    payload: {
      approval_id: ap.id,
      action: ap.action || '',
      state: state,
      reason: reason,
      sender: sender,
      callback: callbackData,
      version: ap.version || ''
    },
    policy_id: policy
  });

  var answer;
  var ok;
  if (state === STATE_GRANTED) {
    answer = '✅ تم التسجيل: موافقة ممنوحة.';
    ok = true;
  } else if (state === STATE_EXPIRED) {
    answer = DENY_ANSWERS.expired;
    ok = false;
  } else {
    answer = 'تم التسجيل: رفض.';
    ok = false;
  }

  if (queryId) {
    telegramAnswerCallback_(botToken, queryId, answer, !ok);
  }
  return {
    ok: ok,
    state: state,
    reason: reason,
    approval_id: ap.id,
    answer: answer
  };
}

function denyCallback_(ss, botToken, approvalId, reason, sender, callbackData, queryId) {
  appendAuditLog_(ss, {
    unit: 'approval',
    action: 'approval.callback',
    outcome: 'denied',
    subjects: approvalId ? [approvalId] : [],
    payload: {
      approval_id: approvalId || '',
      reason: reason,
      sender: sender,
      callback: String(callbackData || '').slice(0, 120)
    },
    policy_id: 'POL-004:DELEGATION-DENIED'
  });

  var answer = DENY_ANSWERS[reason] || 'مرفوض.';
  if (queryId) {
    telegramAnswerCallback_(botToken, queryId, answer, true);
  }
  return {
    ok: false,
    state: STATE_DENIED,
    reason: reason,
    approval_id: approvalId || '',
    answer: answer
  };
}

function buildVoteUrl_(ap, voteBaseUrl) {
  var base = String(voteBaseUrl || '').trim();
  if (!base) return '';
  var cleanBase = base.replace(/\/+$/, '');
  var sep = cleanBase.indexOf('?') !== -1 ? '&' : '?';
  return cleanBase + sep + 'approval=' + (ap.id || '') + '&action=' + (ap.action || '') + '&hash=' + (ap.payload_hash || '');
}

function buildApprovalCard_(ap, voteBaseUrl) {
  var red = sanitizeAndRedact_(String(ap.summary || ''));
  var approvalId = String(ap.id || '');
  var text = [
    '🔐 طلب موافقة — ' + (ap.action || '?'),
    red.text || '(بدون ملخص)',
    'المعرّف: ' + approvalId,
    'ينتهي: ' + (ap.expires_at || '?'),
    'اضغط «موافق» أو «رفض». القرار يُسجَّل في pa_approvals ويُسلسل في pa_audit_logs_202611.'
  ].join('\n');

  var voteUrl = buildVoteUrl_(ap, voteBaseUrl);
  if (voteUrl) {
    text += '\n\n🔑 موافقة بمفتاح المرور (Passkey — الأقوى): ' + voteUrl;
  }
  return {
    approval_id: approvalId,
    text: text,
    buttons: [
      { label: '✔ موافق', data: 'appr:' + approvalId + ':grant' },
      { label: '✘ رفض', data: 'appr:' + approvalId + ':deny' }
    ]
  };
}

function approvalFromRow_(row) {
  var padded = [];
  for (var i = 0; i < APPROVAL_COLUMNS.length; i++) {
    padded.push(i < row.length && row[i] !== undefined && row[i] !== null ? String(row[i]) : '');
  }
  var obj = {};
  for (var j = 0; j < APPROVAL_COLUMNS.length; j++) {
    obj[APPROVAL_COLUMNS[j]] = padded[j];
  }
  return obj;
}

function approvalChecksum_(ap) {
  var keys = [
    'id', 'created_at', 'updated_at', 'version', 'action', 'payload_hash',
    'summary', 'status', 'issued_at', 'expires_at', 'consumed_at', 'single_use'
  ];
  var core = {};
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    core[k] = ap[k] !== undefined && ap[k] !== null ? String(ap[k]) : '';
  }
  return computeSha256_(core);
}

function findApprovalRow_(apprSheet, approvalId) {
  var rows = readAllRowsRaw_(apprSheet);
  for (var idx = 1; idx < rows.length; idx++) {
    var r = rows[idx];
    if (r && String(r[0] || '') === approvalId) {
      var padded = [];
      for (var c = 0; c < APPROVAL_COLUMNS.length; c++) {
        padded.push(c < r.length && r[c] !== undefined && r[c] !== null ? String(r[c]) : '');
      }
      return { sheetRow: idx + 1, row: padded };
    }
  }
  return null;
}

function parseIsoUtc_(value) {
  var text = String(value || '').trim();
  if (!text) return null;
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?$/.test(text)) {
    return null;
  }
  var normalized = text.charAt(text.length - 1) === 'Z' ? text : (text + 'Z');
  var ms = Date.parse(normalized);
  return isNaN(ms) ? null : new Date(ms);
}

function isExpired_(expiresAt, now) {
  var exp = parseIsoUtc_(expiresAt);
  if (!exp) return true;
  return now.getTime() >= exp.getTime();
}

// ============================================================================
// 6. Sheet Row Builders, Hash Chain & RAW Persistence
// ============================================================================

function ingestSourceRecord_(srcSheet, spec) {
  var now = utcNowIso_();
  var recId = newUlid_();
  var contentHash = computeSha256_(spec.excerpt);
  var metaJson = canonicalJson_(spec.metadata || {});
  var checksum = computeSha256_({
    id: recId,
    connector: spec.connector,
    external_id: spec.external_id,
    content_hash: contentHash
  });
  var row = [
    recId, now, now, '1', '', '0.9', 'fact', spec.sensitivity,
    'not_granted', 'review', '', '', 'tainted', checksum,
    spec.connector, spec.external_id, spec.uri, spec.captured_at,
    contentHash, spec.excerpt, 'text/plain', '[]', metaJson
  ];
  appendRowRaw_(srcSheet, row);
  return recId;
}

function createCandidateTask_(taskSheet, existingDedupe, spec) {
  var dedupeKey = computeSha256_(spec.dedupe_basis).slice(0, 24);
  if (existingDedupe[dedupeKey]) return null;

  var now = utcNowIso_();
  var taskId = newUlid_();
  var checksum = computeSha256_({ id: taskId, title: spec.title, dedupe_key: dedupeKey });
  var row = [
    taskId, now, now, '1', spec.source_id, '0.7', 'inferred', spec.sensitivity,
    'not_granted', 'keep', '', '', 'tainted', checksum,
    spec.title, spec.description, 'inbox', spec.priority,
    spec.due_at, '', '["proactive"]', dedupeKey
  ];
  appendRowRaw_(taskSheet, row);
  existingDedupe[dedupeKey] = true;
  return taskId;
}

function appendAuditLog_(ss, entry) {
  var ws = ss.getSheetByName(BRIDGE_CONFIG.AUDIT_TAB_DEFAULT);
  var rows = readAllRowsRaw_(ws);
  var prevHash = '';
  if (rows.length >= 2) {
    var lastRow = rows[rows.length - 1];
    prevHash = lastRow.length >= 25 ? String(lastRow[24] || '') : '';
  }

  var now = utcNowIso_();
  var logId = newUlid_();
  var argsHash = computeSha256_(entry.payload || {});
  var subjectsJson = canonicalJson_(entry.subjects || []);
  var body = {
    id: logId,
    at: now,
    unit: entry.unit,
    action: entry.action,
    actor: BRIDGE_CONFIG.AGENT_ACTOR_ID,
    outcome: entry.outcome,
    subjects: entry.subjects || [],
    args_hash: argsHash,
    policy_id: entry.policy_id,
    prev_hash: prevHash
  };
  var selfHash = computeSha256_(body);
  var row = [
    logId, now, now, '1', '', '1', 'fact', 'personal',
    'not_required', 'review', '2028-11-12T00:00:00.000Z', '',
    'clean', selfHash,
    now, entry.unit, entry.action, BRIDGE_CONFIG.AGENT_ACTOR_ID, entry.outcome,
    subjectsJson, argsHash, entry.policy_id, '', prevHash, selfHash
  ];
  var writtenRowNum = appendRowRaw_(ws, row);

  // Evidence before claims: read back the written row and verify self_hash.
  var readBack = ws.getRange(writtenRowNum, 1, 1, 25).getDisplayValues()[0];
  if (!readBack || String(readBack[24] || '') !== selfHash || String(readBack[0] || '') !== logId) {
    throw new Error('Audit log read-back verification failed for ' + logId);
  }
  return selfHash;
}

function upsertSystemState_(ss, stateKey, valueObj) {
  var ws = ss.getSheetByName('pa_system_state');
  var rows = readAllRowsRaw_(ws);
  var now = utcNowIso_();
  var valJson = JSON.stringify(valueObj);
  for (var idx = 1; idx < rows.length; idx++) {
    if (String(rows[idx][14] || '') === stateKey) {
      var existing = rows[idx];
      var nextVer = String((parseInt(existing[3] || '1', 10) || 1) + 1);
      var updatedId = String(existing[0] || newUlid_());
      var updatedChecksum = computeSha256_({
        id: updatedId,
        key: stateKey,
        value: valJson,
        version: nextVer
      });
      var updatedRow = [
        updatedId,
        String(existing[1] || now),
        now,
        nextVer,
        '',
        '1',
        'fact',
        'public',
        'not_required',
        'keep',
        '',
        '',
        'clean',
        updatedChecksum,
        stateKey,
        valJson,
        String(existing[16] || '1.0.0'),
        'apps-script-bridge'
      ];
      writeRowAtRaw_(ws, idx + 1, updatedRow);
      return updatedId;
    }
  }

  var newId = newUlid_();
  var newChecksum = computeSha256_({
    id: newId,
    key: stateKey,
    value: valJson,
    version: '1'
  });
  var newRow = [
    newId, now, now, '1', '', '1', 'fact', 'public',
    'not_required', 'keep', '', '', 'clean', newChecksum,
    stateKey, valJson, '1.0.0', 'apps-script-bridge'
  ];
  appendRowRaw_(ws, newRow);
  return newId;
}

function readAllRowsRaw_(sheet) {
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow <= 0 || lastCol <= 0) return [];
  return sheet.getRange(1, 1, lastRow, lastCol).getDisplayValues();
}

function readColumnSet_(sheet, zeroBasedCol) {
  var rows = readAllRowsRaw_(sheet);
  var out = {};
  for (var r = 1; r < rows.length; r++) {
    var val = rows[r] && rows[r].length > zeroBasedCol ? String(rows[r][zeroBasedCol] || '') : '';
    if (val) out[val] = true;
  }
  return out;
}

function appendRowRaw_(sheet, row) {
  var nextRow = sheet.getLastRow() + 1;
  writeRowAtRaw_(sheet, nextRow, row);
  return nextRow;
}

function writeRowAtRaw_(sheet, rowNumber, row) {
  var clean = [];
  for (var i = 0; i < row.length; i++) {
    clean.push(row[i] === null || row[i] === undefined ? '' : String(row[i]));
  }
  var range = sheet.getRange(rowNumber, 1, 1, clean.length);
  range.setNumberFormat('@');
  range.setValues([clean]);
  SpreadsheetApp.flush();
}

function verifyRequiredTabs_(ss) {
  var present = [];
  var missing = [];
  for (var i = 0; i < BRIDGE_CONFIG.REQUIRED_TABS.length; i++) {
    var name = BRIDGE_CONFIG.REQUIRED_TABS[i];
    if (ss.getSheetByName(name)) {
      present.push(name);
    } else {
      missing.push(name);
    }
  }
  return { ok: missing.length === 0, present: present, missing: missing };
}

// ============================================================================
// 7. Gmail, Calendar & Telegram HTTP Transports (GET/POST + Secret Redaction)
// ============================================================================

function countUnreadMetadata_(query) {
  var count = 0;
  var pageToken = null;
  var pages = 0;

  do {
    var url = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'
      + '?maxResults=' + BRIDGE_CONFIG.GMAIL_PAGE_SIZE
      + '&q=' + encodeURIComponent(query)
      + (pageToken ? '&pageToken=' + encodeURIComponent(pageToken) : '');
    var body = fetchGoogleJson_(url);
    var messages = Array.isArray(body.messages) ? body.messages : [];
    count += messages.length;
    pageToken = body.nextPageToken || null;
    pages += 1;
  } while (pageToken && pages < BRIDGE_CONFIG.MAX_PAGES);

  return { count: count, truncated: Boolean(pageToken) };
}

function fetchUnreadGmailMetadata_(query, maxResults) {
  var listUrl = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'
    + '?maxResults=' + encodeURIComponent(String(maxResults))
    + '&q=' + encodeURIComponent(query);
  var listBody = fetchGoogleJson_(listUrl);
  var stubs = Array.isArray(listBody.messages) ? listBody.messages : [];
  var items = [];

  for (var i = 0; i < stubs.length; i++) {
    var id = String(stubs[i].id || '');
    if (!id) continue;
    var detailUrl = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/'
      + encodeURIComponent(id)
      + '?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date';
    var msg = fetchGoogleJson_(detailUrl);
    var headersList = (msg.payload && Array.isArray(msg.payload.headers)) ? msg.payload.headers : [];
    var headerMap = {};
    for (var h = 0; h < headersList.length; h++) {
      var hdr = headersList[h];
      if (hdr && hdr.name) headerMap[String(hdr.name).toLowerCase()] = String(hdr.value || '');
    }
    items.push({
      id: id,
      threadId: String(msg.threadId || ''),
      from: headerMap['from'] || '',
      subject: headerMap['subject'] || '(بدون عنوان)',
      date: headerMap['date'] || '',
      snippet: String(msg.snippet || '')
    });
  }
  return items;
}

function inspectCalendarWindow_(start, end) {
  var timed = [];
  var allDayCount = 0;
  var pageToken = null;
  var pages = 0;

  do {
    var url = 'https://www.googleapis.com/calendar/v3/calendars/primary/events'
      + '?singleEvents=true&orderBy=startTime'
      + '&maxResults=' + BRIDGE_CONFIG.CALENDAR_PAGE_SIZE
      + '&timeMin=' + encodeURIComponent(start.toISOString())
      + '&timeMax=' + encodeURIComponent(end.toISOString())
      + (pageToken ? '&pageToken=' + encodeURIComponent(pageToken) : '');
    var body = fetchGoogleJson_(url);
    var items = Array.isArray(body.items) ? body.items : [];

    for (var i = 0; i < items.length; i++) {
      var ev = items[i];
      if (!ev || ev.status === 'cancelled') continue;
      if (ev.start && ev.start.date && !ev.start.dateTime) {
        allDayCount += 1;
        continue;
      }
      var s = ev.start && ev.start.dateTime ? new Date(ev.start.dateTime).getTime() : NaN;
      var e = ev.end && ev.end.dateTime ? new Date(ev.end.dateTime).getTime() : NaN;
      if (!isNaN(s) && !isNaN(e) && e > s) {
        timed.push({ start: s, end: e });
      }
    }
    pageToken = body.nextPageToken || null;
    pages += 1;
  } while (pageToken && pages < BRIDGE_CONFIG.MAX_PAGES);

  timed.sort(function (a, b) { return a.start - b.start; });
  var conflicts = 0;
  var maxEndSoFar = -Infinity;
  for (var j = 0; j < timed.length; j++) {
    if (timed[j].start < maxEndSoFar) conflicts += 1;
    if (timed[j].end > maxEndSoFar) maxEndSoFar = timed[j].end;
  }

  return {
    timedCount: timed.length,
    allDayCount: allDayCount,
    conflicts: conflicts,
    truncated: Boolean(pageToken)
  };
}

function fetchCalendarWindowDetails_(start, end) {
  var url = 'https://www.googleapis.com/calendar/v3/calendars/primary/events'
    + '?singleEvents=true&orderBy=startTime'
    + '&maxResults=' + BRIDGE_CONFIG.CALENDAR_PAGE_SIZE
    + '&timeMin=' + encodeURIComponent(start.toISOString())
    + '&timeMax=' + encodeURIComponent(end.toISOString());
  var body = fetchGoogleJson_(url);
  var items = Array.isArray(body.items) ? body.items : [];
  var out = [];

  for (var i = 0; i < items.length; i++) {
    var ev = items[i];
    if (!ev || ev.status === 'cancelled') continue;
    var startIso = (ev.start && (ev.start.dateTime || ev.start.date)) || '';
    var endIso = (ev.end && (ev.end.dateTime || ev.end.date)) || '';
    out.push({
      id: String(ev.id || ''),
      summary: String(ev.summary || '(حدث بدون عنوان)'),
      startIso: String(startIso),
      endIso: String(endIso),
      htmlLink: String(ev.htmlLink || ''),
      attendeesCount: Array.isArray(ev.attendees) ? ev.attendees.length : 0
    });
  }
  return out;
}

function fetchGoogleJson_(url) {
  var lastStatus = 0;
  for (var attempt = 1; attempt <= BRIDGE_CONFIG.FETCH_MAX_ATTEMPTS; attempt++) {
    var resp = UrlFetchApp.fetch(url, {
      method: 'get',
      headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() },
      muteHttpExceptions: true
    });
    lastStatus = resp.getResponseCode();
    if (lastStatus >= 200 && lastStatus < 300) {
      return JSON.parse(resp.getContentText() || '{}');
    }
    var retryable = (lastStatus === 429) || (lastStatus >= 500 && lastStatus < 600);
    if (!retryable || attempt === BRIDGE_CONFIG.FETCH_MAX_ATTEMPTS) {
      break;
    }
    Utilities.sleep(BRIDGE_CONFIG.FETCH_BACKOFF_MS * Math.pow(2, attempt - 1));
  }
  throw new Error('Upstream Google API returned HTTP ' + lastStatus);
}

function telegramSendCard_(botToken, chatId, card) {
  var inlineKeyboard = [];
  for (var i = 0; i < card.buttons.length; i++) {
    inlineKeyboard.push([{
      text: card.buttons[i].label,
      callback_data: card.buttons[i].data
    }]);
  }
  var body = telegramPost_(botToken, 'sendMessage', {
    chat_id: chatId,
    text: card.text,
    reply_markup: { inline_keyboard: inlineKeyboard }
  });
  return String((body.result && body.result.message_id) || '');
}

function telegramAnswerCallback_(botToken, callbackQueryId, text, showAlert) {
  telegramPost_(botToken, 'answerCallbackQuery', {
    callback_query_id: callbackQueryId,
    text: text,
    show_alert: Boolean(showAlert)
  });
}

function telegramFetchUpdates_(botToken, offset) {
  var url = 'https://api.telegram.org/bot' + botToken + '/getUpdates?timeout=0';
  if (offset !== null && offset !== undefined) {
    url += '&offset=' + encodeURIComponent(String(offset));
  }
  var resp = UrlFetchApp.fetch(url, {
    method: 'get',
    muteHttpExceptions: true
  });
  var code = resp.getResponseCode();
  var parsed = {};
  try {
    parsed = JSON.parse(resp.getContentText() || '{}');
  } catch (_e) {
    parsed = {};
  }
  if (code < 200 || code >= 300 || !parsed.ok) {
    throw new Error('Telegram getUpdates failed with HTTP ' + code);
  }
  return Array.isArray(parsed.result) ? parsed.result : [];
}

function telegramPost_(botToken, method, payload) {
  var url = 'https://api.telegram.org/bot' + botToken + '/' + method;
  var resp = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  var code = resp.getResponseCode();
  var parsed = {};
  try {
    parsed = JSON.parse(resp.getContentText() || '{}');
  } catch (_e) {
    parsed = {};
  }
  if (code < 200 || code >= 300 || !parsed.ok) {
    throw new Error('Telegram ' + method + ' failed with HTTP ' + code);
  }
  return parsed;
}

function loadBotState_(props) {
  var raw = String(props.getProperty(BRIDGE_CONFIG.BOT_STATE_PROPERTY) || '').trim();
  if (!raw) return { sent: [], offset: null };
  try {
    var parsed = JSON.parse(raw);
    return {
      sent: Array.isArray(parsed.sent) ? parsed.sent : [],
      offset: (parsed.offset === null || parsed.offset === undefined) ? null : Number(parsed.offset)
    };
  } catch (_err) {
    return { sent: [], offset: null };
  }
}

function saveBotState_(props, sentIds, offset) {
  props.setProperty(BRIDGE_CONFIG.BOT_STATE_PROPERTY, JSON.stringify({
    sent: sentIds,
    offset: offset === undefined ? null : offset
  }));
}

// ============================================================================
// 8. Pure Crypto, ssGateway Redaction & Formatting Primitives
// ============================================================================

function sanitizeAndRedact_(text) {
  if (!text) return { text: '', sensitivity: 'public' };
  var redacted = String(text);
  var foundSensitive = false;
  for (var i = 0; i < REDACT_PATTERNS.length; i++) {
    REDACT_PATTERNS[i].lastIndex = 0;
    if (REDACT_PATTERNS[i].test(redacted)) {
      foundSensitive = true;
      REDACT_PATTERNS[i].lastIndex = 0;
      redacted = redacted.replace(REDACT_PATTERNS[i], '[REDACTED_SENSITIVE]');
    }
  }
  var clean = redacted.replace(/\s+/g, ' ').trim().slice(0, BRIDGE_CONFIG.EXCERPT_MAX_CHARS);
  return {
    text: clean,
    sensitivity: foundSensitive ? 'confidential' : 'personal'
  };
}

/**
 * Deterministic canonical JSON matching Python's:
 *   json.dumps(data, sort_keys=True, ensure_ascii=False)
 */
function canonicalJson_(value) {
  if (value === null || value === undefined) return 'null';
  var t = typeof value;
  if (t === 'boolean') return value ? 'true' : 'false';
  if (t === 'number') return isFinite(value) ? String(value) : 'null';
  if (t === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) {
    var parts = [];
    for (var i = 0; i < value.length; i++) {
      parts.push(canonicalJson_(value[i]));
    }
    return '[' + parts.join(', ') + ']';
  }
  if (t === 'object') {
    var keys = Object.keys(value).sort();
    var entries = [];
    for (var k = 0; k < keys.length; k++) {
      var key = keys[k];
      entries.push(JSON.stringify(key) + ': ' + canonicalJson_(value[key]));
    }
    return '{' + entries.join(', ') + '}';
  }
  return JSON.stringify(String(value));
}

function computeSha256_(data) {
  var canonical = canonicalJson_(data);
  var digest = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    canonical,
    Utilities.Charset.UTF_8
  );
  var hex = '';
  for (var i = 0; i < digest.length; i++) {
    var unsigned = (digest[i] + 256) % 256;
    hex += (unsigned < 16 ? '0' : '') + unsigned.toString(16);
  }
  return hex;
}

function newUlid_() {
  var alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
  var tsMs = Date.now();
  var tsPart = '';
  var rem = tsMs;
  for (var i = 0; i < 10; i++) {
    tsPart = alphabet.charAt(rem % 32) + tsPart;
    rem = Math.floor(rem / 32);
  }
  var seed = Utilities.getUuid() + ':' + tsMs + ':' + Math.random();
  var hashHex = computeSha256_(seed);
  var randPart = '';
  for (var j = 0; j < 16; j++) {
    var byteVal = parseInt(hashHex.substr(j * 2, 2), 16);
    randPart += alphabet.charAt(byteVal % 32);
  }
  return tsPart + randPart;
}

function parseAllowedSenders_(raw) {
  var parts = String(raw || '').split(',');
  var out = [];
  for (var i = 0; i < parts.length; i++) {
    var trimmed = parts[i].trim();
    if (trimmed) out.push(trimmed);
  }
  return out;
}

function utcNowIso_() {
  return formatIsoMs_(new Date());
}

function formatIsoMs_(dateObj) {
  var iso = dateObj.toISOString();
  if (/\.\d{3}Z$/.test(iso)) return iso;
  return iso.replace(/Z$/, '.000Z');
}

function sanitizeError_(err) {
  var raw = (err && err.message) ? String(err.message) : String(err);
  return raw
    .replace(/bot\d+:[A-Za-z0-9_-]+/g, 'bot[REDACTED]')
    .replace(/Bearer\s+\S+/gi, 'Bearer [REDACTED]')
    .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+/g, '[email]')
    .slice(0, 160);
}
