const $ = (s) => document.querySelector(s);
const pct = (v) => v == null ? '-' : Math.round(v * 100) + '%';
const spct = (v) => v == null ? '-' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
const price = (v) => v == null ? '-' : v.toFixed(2);
const money = (v) => (v < 0 ? '-£' : '£') + Math.abs(v).toFixed(2);
const starsHtml = (n) => `<span class="stars ${n <= 1 ? 'dim' : ''}" title="${n} out of 5">${'★'.repeat(n)}${'☆'.repeat(5 - n)}</span>`;
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const PH = { entry: 'Before kick-off', inplay: 'In play', exit: 'Get out', stop: 'Stop loss', note: 'Note' };

let data = null, strategies = [], selected = null, status = {}, calendar = {}, autoJumped = false, warnedBetfair = false;

function toast(msg, ms = 4000) { const t = $('#toast'); t.textContent = msg; t.style.display = 'block'; clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', ms); }
function isoShift(iso, days) { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + days); return d.toISOString().slice(0, 10); }
function showView(name) {
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x.dataset.view === name));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  if (name === 'picks') renderPicks(); if (name === 'strategies') renderStrategy(); if (name === 'journal') loadJournal(); if (name === 'settings') loadSettings();
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  let body = null; try { body = await r.json(); } catch (e) { }
  if (!r.ok) throw new Error((body && body.detail) || r.statusText);
  return body;
}

async function init() {
  await loadStatus();
  strategies = await api('/api/strategies');
  $('#date').value = status.today;
  const ss = $('#stratSelect');
  strategies.forEach(s => { const o = document.createElement('option'); o.value = s.key; o.textContent = s.label; ss.appendChild(o); });
  document.querySelectorAll('.tab').forEach(t => t.onclick = () => showView(t.dataset.view));
  $('#go').onclick = () => scan(true); $('#prev').onclick = () => { $('#date').value = isoShift($('#date').value, -1); autoJumped = true; scan(); };
  $('#next').onclick = () => { $('#date').value = isoShift($('#date').value, 1); autoJumped = true; scan(); };
  $('#today').onclick = () => { $('#date').value = status.today; autoJumped = false; scan(); };
  $('#date').onchange = () => { autoJumped = true; scan(); };
  ['#search', '#leagueFilter', '#sort'].forEach(s => $(s).oninput = renderList);
  ['#picksCount', '#picksPerMatch', '#picksSort', '#picksMin'].forEach(s => $(s).onchange = renderPicks);
  $('#stratSelect').onchange = renderStrategy;
  $('#settleBtn').onclick = settleJournal;
  $('#saveFd').onclick = () => saveSettings({ football_data_org_key: $('#fdKey').value }, '#fdResult');
  $('#clearFd').onclick = () => saveSettings({ clear_football: true }, '#fdResult');
  $('#testFd').onclick = () => testConn('/api/test/fixtures', '#fdResult', r => `Connected. ${r.fixtures_next_3_days} fixtures in the next 3 days.`);
  $('#saveBf').onclick = () => saveSettings({ betfair_app_key: $('#bfKey').value, betfair_username: $('#bfUser').value, betfair_password: $('#bfPass').value }, '#bfResult');
  $('#clearBf').onclick = () => saveSettings({ clear_betfair: true }, '#bfResult');
  $('#testBf').onclick = () => testConn('/api/test/betfair', '#bfResult', r => `Logged in. ${r.football_events_next_2_days} football events in the next 2 days.`);
  $('#saveStake').onclick = () => saveSettings({ bank: +$('#bank').value, kelly_fraction: +$('#kelly').value }, '#stakeResult');
  await loadCalendar(status.today);
  scan();
}

async function loadStatus() {
  status = await api('/api/status');
  const lf = $('#leagueFilter'); lf.innerHTML = '<option value="">All leagues</option>';
  Object.entries(status.leagues).forEach(([k, v]) => { const o = document.createElement('option'); o.value = k; o.textContent = v; lf.appendChild(o); });
  renderPills();
}

function renderPills() {
  const j = status.journal || {};
  $('#pills').innerHTML = `
    <span class="pill" data-go="settings" title="Click to set up"><span class="dot ${status.live_fixtures ? 'on' : ''}"></span>${status.live_fixtures ? 'Live fixtures' : 'Built-in fixtures only'}</span>
    <span class="pill" data-go="settings" title="Click to set up"><span class="dot ${status.betfair ? 'on' : status.betfair_error ? 'warn' : ''}"></span>${status.betfair ? 'Betfair prices' : 'No exchange prices'}</span>
    <span class="pill" data-go="settings"><span class="dot on"></span>Bank £${status.bank}</span>`;
  document.querySelectorAll('.pill').forEach(p => p.onclick = () => showView(p.dataset.go));
  $('#journalCount').textContent = j.picks ? j.picks : '';
}

async function loadCalendar(startIso) {
  try { const c = await api(`/api/calendar?start=${startIso}&days=10`); calendar = c.counts || {}; } catch (e) { calendar = {}; }
  renderDayStrip(startIso);
}

function renderDayStrip(startIso) {
  const sel = $('#date').value; const out = [];
  for (let k = 0; k < 10; k++) {
    const iso = isoShift(startIso, k); const d = new Date(iso + 'T00:00:00'); const n = calendar[iso] || 0;
    out.push(`<div class="day ${n ? 'has' : 'none'} ${iso === sel ? 'sel' : ''}" data-d="${iso}">${d.toLocaleDateString(undefined, { weekday: 'short' })} ${d.getDate()}<small>${n ? n + ' games' : 'no games'}</small></div>`);
  }
  $('#daystrip').innerHTML = out.join('');
  document.querySelectorAll('.day').forEach(el => el.onclick = () => { $('#date').value = el.dataset.d; autoJumped = true; scan(); });
}

async function scan(refresh = false) {
  const d = $('#date').value; if (!d) return;
  renderDayStrip(status.today);
  $('#banner').innerHTML = ''; $('#matchList').innerHTML = '<div class="spinner">Scanning ' + d + '…</div>'; $('#detail').innerHTML = ''; $('#picks').innerHTML = '<div class="spinner">Scanning…</div>';
  try { data = await api('/api/scan?date=' + d + (refresh ? '&refresh=true' : '')); selected = null; }
  catch (e) {
    data = null; $('#picks').innerHTML = ''; $('#matchList').innerHTML = '';
    $('#banner').innerHTML = `<div class="banner err"><b>Could not scan ${d}.</b> ${esc(e.message)}${/key/i.test(e.message) ? ' <a href="#" data-go="settings">Open Settings</a>' : ''}</div>`;
    document.querySelectorAll('#banner a').forEach(a => a.onclick = (ev) => { ev.preventDefault(); showView(a.dataset.go); });
    return;
  }
  $('#scanmeta').textContent = `${data.fixtures} fixtures · ${data.ideas} ideas · model on ${data.model_matches.toLocaleString()} matches`;
  if (!data.matches.length) {
    const up = data.upcoming || {}; const nextDay = Object.keys(up)[0]; const blank = data.weekday;
    if (!autoJumped && nextDay) {
      autoJumped = true; $('#date').value = nextDay; await scan();
      $('#banner').innerHTML = `<div class="banner">No matches in the covered leagues on <b>${blank}</b>, so this is the next match day. Pick any other day from the strip above.</div>`;
      return;
    }
    const list = Object.entries(up).map(([k, n]) => `<li><a href="#" data-d="${k}">${new Date(k + 'T00:00:00').toDateString()}</a> — ${n} fixtures</li>`).join('');
    $('#picks').innerHTML = $('#matchList').innerHTML = `<div class="empty">No matches in the covered leagues on ${data.weekday}.${list ? '<p>Next match days:</p><ul style="text-align:left;display:inline-block">' + list + '</ul>' : ''}</div>`;
    document.querySelectorAll('#picks a, #matchList a').forEach(a => a.onclick = (ev) => { ev.preventDefault(); $('#date').value = a.dataset.d; scan(); });
    return;
  }
  renderList(); renderPicks(); renderStrategy();
  if (!status.betfair && !warnedBetfair) { warnedBetfair = true; toast('Tip: connect Betfair in Settings to see real prices and edge.', 6000); }
}

function filteredMatches() {
  const q = $('#search').value.toLowerCase(), lg = $('#leagueFilter').value, sort = $('#sort').value;
  let ms = data.matches.filter(m => (!lg || m.league === lg) && (!q || (m.home + ' ' + m.away).toLowerCase().includes(q)));
  if (sort === 'score') ms.sort((a, b) => (b.best_score || 0) - (a.best_score || 0));
  if (sort === 'time') ms.sort((a, b) => (a.kickoff || '').localeCompare(b.kickoff || ''));
  if (sort === 'goals') ms.sort((a, b) => (b.forecast.home_xg + b.forecast.away_xg) - (a.forecast.home_xg + a.forecast.away_xg));
  return ms;
}

function renderList() {
  if (!data || !data.matches.length) return;
  $('#matchList').innerHTML = filteredMatches().map(m => `
    <div class="m ${selected === m.id ? 'sel' : ''}" data-id="${esc(m.id)}">
      <div class="ko">${m.kickoff || ''}<br>${esc(m.league_name)}</div>
      <div class="teams">${esc(m.home)}<br>${esc(m.away)}<div class="sub">${m.best_strategy ? esc(m.best_strategy) : 'no strategy fits'} · xG ${m.forecast.home_xg.toFixed(1)}-${m.forecast.away_xg.toFixed(1)}${m.result ? ' · FT ' + m.result.home + '-' + m.result.away : ''}</div></div>
      <div>${starsHtml(m.best_stars)}</div>
    </div>`).join('');
  document.querySelectorAll('.m').forEach(el => el.onclick = () => showMatch(el.dataset.id));
}

function showMatch(id) {
  selected = id; showView('matches'); renderList();
  const m = data.matches.find(x => x.id === id); if (!m) return; const f = m.forecast;
  const result = m.result ? `<div class="result">Actual result: <b>${esc(m.home)} ${m.result.home}-${m.result.away} ${esc(m.away)}</b>${m.result.ht_home != null ? ` (half-time ${m.result.ht_home}-${m.result.ht_away})` : ''}</div>` : '';
  $('#detail').innerHTML = `
    <div class="card">
      <h2>${esc(m.home)} v ${esc(m.away)}</h2>
      <div class="meta">${esc(m.league_name)} · ${data.weekday}${m.kickoff ? ' · ' + m.kickoff : ''} · data confidence ${f.confidence.toFixed(2)}</div>
      ${result}
      <h3>The model's view</h3>
      ${m.summary.map(s => `<p style="margin:4px 0">${esc(s)}</p>`).join('')}
      <div class="grid">
        <div class="stat"><b>${f.home_xg.toFixed(2)} – ${f.away_xg.toFixed(2)}</b><span>expected goals</span></div>
        <div class="stat"><b>${pct(f.p_home)} / ${pct(f.p_draw)} / ${pct(f.p_away)}</b><span>home / draw / away</span></div>
        <div class="stat"><b>${pct(f.p_over['2.5'])}</b><span>over 2.5 goals</span></div>
        <div class="stat"><b>${pct(f.p_btts)}</b><span>both teams score</span></div>
        <div class="stat"><b>${pct(f.p_00)}</b><span>0-0</span></div>
        <div class="stat"><b>${pct(f.p_goal_before['70'])}</b><span>goal before 70'</span></div>
        <div class="stat"><b>${pct(f.p_fav_scores_first)}</b><span>${esc(f.favourite)} score first</span></div>
        <div class="stat"><b>${f.top_scores.slice(0, 3).map(s => s.score + ' ' + pct(s.p)).join(' · ')}</b><span>most likely scores</span></div>
      </div>
      <div class="meta">Match odds</div><div class="bar"><i style="width:${f.p_home * 100}%;background:var(--accent)" title="home"></i><i style="width:${f.p_draw * 100}%;background:var(--muted)" title="draw"></i><i style="width:${f.p_away * 100}%;background:var(--inplay)" title="away"></i></div>
    </div>
    <h3 style="margin:18px 0 8px;color:var(--muted)">Strategies for this match, best first</h3>
    ${m.ideas.length ? m.ideas.map(i => ideaCard(i, m)).join('') : '<div class="card">No strategy passes its entry rules for this match. That is a legitimate answer: leave it.</div>'}`;
  wireIdeaButtons(m);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function ideaCard(i, m) {
  const settled = i.settled ? `<div class="result">Replay: this plan ${i.settled.hit >= 0.5 ? '<b class="pos">paid off</b>' : '<b class="neg">did not pay off</b>'} — ${spct(i.settled.pnl)} per unit risked${i.settled.hit > 0 && i.settled.hit < 1 ? ' (goal minutes not in the data, so this is an expectation)' : ''}</div>` : '';
  const hist = i.historical_strike_rate != null ? `<span>History: <b>${pct(i.historical_strike_rate)}</b> strike over ${i.historical_sample} similar trades</span>` : '';
  return `<div class="card idea s${i.stars}" id="idea-${esc(i.strategy)}">
    <div class="head">${starsHtml(i.stars)}<h2>${esc(i.strategy_label)}</h2><span class="meta">${i.side.toUpperCase()} · ${esc(i.market)} · ${esc(i.selection)} · score ${Math.round(i.score)}</span>
      <button class="small ${i.tracked ? 'ghost' : 'primary'}" data-track="${esc(i.strategy)}" ${i.tracked ? 'disabled' : ''}>${i.tracked ? 'Tracked ✓' : '+ Track this pick'}</button>
      <button class="small" data-copy="${esc(i.strategy)}">Copy plan</button></div>
    <div class="verdict">${esc(i.verdict)}</div>
    <div class="kv">
      <span>Pays off: <b>${pct(i.calibrated_hit_prob)}</b></span>
      <span>Return: <b>${spct(i.calibrated_roi)}</b> per unit risked</span>
      <span>Win <b class="pos">${spct(i.win_return)}</b> / lose <b class="neg">${spct(i.loss_return)}</b></span>
      <span>Entry price: <b>${price(i.market_price || i.model_price)}</b>${i.market_price ? ' (exchange)' : ' (model)'}</span>
      <span>Edge: <b>${spct(i.edge)}</b></span>
      <span>Stake: <b>${money(i.stake_money)}</b> (${i.stake_pct.toFixed(1)}% of bank)</span>
      ${hist}
    </div>
    ${settled}
    <h3>The plan</h3>
    <ul class="steps">${i.plan.map(p => `<li><span class="ph ${p.phase}">${PH[p.phase]}</span><span>${esc(p.text)}</span></li>`).join('')}</ul>
    <h3>Why</h3>
    <ul style="margin:4px 0 4px 18px">${i.rationale.map(r => `<li>${esc(r)}</li>`).join('')}</ul>
    <div class="meta" style="margin-top:6px"><b>Best for:</b> ${esc(i.best_for)}<br><b>Avoid when:</b> ${esc(i.avoid_when)}</div>
    ${i.warnings.map(w => `<div class="warn">! ${esc(w)}</div>`).join('')}
    <details style="margin-top:8px"><summary class="meta">How the match can go: every scenario, its chance and your profit or loss</summary>${scenarioTable(i)}</details>
  </div>`;
}

function scenarioTable(i) {
  const rows = [...i.scenarios].sort((a, b) => b.prob - a.prob);
  return `<table class="sc"><tr><th>What happens</th><th class="n">Chance</th><th class="n">Profit per unit risked</th></tr>
    ${rows.map(s => `<tr><td>${esc(s.label)}</td><td class="n">${pct(s.prob)}</td><td class="n ${s.profit >= 0 ? 'pos' : 'neg'}">${spct(s.profit)}</td></tr>`).join('')}
    <tr class="sum"><td>Expected (model)</td><td class="n">100%</td><td class="n">${spct(i.expected_roi)}</td></tr></table>
    <div class="meta" style="margin-top:6px">Prices after a goal are model estimates of where the market will trade. A 3% allowance for spread and commission is already deducted.</div>`;
}

function planText(i, m) {
  return [`${m.home} v ${m.away} (${m.league_name}, ${data.weekday}${m.kickoff ? ' ' + m.kickoff : ''})`, `${i.strategy_label} — ${i.verdict}`,
    `Pays off ${pct(i.calibrated_hit_prob)} · return ${spct(i.calibrated_roi)} · stake ${money(i.stake_money)}`, '',
    ...i.plan.map(p => `${PH[p.phase]}: ${p.text}`), '', 'Why:', ...i.rationale.map(r => `- ${r}`)].join('\n');
}

function wireIdeaButtons(m) {
  document.querySelectorAll('[data-track]').forEach(b => b.onclick = async (ev) => {
    ev.stopPropagation(); b.disabled = true; b.textContent = 'Saving…';
    try { await api('/api/journal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ date: data.date, match_id: m.id, strategy: b.dataset.track }) });
      const idea = m.ideas.find(x => x.strategy === b.dataset.track); if (idea) idea.tracked = true; b.textContent = 'Tracked ✓'; b.classList.remove('primary'); b.classList.add('ghost');
      await loadStatus(); toast('Added to My picks. It will settle itself when the result is in.');
    } catch (e) { b.disabled = false; b.textContent = '+ Track this pick'; toast('Could not track: ' + e.message, 6000); }
  });
  document.querySelectorAll('[data-copy]').forEach(b => b.onclick = async (ev) => {
    ev.stopPropagation(); const idea = m.ideas.find(x => x.strategy === b.dataset.copy);
    try { await navigator.clipboard.writeText(planText(idea, m)); toast('Plan copied to clipboard.'); } catch (e) { toast('Copy failed: ' + e.message); }
  });
}

function allIdeas() { return data ? data.matches.flatMap(m => m.ideas.map(i => ({ ...i, m }))) : []; }

function pickRow(i, k, extra = '') {
  return `<div class="card pick" data-id="${esc(i.m.id)}">
    <div>${starsHtml(i.stars)}<div class="s">score ${Math.round(i.score)}</div></div>
    <div><div class="t">${k + 1}. ${esc(i.m.home)} v ${esc(i.m.away)} <span class="s">· ${esc(i.m.league_name)} · ${i.m.kickoff || ''}</span></div>
      <div><b>${esc(i.strategy_label)}</b> <span class="s">· ${esc(i.verdict)}</span></div>
      <div class="s">${esc(i.plan[0].text)}</div>
      <div class="s">Pays off ${pct(i.calibrated_hit_prob)} · return ${spct(i.calibrated_roi)} · edge ${spct(i.edge)} · stake ${money(i.stake_money)}${i.settled ? ` · <b class="${i.settled.hit >= 0.5 ? 'pos' : 'neg'}">${i.settled.hit >= 0.5 ? 'paid off' : 'missed'} ${spct(i.settled.pnl)}</b>` : ''}${extra}</div></div>
    <div class="actions"><button class="small primary">Open plan ›</button>${i.tracked ? '<span class="s">Tracked ✓</span>' : ''}</div></div>`;
}

function renderPicks() {
  if (!data || !data.matches.length) return;
  const n = +$('#picksCount').value, perMatch = $('#picksPerMatch').checked, sort = $('#picksSort').value, minStars = +$('#picksMin').value;
  const key = { score: (i) => i.score, hit: (i) => i.calibrated_hit_prob, roi: (i) => i.calibrated_roi, edge: (i) => i.edge ?? -9 }[sort];
  let ideas = allIdeas().filter(i => i.stars >= minStars).sort((a, b) => key(b) - key(a));
  if (perMatch) { const seen = new Set(); ideas = ideas.filter(i => !seen.has(i.m.id) && seen.add(i.m.id)); }
  ideas = ideas.slice(0, n);
  if (!ideas.length) {
    $('#picks').innerHTML = `<div class="empty">Nothing reaches ${'★'.repeat(minStars)} on ${data.weekday}. A quiet day is a legitimate answer: the best trades are often the ones you do not make.<br><br><button id="showAll" class="ghost">Show the best available anyway</button></div>`;
    $('#showAll').onclick = () => { $('#picksMin').value = '0'; renderPicks(); };
    return;
  }
  const settledOnes = ideas.filter(i => i.settled); const hits = settledOnes.reduce((a, i) => a + i.settled.hit, 0);
  const expected = ideas.reduce((a, i) => a + i.calibrated_hit_prob, 0);
  const head = `<div class="meta" style="margin-bottom:10px">${data.weekday}: ${ideas.length} picks shown of ${data.ideas} ideas across ${data.fixtures} fixtures. Expected to pay off: about ${Math.round(expected)} of ${ideas.length}.</div>`;
  const replay = settledOnes.length ? `<div class="result" style="margin-bottom:10px">Replay: <b>${hits.toFixed(1)} of ${settledOnes.length}</b> settled picks paid off (model expected ${settledOnes.reduce((a, i) => a + i.calibrated_hit_prob, 0).toFixed(1)}). Total ${spct(settledOnes.reduce((a, i) => a + i.settled.pnl, 0) / settledOnes.length)} per unit risked on average.</div>` : '';
  $('#picks').innerHTML = head + replay + ideas.map((i, k) => pickRow(i, k)).join('');
  document.querySelectorAll('#picks .pick').forEach(el => el.onclick = () => showMatch(el.dataset.id));
}

function renderStrategy() {
  const key = $('#stratSelect').value; const s = strategies.find(x => x.key === key); if (!s) return;
  $('#stratInfo').innerHTML = `<h2>${esc(s.label)}</h2><p>${esc(s.description)}</p><p><b>Use it when:</b> ${esc(s.best_for)}</p><p><b>Avoid when:</b> ${esc(s.avoid_when)}</p>`;
  if (!data || !data.matches.length) { $('#stratList').innerHTML = ''; return; }
  const ideas = allIdeas().filter(i => i.strategy === key).sort((a, b) => b.score - a.score);
  $('#stratList').innerHTML = ideas.length ? ideas.map((i, k) => pickRow(i, k)).join('') : `<div class="empty">No match on ${data.weekday} passes the entry rules for this strategy.</div>`;
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
  if (!j.entries.length) { $('#journalList').innerHTML = '<div class="empty">No picks tracked yet. Open a plan and press <b>+ Track this pick</b>.</div>'; return; }
  $('#journalList').innerHTML = `<table class="j"><tr><th>Date</th><th>Match</th><th>Strategy</th><th class="n">Rating</th><th class="n">Pays off</th><th class="n">Entry</th><th class="n">Stake</th><th>Status</th><th>Result</th><th class="n">P/L</th><th></th></tr>
    ${j.entries.map(e => `<tr><td>${e.date}</td><td>${esc(e.home)} v ${esc(e.away)}<div class="s meta">${esc(e.league)}</div></td><td>${esc(e.strategy_label)}</td>
      <td class="n">${Math.round(e.score)}</td><td class="n">${pct(e.hit_prob)}</td><td class="n">${price(e.entry_price)}</td><td class="n">${money(e.stake_money)}</td>
      <td class="status-${e.status}">${e.status}</td><td>${e.result || '-'}</td><td class="n ${e.pnl_money == null ? '' : e.pnl_money >= 0 ? 'pos' : 'neg'}">${e.pnl_money == null ? '-' : money(e.pnl_money)}</td>
      <td><button class="small ghost danger" data-del="${e.id}" title="Remove">✕</button></td></tr>`).join('')}</table>`;
  document.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => { await api('/api/journal/' + b.dataset.del, { method: 'DELETE' }); await loadJournal(); await loadStatus(); });
}
async function loadJournal() { try { renderJournal(await api('/api/journal')); } catch (e) { toast('Could not load picks: ' + e.message); } }
async function settleJournal() {
  const b = $('#settleBtn'); b.disabled = true; b.textContent = 'Checking results…';
  try { const r = await api('/api/journal/settle', { method: 'POST' }); renderJournal(r); await loadStatus(); toast(r.settled ? `${r.settled} pick(s) settled.` : 'No new results yet.'); }
  catch (e) { toast('Could not update: ' + e.message, 6000); }
  b.disabled = false; b.textContent = 'Update results';
}

// ---------------- settings
async function loadSettings() {
  try {
    const s = await api('/api/settings');
    $('#fdKey').value = ''; $('#fdKey').placeholder = s.has_football_key ? `saved: ${s.football_data_org_key}  (paste a new one to replace)` : 'paste the code from the email';
    $('#bfKey').value = ''; $('#bfKey').placeholder = s.betfair_app_key ? `saved: ${s.betfair_app_key}` : 'e.g. aBcDeFgHiJkLmNoP';
    $('#bfUser').value = s.betfair_username || ''; $('#bfPass').value = ''; $('#bfPass').placeholder = s.has_betfair_password ? 'saved (type to replace)' : '';
    $('#bank').value = s.bank; $('#kelly').value = String(s.kelly_fraction);
    $('#lightFixtures').className = 'light ' + (status.live_fixtures ? 'on' : ''); $('#lightBetfair').className = 'light ' + (status.betfair ? 'on' : '');
    const lr = status.live_results || {};
    $('#dataInfo').innerHTML = `Seasons loaded: ${status.seasons.join(', ')}.<br>Live results cache: ${lr.state}${lr.updated ? ' (updated ' + lr.updated + ')' : ''}.<br>Settings file: <code>${esc(s.env_path)}</code> (keep it private).`
      + (status.betfair_error ? `<br><span class="warn">Betfair error: ${esc(status.betfair_error)}</span>` : '');
  } catch (e) { toast('Could not load settings: ' + e.message); }
}
async function saveSettings(body, resultSel) {
  const el = $(resultSel); el.textContent = 'Saving…';
  try {
    const r = await api('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    status = r.status; renderPills(); await loadSettings(); el.textContent = 'Saved.'; autoJumped = false; warnedBetfair = false;
    await loadCalendar(status.today); scan(true);
  } catch (e) { el.textContent = 'Error: ' + e.message; }
}
async function testConn(path, resultSel, okText) {
  const el = $(resultSel); el.textContent = 'Testing…';
  try { const r = await api(path, { method: 'POST' }); el.textContent = r.ok ? okText(r) : 'Failed: ' + r.error; el.className = 'meta ' + (r.ok ? 'pos' : 'neg'); }
  catch (e) { el.textContent = 'Failed: ' + e.message; }
}

init();
