/* AI-2027 Unified Command HUD — browser side.
 * Zero dependencies, zero external requests, RELATIVE api paths only.
 * No framework, no CDN, no build step. */
'use strict';

var lang = 'en', state = null, filter = 'ALL', pulseTimer = null, busyRT = false;
var L = {
  en: {events:'events', noEvents:'No events yet — press “Run goal”.',
       pressRT:'press Red-Team ×100', running:'running 100 attacks…',
       denied:'DENIED', contained:'CONTAINED (literal)', breaches:'BREACHES',
       controls:'controls VERIFIED', met:'expectations met', p50:'p50 / attack',
       auditReady:'read-only audit', computing:'probes executing…',
       all:'ALL', proven:'PROVEN', declared:'DECLARED', simulated:'SIMULATED',
       failed:'FAILED', notver:'NOT VERIFIED', high:'HIGH-severity findings',
       refresh:'audit refresh started — 5 real probes running',
       frozen:'FREEZE ON', active:'dispatch: ACTIVE', freezeOn:'dispatch: FROZEN',
       killOn:'Kill switch ENGAGED — HUD dispatch is frozen (verification still works).',
       killOff:'Kill switch RELEASED — dispatch active.',
       rtDone:'Red-team finished', rtFail:'Red-team gate FAILED',
       noAudit:'audit still computing', ms:'ms'},
  ar: {events:'حدثًا', noEvents:'لا أحداث بعد — اضغط «شغّل الهدف».',
       pressRT:'اضغط هجمات ×100', running:'جاري تنفيذ ١٠٠ هجوم…',
       denied:'مرفوض', contained:'محتوى كاسم ملف', breaches:'اختراقات',
       controls:'ضوابط مُتحقَّقة', met:'توقعات محققة', p50:'p50 / هجوم',
       auditReady:'تدقيق قراءة فقط', computing:'الأوامر قيد التنفيذ…',
       all:'الكل', proven:'مُثبت', declared:'مُعلَن', simulated:'محاكاة',
       failed:'فاشل', notver:'غير مُثبت', high:'اكتشافات عالية الخطورة',
       refresh:'بدأ تحديث التدقيق — ٥ أوامر حقيقية قيد التنفيذ',
       frozen:'التجميد مفعّل', active:'الإرسال: نشط', freezeOn:'الإرسال: مجمّد',
       killOn:'مفتاح الإيقاف مفعّل — إرسال اللوحة مجمّد (التحقق ما زال يعمل).',
       killOff:'أُطلق مفتاح الإيقاف — الإرسال نشط.',
       rtDone:'انتهت الهجمات', rtFail:'فشلت بوابة الهجمات',
       noAudit:'التدقيق قيد الحساب', ms:'ms'}
};
function t(k){ return L[lang][k]; }
function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }

function toggleLang(){
  lang = lang === 'en' ? 'ar' : 'en';
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr';
  document.getElementById('langBtn').textContent = lang === 'ar' ? 'English' : 'العربية';
  document.querySelectorAll('[data-en]').forEach(function(el){
    el.innerHTML = lang === 'ar' ? el.dataset.ar : el.dataset.en;
  });
  if (state) render();
}

async function api(path, body){
  var opts = body === undefined ? {} : {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
  };
  var r = await fetch(path, opts);
  if (!r.ok) throw new Error(path + ' → HTTP ' + r.status);
  return r.json();
}
function post(path, body){ return api(path, body || {}); }

function fmtTime(ts){
  if (!ts) return '—';
  var d = new Date(ts * 1000);
  return d.toISOString().slice(11, 23);
}

function drawSpark(tm){
  var svg = document.getElementById('spark');
  var s = (tm && tm.series) || [];
  if (!s.length){
    svg.innerHTML = '<line x1="0" y1="60" x2="600" y2="60" stroke="#1b2a40" stroke-dasharray="4 6"/>' +
      '<text x="300" y="66" fill="#8fa1bd" font-size="12" text-anchor="middle" font-family="monospace">' +
      (lang === 'ar' ? 'بانتظار تشغيل…' : 'awaiting runs…') + '</text>';
    return;
  }
  var max = Math.max.apply(null, s.concat([1]));
  var min = Math.min.apply(null, s.concat([0]));
  var span = Math.max(0.001, max - min), W = 600, H = 120, pad = 8;
  var pts = s.map(function(v, i){
    var x = s.length === 1 ? W : (i / (s.length - 1)) * W;
    var y = H - pad - ((v - min) / span) * (H - 2 * pad);
    return x.toFixed(1) + ',' + y.toFixed(1);
  });
  var p50y = H - pad - (((tm.p50_ms || 0) - min) / span) * (H - 2 * pad);
  svg.innerHTML =
    '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0%" stop-color="#00e5a0" stop-opacity=".35"/>' +
    '<stop offset="100%" stop-color="#00e5a0" stop-opacity="0"/></linearGradient></defs>' +
    [0.25,0.5,0.75].map(function(f){ return '<line x1="0" y1="'+(H*f)+'" x2="600" y2="'+(H*f)+
      '" stroke="#1b2a40" stroke-dasharray="3 6"/>'; }).join('') +
    '<line x1="0" y1="'+p50y.toFixed(1)+'" x2="600" y2="'+p50y.toFixed(1)+
      '" stroke="#38bdf8" stroke-opacity=".55" stroke-dasharray="6 5"/>' +
    '<polygon points="0,'+H+' '+pts.join(' ')+' '+W+','+H+'" fill="url(#g)"/>' +
    '<polyline points="'+pts.join(' ')+'" fill="none" stroke="#00e5a0" stroke-width="2" ' +
      'stroke-linejoin="round" stroke-linecap="round"/>' +
    '<circle cx="'+pts[pts.length-1].split(',')[0]+'" cy="'+pts[pts.length-1].split(',')[1]+
      '" r="3.5" fill="#00e5a0"/>';
}

function renderKpis(){
  var c = state.counters || {}, tm = state.timings || {};
  document.getElementById('kVerified').textContent = c.VERIFIED || 0;
  document.getElementById('kDenied').textContent = c.DENIED || 0;
  document.getElementById('kFailed').textContent = (c.FAILED || 0) + (c.ERROR || 0);
  document.getElementById('kFrozen').textContent = c.FROZEN || 0;
  document.getElementById('kP50').textContent = tm.p50_ms != null ? tm.p50_ms + ' ms' : '—';
  document.getElementById('kP50Sub').textContent = 'n=' + (tm.n || 0) + ' · p95 ' + (tm.p95_ms != null ? tm.p95_ms + ' ms' : '—');
  var a = state.audit;
  if (a){
    document.getElementById('kProjects').textContent = a.summary.projects;
    document.getElementById('kProjectsSub').textContent =
      'PROVEN ' + (a.summary.status_counts.PROVEN || 0) + ' · DECLARED ' + (a.summary.status_counts.DECLARED || 0) +
      ' · HIGH ' + a.summary.critical_high;
  }
  document.getElementById('lgP50').textContent = tm.p50_ms != null ? tm.p50_ms + ' ms' : '—';
  document.getElementById('lgP95').textContent = tm.p95_ms != null ? tm.p95_ms + ' ms' : '—';
  document.getElementById('lgMax').textContent = tm.max_ms != null ? tm.max_ms + ' ms' : '—';
  document.getElementById('lgSpread').textContent = tm.spread_pct != null ? tm.spread_pct + '%' : '—';
  document.getElementById('lgN').textContent = tm.n || 0;
  drawSpark(tm);
}

function renderLog(){
  var body = document.getElementById('logBody');
  var rows = state.history || [];
  document.getElementById('logCount').textContent = rows.length + ' ' + t('events');
  if (!rows.length){
    body.innerHTML = '<tr><td colspan="5" class="mono">' + t('noEvents') + '</td></tr>';
    return;
  }
  body.innerHTML = rows.slice(0, 30).map(function(r){
    var st = r.status || 'ERROR';
    var path = (r.params && r.params.path) || (r.goal_type || '');
    var ev = r.sha256 ? '<span class="hash">sha256 ' + esc(r.sha256.sha256.slice(0, 20)) + '…</span> '
                      : (r.evidence_checks && Object.keys(r.evidence_checks).length
                         ? '<span class="hash">' + Object.keys(r.evidence_checks).length + ' checks</span> ' : '');
    var why = esc(r.reason || '—').slice(0, 150);
    return '<tr><td class="mono">' + fmtTime(r.ts) + '</td>' +
      '<td><span class="tag t-' + esc(st) + '">' + esc(st) + '</span></td>' +
      '<td><b>' + esc(r.goal_type || '') + '</b><br><span class="mono">' + esc(path) + '</span></td>' +
      '<td class="mono">' + (r.elapsed_ms != null ? r.elapsed_ms : '—') + '</td>' +
      '<td>' + ev + '<span class="mono">' + why + '</span></td></tr>';
  }).join('');
}

function renderRedteam(){
  var r = state.redteam, grid = document.getElementById('rtGrid'), warn = document.getElementById('rtWarn');
  var pill = document.getElementById('rtPill');
  if (!r){
    pill.textContent = t('pressRT');
    grid.innerHTML = '<div class="rt"><b>—</b><span>' + t('pressRT') + '</span></div>';
    warn.style.display = 'none';
    return;
  }
  pill.textContent = (r.all_gates_pass ? '✅ ' : '🔴 ') + r.attacks + ' attacks / ' + r.timing.total_ms + ' ms';
  pill.className = 'pill ' + (r.all_gates_pass ? 'live' : 'frozen');
  grid.innerHTML = [
    [r.denied, t('denied')], [r.contained, t('contained')],
    [r.breaches, t('breaches')], [r.controls.verified + '/' + r.controls.total, t('controls')],
    [r.expected_met + '/' + r.attacks, t('met')], [r.timing.p50_ms + ' ms', t('p50')]
  ].map(function(x){ return '<div class="rt"><b>' + esc(x[0]) + '</b><span>' + esc(x[1]) + '</span></div>'; }).join('');
  warn.style.display = 'block';
  warn.textContent = (r.all_gates_pass ? '✅ ' + t('rtDone') + ' — ' : '🔴 ' + t('rtFail') + ' — ') +
    r.portability_warning;
}

function statusClass(s){
  return s === 'PROVEN' ? 't-PROVEN' : s === 'DECLARED' ? 't-DECLARED' :
         s === 'SIMULATED' ? 't-SIMULATED' : s === 'FAILED' ? 't-FAILED2' : 't-NOTVER';
}
function renderAudit(){
  var a = state.audit;
  var pill = document.getElementById('auditPill');
  if (!a){ pill.textContent = t('noAudit'); return; }
  pill.textContent = (a.mode === 'full' ? t('auditReady') : t('computing')) +
                     ' · ' + a.summary.probes_passed + '/' + (a.summary.probes_passed + a.summary.probes_failed) + ' probes';
  pill.className = 'pill ' + (a.mode === 'full' ? 'live' : '');

  var sc = a.summary.status_counts || {};
  var chips = [['ALL', t('all')], ['PROVEN', t('proven')], ['DECLARED', t('declared')],
               ['SIMULATED', t('simulated')], ['FAILED', t('failed')]];
  document.getElementById('filters').innerHTML = chips.map(function(c){
    var n = c[0] === 'ALL' ? a.projects.length : (sc[c[0]] || 0);
    return '<button class="chip ' + (filter === c[0] ? 'on' : '') + '" data-f="' + c[0] + '">' +
           esc(c[1]) + ' <b>' + n + '</b></button>';
  }).join('');
  document.querySelectorAll('[data-f]').forEach(function(b){
    b.addEventListener('click', function(){ filter = b.dataset.f; renderAudit(); });
  });

  var rows = a.projects.filter(function(p){ return filter === 'ALL' || p.status === filter; });
  document.getElementById('auditBody').innerHTML = rows.map(function(p){
    return '<tr><td><b>' + esc(p.name) + '</b><br><span class="mono">' + esc(p.category || '') + '</span></td>' +
      '<td><span class="tag ' + statusClass(p.status) + '">' + esc(p.status) + '</span></td>' +
      '<td>' + p.files + '</td><td class="mono">' + esc(p.license) + '</td>' +
      '<td>' + p.tests + '</td><td>' + p.ci_workflows + '</td></tr>';
  }).join('') || '<tr><td colspan="6" class="mono">—</td></tr>';

  var box = document.getElementById('criticalBox');
  var high = (a.critical || []).filter(function(c){ return c.severity === 'HIGH'; });
  if (high.length){
    box.style.display = 'block';
    box.innerHTML = '<b>' + t('high') + ' (' + high.length + '):</b> ' +
      high.map(function(c){ return '<code>' + esc(c.project) + '</code> ' + esc(c.issue); }).join(' · ');
  } else box.style.display = 'none';
}

function render(){
  if (!state) return;
  renderKpis(); renderLog(); renderRedteam(); renderAudit();
  document.getElementById('uptimePill').textContent = 'uptime ' + state.uptime_s + 's';
  var fp = document.getElementById('freezePill');
  fp.textContent = state.frozen ? t('freezeOn') : t('active');
  fp.className = 'pill ' + (state.frozen ? 'frozen' : '');
  document.getElementById('livePill').textContent = state.frozen ? '● ' + t('frozen') : '● LIVE · REAL KERNEL';
  document.getElementById('livePill').className = 'pill ' + (state.frozen ? 'frozen' : 'live');
  document.getElementById('kernelPill').textContent = 'NO LLM · stdlib only';
  var goals = (state.kernel && state.kernel.supported_goals) || [];
  var sel = document.getElementById('goal');
  if (!sel.options.length){
    sel.innerHTML = goals.map(function(g){ return '<option value="' + esc(g) + '">' + esc(g) + '</option>'; }).join('');
  }
  if (document.getElementById('killBox').checked !== state.frozen)
    document.getElementById('killBox').checked = state.frozen;
}

async function refresh(){
  try { state = await api('api/state'); render(); }
  catch (e) { console.error('state poll failed', e); }
}

function flash(msg){ console.log(msg); }

document.getElementById('runBtn').addEventListener('click', async function(){
  var gt = document.getElementById('goal').value || 'create_file';
  var params = { path: document.getElementById('path').value };
  if (gt === 'create_file' || gt === 'append_file') params.content = document.getElementById('content').value;
  try { await post('api/run', {goal_type:gt, params:params}); await refresh(); }
  catch(e){ flash(String(e)); }
});
document.getElementById('escapeBtn').addEventListener('click', async function(){
  try { await post('api/escape'); await refresh(); } catch(e){ flash(String(e)); }
});
document.getElementById('rtBtn').addEventListener('click', async function(){
  if (busyRT) return;
  busyRT = true; var b = this, old = b.textContent;
  b.disabled = true; b.textContent = t('running');
  document.getElementById('rtPill').textContent = t('running');
  try { await post('api/redteam'); await refresh(); } catch(e){ flash(String(e)); }
  finally { busyRT = false; b.disabled = false; b.textContent = old; }
});
document.getElementById('auditBtn').addEventListener('click', async function(){
  try { await post('api/audit/refresh'); flash(t('refresh')); setTimeout(refresh, 1200); } catch(e){ flash(String(e)); }
});
document.getElementById('killBox').addEventListener('change', async function(){
  var on = this.checked;
  try {
    var r = await post('api/kill', {enabled:on});
    flash(on ? t('killOn') : t('killOff'));
    await refresh();
  } catch(e){ flash(String(e)); }
});
document.getElementById('pulseBox').addEventListener('change', function(){
  if (pulseTimer){ clearInterval(pulseTimer); pulseTimer = null; }
  if (this.checked){
    pulseTimer = setInterval(async function(){
      if (state && state.frozen) return;   // freeze really gates dispatch
      try { await post('api/pulse'); } catch(e){ /* keep the pulse quiet */ }
    }, 1200);
  }
});

refresh();
setInterval(refresh, 1000);
