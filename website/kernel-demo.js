/*!
 * AI-2027 — browser reference port of the deterministic evidence-first loop.
 *
 * HONESTY CONTRACT (do not remove):
 *   This is a *port* of the rules in `projects/agi-system/core`, written in
 *   dependency-free JavaScript so it can run on static GitHub Pages. It is NOT
 *   the Python kernel and it is NOT a simulation dressed up as one: the policy
 *   wall, the executor and the independent verifier are real, separate stages
 *   operating on a real (in-memory, per-run) world, with real SHA-256 evidence.
 *   The published numbers (p50 7.5 ms, 86/86 tests) belong to the Python
 *   kernel and are measured by `live-agent/bench.py`; browser timings shown
 *   here are NOT benchmarks and must never be quoted as such.
 *
 * Zero dependencies: no imports, no network, no CDN, no WASM.
 * Works in a browser (window.KernelDemo) and in Node (module.exports).
 */
(function (global) {
  'use strict';

  /* ------------------------------------------------------------------ *
   * SHA-256 — compact pure-JS implementation (FIPS 180-4), no Web Crypto
   * dependency so the demo also works on plain HTTP / older browsers.
   * ------------------------------------------------------------------ */
  var K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ];
  var H0 = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];

  function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }

  function utf8(str) {
    if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(str);
    // Fallback: manual UTF-8 for very old runtimes.
    var out = [], i, c;
    for (i = 0; i < str.length; i++) {
      c = str.codePointAt(i);
      if (c > 0xffff) i++;
      if (c < 0x80) out.push(c);
      else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
      else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
      else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    }
    return new Uint8Array(out);
  }

  function sha256Hex(bytes) {
    var msg, i, off, w = new Uint32Array(64), h = H0.slice(), a, b, c, d, e, f, g, hh, s0, s1, S0, S1, ch, maj, t1, t2;
    var len = bytes.length, bitLen = len * 8;
    var total = (((len + 1 + 8) + 63) >> 6) << 6;
    msg = new Uint8Array(total);
    msg.set(bytes);
    msg[len] = 0x80;
    var dv = new DataView(msg.buffer);
    dv.setUint32(total - 8, Math.floor(bitLen / 0x100000000));
    dv.setUint32(total - 4, bitLen >>> 0);
    for (off = 0; off < total; off += 64) {
      for (i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
      for (i = 16; i < 64; i++) {
        s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      a = h[0]; b = h[1]; c = h[2]; d = h[3]; e = h[4]; f = h[5]; g = h[6]; hh = h[7];
      for (i = 0; i < 64; i++) {
        S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        ch = (e & f) ^ (~e & g);
        t1 = (hh + S1 + ch + K[i] + w[i]) >>> 0;
        S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        maj = (a & b) ^ (a & c) ^ (b & c);
        t2 = (S0 + maj) >>> 0;
        hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      h[0] = (h[0] + a) >>> 0; h[1] = (h[1] + b) >>> 0; h[2] = (h[2] + c) >>> 0; h[3] = (h[3] + d) >>> 0;
      h[4] = (h[4] + e) >>> 0; h[5] = (h[5] + f) >>> 0; h[6] = (h[6] + g) >>> 0; h[7] = (h[7] + hh) >>> 0;
    }
    return h.map(function (x) { return ('00000000' + x.toString(16)).slice(-8); }).join('');
  }

  function sha256Text(str) { return sha256Hex(utf8(str)); }
  function bytesEq(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }
  function concatBytes(a, b) {
    var out = new Uint8Array(a.length + b.length);
    out.set(a); out.set(b, a.length);
    return out;
  }
  function now() {
    return (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
  }

  /* ------------------------------------------------------------------ *
   * Kernel rules (ported 1:1 from core/policy.py + core/verifier.py)
   * ------------------------------------------------------------------ */
  var ALLOWLIST = Object.freeze([
    'filesystem.read', 'filesystem.write', 'filesystem.mkdir', 'filesystem.list', 'filesystem.delete'
  ]);
  var MAX_WRITE_BYTES = 8 * 1024 * 1024;   // policy.py guard rail
  var MAX_PARAM_BYTES = 5000;              // live-agent/server.py input guard

  var GOALS = Object.freeze({
    create_file: { capability: 'filesystem.write', append: false },
    append_file: { capability: 'filesystem.write', append: true },
    read_file:   { capability: 'filesystem.read' },
    ensure_dir:  { capability: 'filesystem.mkdir' },
    list_dir:    { capability: 'filesystem.list', allowRoot: true },
    delete_file: { capability: 'filesystem.delete' }
  });
  var STAGES = ['Goal', 'Planner', 'Policy', 'Executor', 'Verifier', 'Evidence', 'Ledger'];

  function contain(rawPath, allowRoot) {
    if (typeof rawPath !== 'string') return { error: "param 'path' must be a string" };
    if (rawPath.startsWith('/') || /^[A-Za-z]:[\\/]/.test(rawPath) || rawPath.startsWith('\\\\')) {
      return { error: "absolute path is outside the sandbox: '" + rawPath + "'" };
    }
    var parts = rawPath.split(/[\\/]+/);
    if (parts.indexOf('..') !== -1) {
      return { error: "path escapes the sandbox: '" + rawPath + "' contains a '..' segment" };
    }
    var clean = parts.filter(function (p) { return p && p !== '.'; }).join('/');
    if (!clean) {
      if (allowRoot) return { path: '' };
      return { error: "refusing action on the sandbox root itself" };
    }
    return { path: clean };
  }

  function policyEvaluate(action, opts) {
    opts = opts || {};
    var allowlist = opts.allowlist || ALLOWLIST;
    var cap = action.capability;
    if (allowlist.indexOf(cap) === -1) {
      return { allowed: false, reason: "capability '" + cap + "' is not in the allowlist", risk: 1.0 };
    }
    var raw = action.params.path;
    var c = contain(raw, !!action.allowRoot);
    if (c.error) return { allowed: false, reason: c.error, risk: 0.95 };
    if (c.path === '' && (cap === 'filesystem.write' || cap === 'filesystem.delete')) {
      return { allowed: false, reason: 'refusing ' + cap + ' on the sandbox root itself', risk: 1.0 };
    }
    var content = action.params.content;
    if (content !== undefined && content !== null) {
      var size = utf8(String(content)).length;
      if (size > MAX_PARAM_BYTES) {
        return { allowed: false, reason: 'payload of ' + size + ' bytes exceeds the 5000-byte input guard', risk: 0.7 };
      }
      if (size > MAX_WRITE_BYTES) {
        return { allowed: false, reason: 'payload of ' + size + ' bytes exceeds max_write_bytes', risk: 0.7 };
      }
    }
    return { allowed: true, reason: 'capability allowed; path contained in sandbox', risk: 0.2, path: c.path };
  }

  function createWorld() {
    return { fs: Object.create(null), ledger: [], seq: 0, runs: 0 };
  }

  function appendLedger(world, record) {
    var prev = world.ledger.length ? world.ledger[world.ledger.length - 1].hash : new Array(65).join('0');
    var entry = {
      seq: world.ledger.length + 1,
      run_id: record.run_id,
      status: record.status,
      prev_hash: prev
    };
    entry.hash = sha256Text(JSON.stringify(entry));
    world.ledger.push(entry);
    return entry;
  }

  function verifyLedger(world) {
    var prev = new Array(65).join('0');
    for (var i = 0; i < world.ledger.length; i++) {
      var e = world.ledger[i];
      var body = { seq: e.seq, run_id: e.run_id, status: e.status, prev_hash: e.prev_hash };
      if (e.seq !== i + 1 || e.prev_hash !== prev || sha256Text(JSON.stringify(body)) !== e.hash) return false;
      prev = e.hash;
    }
    return true;
  }

  /**
   * Run one goal through the full loop. Returns a DecisionRecord (plain object).
   * Throws on an unknown goal_type — matching live-agent/server.py behaviour.
   */
  function runGoal(world, goalType, params, opts) {
    params = params || {};
    opts = opts || {};
    var t0 = now();
    if (!Object.prototype.hasOwnProperty.call(GOALS, goalType)) {
      throw new Error('unknown goal_type: ' + goalType);
    }
    var spec = GOALS[goalType];
    var goal = { goal_type: goalType, params: JSON.parse(JSON.stringify(params)) };
    var action = {
      action_id: 'act_' + sha256Text(goalType + ':' + JSON.stringify(params)).slice(0, 12),
      capability: spec.capability,
      params: JSON.parse(JSON.stringify(params)),
      allowRoot: !!spec.allowRoot,
      append: !!spec.append
    };

    var stages = STAGES.map(function (s) { return { stage: s, state: 'pending', detail: '' }; });
    function mark(name, state, detail) {
      for (var i = 0; i < stages.length; i++) if (stages[i].stage === name) { stages[i].state = state; stages[i].detail = detail; return i; }
      return -1;
    }
    function skipFrom(name) {
      var from = mark(name, 'skipped', 'not reached');
      for (var i = from + 1; i < stages.length; i++) stages[i].state = 'skipped';
    }

    mark('Goal', 'ok', goalType + ' ' + JSON.stringify(params));
    world.seq += 1; world.runs += 1;
    var runId = 'run_' + String(world.seq).padStart(4, '0') + '_' + sha256Text(JSON.stringify(goal)).slice(0, 8);

    // ---- Planner: deterministic rule-based, NO LLM ------------------------
    var plan = {
      plan_id: 'plan_' + sha256Text(runId).slice(0, 12),
      steps: [action],
      risk_prior: spec.capability === 'filesystem.delete' ? 0.55 : 0.20,
      planner: 'deterministic-rule-based (no model call)'
    };
    mark('Planner', 'ok', '1 step · capability=' + action.capability + ' · risk=' + plan.risk_prior);

    // ---- Policy: the only component allowed to authorise -----------------
    var decision = policyEvaluate(action, opts);
    mark('Policy', decision.allowed ? 'ok' : 'denied', decision.reason);
    if (!decision.allowed) {
      skipFrom('Executor');
      var denied = {
        run_id: runId, status: 'DENIED', goal: goal, plan: plan,
        policy: decision, observation: null, evidence: [],
        denial_reasons: [decision.reason],
        stages: stages, elapsed_ms: +(now() - t0).toFixed(3),
        implementation: 'browser-reference-port',
        note: 'Browser timing is not a benchmark. Canonical kernel: projects/agi-system.'
      };
      denied.ledger_entry = appendLedger(world, denied);
      denied.ledger_valid = verifyLedger(world);
      stages[stages.length - 1].state = 'ok';
      stages[stages.length - 1].detail = 'DENIED event filed (append-only, hash-chained)';
      return denied;
    }
    action.params.path = decision.path;

    // ---- Executor: touches the world, reports an Observation -------------
    var observation = { ok: false, data: {}, error: null };
    var p = decision.path, entry = world.fs[p], bytes;
    try {
      switch (action.capability) {
        case 'filesystem.write':
          bytes = utf8(String(params.content === undefined ? '' : params.content));
          if (action.append && entry && entry.type === 'file') bytes = concatBytes(entry.bytes, bytes);
          world.fs[p] = { type: 'file', bytes: bytes };
          observation = { ok: true, data: { sha256: sha256Hex(bytes), bytes: bytes.length }, error: null };
          break;
        case 'filesystem.read':
          if (!entry || entry.type !== 'file') {
            observation = { ok: false, data: {}, error: 'file not found: ' + p };
          } else {
            observation = { ok: true, data: { sha256: sha256Hex(entry.bytes), bytes: entry.bytes.length }, error: null };
          }
          break;
        case 'filesystem.mkdir':
          world.fs[p] = { type: 'dir' };
          observation = { ok: true, data: { path: p }, error: null };
          break;
        case 'filesystem.delete':
          delete world.fs[p];
          observation = { ok: true, data: { path: p }, error: null };
          break;
        case 'filesystem.list':
          var prefix = p ? p + '/' : '';
          var names = Object.keys(world.fs).filter(function (k) { return k === p || k.indexOf(prefix) === 0; });
          observation = { ok: true, data: { entries: names.sort() }, error: null };
          break;
        default:
          observation = { ok: false, data: {}, error: 'unsupported capability' };
      }
    } catch (err) {
      observation = { ok: false, data: {}, error: String(err) };
    }
    mark('Executor', observation.ok ? 'ok' : 'failed', observation.ok
      ? 'world mutated · sha256=' + (observation.data.sha256 || 'n/a')
      : (observation.error || 'executor reported failure'));

    // ---- Verifier: INDEPENDENT re-read of the world ----------------------
    var checks = { claimed_ok: observation.ok };
    var worldEntry = world.fs[p];
    var details = {
      capability: action.capability,
      path: p,
      executor_error: observation.error,
      executor_reported_sha256: observation.data.sha256 || null,
      verifier_recomputed_sha256: null
    };
    switch (action.capability) {
      case 'filesystem.write':
        var expected = utf8(String(params.content === undefined ? '' : params.content));
        checks.file_exists = !!worldEntry && worldEntry.type === 'file';
        checks.correct_path = p === contain(String(params.path), false).path;
        if (!checks.file_exists) {
          checks.correct_content = false; checks.hash_matches = false;
        } else {
          var actual = worldEntry.bytes;
          details.verifier_recomputed_sha256 = sha256Hex(actual);
          checks.correct_content = action.append
            ? endsWithBytes(actual, expected)
            : bytesEq(actual, expected);
          checks.hash_matches = action.append
            ? details.verifier_recomputed_sha256 === observation.data.sha256
            : sha256Hex(actual) === sha256Hex(expected);
          checks.observation_matches_world = observation.data.sha256 === details.verifier_recomputed_sha256;
        }
        break;
      case 'filesystem.read':
        checks.file_exists = !!worldEntry && worldEntry.type === 'file';
        checks.correct_path = p === contain(String(params.path), false).path;
        if (checks.file_exists) {
          details.verifier_recomputed_sha256 = sha256Hex(worldEntry.bytes);
          checks.observation_matches_disk = observation.data.sha256 === details.verifier_recomputed_sha256;
        } else {
          checks.observation_matches_disk = false;
        }
        break;
      case 'filesystem.mkdir':
        checks.correct_path = p === contain(String(params.path), false).path;
        checks.dir_exists = !!worldEntry && worldEntry.type === 'dir';
        break;
      case 'filesystem.delete':
        checks.correct_path = p === contain(String(params.path), false).path;
        checks.path_absent = !worldEntry;
        break;
      case 'filesystem.list':
        checks.correct_path = true;
        checks.listing_matches_world = Array.isArray(observation.data.entries) &&
          observation.data.entries.every(function (k) { return Object.prototype.hasOwnProperty.call(world.fs, k); });
        break;
      default:
        checks.unsupported_capability = false;
    }
    var allPassed = Object.keys(checks).every(function (k) { return checks[k] === true; });
    mark('Verifier', allPassed ? 'ok' : 'failed', allPassed
      ? Object.keys(checks).length + ' independent checks passed'
      : 'one or more checks failed: ' + Object.keys(checks).filter(function (k) { return !checks[k]; }).join(', '));

    var evidence = [{
      action_id: action.action_id,
      capability: action.capability,
      checks: checks,
      details: details,
      verifier: 'independent re-read + SHA-256 recomputation (never trusts the executor report)'
    }];
    mark('Evidence', 'ok', 'filed with ' + Object.keys(checks).length + ' checks');

    var status = allPassed ? 'VERIFIED' : 'FAILED';
    var record = {
      run_id: runId, status: status, goal: goal, plan: plan,
      policy: decision, observation: observation, evidence: evidence,
      denial_reasons: [], stages: stages,
      elapsed_ms: +(now() - t0).toFixed(3),
      implementation: 'browser-reference-port',
      note: 'Browser timing is not a benchmark. Canonical kernel: projects/agi-system.'
    };
    record.ledger_entry = appendLedger(world, record);
    mark('Ledger', 'ok', status + ' event · chain seq #' + record.ledger_entry.seq);
    record.ledger_valid = verifyLedger(world);
    return record;
  }

  function endsWithBytes(a, b) {
    if (b.length > a.length) return false;
    for (var i = 1; i <= b.length; i++) if (a[a.length - i] !== b[b.length - i]) return false;
    return true;
  }

  var api = {
    sha256Hex: sha256Hex, sha256Text: sha256Text, utf8: utf8,
    ALLOWLIST: ALLOWLIST, GOALS: GOALS, STAGES: STAGES,
    contain: contain, policyEvaluate: policyEvaluate,
    createWorld: createWorld, runGoal: runGoal, verifyLedger: verifyLedger
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  global.KernelDemo = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
