/**
 * Read-only P1 connectivity probe. No Sheets, no actions and no persistent state.
 * Only IDs and calendar intervals are processed, as untrusted data, in memory.
 * The user must authorize the manifest scopes and create a trigger explicitly.
 */
const WATCHER_MAX_ITEMS = 1000;
const WATCHER_MAX_PAGES = 10;
const WATCHER_MAX_CONFLICTS = 20000;
const WATCHER_MAX_RESPONSE_CHARS = 1024 * 1024;
const WATCHER_DAY_MS = 24 * 60 * 60 * 1000;

/** Run manually first, or with a user-created time-driven trigger. */
function watcherPoll() {
  try {
    // This protects only executions of this particular Apps Script project.
    const lock = LockService.getScriptLock();
    if (!lock.tryLock(0)) {
      console.log(JSON.stringify({status: 'skipped', reason: 'overlap', writes: 0}));
      return {status: 'skipped', reason: 'overlap', writes: 0};
    }
    let result;
    try {
      result = watcherSnapshot_(new Date());
    } finally {
      lock.releaseLock();
    }
    console.log(JSON.stringify(result));
    return result;
  } catch (ignored) {
    // Never propagate provider responses, request URLs, or tokens to logs.
    console.log(JSON.stringify({status: 'failed', writes: 0}));
    throw new Error('Read-only watcher poll failed; see setup and quota checklist.');
  }
}

/** Fetch one Google API page with bounded, GET-only retries. */
function watcherGet_(baseUrl, params) {
  const query = Object.keys(params).map(function (key) {
    return encodeURIComponent(key) + '=' + encodeURIComponent(params[key]);
  }).join('&');
  // baseUrl is always a literal controlled by the code, never external content.
  const url = baseUrl + '?' + query;
  for (let attempt = 0; attempt < 3; attempt++) {
    let response;
    try {
      response = UrlFetchApp.fetch(url, {
        method: 'get',
        headers: {Authorization: 'Bearer ' + ScriptApp.getOAuthToken()},
        muteHttpExceptions: true,
        followRedirects: false,
      });
    } catch (ignored) {
      if (attempt === 2) throw new Error('read failed');
      Utilities.sleep(250 * Math.pow(2, attempt));
      continue;
    }
    const status = response.getResponseCode();
    if (status === 429 || status >= 500 && status <= 599) {
      if (attempt === 2) throw new Error('read failed');
      Utilities.sleep(250 * Math.pow(2, attempt));
      continue;
    }
    if (status !== 200) throw new Error('read denied');
    const text = response.getContentText();
    if (text.length > WATCHER_MAX_RESPONSE_CHARS) throw new Error('response too large');
    let payload;
    try {
      payload = JSON.parse(text);
    } catch (ignored) {
      throw new Error('invalid response');
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      throw new Error('invalid response');
    }
    return payload;
  }
  throw new Error('read failed');
}

/** Read every page within limits; reject incomplete snapshots. */
function watcherPages_(baseUrl, params, itemKey) {
  const rows = [];
  const visited = new Set();
  let pageToken;
  for (let pageNumber = 0; pageNumber < WATCHER_MAX_PAGES; pageNumber++) {
    const request = Object.assign({}, params);
    if (pageToken) request.pageToken = pageToken;
    const page = watcherGet_(baseUrl, request);
    const items = page[itemKey] === undefined ? [] : page[itemKey];
    if (!Array.isArray(items) || items.some(function (item) {
      return !item || typeof item !== 'object' || Array.isArray(item);
    })) throw new Error('invalid page');
    rows.push.apply(rows, items);
    if (rows.length > WATCHER_MAX_ITEMS) throw new Error('item limit');
    const next = page.nextPageToken;
    if (next === undefined || next === null || next === '') return rows;
    if (typeof next !== 'string' || visited.has(next)) throw new Error('invalid pagination');
    visited.add(next);
    pageToken = next;
  }
  throw new Error('page limit');
}

/** Validate an RFC 3339 timestamp instead of letting Date.parse normalize bad dates. */
function watcherTime_(value) {
  const match = typeof value === 'string' && value.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/
  );
  if (!match) throw new Error('invalid event time');
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const zone = match[7];
  if (year === 0 || month < 1 || month > 12 || day < 1 || day > days[month - 1] ||
      Number(match[4]) > 23 || Number(match[5]) > 59 || Number(match[6]) > 59 ||
      zone === '-00:00' ||
      (zone !== 'Z' && (Number(zone.slice(1, 3)) > 23 || Number(zone.slice(4, 6)) > 59))) {
    throw new Error('invalid event time');
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) throw new Error('invalid event time');
  return parsed;
}

/** Compute an aggregate snapshot. Results are never used as instructions. */
function watcherSnapshot_(now) {
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) throw new Error('invalid clock');
  const gmail = watcherPages_('https://gmail.googleapis.com/gmail/v1/users/me/messages', {
    q: 'in:inbox newer_than:7d', maxResults: 100, fields: 'messages(id),nextPageToken',
  }, 'messages');
  const calendar = watcherPages_(
    'https://www.googleapis.com/calendar/v3/calendars/primary/events', {
      timeMin: now.toISOString(),
      timeMax: new Date(now.getTime() + 7 * WATCHER_DAY_MS).toISOString(),
      singleEvents: 'true', maxResults: 250,
      fields: 'items(id,start,end,status,transparency),nextPageToken',
    }, 'items'
  );
  const sources = new Set();
  const intervals = new Map();
  let allDayExcluded = 0;
  gmail.forEach(function (item) {
    if (typeof item.id !== 'string' || !item.id) throw new Error('invalid id');
    sources.add('gmail:' + item.id);
  });
  calendar.forEach(function (item) {
    if (item.status === 'cancelled') return;
    if (typeof item.id !== 'string' || !item.id) throw new Error('invalid id');
    const id = 'calendar:' + item.id;
    if (sources.has(id)) throw new Error('duplicate calendar event');
    sources.add(id);
    if (item.transparency === 'transparent') return;
    if (!item.start || !item.end) throw new Error('invalid interval');
    if (typeof item.start.date === 'string' && typeof item.end.date === 'string') {
      // Deliberately excluded until zone/DST handling has independent verification.
      allDayExcluded++;
      return;
    }
    const start = watcherTime_(item.start.dateTime);
    const end = watcherTime_(item.end.dateTime);
    if (end <= start) throw new Error('invalid interval');
    intervals.set(id, {start: start, end: end});
  });
  const events = Array.from(intervals.values()).sort(function (a, b) { return a.start - b.start; });
  let conflicts = 0;
  for (let i = 0; i < events.length; i++) {
    for (let j = i + 1; j < events.length && events[j].start < events[i].end; j++) {
      conflicts++;
      if (conflicts > WATCHER_MAX_CONFLICTS) throw new Error('conflict limit');
    }
  }
  return {
    status: 'ok', baseline: true, sources: sources.size, calendar_conflicts: conflicts,
    all_day_excluded: allDayExcluded, writes: 0,
  };
}
