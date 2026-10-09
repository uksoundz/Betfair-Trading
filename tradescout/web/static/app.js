const $ = (s) => document.querySelector(s);
const pct = (v) => v == null ? '-' : Math.round(v * 100) + '%';
const spct = (v) => v == null ? '-' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
const price = (v) => v == null ? '-' : v.toFixed(2);
const money = (v) => v == null ? '-' : (v < 0 ? '-£' : '£') + Math.abs(v).toFixed(2);
const STAR_HELP = 'Stars grade TRADE ideas by conservative edge x execution x evidence: 1 star under 0.5% clean edge, 2 from 0.5%, 3 from 1.5%, 4 from 3%, 5 from 5%. In-play plans count 60% of their edge. NO TRADE and RESEARCH get no stars.';
const starsHtml = (n) => n ? `<span class="stars ${n <= 1 ? 'dim' : ''}" title="${n} out of 5. ${STAR_HELP}">${'★'.repeat(n)}${'☆'.repeat(5 - n)}</span>` : `<span class="stars dim" title="${STAR_HELP}">no stars</span>`;
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const PH = { entry: 'Before start', inplay: 'In play', exit: 'Get out', stop: 'Stop loss', note: 'Note' };
const DEC = { 'TRADE': 'trade', 'NO TRADE': 'notrade', 'RESEARCH': 'research' };
const decBadge = (d) => `<span class="badge-dec ${DEC[d] || 'research'}">${esc(d)}</span>`;
const EVID = { 'exchange-priced-static': 'Exchange-priced, settles at result', 'simulated-inplay': 'Simulated in-play exits', 'model-synthetic': 'Model only, no market price' };
const hhmm = (iso) => iso ? iso.replace('T', ' ').slice(11, 19) : '';
const PRICE_LABEL = { ok: 'priced', none: '', no_event: 'no Betfair match', no_markets: 'no markets yet', inplay: 'in play', suspended: 'suspended', closed: 'closed', error: 'feed error' };

let sport = 'football', data = null, dataOther = null, strategies = [], selected = null, status = {}, calendar = {}, autoJumped = false, warnedBetfair = false;
let refreshTimer = null, countdownTimer = null, nextRefreshAt = null, feedOpen = false, lastDiag = '';

function toast(msg, ms = 4000) { const t = $('#toast'); t.textContent = msg; t.style.display = 'block'; clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', ms); }
function isoShift(iso, days) { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + days); return d.toISOString().slice(0, 10); }
function showView(name) {
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x.dataset.view === name));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  if (name === 'picks') renderPicks(); if (name === 'strategies') renderStrategy(); if (name === 'journal') loadJournal();
  if (name === 'settings') loadSettings(); if (name === 'performance') loadPerformance(); if (name === 'bankroll') loadBankroll();
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  let body = null; try { body = await r.json(); } catch (e) { }
  if (!r.ok) throw new Error((body && body.detail) || r.statusText);
  return body;
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

async function init() {
  await loadStatus();
  $('#date').value = status.today;
  document.querySelectorAll('.sport').forEach(b => b.onclick = () => setSport(b.dataset.sport));
  document.querySelectorAll('.tab').forEach(t => t.onclick = () => showView(t.dataset.view));
  $('#go').onclick = () => scan('full'); $('#prev').onclick = () => { $('#date').value = isoShift($('#date').value, -1); autoJumped = true; scan(); };
  $('#next').onclick = () => { $('#date').value = isoShift($('#date').value, 1); autoJumped = true; scan(); };
  $('#today').onclick = () => { $('#date').value = status.today; autoJumped = false; scan(); };
  $('#date').onchange = () => { autoJumped = true; scan(); };
  ['#search', '#leagueFilter', '#sort'].forEach(s => $(s).oninput = renderList);
  ['#picksMode', '#picksBoth', '#picksPerMatch', '#picksSort', '#picksCount'].forEach(s => $(s).onchange = () => { if (s === '#picksBoth' && $('#picksBoth').checked) loadOther(); else renderPicks(); });
  $('#stratSelect').onchange = renderStrategy;
  $('#settleBtn').onclick = settleJournal;
  $('#saveFd').onclick = () => saveSettings({ football_data_org_key: $('#fdKey').value }, '#fdResult');
  $('#clearFd').onclick = () => saveSettings({ clear_football: true }, '#fdResult');
  $('#testFd').onclick = () => testConn('/api/test/fixtures', '#fdResult', r => `Connected. ${r.fixtures_next_3_days} fixtures in the next 3 days.`);
  $('#saveBf').onclick = () => saveSettings({ betfair_app_key: $('#bfKey').value, betfair_username: $('#bfUser').value, betfair_password: $('#bfPass').value, betfair_jurisdiction: $('#bfJurisdiction').value, betfair_cert_file: $('#bfCert').value, betfair_key_file: $('#bfKeyFile').value }, '#bfResult');
  $('#clearBf').onclick = () => saveSettings({ clear_betfair: true }, '#bfResult');
  $('#testBf').onclick = () => testConn('/api/test/betfair', '#bfResult', r => `Logged in via ${r.login_host.replace(/https?:\/\//, '').split('/')[0]}. ${r.football_events_next_2_days} football and ${r.tennis_events_next_2_days} tennis events in the next 2 days.${r.delayed ? ' Delayed application key: prices up to 3 minutes old.' : r.delayed === false ? ' Live application key.' : ''}`);
  $('#reconnectBf').onclick = reconnectBetfair;
  $('#saveStake').onclick = () => saveSettings({ bank: +$('#bank').value, kelly_fraction: +$('#kelly').value, commission: (+$('#commission').value) / 100, min_edge: (+$('#minEdge').value) / 100, model_weight_scale: +$('#modelWeight').value }, '#stakeResult');
  $('#saveBetting').onclick = () => {
    const mode = $('#betMode').value;
    if (mode === 'live' && !confirm('Live mode lets the Bet slip send real orders to Betfair after you press a confirmation button. Ideas the app marks TRADE place directly; anything else needs your explicit override on the slip. Turn it on?')) { $('#betMode').value = status.betting_mode || 'off'; return; }
    saveSettings({ betting_mode: mode, daily_cap: +$('#dailyCap').value }, '#bettingResult');
  };
  $('#saveStrats').onclick = () => saveSettings({ enabled_strategies: [...document.querySelectorAll('#stratToggles input:checked')].map(i => i.value) }, '#stratResult');
  $('#openBetsBtn').onclick = loadOpenBets;
  $('#modal').onclick = (ev) => { if (ev.target.id === 'modal') $('#modal').hidden = true; };
  document.addEventListener('visibilitychange', () => { if (!document.hidden && nextRefreshAt && Date.now() >= nextRefreshAt) silentRefresh(); });
  await setSport('football');
}

async function setSport(s) {
  sport = s; dataOther = null; selected = null; autoJumped = false;
  document.querySelectorAll('.sport').forEach(b => b.classList.toggle('active', b.dataset.sport === s));
  strategies = await api('/api/strategies?sport=' + s);
  const ss = $('#stratSelect'); ss.innerHTML = '';
  strategies.forEach(x => { const o = document.createElement('option'); o.value = x.key; o.textContent = x.label + (x.enabled ? '' : ' (off)'); ss.appendChild(o); });
  const lf = $('#leagueFilter'); lf.innerHTML = '<option value="">All leagues</option>';
  Object.entries((status.leagues || {})[s] || {}).forEach(([k, v]) => { const o = document.createElement('option'); o.value = k; o.textContent = v; lf.appendChild(o); });
  await loadCalendar(status.today);
  scan();
}

async function loadStatus() {
  status = await api('/api/status');
  renderPills();
}

function renderPills() {
  const j = status.journal || {};
  const live = sport === 'tennis' ? status.tennis_live : status.live_fixtures;
  const bf = status.betfair_state || {};
  let bfText, bfDot;
  if (!bf.configured) { bfText = 'No exchange prices: research only'; bfDot = ''; }
  else if (bf.connected) { bfText = 'Exchange prices' + (data && data.betfair && data.priced != null ? ` · ${data.priced}/${data.fixtures} priced` : '') + (bf.delayed ? ' · delayed key' : ''); bfDot = 'on'; }
  else { bfText = 'Betfair not connected' + (bf.error ? ': ' + bf.error.split('[')[0].slice(0, 60) : ' (connecting…)'); bfDot = 'warn'; }
  $('#pills').innerHTML = `
    <span class="pill" data-go="settings" title="Click to set up"><span class="dot ${live ? 'on' : ''}"></span>${live ? 'Live fixtures' : (sport === 'tennis' ? 'Replay data (connect Betfair for live tennis)' : 'Built-in fixtures only')}</span>
    <span class="pill" data-feed="1" title="Click for the price feed details"><span class="dot ${bfDot}"></span>${esc(bfText)}</span>
    <span class="pill" data-go="settings" title="Betting mode"><span class="dot ${status.betting_mode === 'live' ? 'warn' : status.betting_mode === 'paper' ? 'on' : ''}"></span>${status.betting_mode === 'live' ? 'LIVE betting on' : status.betting_mode === 'paper' ? 'Paper betting' : 'Betting off'}</span>
    <span class="pill" data-go="bankroll"><span class="dot on"></span>Bank £${status.bank} · open risk £${(status.exposure && status.exposure.open_total || 0).toFixed(0)}</span>`;
  document.querySelectorAll('.pill[data-go]').forEach(p => p.onclick = () => showView(p.dataset.go));
  document.querySelectorAll('.pill[data-feed]').forEach(p => p.onclick = () => { if (!bf.configured) { showView('settings'); return; } feedOpen = !feedOpen; renderFeed(); });
  $('#journalCount').textContent = j.picks ? j.picks : '';
}

async function loadCalendar(startIso) {
  try { const c = await api(`/api/calendar?start=${startIso}&days=10&sport=${sport}`); calendar = c.counts || {}; } catch (e) { calendar = {}; }
  renderDayStrip(startIso);
}

function renderDayStrip(startIso) {
  const sel = $('#date').value; const out = [];
  for (let k = 0; k < 10; k++) {
    const iso = isoShift(startIso, k); const d = new Date(iso + 'T00:00:00'); const n = calendar[iso] || 0;
    out.push(`<div class="day ${n ? 'has' : 'none'} ${iso === sel ? 'sel' : ''}" data-d="${iso}">${d.toLocaleDateString(undefined, { weekday: 'short' })} ${d.getDate()}<small>${n ? n + (sport === 'tennis' ? ' matches' : ' games') : 'none'}</small></div>`);
  }
  $('#daystrip').innerHTML = out.join('');
  document.querySelectorAll('.day').forEach(el => el.onclick = () => { $('#date').value = el.dataset.d; autoJumped = true; scan(); });
}

async function stripStart() {
  // replaying the past: centre the strip on the chosen day so neighbouring days are reachable
  const d = $('#date').value;
  return d < status.today ? isoShift(d, -3) : status.today;
}

// ---------------- scanning and the price feed
let scanSeq = 0;
function scanMeta(extra) {
  if (!data) return;
  const dec = data.decisions || {};
  $('#scanmeta').textContent = `${data.fixtures} fixtures · ${data.ideas} ideas · ${dec.TRADE || 0} TRADE · ${dec['NO TRADE'] || 0} no trade · ${dec.RESEARCH || 0} research · model on ${data.model_matches.toLocaleString()} matches` + (extra || '');
}
function scheduleRefresh() {
  clearTimeout(refreshTimer); clearInterval(countdownTimer); nextRefreshAt = null;
  if (!data || !data.auto_refresh_seconds || !data.matches.length || data.date < status.today) { scanMeta(data && data.prices_as_of ? ` · prices ${hhmm(data.prices_as_of)} UTC` : ''); return; }
  nextRefreshAt = Date.now() + data.auto_refresh_seconds * 1000;
  const tick = () => { const s = Math.max(0, Math.round((nextRefreshAt - Date.now()) / 1000)); scanMeta(` · prices ${data.prices_as_of ? hhmm(data.prices_as_of) + ' UTC' : 'not attached'} · next refresh in ${s}s`); };
  tick(); countdownTimer = setInterval(tick, 1000);
  refreshTimer = setTimeout(() => { if (document.hidden) { nextRefreshAt = Date.now(); return; } silentRefresh(); }, data.auto_refresh_seconds * 1000);
}
async function silentRefresh() {
  // re-pull prices for the day on screen without wiping what is shown; keep the open match open
  const d = $('#date').value; const seq = ++scanSeq, mySport = sport;
  try {
    const fresh = await api(`/api/scan?date=${d}&sport=${mySport}&refresh=prices`);
    if (seq !== scanSeq || !fresh.matches.length) return;
    data = fresh;
    await loadStatus();
    if ($('#picksBoth').checked) { await loadOther(); } else { renderPicks(); }
    renderList(); renderStrategy(); renderFeed();
    if (selected) { const y = window.scrollY; showMatch(selected, data, true, true); window.scrollTo(0, y); }
  } catch (e) { toast('Price refresh failed: ' + e.message, 5000); }
  finally { if (seq === scanSeq) scheduleRefresh(); }
}
async function scan(refresh = '') {
  const d = $('#date').value; if (!d) return;
  const seq = ++scanSeq, mySport = sport;  // a newer scan (date or sport change) makes this one stale
  clearTimeout(refreshTimer); clearInterval(countdownTimer); nextRefreshAt = null;
  const start = await stripStart();
  if (start !== status.today && !calendar[d] && !calendar[isoShift(d, 1)]) { await loadCalendar(start); } else { renderDayStrip(start); }
  $('#banner').innerHTML = ''; $('#feedPanel').innerHTML = ''; $('#matchList').innerHTML = '<div class="spinner">Scanning ' + d + (status.betfair_configured ? ' and pulling exchange prices' : '') + '…</div>'; $('#detail').innerHTML = ''; $('#picks').innerHTML = '<div class="spinner">Scanning…</div>';
  let fresh;
  try { fresh = await api(`/api/scan?date=${d}&sport=${mySport}${refresh ? '&refresh=' + refresh : ''}`); }
  catch (e) {
    if (seq !== scanSeq) return;
    data = null; $('#picks').innerHTML = ''; $('#matchList').innerHTML = '';
    $('#banner').innerHTML = `<div class="banner err"><b>Could not scan ${d}.</b> ${esc(e.message)}${/key|Betfair|Settings/i.test(e.message) ? ' <a href="#" data-go="settings">Open Settings</a>' : ''}</div>`;
    document.querySelectorAll('#banner a').forEach(a => a.onclick = (ev) => { ev.preventDefault(); showView(a.dataset.go); });
    return;
  }
  if (seq !== scanSeq) return;  // superseded while loading: drop it
  data = fresh; selected = null; dataOther = null;
  if (data.betfair) { status.betfair_state = data.betfair; status.betfair = data.betfair.connected; renderPills(); }
  scanMeta();
  if (!data.matches.length) {
    const up = data.upcoming || {}; const nextDay = Object.keys(up)[0]; const blank = data.weekday;
    if (!autoJumped && nextDay) {
      autoJumped = true; $('#date').value = nextDay; await scan();
      if (seq + 1 !== scanSeq) return;  // something else moved on in the meantime
      $('#banner').innerHTML = `<div class="banner">No ${sport} in the covered competitions on <b>${blank}</b>, so this is the next match day.${sport === 'tennis' && !status.tennis_live ? ' Tennis is in replay mode: connect Betfair in Settings for live ATP fixtures and prices.' : ''}</div>`;
      return;
    }
    const list = Object.entries(up).map(([k, n]) => `<li><a href="#" data-d="${k}">${new Date(k + 'T00:00:00').toDateString()}</a> — ${n} fixtures</li>`).join('');
    $('#picks').innerHTML = $('#matchList').innerHTML = `<div class="empty">No ${sport} in the covered competitions on ${data.weekday}.${list ? '<p>Next match days:</p><ul style="text-align:left;display:inline-block">' + list + '</ul>' : ''}</div>`;
    document.querySelectorAll('#picks a, #matchList a').forEach(a => a.onclick = (ev) => { ev.preventDefault(); $('#date').value = a.dataset.d; scan(); });
    return;
  }
  renderList(); renderPicks(); renderStrategy(); renderFeed(); scheduleRefresh();
  if (!status.betfair_configured && !warnedBetfair) { warnedBetfair = true; toast('No exchange prices: every idea is RESEARCH ONLY until Betfair is connected in Settings.', 7000); }
}

function renderFeed() {
  const el = $('#feedPanel'); if (!data || !data.matches.length) { el.innerHTML = ''; return; }
  const bf = data.betfair || status.betfair_state || {}; const f = data.feed;
  if (!bf.configured) { el.innerHTML = ''; return; }
  const today = data.date >= status.today;
  let head, cls = '';
  if (!bf.connected && (bf.error || !f)) { cls = 'err'; head = `<b>Betfair is not connected.</b> ${esc(bf.error || 'No session yet.')}`; }
  else if (f && f.error) { cls = 'err'; head = `<b>Price feed error.</b> ${esc(f.error)}`; }
  else if (f) {
    const unpriced = f.fixtures - f.priced;
    head = `<b>Exchange prices: ${f.priced} of ${f.fixtures} fixtures priced</b> at ${hhmm(f.fetched_at)} UTC (${f.events_on_day} events on the exchange, ${f.calls} calls, ${f.elapsed_ms} ms).`
      + (unpriced ? ` ${f.unmatched.length ? f.unmatched.length + ' not matched to an exchange event' : ''}${f.inplay.length ? ', ' + f.inplay.length + ' in play' : ''}${f.suspended.length ? ', ' + f.suspended.length + ' suspended' : ''}.` : '')
      + (bf.delayed ? ' <span class="warn">Delayed application key: prices are up to 3 minutes old.</span>' : '')
      + (!today ? ' This is a past day, so the exchange has no pre-match prices; the decisions above are model research.' : '');
    if (!today) cls = '';
  } else { head = '<b>Exchange prices:</b> none attached for this day.'; }
  const toggle = `<a href="#" id="feedToggle" style="margin-left:8px">${feedOpen ? 'hide details' : 'details'}</a>`;
  let body = '';
  if (feedOpen) {
    const rows = [];
    if (f && f.unmatched && f.unmatched.length) rows.push(`<p><b>Not matched to an exchange event</b> (the fixture feed and Betfair spell the clubs differently, or Betfair has not listed the match):</p><ul>` + f.unmatched.map(u => `<li>${esc(u.fixture)} — ${esc(u.reason)}${u.candidates && u.candidates.length ? ' Nearest on the exchange: ' + u.candidates.slice(0, 2).map(c => `<i>${esc(c[0])}</i> (${(c[1] * 100).toFixed(0)}%)`).join(', ') : ''}</li>`).join('') + '</ul>');
    if (f && f.inplay && f.inplay.length) rows.push(`<p><b>In play</b> (started, no pre-match prices): ${f.inplay.map(esc).join(', ')}</p>`);
    if (f && f.suspended && f.suspended.length) rows.push(`<p><b>Suspended right now</b>: ${f.suspended.map(esc).join(', ')}</p>`);
    if (data.skipped && data.skipped.length) rows.push(`<p><b>Feed errors</b>:</p><ul>${data.skipped.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`);
    const h = bf.health || {};
    rows.push(`<p class="meta">Session: ${bf.connected ? 'connected' : 'not connected'} · logins ${h.logins || 0} · API calls ${h.calls || 0} · last OK ${h.last_ok ? hhmm(h.last_ok) + ' UTC' : 'never'}${h.last_error ? ' · last error ' + esc(h.last_error) : ''} · login host ${esc(h.identity_host || '')} · jurisdiction ${esc(bf.jurisdiction || 'com')}</p>`);
    rows.push(`<div class="row"><button class="small" id="feedDiag">Run full diagnosis</button><button class="small" id="feedReconnect">Reconnect to Betfair</button><button class="small" id="feedRefresh">Refresh prices now</button></div><div id="feedDiagOut">${lastDiag}</div>`);
    body = rows.join('');
  }
  el.innerHTML = `<div class="banner feed ${cls}">${head}${toggle}${body}</div>`;
  $('#feedToggle').onclick = (ev) => { ev.preventDefault(); feedOpen = !feedOpen; renderFeed(); };
  if ($('#feedDiag')) $('#feedDiag').onclick = runDiagnosis;
  if ($('#feedReconnect')) $('#feedReconnect').onclick = async () => { await reconnectBetfair(); scan('full'); };
  if ($('#feedRefresh')) $('#feedRefresh').onclick = () => silentRefresh();
}
async function runDiagnosis() {
  $('#feedDiagOut').innerHTML = '<div class="spinner">Asking the exchange…</div>';
  const put = (html) => { lastDiag = html; const el = $('#feedDiagOut'); if (el) el.innerHTML = html; };
  try {
    const r = await api(`/api/betfair/diagnose?date=${data.date}&sport=${sport}`);
    if (!r.configured) { put(`<div class="warn">! ${esc(r.error)}</div>`); return; }
    const rep = r.report || {}; const h = r.health || {};
    put(`<div class="meta" style="margin-top:8px">Login ${h.connected ? 'OK' : 'FAILED'} · key ${h.delayed ? 'DELAYED' : h.delayed === false ? 'live' : 'unknown'} · ${rep.events_on_day || 0} exchange events on ${r.day} · matched ${rep.matched || 0} of ${rep.fixtures || 0}, priced ${rep.priced || 0}${rep.error ? ' · <span class="warn">' + esc(rep.error) + '</span>' : ''}${h.last_error && !rep.error ? ' · last error earlier: ' + esc(h.last_error) : ''}</div>
      <div class="wrap"><table class="sc"><tr><th>Fixture</th><th>Status</th><th>Exchange event</th><th>Prices</th><th>Note</th></tr>
      ${(r.fixtures || []).map(x => `<tr><td>${esc(x.fixture)}</td><td class="${x.status === 'ok' ? 'pos' : 'neg'}">${esc(x.status)}</td><td>${esc(x.event_name || '-')}</td><td class="n">${x.quotes || 0}</td><td class="meta">${esc(x.note || '')}${x.candidates && x.candidates.length ? ' Nearest: ' + x.candidates.slice(0, 2).map(c => esc(c[0])).join(' / ') : ''}${(x.raw_markets || []).map(m => `<div>${esc(m.type)}: ${esc(m.status)}${m.inplay ? ' · in play' : ''} · start ${esc((m.start || '').replace('T', ' ').slice(0, 16))} · £${Math.round(m.matched || 0)} matched · ${m.runners_priced || 0}/${m.runners || 0} runners priced</div>`).join('')}${(x.flags || []).map(f => `<div class="warn">! ${esc(f)}</div>`).join('')}</td></tr>`).join('')}</table></div>
      ${rep.event_names && rep.event_names.length ? `<details style="margin-top:6px"><summary class="meta">Every event the exchange lists that day (${rep.event_names.length})</summary><div class="meta">${rep.event_names.map(esc).join(' · ')}</div></details>` : ''}`);
  } catch (e) { put(`<div class="warn">! ${esc(e.message)}</div>`); }
}
async function reconnectBetfair() {
  const el = $('#bfResult'); if (el) el.textContent = 'Reconnecting…';
  try {
    const r = await post('/api/betfair/reconnect');
    await loadStatus();
    if (el) { el.textContent = r.ok ? 'Reconnected. Betfair session is live.' : 'Failed: ' + r.error; el.className = 'meta ' + (r.ok ? 'pos' : 'neg'); }
    toast(r.ok ? 'Betfair reconnected.' : 'Betfair reconnect failed: ' + r.error, 6000);
    if (data) renderFeed();
  } catch (e) { if (el) el.textContent = 'Failed: ' + e.message; toast('Reconnect failed: ' + e.message, 6000); }
}

async function loadOther() {
  const other = sport === 'tennis' ? 'football' : 'tennis';
  try { dataOther = await api(`/api/scan?date=${$('#date').value}&sport=${other}`); } catch (e) { dataOther = null; toast('Could not load ' + other + ': ' + e.message); }
  renderPicks();
}

function filteredMatches() {
  const q = $('#search').value.toLowerCase(), lg = $('#leagueFilter').value, sortBy = $('#sort').value;
  let ms = data.matches.filter(m => (!lg || m.league === lg) && (!q || (m.home + ' ' + m.away).toLowerCase().includes(q)));
  if (sortBy === 'score') ms.sort((a, b) => (b.n_trades - a.n_trades) || ((b.best_score || 0) - (a.best_score || 0)));
  if (sortBy === 'time') ms.sort((a, b) => (a.kickoff || '').localeCompare(b.kickoff || ''));
  return ms;
}

function priceChip(m) {
  if (!status.betfair_configured && !(data && data.betfair && data.betfair.configured)) return '';
  const st = m.price_status || 'none'; const label = PRICE_LABEL[st] ?? st;
  if (!label) return '';
  const cls = st === 'ok' ? 'ok' : (st === 'inplay' || st === 'closed') ? 'dim' : 'warn';
  return `<span class="chip ${cls}" title="${esc(m.price_note || '')}">${label}${st === 'ok' && m.price_as_of ? ' ' + hhmm(m.price_as_of).slice(0, 5) : ''}</span>`;
}

function renderList() {
  if (!data || !data.matches.length) return;
  $('#matchList').innerHTML = filteredMatches().map(m => `
    <div class="m ${selected === m.id ? 'sel' : ''}" data-id="${esc(m.id)}">
      <div class="ko">${m.kickoff || ''}<br>${esc(m.league_name)}</div>
      <div class="teams">${esc(m.home)}<br>${esc(m.away)}<div class="sub">${m.best_strategy ? esc(m.best_strategy) : 'no strategy fits'}${m.result ? ' · ' + (m.sport === 'tennis' ? esc(m.result.winner.split(' ').slice(-1)[0] + ' ' + m.result.sets) : 'FT ' + m.result.home + '-' + m.result.away) : ''} ${priceChip(m)}</div></div>
      <div>${decBadge(m.best_decision)}<br>${starsHtml(m.best_stars)}</div>
    </div>`).join('');
  document.querySelectorAll('.m').forEach(el => el.onclick = () => showMatch(el.dataset.id));
}

function forecastPanel(m) {
  const f = m.forecast;
  if (f.sport === 'tennis') {
    const setsTxt = Object.entries(f.p_sets).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, v]) => `${k} ${pct(v)}`).join(' · ');
    return `<div class="grid">
      <div class="stat"><b>${pct(f.p_a)} / ${pct(f.p_b)}</b><span>${esc(m.home)} / ${esc(m.away)} to win</span></div>
      <div class="stat"><b>${Math.round(f.elo_a)} v ${Math.round(f.elo_b)}</b><span>Elo rating on ${esc(f.surface.toLowerCase())}</span></div>
      <div class="stat"><b>${pct(f.pa_serve)} / ${pct(f.pb_serve)}</b><span>points won on serve</span></div>
      <div class="stat"><b>${pct(f.p_set1_a)}</b><span>${esc(m.home)} wins set 1</span></div>
      <div class="stat"><b>${f.expected_games.toFixed(1)}</b><span>expected games (best of ${f.best_of})</span></div>
      <div class="stat"><b>${setsTxt}</b><span>set betting</span></div>
      <div class="stat"><b>${pct(f.cond.set1_won)} / ${pct(f.cond.set1_lost)}</b><span>${esc(m.home)} after winning / losing set 1</span></div>
      <div class="stat"><b>${pct(f.p_first_break_a)} / ${pct(f.p_first_break_b)}</b><span>breaks first</span></div>
    </div>
    <div class="meta">Match odds</div><div class="bar"><i style="width:${f.p_a * 100}%;background:var(--accent)"></i><i style="width:${(1 - f.p_a) * 100}%;background:var(--inplay)"></i></div>`;
  }
  return `<div class="grid">
    <div class="stat"><b>${f.home_xg.toFixed(2)} – ${f.away_xg.toFixed(2)}</b><span>expected goals</span></div>
    <div class="stat"><b>${pct(f.p_home)} / ${pct(f.p_draw)} / ${pct(f.p_away)}</b><span>home / draw / away</span></div>
    <div class="stat"><b>${pct(f.p_over['2.5'])}</b><span>over 2.5 goals</span></div>
    <div class="stat"><b>${pct(f.p_btts)}</b><span>both teams score</span></div>
    <div class="stat"><b>${pct(f.p_00)}</b><span>0-0</span></div>
    <div class="stat"><b>${pct(f.p_goal_before['70'])}</b><span>goal before 70'</span></div>
    <div class="stat"><b>${pct(f.p_fav_scores_first)}</b><span>${esc(f.favourite)} score first</span></div>
    <div class="stat"><b>${f.top_scores.slice(0, 3).map(s => s.score + ' ' + pct(s.p)).join(' · ')}</b><span>most likely scores</span></div>
  </div>
  <div class="meta">Match odds</div><div class="bar"><i style="width:${f.p_home * 100}%;background:var(--accent)"></i><i style="width:${f.p_draw * 100}%;background:var(--muted)"></i><i style="width:${f.p_away * 100}%;background:var(--inplay)"></i></div>`;
}

function priceBanner(m) {
  const cfg = status.betfair_configured || (data && data.betfair && data.betfair.configured);
  if (!cfg) return '<div class="meta" style="margin:6px 0">No exchange prices: Betfair is not set up, so every plan here is model research. <a href="#" data-go="settings">Set it up in Settings</a>.</div>';
  const st = m.price_status || 'none';
  if (st === 'ok') return `<div class="meta" style="margin:6px 0">Exchange: <b>${esc(m.exchange_event || '')}</b> · prices ${hhmm(m.price_as_of)} UTC${m.delayed ? ' (delayed key: up to 3 min old)' : ''}${Object.entries(m.markets || {}).filter(([k, v]) => v !== 'OPEN').map(([k, v]) => ` · ${esc(k)} ${esc(v.toLowerCase())}`).join('')}</div>`;
  const cands = (m.price_candidates || []).slice(0, 2).map(c => `<i>${esc(c[0])}</i> (${(c[1] * 100).toFixed(0)}%)`).join(', ');
  return `<div class="warn" style="margin:6px 0">! ${esc(m.price_note || 'No exchange prices for this match.')}${cands ? ' Nearest exchange events: ' + cands + '.' : ''}${st === 'no_event' ? ' Open the price feed details (top right pill) to run a full diagnosis.' : ''}</div>`;
}

function showMatch(id, fromData, keepScroll, keepView) {
  const src = fromData || data; const m = src.matches.find(x => x.id === id); if (!m) return;
  if (src === data) { selected = id; renderList(); }
  if (!keepView) showView('matches');
  const f = m.forecast;
  const result = m.result ? `<div class="result">Actual result: <b>${m.sport === 'tennis' ? esc(m.result.winner) + ' won ' + esc(m.result.score) + (m.result.retired ? ' (retirement)' : '') : esc(m.home) + ' ' + m.result.home + '-' + m.result.away + ' ' + esc(m.away)}</b></div>` : '';
  $('#detail').innerHTML = `
    <div class="card">
      <h2>${esc(m.home)} v ${esc(m.away)}</h2>
      <div class="meta">${esc(m.league_name)}${f.tourney ? ' · ' + esc(f.tourney) : ''} · ${src.weekday}${m.kickoff ? ' · ' + m.kickoff : ''} · data confidence ${f.confidence.toFixed(2)}</div>
      ${priceBanner(m)}
      ${result}
      <h3>The model's view</h3>
      ${m.summary.map(s => `<p style="margin:4px 0">${esc(s)}</p>`).join('')}
      ${forecastPanel(m)}
    </div>
    <h3 style="margin:18px 0 8px;color:var(--muted)">Strategies for this match, best first</h3>
    ${m.ideas.length ? m.ideas.map(i => ideaCard(i, m)).join('') : '<div class="card">No enabled strategy passes its entry rules for this match. That is a legitimate answer: leave it.</div>'}`;
  wireIdeaButtons(m, src);
  document.querySelectorAll('#detail a[data-go]').forEach(a => a.onclick = (ev) => { ev.preventDefault(); showView(a.dataset.go); });
  if (!keepScroll) window.scrollTo({ top: 0, behavior: 'smooth' });
}

function placeButton(i, m) {
  if (!i.orders || !i.orders.length) return '';
  const cfg = status.betfair_configured || (data && data.betfair && data.betfair.configured);
  const liveMode = status.betting_mode === 'live';
  const ready = cfg && liveMode && i.decision === 'TRADE' && m.price_status === 'ok';
  const label = cfg && liveMode ? 'Place on Betfair' : cfg ? 'Bet slip · place' : 'Bet slip';
  const title = ready ? 'Opens the slip; orders are sent only after you confirm' : !cfg ? 'Review the slip (connect Betfair in Settings to place)' : !liveMode ? 'Review the slip; switch Betting to Live in Settings to place' : i.decision !== 'TRADE' ? 'Opens the slip; placing a non-TRADE idea needs your explicit override' : 'Opens the slip';
  return `<button class="small ${ready ? 'primary place' : ''}" data-slip="${esc(i.strategy)}" title="${esc(title)}">${label}</button>`;
}

function ideaCard(i, m) {
  const settled = i.settled ? `<div class="result">Replay: this plan ${i.settled.hit >= 0.5 ? '<b class="pos">paid off</b>' : '<b class="neg">did not pay off</b>'} — ${spct(i.settled.pnl)} per unit risked${i.evidence !== 'exchange-priced-static' ? ' (modelled exits)' : ''}${i.settled.hit > 0 && i.settled.hit < 1 ? ' (event timing not in the data, so this is an expectation)' : ''}</div>` : '';
  const hist = i.historical_strike_rate != null ? `<span>History: <b>${pct(i.historical_strike_rate)}</b> strike over ${i.historical_sample} similar trades</span>` : '<span>History: <b>none</b> for this strategy and league</span>';
  const legs = (i.legs || []).map((l, k) => { const o = i.orders[k] || {}; return `<tr><td>${esc(o.side || '').toUpperCase()} ${esc(o.selection)} <span class="meta">(${esc(o.market)})</span></td><td class="n">${pct(o.p_model)}</td><td class="n">${pct(l.p_market)}</td><td class="n">${pct(l.p_conservative)}</td><td class="n">${l.price ? l.price.toFixed(2) : '-'}</td><td class="n">${l.spread == null ? '-' : (l.spread * 100).toFixed(1) + '%'}</td><td class="n">${l.fill_fraction == null ? '-' : pct(l.fill_fraction)}</td><td class="n ${l.ev_conservative >= 0 ? 'pos' : 'neg'}">${spct(l.ev_conservative)}</td><td class="meta">${esc((l.reasons || []).join(' '))}</td></tr>`; }).join('');
  return `<div class="card idea ${DEC[i.decision]}" id="idea-${esc(i.strategy)}">
    <div class="head">${decBadge(i.decision)}${starsHtml(i.stars)}<h2>${esc(i.strategy_label)}</h2><span class="evidence" title="How the return is established">${esc(EVID[i.evidence] || i.evidence)}</span>
      <button class="small ${i.tracked ? 'ghost' : ''}" data-track="${esc(i.strategy)}" ${i.tracked ? 'disabled' : ''}>${i.tracked ? 'Tracked ✓' : '+ Track'}</button>
      <button class="small" data-copy="${esc(i.strategy)}">Copy plan</button>
      ${placeButton(i, m)}</div>
    <div class="verdict">${esc(i.verdict)}</div>
    <div class="kv">
      <span>Pays off: <b>${pct(i.calibrated_hit_prob)}</b> <span title="raw model">(model ${pct(i.hit_prob)})</span></span>
      <span>Net edge (conservative): <b>${spct(i.ev_conservative)}</b> <span title="on the raw model probability">(model ${spct(i.ev_model)})</span></span>
      <span>Modelled plan return: <b>${spct(i.calibrated_roi)}</b> per unit</span>
      <span>Win <b class="pos">${spct(i.win_return)}</b> / lose <b class="neg">${spct(i.loss_return)}</b> / worst <b class="neg">${spct(i.max_loss_per_unit)}</b></span>
      <span>Entry: <b>${price(i.market_price || i.model_price)}</b>${i.market_price ? ' (exchange)' : ' (model fair)'}</span>
      <span>Market implies: <b>${pct(i.p_market)}</b></span>
      <span>Confidence: <b>${i.confidence.toFixed(2)}</b></span>
      <span>Execution: <b>${i.execution ? i.execution.toFixed(2) : '-'}</b></span>
      <span>Stake: <b>${i.stake_money ? money(i.stake_money) : 'none'}</b>${i.risk_money ? ' (risk ' + money(i.risk_money) + ')' : ''}</span>
      ${hist}
    </div>
    ${(i.decision_reasons || []).map(r => `<div class="meta">· ${esc(r)}</div>`).join('')}
    ${(i.risk_notes || []).map(r => `<div class="meta">· risk: ${esc(r)}</div>`).join('')}
    ${settled}
    <h3>The plan</h3>
    <ul class="steps">${i.plan.map(p => `<li><span class="ph ${p.phase}">${PH[p.phase]}</span><span>${esc(p.text)}</span></li>`).join('')}</ul>
    <h3>Why</h3>
    <ul style="margin:4px 0 4px 18px">${i.rationale.map(r => `<li>${esc(r)}</li>`).join('')}</ul>
    <div class="meta" style="margin-top:6px"><b>Best for:</b> ${esc(i.best_for)}<br><b>Avoid when:</b> ${esc(i.avoid_when)}<br><b>Settlement in tests:</b> ${esc(i.settlement)}${i.inplay ? ' · needs in-play action' : ' · settles at the result'}</div>
    ${i.warnings.map(w => `<div class="warn">! ${esc(w)}</div>`).join('')}
    ${legs ? `<details style="margin-top:8px"><summary class="meta">Value check per selection (model vs market vs conservative)</summary><div class="wrap"><table class="sc"><tr><th>Selection</th><th class="n">Model</th><th class="n">Market</th><th class="n">Used</th><th class="n">Price</th><th class="n">Spread</th><th class="n">Fill</th><th class="n">Net EV</th><th>Notes</th></tr>${legs}</table></div></details>` : ''}
    <details style="margin-top:8px"><summary class="meta">How the match can go: every scenario, its chance and your profit or loss</summary>${scenarioTable(i)}</details>
  </div>`;
}

function scenarioTable(i) {
  const rows = [...i.scenarios].sort((a, b) => b.prob - a.prob);
  return `<table class="sc"><tr><th>What happens</th><th class="n">Chance</th><th class="n">Profit per unit risked</th></tr>
    ${rows.map(s => `<tr><td>${esc(s.label)}</td><td class="n">${pct(s.prob)}</td><td class="n ${s.profit >= 0 ? 'pos' : 'neg'}">${spct(s.profit)}</td></tr>`).join('')}
    <tr class="sum"><td>Expected (model)</td><td class="n">100%</td><td class="n">${spct(i.expected_roi)}</td></tr></table>
    <div class="meta" style="margin-top:6px">Prices after an event are the model's estimate of where the market will trade, less a 3% allowance for spread and commission. They are not guaranteed.</div>`;
}

function planText(i, m, src) {
  return [`${m.home} v ${m.away} (${m.league_name}, ${src.weekday}${m.kickoff ? ' ' + m.kickoff : ''})`, `${i.decision} · ${i.strategy_label} — ${i.verdict}`,
    `Pays off ${pct(i.calibrated_hit_prob)} · conservative net edge ${spct(i.ev_conservative)} · stake ${money(i.stake_money)} · worst case ${spct(i.max_loss_per_unit)} of risk`, '',
    ...i.plan.map(p => `${PH[p.phase]}: ${p.text}`), '', 'Why:', ...i.rationale.map(r => `- ${r}`)].join('\n');
}

// ---------------- the bet slip and placement
async function openSlip(m, strategy, stake, src) {
  const modal = $('#modal'), card = $('#modalCard'); modal.hidden = false;
  card.innerHTML = '<div class="spinner">Building the slip and checking prices…</div>';
  try {
    const body = { date: src.date, match_id: m.id, strategy, sport: m.sport }; if (stake) body.stake_money = stake;
    const s = await post('/api/betslip/preview', body);
    const rows = s.lines.map(l => `<tr class="slipline ${l.warnings.length ? 'bad' : ''}">
      <td>${esc(l.market_label)}</td><td>${esc(l.runner_name)}</td><td>${l.side.toUpperCase()}</td>
      <td class="n">${l.plan_price.toFixed(2)}</td><td class="n">${l.live_price ? l.live_price.toFixed(2) : '-'} ${l.price_ok === true ? '<span class="ok">✓</span>' : l.price_ok === false ? '<span class="no">✗</span>' : ''}</td>
      <td class="n">${money(l.size)}</td><td class="n">${money(l.liability)}</td><td class="n">${money(l.payout)}</td>
      <td class="meta">${esc(l.note)}${l.warnings.map(w => '<div class="warn">! ' + esc(w) + '</div>').join('')}</td>
      <td>${l.betfair_url ? `<a class="btnlink" href="${esc(l.betfair_url)}" target="_blank" rel="noopener">Open in Betfair ›</a>` : '-'}</td></tr>`).join('');
    const urls = [...new Set(s.lines.map(l => l.betfair_url).filter(Boolean))];
    const nSend = s.sendable_lines || 0;
    const placeBtn = `<button id="slipPlace" class="primary place" ${s.can_place ? '' : 'disabled'}>Place ${nSend} bet${nSend === 1 ? '' : 's'} on Betfair · ${money(s.total_liability)} at risk</button>`;
    const gate = s.can_place ? `<div class="meta" style="margin-top:8px">The app calls this a <b>TRADE</b>. Pressing Place shows a final confirmation before anything is sent.</div>`
      : `<div class="banner ${s.override_allowed ? '' : 'err'}" style="margin-top:10px"><b>${s.override_allowed ? 'Placing needs your override.' : 'Why the Place button is off:'}</b>
          <ul style="margin:6px 0 0 18px">${(s.place_block_reasons || []).map(r => `<li>${esc(r)}</li>`).join('')}</ul>
          ${s.override_allowed ? `<label style="display:block;margin-top:8px"><input type="checkbox" id="slipOverride"> <b>Place anyway.</b> I understand the points above${s.decision !== 'TRADE' ? ` (the app's decision is <b>${esc(s.decision)}</b>)` : ''}, this is my own call, and it will be logged as an override.</label>` : ''}
          ${(s.place_block_reasons || []).some(r => /Settings/.test(r)) ? '<div class="row" style="margin-top:8px"><a href="#" id="slipSettings" class="btnlink">Open Settings ›</a></div>' : ''}</div>`;
    card.innerHTML = `<h2>${decBadge(s.decision)} Bet slip: ${esc(s.strategy_label)}</h2><div class="meta">${esc(s.fixture)} · ${s.date} · prices from ${s.price_source}${s.delayed ? ' (delayed key, up to 3 min old)' : ''}</div>
      ${s.decision !== 'TRADE' ? `<div class="banner" style="margin-top:8px"><b>${esc(s.decision)}.</b> ${esc((s.decision_reasons || []).join(' '))}</div>` : ''}
      <div class="row" style="margin:10px 0"><label class="meta">Plan stake (unit risked) £ <input id="slipStake" type="number" min="1" step="1" value="${s.stake_money}" style="width:90px"></label><button id="slipRecalc" class="small">Recalculate</button><span class="meta">advised ${money(s.stake_advised || 0)} · minimum placeable ${money(s.stake_floor)}</span></div>
      ${s.stake_note ? `<div class="warn" style="margin:0 0 8px">! ${esc(s.stake_note)}</div>` : ''}
      <div class="wrap"><table class="sc"><tr><th>Market</th><th>Selection</th><th>Side</th><th class="n">Plan price</th><th class="n">Live best</th><th class="n">Size</th><th class="n">Risk</th><th class="n">Wins</th><th>Notes</th><th></th></tr>${rows}
        <tr class="sum"><td colspan="5">Total</td><td class="n">${money(s.total_staked)} staked</td><td class="n">${money(s.total_liability)} at risk</td><td></td><td></td><td></td></tr></table></div>
      ${s.warnings.map(w => `<div class="warn" style="margin-top:6px">! ${esc(w)}</div>`).join('')}
      <p class="meta" style="margin-top:10px"><b>Size</b> is the backer's stake to enter on Betfair for each line. <b>Live best</b> ✓ means the plan price is available now; ✗ means it is not, so the order rests at the plan price and lapses at the start if unmatched.</p>
      ${gate}
      <div class="row" style="margin-top:12px">
        ${placeBtn}
        ${urls.length ? `<button id="slipOpenAll" class="${s.can_place ? '' : 'primary'}">Open all markets in Betfair (${urls.length})</button>` : '<button class="primary" disabled title="Connect Betfair in Settings">Open in Betfair</button>'}
        <button id="slipPaper">Record as paper bet</button><button id="slipCopy">Copy slip</button><button id="slipClose" class="ghost">Close</button>
      </div>
      <div class="meta" style="margin-top:8px">${s.betting_mode === 'live' ? `Live betting is ON. Daily cap £${status.daily_cap}, £${(status.committed_today || 0).toFixed(2)} committed today.` : s.betting_mode === 'paper' ? 'Paper mode: slips are recorded, nothing is sent to Betfair.' : 'Betting mode is Off: review and copy only.'}</div>
      <div id="slipConfirm"></div>`;
    $('#slipClose').onclick = () => modal.hidden = true;
    $('#slipRecalc').onclick = () => openSlip(m, strategy, +$('#slipStake').value, src);
    $('#slipStake').oninput = () => { if (+$('#slipStake').value !== s.stake_money) { $('#slipPlace').disabled = true; $('#slipPlace').title = 'Press Recalculate to rebuild the slip at this stake'; } };
    if ($('#slipSettings')) $('#slipSettings').onclick = (ev) => { ev.preventDefault(); modal.hidden = true; showView('settings'); };
    if (urls.length) $('#slipOpenAll').onclick = () => { urls.forEach((u, k) => setTimeout(() => window.open(u, '_blank', 'noopener'), k * 150)); };
    if ($('#slipOverride')) $('#slipOverride').onchange = () => { $('#slipPlace').disabled = !$('#slipOverride').checked; };
    $('#slipCopy').onclick = async () => {
      const txt = [`${s.decision} · ${s.fixture} · ${s.strategy_label} · stake £${s.stake_money}`, ...s.lines.map(l => `${l.side.toUpperCase()} ${l.runner_name} (${l.market_label}) @ ${l.plan_price.toFixed(2)} size £${l.size.toFixed(2)} risk £${l.liability.toFixed(2)}`)].join('\n');
      try { await navigator.clipboard.writeText(txt); toast('Slip copied.'); } catch (e) { toast('Copy failed: ' + e.message); }
    };
    $('#slipPaper').onclick = async () => {
      $('#slipPaper').disabled = true;
      try { await post('/api/betslip/paper', { ...body, stake_money: s.stake_money });
        const idea = m.ideas.find(x => x.strategy === strategy); if (idea) idea.tracked = true; await loadStatus(); modal.hidden = true; toast('Recorded as a paper bet in My picks.'); showMatch(m.id, src); }
      catch (e) { $('#slipPaper').disabled = false; toast('Could not record: ' + e.message, 6000); }
    };
    $('#slipPlace').onclick = () => {
      if ($('#slipPlace').disabled) return;
      const override = !!($('#slipOverride') && $('#slipOverride').checked);
      const sendable = s.lines.filter(l => l.market_id && !l.below_minimum && !l.blocked);
      $('#slipConfirm').innerHTML = `<div class="banner err" style="margin-top:10px"><b>Confirm: send ${sendable.length} order${sendable.length === 1 ? '' : 's'} to Betfair now?</b>${override ? ' <span class="warn">(override: the app did not call this a TRADE)</span>' : ''}
        <ul style="margin:6px 0 6px 18px">${sendable.map(l => `<li>${l.side.toUpperCase()} ${esc(l.runner_name)} (${esc(l.market_label)}) at ${l.plan_price.toFixed(2)}, size ${money(l.size)}, risk ${money(l.liability)}</li>`).join('')}</ul>
        Total at risk ${money(sendable.reduce((a, l) => a + l.liability, 0))}. Limit orders at the plan price, lapsing at the start if unmatched. This uses real money.
        <div class="row" style="margin-top:8px"><button id="slipGo" class="primary place">Yes, place the bets</button><button id="slipNo" class="ghost">No, go back</button></div></div>`;
      $('#slipNo').onclick = () => $('#slipConfirm').innerHTML = '';
      $('#slipGo').onclick = async () => {
        $('#slipGo').disabled = true; $('#slipGo').textContent = 'Sending to Betfair…';
        try {
          const r = await post('/api/betslip/place', { ...body, stake_money: s.stake_money, confirm: true, override });
          const res = r.result;
          $('#slipConfirm').innerHTML = `<div class="banner ${res.ok ? '' : 'err'}" style="margin-top:10px"><b>${esc(res.message)}</b>
            <table class="sc" style="margin-top:6px"><tr><th>Line</th><th class="n">Price</th><th class="n">Size</th><th>Status</th><th class="n">Matched</th><th>Bet id</th></tr>
            ${res.lines.map(l => `<tr><td>${l.side.toUpperCase()} ${esc(l.runner_name)} (${esc(l.market_label)})</td><td class="n">${l.price.toFixed(2)}</td><td class="n">${money(l.size)}</td><td class="${l.status === 'SUCCESS' ? 'pos' : 'neg'}">${l.status}${l.order_status ? ' · ' + (l.order_status === 'EXECUTION_COMPLETE' ? 'matched' : 'waiting for price') : ''}${l.error ? ' · ' + esc(l.error) : ''}</td><td class="n">${l.size_matched ? money(l.size_matched) : '-'}</td><td class="meta">${l.bet_id || '-'}</td></tr>`).join('')}</table>
            <div class="row" style="margin-top:8px"><button id="slipDone" class="small">Close</button><button id="slipOrders" class="small ghost">See open orders</button></div></div>`;
          $('#slipDone').onclick = () => { modal.hidden = true; showMatch(m.id, src); };
          $('#slipOrders').onclick = () => { modal.hidden = true; showView('journal'); };
          if (res.ok || res.pending) { const idea = m.ideas.find(x => x.strategy === strategy); if (idea) idea.tracked = true; await loadStatus(); toast(res.ok ? 'Bets placed and logged in My picks.' : 'Check open orders: Betfair did not confirm.', 6000); }
        } catch (e) { $('#slipConfirm').innerHTML = `<div class="banner err" style="margin-top:10px"><b>Not placed.</b> ${esc(e.message)}</div>`; }
      };
    };
  } catch (e) { card.innerHTML = `<h2>Bet slip</h2><div class="warn">! ${esc(e.message)}</div><div class="row" style="margin-top:10px"><button id="slipClose" class="ghost">Close</button></div>`; $('#slipClose').onclick = () => modal.hidden = true; }
}

function wireIdeaButtons(m, src) {
  document.querySelectorAll('[data-slip]').forEach(b => b.onclick = (ev) => { ev.stopPropagation(); openSlip(m, b.dataset.slip, null, src); });
  document.querySelectorAll('[data-track]').forEach(b => b.onclick = async (ev) => {
    ev.stopPropagation(); b.disabled = true; b.textContent = 'Saving…';
    try { await post('/api/journal', { date: src.date, match_id: m.id, strategy: b.dataset.track, sport: m.sport });
      const idea = m.ideas.find(x => x.strategy === b.dataset.track); if (idea) idea.tracked = true; b.textContent = 'Tracked ✓'; b.classList.add('ghost');
      await loadStatus(); toast('Added to My picks. It will settle itself when the result is in.');
    } catch (e) { b.disabled = false; b.textContent = '+ Track'; toast('Could not track: ' + e.message, 6000); }
  });
  document.querySelectorAll('[data-copy]').forEach(b => b.onclick = async (ev) => {
    ev.stopPropagation(); const idea = m.ideas.find(x => x.strategy === b.dataset.copy);
    try { await navigator.clipboard.writeText(planText(idea, m, src)); toast('Plan copied to clipboard.'); } catch (e) { toast('Copy failed: ' + e.message); }
  });
}

function allIdeas(src) { return src ? src.matches.flatMap(m => m.ideas.map(i => ({ ...i, m, src }))) : []; }

function pickRow(i, k) {
  const m = i.m;
  return `<div class="card pick" data-id="${esc(m.id)}" data-sport="${esc(m.sport)}">
    <div>${decBadge(i.decision)}<div style="margin-top:4px">${starsHtml(i.stars)}</div><div class="s">score ${Math.round(i.score)}</div></div>
    <div><div class="t">${k + 1}. ${esc(m.home)} v ${esc(m.away)} <span class="s">· ${m.sport === 'tennis' ? '🎾 ' : '⚽ '}${esc(m.league_name)} · ${m.kickoff || ''}</span> ${priceChip(m)}</div>
      <div><b>${esc(i.strategy_label)}</b> <span class="evidence">${esc(EVID[i.evidence] || i.evidence)}</span></div>
      <div class="s">${esc(i.verdict)}</div>
      <div class="s">${esc(i.plan[0].text)}</div>
      <div class="s">Pays off ${pct(i.calibrated_hit_prob)} · net edge ${spct(i.ev_conservative)} · market ${pct(i.p_market)} · confidence ${i.confidence.toFixed(2)} · stake ${i.stake_money ? money(i.stake_money) : 'none'} · worst ${spct(i.max_loss_per_unit)}${i.settled ? ` · <b class="${i.settled.hit >= 0.5 ? 'pos' : 'neg'}">${i.settled.hit >= 0.5 ? 'paid off' : 'missed'} ${spct(i.settled.pnl)}</b>` : ''}</div></div>
    <div class="actions"><button class="small primary">Open plan ›</button>${i.decision === 'TRADE' && status.betting_mode === 'live' && status.betfair ? '<div class="s" style="margin-top:4px">placeable</div>' : ''}${i.tracked ? '<span class="s">Tracked ✓</span>' : ''}</div></div>`;
}

// Why is nothing a TRADE today? Tally the reasons across every idea so the answer is on screen, not buried in cards.
function whyNoTrade(src) {
  const ideas = allIdeas(src); if (!ideas.length) return null;
  const tally = {}, bump = (k) => tally[k] = (tally[k] || 0) + 1;
  let priced = 0, best = null;
  for (const i of ideas) {
    if (i.decision === 'TRADE') continue;
    if (i.decision === 'RESEARCH') { const st = i.m.price_status; bump(st === 'no_event' ? 'fixture not matched to an exchange event' : st === 'no_markets' ? 'exchange has not priced the markets yet' : st === 'error' ? 'price feed error' : st === 'inplay' ? 'match already in play' : src.date < status.today ? 'past day: pre-match markets are gone' : 'no exchange price'); continue; }
    priced++;
    if (i.ev_conservative != null && (best == null || i.ev_conservative > best.ev_conservative)) best = i;
    const txt = (i.decision_reasons || []).join(' '); let any = false;
    if (i.m.price_status === 'inplay' || /^The match has started/.test(txt)) { bump('match already in play'); any = true; }
    if (/No reliable exchange price/i.test(txt)) { bump('no reliable market price yet (empty market; check nearer kick-off)'); any = true; }
    if (/below/i.test(txt)) { bump(/plan structure costs/i.test(txt) ? 'edge below the threshold once in-play exit costs are charged' : 'edge below the threshold after commission'); any = true; }
    if (/not on offer right now/i.test(txt)) { bump('plan price not on offer yet (order would rest until kick-off; not a blocker)'); }
    if (/matched so far: thin now/i.test(txt)) { bump('thin now (not a blocker: fills by kick-off if the price comes)'); }
    if (/too old/i.test(txt)) { bump('prices too old'); any = true; }
    if (!any) bump('other');
  }
  return { tally, priced, best, total: ideas.length, trades: ideas.filter(i => i.decision === 'TRADE').length };
}
function diagnosisText(src) {
  const w = whyNoTrade(src) || {}; const f = src.feed || {}; const bf = src.betfair || {};
  return [`TradeScout diagnosis · ${src.sport} · ${src.date} · generated ${src.generated}`,
    `Betfair: ${bf.configured ? (bf.connected ? 'connected' : 'NOT connected: ' + (bf.error || '')) : 'not set up'}${bf.delayed ? ' · delayed key' : ''}`,
    `Fixtures ${src.fixtures}, priced ${src.priced}, exchange events ${f.events_on_day ?? '-'}, matched ${f.matched ?? '-'}, unmatched ${(f.unmatched || []).length}, in play ${(f.inplay || []).length}, suspended ${(f.suspended || []).length}${f.error ? ', feed error: ' + f.error : ''}`,
    ...(f.unmatched || []).slice(0, 10).map(u => `  unmatched: ${u.fixture} -> ${u.reason}`),
    `Ideas ${w.total}: TRADE ${w.trades}, priced NO TRADE ${w.priced}; settings: min edge ${(status.min_edge * 100).toFixed(1)}%, max spread ${(status.max_spread * 100).toFixed(0)}%, commission ${(status.commission * 100).toFixed(1)}%, mode ${status.betting_mode}`,
    ...Object.entries(w.tally || {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => `  ${n} x ${k}`),
    ...src.matches.filter(m => m.price_status !== 'ok').slice(0, 8).map(m => `  ${m.price_status.toUpperCase()} ${m.home} v ${m.away} (${m.kickoff || '?'}) -> ${m.price_note}` + (m.raw_markets || []).map(r => `\n      ${r.type}: status ${r.status}, inplay ${r.inplay}, delay ${r.bet_delay}, start ${r.start}, matched ${r.matched}, priced runners ${r.runners_priced}/${r.runners}`).join('') + (m.price_flags || []).map(f => `\n      flag: ${f}`).join('')),
    w.best ? `Closest to a trade: ${w.best.m.home} v ${w.best.m.away} · ${w.best.strategy_label} · conservative edge ${spct(w.best.ev_conservative)} (model ${spct(w.best.ev_model)}) · market implies ${pct(w.best.p_market)} · ${(w.best.decision_reasons || []).join(' ')}` : '',
    ...(src.skipped || []).slice(0, 5).map(x => `  error: ${x}`)].filter(Boolean).join('\n');
}
function whyPanel(src) {
  const w = whyNoTrade(src); if (!w) return '';
  const rows = Object.entries(w.tally).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<li><b>${n}</b> × ${esc(k)}</li>`).join('');
  const threshold = `${(status.min_edge * 100).toFixed(1)}%`;
  return `<details class="card why" ${w.trades ? '' : 'open'}><summary><b>${w.trades ? w.trades + ' TRADE' + (w.trades === 1 ? '' : 'S') + ' today.' : 'Why is nothing a TRADE today?'}</b> <span class="meta">${w.priced} ideas priced by the exchange, ${w.total - w.priced - w.trades} without a usable price. A TRADE needs a conservative net edge of at least ${threshold} after commission at the plan price. Thin money and wide spreads now are noted, not blockers: orders rest until kick-off and exits happen in play.</span></summary>
    <div class="meta">Reasons across the priced ideas (one idea can fail for more than one reason):</div><ul style="margin:6px 0 6px 18px">${rows}</ul>
    ${w.best ? `<div class="meta">Closest to a trade: <b>${esc(w.best.m.home)} v ${esc(w.best.m.away)}</b> · ${esc(w.best.strategy_label)} · conservative edge <b>${spct(w.best.ev_conservative)}</b> (the raw model says ${spct(w.best.ev_model)}; the market implies ${pct(w.best.p_market)}). ${esc((w.best.decision_reasons || [])[0] || '')}</div>` : ''}
    <div class="meta" style="margin-top:6px">The model only gets a small say against the exchange (15-30% by market), so a TRADE needs a clear mispricing. You can lower the threshold in Settings > "Minimum conservative net edge", place any plan yourself with the override on its slip, or rank by conservative edge above to see the nearest misses.</div>
    <div class="row" style="margin-top:8px"><button class="small" id="copyDiag">Copy diagnosis</button><span class="meta">copies the feed state, the counts above and your settings as text, ready to paste.</span></div>
  </details>`;
}

function renderPicks() {
  if (!data || !data.matches.length) return;
  const mode = $('#picksMode').value, perMatch = $('#picksPerMatch').checked, sortBy = $('#picksSort').value, n = +$('#picksCount').value;
  let ideas = allIdeas(data);
  if ($('#picksBoth').checked && dataOther) ideas = ideas.concat(allIdeas(dataOther));
  if (mode === 'trade') ideas = ideas.filter(i => i.decision === 'TRADE');
  if (mode === 'priced') ideas = ideas.filter(i => i.decision !== 'RESEARCH');
  const key = { score: (i) => i.score, ev: (i) => i.ev_conservative ?? -9, hit: (i) => i.calibrated_hit_prob }[sortBy];
  const rank = { 'TRADE': 2, 'NO TRADE': 1, 'RESEARCH': 0 };
  ideas.sort((a, b) => (rank[b.decision] - rank[a.decision]) || (key(b) - key(a)));
  if (perMatch) { const seen = new Set(); ideas = ideas.filter(i => !seen.has(i.m.sport + i.m.id) && seen.add(i.m.sport + i.m.id)); }
  ideas = ideas.slice(0, n);
  const trades = allIdeas(data).filter(i => i.decision === 'TRADE').length + (dataOther ? allIdeas(dataOther).filter(i => i.decision === 'TRADE').length : 0);
  const cfg = status.betfair_configured || (data.betfair && data.betfair.configured);
  let head = `<div class="meta" style="margin-bottom:10px">${data.weekday}: <b>${trades} TRADE</b> decision${trades === 1 ? '' : 's'} across ${data.fixtures}${dataOther ? ' + ' + dataOther.fixtures : ''} fixtures. ${trades === 0 ? 'NO TRADE today: no idea clears the conservative edge threshold at an available price.' : ''}${!cfg ? ' No exchange prices are connected, so nothing can be a TRADE: the list below is model research only.' : data.priced === 0 ? ' No exchange prices were attached to this day (see the price feed details at the top right), so nothing can be a TRADE.' : ''}</div>`;
  if (!ideas.length) { $('#picks').innerHTML = head + whyPanel(data) + `<div class="empty">Nothing to show in this view. ${mode === 'trade' ? 'Switch the filter to see priced NO TRADE ideas or research.' : ''}</div>`; if ($('#copyDiag')) $('#copyDiag').onclick = async () => { try { await navigator.clipboard.writeText(diagnosisText(data)); toast('Diagnosis copied: paste it into a message.'); } catch (e) { toast('Copy failed: ' + e.message); } }; return; }
  const settledOnes = ideas.filter(i => i.settled);
  if (settledOnes.length) head += `<div class="result" style="margin-bottom:10px">Replay: <b>${settledOnes.reduce((a, i) => a + i.settled.hit, 0).toFixed(1)} of ${settledOnes.length}</b> shown ideas paid off (expected ${settledOnes.reduce((a, i) => a + i.calibrated_hit_prob, 0).toFixed(1)}), ${spct(settledOnes.reduce((a, i) => a + i.settled.pnl, 0) / settledOnes.length)} per unit risked on average at model prices.</div>`;
  $('#picks').innerHTML = head + whyPanel(data) + ideas.map((i, k) => pickRow(i, k)).join('');
  document.querySelectorAll('#picks .pick').forEach(el => el.onclick = () => { const src = el.dataset.sport === sport ? data : dataOther; showMatch(el.dataset.id, src); });
  if ($('#copyDiag')) $('#copyDiag').onclick = async () => { try { await navigator.clipboard.writeText(diagnosisText(data)); toast('Diagnosis copied: paste it into a message.'); } catch (e) { toast('Copy failed: ' + e.message); } };
}

function renderStrategy() {
  const key = $('#stratSelect').value; const s = strategies.find(x => x.key === key); if (!s) return;
  const h = s.holdout;
  $('#stratInfo').innerHTML = `<h2>${esc(s.label)} ${s.enabled ? '' : '<span class="evidence">disabled</span>'}</h2><p>${esc(s.description)}</p><p><b>Use it when:</b> ${esc(s.best_for)}</p><p><b>Avoid when:</b> ${esc(s.avoid_when)}</p>
    ${h ? `<p class="meta"><b>Out-of-sample (holdout season, model prices):</b> ${h.n} trades · strike ${pct(h.strike)} vs predicted ${pct(h.predicted)} · ROI ${spct(h.roi)} [${spct(h.roi_ci_low)}, ${spct(h.roi_ci_high)}] · profit factor ${h.profit_factor.toFixed(2)} · max drawdown ${h.max_drawdown.toFixed(1)} units · longest losing run ${h.longest_losing_streak} · evidence: ${esc(h.evidence)}</p>` : '<p class="meta">No holdout statistics yet (run the holdout evaluation).</p>'}`;
  if (!data || !data.matches.length) { $('#stratList').innerHTML = ''; return; }
  const ideas = allIdeas(data).filter(i => i.strategy === key).sort((a, b) => b.score - a.score);
  $('#stratList').innerHTML = ideas.length ? ideas.map((i, k) => pickRow(i, k)).join('') : `<div class="empty">No match on ${data.weekday} passes the entry rules for this strategy${s.enabled ? '' : ' (it is disabled in Settings)'}.</div>`;
  document.querySelectorAll('#stratList .pick').forEach(el => el.onclick = () => showMatch(el.dataset.id));
}

// ---------------- journal
function renderJournal(j) {
  const s = j.summary;
  $('#journalSummary').innerHTML = `
    <div class="stat"><b>${s.picks}</b><span>picks tracked</span></div>
    <div class="stat"><b>${s.open}</b><span>waiting for results</span></div>
    <div class="stat"><b>${s.settled ? pct(s.strike) : '-'}</b><span>strike rate (${s.won}/${s.settled})</span></div>
    <div class="stat"><b class="${s.pnl >= 0 ? 'pos' : 'neg'}">${money(s.pnl)}</b><span>profit / loss</span></div>
    <div class="stat"><b>${s.roi == null ? '-' : spct(s.roi)}</b><span>return on £${s.staked} staked</span></div>`;
  if (!j.entries.length) { $('#journalList').innerHTML = '<div class="empty">No picks tracked yet. Open a plan and press <b>+ Track</b>.</div>'; return; }
  $('#journalList').innerHTML = `<div class="wrap"><table class="j"><tr><th>Date</th><th>Match</th><th>Strategy</th><th>Decision</th><th class="n">Pays off</th><th class="n">Edge</th><th class="n">Entry</th><th class="n">Stake</th><th>Status</th><th>Result</th><th class="n">P/L</th><th></th></tr>
    ${j.entries.map(e => `<tr><td>${e.date}</td><td>${e.sport === 'tennis' ? '🎾 ' : '⚽ '}${esc(e.home)} v ${esc(e.away)}<div class="s meta">${esc(((status.leagues || {})[e.sport] || {})[e.league] || e.league)}</div></td><td>${esc(e.strategy_label)}${e.note && e.note.includes('override') ? ' <span class="evidence" title="placed by you against the app\'s decision">override</span>' : ''}</td>
      <td>${e.decision ? decBadge(e.decision) : '-'}</td><td class="n">${pct(e.hit_prob)}</td><td class="n">${spct(e.ev_conservative)}</td><td class="n">${price(e.entry_price)}</td><td class="n">${money(e.stake_money)}</td>
      <td class="status-${e.status}">${e.status}${e.placed === 'live' ? ' <span class="count">LIVE</span>' : e.placed === 'paper' ? ' <span class="meta">paper</span>' : ''}</td><td>${esc(e.result || '-')}${e.note && e.note.includes('modelled') ? ' <span class="meta" title="in-play exits settled at modelled prices">(modelled)</span>' : ''}</td><td class="n ${e.pnl_money == null ? '' : e.pnl_money >= 0 ? 'pos' : 'neg'}">${e.pnl_money == null ? '-' : money(e.pnl_money)}</td>
      <td><button class="small ghost danger" data-del="${e.id}" title="Remove">✕</button></td></tr>`).join('')}</table></div>`;
  document.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => { await api('/api/journal/' + b.dataset.del, { method: 'DELETE' }); await loadJournal(); await loadStatus(); });
}
async function loadJournal() { try { renderJournal(await api('/api/journal')); } catch (e) { toast('Could not load picks: ' + e.message); } loadOpenBets(); }
async function settleJournal() {
  const b = $('#settleBtn'); b.disabled = true; b.textContent = 'Checking results…';
  try { const r = await post('/api/journal/settle'); renderJournal(r); await loadStatus(); toast(r.settled ? `${r.settled} pick(s) settled.` : 'No new results yet.'); }
  catch (e) { toast('Could not update: ' + e.message, 6000); }
  b.disabled = false; b.textContent = 'Update results';
}
async function loadOpenBets() {
  const el = $('#openBets');
  if (!status.betfair_configured) { el.innerHTML = '<div class="meta">Connect Betfair in Settings to see open orders.</div>'; return; }
  el.innerHTML = '<div class="spinner">Loading…</div>';
  try {
    const r = await api('/api/bets/open');
    if (!r.ok) { el.innerHTML = `<div class="warn">! ${esc(r.error)}</div>`; return; }
    if (!r.orders.length) { el.innerHTML = '<div class="meta">No open orders from this app.</div>'; return; }
    el.innerHTML = `<table class="j"><tr><th>Placed</th><th>Market</th><th>Side</th><th class="n">Price</th><th class="n">Size</th><th class="n">Matched</th><th class="n">Waiting</th><th>Status</th><th></th></tr>
      ${r.orders.map(o => `<tr><td>${(o.placed || '').replace('T', ' ').slice(0, 16)}</td><td><a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.market_id)}</a> · sel ${o.selection_id}</td><td>${o.side}</td><td class="n">${o.price}</td><td class="n">${money(o.size || 0)}</td><td class="n">${money(o.matched || 0)}</td><td class="n">${money(o.remaining || 0)}</td><td>${o.status}</td>
        <td>${(o.remaining || 0) > 0 ? `<button class="small ghost danger" data-cancel="${esc(o.market_id)}" data-bet="${esc(o.bet_id)}">Cancel unmatched</button>` : ''}</td></tr>`).join('')}</table>`;
    document.querySelectorAll('[data-cancel]').forEach(b => b.onclick = async () => {
      if (!confirm('Cancel the unmatched part of this order?')) return;
      try { await post('/api/bets/cancel', { market_id: b.dataset.cancel, bet_ids: [b.dataset.bet] }); toast('Cancelled.'); loadOpenBets(); }
      catch (e) { toast('Cancel failed: ' + e.message, 6000); }
    });
  } catch (e) { el.innerHTML = `<div class="warn">! ${esc(e.message)}</div>`; }
}

// ---------------- performance & bankroll
async function loadPerformance() {
  try {
    const r = await api('/api/stats'); const st = r.strategy_stats || {};
    $('#perfNote').textContent = st.evidence_note || 'No statistics file yet.';
    let html = '';
    for (const sp of ['football', 'tennis']) {
      const h = (st[sp] || {}).holdout; if (!h) continue;
      const rows = Object.entries(h.by_strategy || {}).sort((a, b) => b[1].roi - a[1].roi).map(([k, v]) => {
        const label = (sp === sport ? strategies.find(x => x.key === k) : null); return `<tr><td>${esc(label ? label.label : k)}</td><td class="n">${v.n}</td><td class="n">${pct(v.strike)}</td><td class="n">${pct(v.predicted)}</td><td class="n ${v.roi >= 0 ? 'pos' : 'neg'}">${spct(v.roi)}</td><td class="n">${spct(v.roi_ci_low)} … ${spct(v.roi_ci_high)}</td><td class="n">${v.profit_factor.toFixed(2)}</td><td class="n">${v.max_drawdown.toFixed(1)}</td><td class="n">${v.longest_losing_streak}</td><td class="meta">${esc(v.evidence)}</td></tr>`; }).join('');
      html += `<div class="card"><h2>${sp === 'tennis' ? '🎾 Tennis' : '⚽ Football'} · holdout ${h.window[0]} to ${h.window[1]}</h2><div class="wrap"><table class="sc"><tr><th>Strategy</th><th class="n">Trades</th><th class="n">Strike</th><th class="n">Predicted</th><th class="n">ROI/unit</th><th class="n">95% CI</th><th class="n">Profit factor</th><th class="n">Max DD (units)</th><th class="n">Losing run</th><th>Evidence</th></tr>${rows}</table></div>
        <p class="meta">Strike vs predicted tests calibration. ROI is at the model's own fair prices less friction, so it is a test of the plan structure, not of edge against the exchange.</p></div>`;
    }
    $('#perfTables').innerHTML = html || '<div class="card meta">Run <code>python -m tradescout.eval.holdout</code> to produce out-of-sample statistics.</div>';
    const sg = r.signals || {};
    $('#signalsSummary').innerHTML = sg.total ? `${sg.total} signals recorded · decisions ${esc(JSON.stringify(sg.by_decision))} · TRADE signals ${sg.trade_signals}, settled ${sg.trade_settled}, hits ${sg.trade_hits}, P/L ${sg.trade_pnl.toFixed(2)} units. This is the forward record: it only grows while the app scans live days with exchange prices.` : 'No signals yet. The log fills in as the app scans live days; with Betfair connected it records the prices seen, which is the only way to prove edge against the market.';
  } catch (e) { $('#perfTables').innerHTML = `<div class="warn">! ${esc(e.message)}</div>`; }
}
async function loadBankroll() {
  try {
    await loadStatus(); const ex = status.exposure, L = status.limits;
    $('#bankSummary').innerHTML = `
      <div class="stat"><b>£${status.bank}</b><span>bank (Settings)</span></div>
      <div class="stat"><b>£${ex.open_total.toFixed(2)}</b><span>open risk (${(100 * ex.open_total / status.bank).toFixed(1)}% of bank, cap ${(L.max_open_exposure * 100).toFixed(0)}%)</span></div>
      <div class="stat"><b class="${ex.realised_today >= 0 ? 'pos' : 'neg'}">${money(ex.realised_today)}</b><span>realised today (limit -${(L.daily_loss_limit * 100).toFixed(0)}%)</span></div>
      <div class="stat"><b class="${ex.realised_week >= 0 ? 'pos' : 'neg'}">${money(ex.realised_week)}</b><span>realised this week (limit -${(L.weekly_loss_limit * 100).toFixed(0)}%)</span></div>
      <div class="stat"><b>${pct(ex.drawdown)}</b><span>drawdown from peak (stakes halve above ${(L.drawdown_halve * 100).toFixed(0)}%)</span></div>`;
    $('#limitsList').innerHTML = `<ul><li>Fractional Kelly: ${(L.kelly_fraction * 100).toFixed(0)}% of full Kelly on the conservative probability</li><li>Max per trade: ${(L.max_per_trade * 100).toFixed(0)}% of bank</li><li>Max open exposure: ${(L.max_open_exposure * 100).toFixed(0)}%</li><li>Max per strategy: ${(L.max_per_strategy * 100).toFixed(0)}% · per sport: ${(L.max_per_sport * 100).toFixed(0)}%</li><li>Daily loss limit: ${(L.daily_loss_limit * 100).toFixed(0)}% · weekly: ${(L.weekly_loss_limit * 100).toFixed(0)}%</li><li>Exchange minimum stake £${L.min_stake}</li></ul><div>By strategy: ${esc(JSON.stringify(ex.by_strategy))} · by sport: ${esc(JSON.stringify(ex.by_sport))}</div>`;
    const r = await api('/api/risk?p=0.55&win=1&loss=-1'); const ro = r.risk_of_ruin;
    $('#ruin').innerHTML = `Risking ${(ro.fraction * 100).toFixed(2)}% per trade: probability of a 50% drawdown within 500 trades <b>${pct(ro.p_ruin)}</b>; median bank after 500 trades <b>${(ro.median_final * 100).toFixed(0)}%</b> of start; 5th percentile <b>${(ro.p5_final * 100).toFixed(0)}%</b>; chance of ending below start <b>${pct(ro.p_loss)}</b>.`;
  } catch (e) { $('#ruin').innerHTML = `<div class="warn">! ${esc(e.message)}</div>`; }
}

// ---------------- settings
async function loadSettings() {
  try {
    const s = await api('/api/settings');
    $('#fdKey').value = ''; $('#fdKey').placeholder = s.has_football_key ? `saved: ${s.football_data_org_key}  (paste a new one to replace)` : 'paste the code from the email';
    $('#bfKey').value = ''; $('#bfKey').placeholder = s.betfair_app_key ? `saved: ${s.betfair_app_key}` : 'e.g. aBcDeFgHiJkLmNoP';
    $('#bfUser').value = s.betfair_username || ''; $('#bfPass').value = ''; $('#bfPass').placeholder = s.has_betfair_password ? 'saved (type to replace)' : '';
    $('#bfJurisdiction').value = s.betfair_jurisdiction || 'com'; $('#bfCert').value = s.betfair_cert_file || ''; $('#bfKeyFile').value = s.betfair_key_file || '';
    $('#bank').value = s.bank; $('#kelly').value = String(s.kelly_fraction); $('#commission').value = (s.commission * 100).toFixed(1); $('#minEdge').value = (s.min_edge * 100).toFixed(1); $('#modelWeight').value = String(s.model_weight_scale || 1);
    $('#betMode').value = s.betting_mode || 'off'; $('#dailyCap').value = s.daily_cap;
    const bf = s.betfair || {};
    $('#lightFixtures').className = 'light ' + (status.live_fixtures ? 'on' : ''); $('#lightBetfair').className = 'light ' + (bf.connected ? 'on' : bf.configured ? 'warn' : ''); $('#lightBetting').className = 'light ' + (s.betting_mode === 'live' ? 'on' : '');
    const h = bf.health || {};
    $('#bfState').innerHTML = !bf.configured ? 'Not set up.' : bf.connected ? `<span class="pos">Connected.</span> ${h.delayed ? 'Delayed application key (prices up to 3 minutes old; orders still go through).' : h.delayed === false ? 'Live application key.' : ''} Logins ${h.logins || 0}, API calls ${h.calls || 0}, last OK ${h.last_ok ? hhmm(h.last_ok) + ' UTC' : '-'}.` : `<span class="neg">Not connected.</span> ${esc(bf.error || 'Connecting…')}`;
    $('#bettingInfo').textContent = s.betting_mode === 'live' ? `Live: £${(s.committed_today || 0).toFixed(2)} of £${s.daily_cap} committed today.` + (bf.connected ? '' : ' Betfair is not connected, so nothing can be placed.') : '';
    const [fb, tn] = await Promise.all([api('/api/strategies?sport=football'), api('/api/strategies?sport=tennis')]);
    $('#stratToggles').innerHTML = [...fb.map(x => ({ ...x, sp: '⚽' })), ...tn.map(x => ({ ...x, sp: '🎾' }))].map(x => `<label class="togglerow"><input type="checkbox" value="${esc(x.key)}" ${x.enabled ? 'checked' : ''}> <span>${x.sp} <b>${esc(x.label)}</b> <span class="evidence">${esc(x.settlement)}</span>${x.holdout ? ` <span class="meta">holdout: ${x.holdout.n} trades, strike ${pct(x.holdout.strike)}, ROI ${spct(x.holdout.roi)}</span>` : ''}${x.enabled_default ? '' : ' <span class="meta">(off by default)</span>'}</span></label>`).join('');
    const lr = status.live_results || {};
    $('#dataInfo').innerHTML = `Football seasons loaded: ${status.seasons.join(', ')} (openfootball, public domain). Tennis results to ${status.tennis_data_to || '?'} (TML-Database mirror of the Sackmann layout, research use; a commercial release needs a licensed feed).<br>Live results cache: ${lr.state}${lr.updated ? ' (updated ' + lr.updated + ')' : ''}.<br>Settings file: <code>${esc(s.env_path)}</code> (keep it private).` + (bf.error ? `<br><span class="warn">Betfair: ${esc(bf.error)}</span>` : '');
  } catch (e) { toast('Could not load settings: ' + e.message); }
}
async function saveSettings(body, resultSel) {
  const el = $(resultSel); el.textContent = 'Saving…';
  try {
    const r = await post('/api/settings', body);
    status = r.status; renderPills(); await loadSettings(); el.textContent = 'Saved.'; autoJumped = false; warnedBetfair = false;
    await setSport(sport);
  } catch (e) { el.textContent = 'Error: ' + e.message; }
}
async function testConn(path, resultSel, okText) {
  const el = $(resultSel); el.textContent = 'Testing…';
  try { const r = await post(path); el.textContent = r.ok ? okText(r) : 'Failed: ' + r.error; el.className = 'meta ' + (r.ok ? 'pos' : 'neg'); if (r.ok) { await loadStatus(); loadSettings(); } }
  catch (e) { el.textContent = 'Failed: ' + e.message; }
}

init();
