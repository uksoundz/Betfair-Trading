const $ = (s) => document.querySelector(s);
const pct = (v) => v == null ? '-' : Math.round(v * 100) + '%';
const spct = (v) => v == null ? '-' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
const price = (v) => v == null ? '-' : v.toFixed(2);
const money = (v) => '£' + v.toFixed(2);
const cls = (s) => s >= 60 ? 'good' : s >= 50 ? 'mid' : 'low';
const PH = { entry: 'Before kick-off', inplay: 'In play', exit: 'Get out', stop: 'Stop loss', note: 'Note' };

let data = null, strategies = [], selected = null, status = {}, calendar = {}, autoJumped = false;

function toast(msg, ms = 4000) { const t = $('#toast'); t.textContent = msg; t.style.display = 'block'; clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', ms); }
function shift(days) { const d = new Date($('#date').value); d.setDate(d.getDate() + days); $('#date').value = d.toISOString().slice(0, 10); scan(); }

async function init() {
  status = await (await fetch('/api/status')).json();
  strategies = await (await fetch('/api/strategies')).json();
  $('#date').value = status.today;
  const lf = $('#leagueFilter');
  Object.entries(status.leagues).forEach(([k, v]) => { const o = document.createElement('option'); o.value = k; o.textContent = v; lf.appendChild(o); });
  const ss = $('#stratSelect');
  strategies.forEach(s => { const o = document.createElement('option'); o.value = s.key; o.textContent = s.label; ss.appendChild(o); });
  renderStatus();
  await loadCalendar(status.today);
  document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active')); t.classList.add('active');
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active')); $('#view-' + t.dataset.view).classList.add('active');
    if (t.dataset.view === 'picks') renderPicks(); if (t.dataset.view === 'strategies') renderStrategy();
  });
  $('#go').onclick = scan; $('#prev').onclick = () => shift(-1); $('#next').onclick = () => shift(1);
  $('#today').onclick = () => { $('#date').value = status.today; scan(); };
  $('#date').onchange = () => { autoJumped = true; scan(); };
  ['#search', '#leagueFilter', '#sort'].forEach(s => $(s).oninput = renderList);
  ['#picksCount', '#picksPerMatch', '#picksSort'].forEach(s => $(s).onchange = renderPicks);
  $('#stratSelect').onchange = renderStrategy;
  scan();
}

function renderStatus() {
  const bits = [];
  bits.push(status.live_fixtures ? 'Live fixtures: on' : 'Live fixtures: off (built-in data)');
  bits.push(status.betfair ? 'Betfair prices: on' : 'Betfair prices: off');
  bits.push('Bank £' + status.bank);
  if (data) bits.push(`${data.fixtures} fixtures · ${data.ideas} ideas · model on ${data.model_matches} matches`);
  $('#status').textContent = bits.join(' · ');
}

async function loadCalendar(startIso) {
  try {
    const r = await fetch(`/api/calendar?start=${startIso}&days=10`); const c = await r.json();
    calendar = c.counts || {};
  } catch (e) { calendar = {}; }
  renderDayStrip(startIso);
}

function renderDayStrip(startIso) {
  const start = new Date(startIso + 'T00:00:00'); const sel = $('#date').value; const out = [];
  for (let k = 0; k < 10; k++) {
    const d = new Date(start); d.setDate(start.getDate() + k); const iso = d.toISOString().slice(0, 10); const n = calendar[iso] || 0;
    out.push(`<div class="day ${n ? 'has' : 'none'} ${iso === sel ? 'sel' : ''}" data-d="${iso}">${d.toLocaleDateString(undefined, { weekday: 'short' })} ${d.getDate()}<small>${n ? n + ' games' : 'no games'}</small></div>`);
  }
  $('#daystrip').innerHTML = out.join('');
  document.querySelectorAll('.day').forEach(el => el.onclick = () => { $('#date').value = el.dataset.d; autoJumped = true; scan(); });
}

async function scan() {
  const d = $('#date').value; if (!d) return;
  renderDayStrip(status.today);
  $('#matchList').innerHTML = '<div class="spinner">Scanning ' + d + '…</div>'; $('#detail').innerHTML = '';
  try {
    const r = await fetch('/api/scan?date=' + d);
    if (!r.ok) { const e = await r.json(); throw new Error(e.detail || r.statusText); }
    data = await r.json(); selected = null;
  } catch (e) { $('#matchList').innerHTML = `<div class="empty">Could not scan: ${e.message}</div>`; toast('Scan failed: ' + e.message, 8000); return; }
  renderStatus(); renderList(); renderPicks(); renderStrategy();
  if (!data.matches.length) {
    const up = data.upcoming || {};
    const nextDay = Object.keys(up)[0];
    if (!autoJumped && nextDay) {
      // first load on a blank day: jump straight to the next day that has matches
      autoJumped = true; $('#date').value = nextDay; await scan();
      $('#detail').innerHTML = `<div class="banner">No matches today (${data.weekday} was blank), so this is the next match day. Use the day strip above or the calendar to pick another date.</div>` + $('#detail').innerHTML;
      return;
    }
    const list = Object.entries(up).map(([k, n]) => `<li><a href="#" data-d="${k}">${new Date(k).toDateString()}</a> — ${n} fixtures</li>`).join('');
    $('#matchList').innerHTML = `<div class="empty">No matches in the covered leagues on ${data.weekday}.${list ? '<p>Next match days:</p><ul style="text-align:left">' + list + '</ul>' : ''}</div>`;
    document.querySelectorAll('#matchList a').forEach(a => a.onclick = (ev) => { ev.preventDefault(); $('#date').value = a.dataset.d; scan(); });
  } else if (!status.betfair) { toast('Betfair not connected: prices are the model\'s fair prices. Edge is not measured.', 6000); }
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
  const ms = filteredMatches();
  $('#matchList').innerHTML = ms.map(m => `
    <div class="m ${selected === m.id ? 'sel' : ''}" data-id="${m.id}">
      <div class="ko">${m.kickoff || ''}<br><span class="sub">${m.league_name}</span></div>
      <div class="teams">${m.home}<br>${m.away}<div class="sub">${m.best_strategy || 'no strategy fits'} · xG ${m.forecast.home_xg.toFixed(1)}-${m.forecast.away_xg.toFixed(1)}${m.result ? ' · FT ' + m.result.home + '-' + m.result.away : ''}</div></div>
      <div><span class="badge ${m.best_score != null ? cls(m.best_score) : 'low'}">${m.best_score != null ? Math.round(m.best_score) : '–'}</span></div>
    </div>`).join('');
  document.querySelectorAll('.m').forEach(el => el.onclick = () => showMatch(el.dataset.id));
}

function showMatch(id) {
  selected = id; renderList();
  document.querySelector('.tab[data-view="matches"]').click();
  const m = data.matches.find(x => x.id === id); const f = m.forecast;
  const pr = (p) => `<i style="width:${p * 100}%;background:var(--accent)"></i>`;
  const result = m.result ? `<div class="result">Actual result: <b>${m.home} ${m.result.home}-${m.result.away} ${m.away}</b>${m.result.ht_home != null ? ` (half-time ${m.result.ht_home}-${m.result.ht_away})` : ''}</div>` : '';
  $('#detail').innerHTML = `
    <div class="card">
      <h2>${m.home} v ${m.away}</h2>
      <div class="meta">${m.league_name} · ${data.weekday}${m.kickoff ? ' · ' + m.kickoff : ''} · data confidence ${f.confidence.toFixed(2)}</div>
      ${result}
      <h3>The model's view</h3>
      ${m.summary.map(s => `<p style="margin:4px 0">${s}</p>`).join('')}
      <div class="grid">
        <div class="stat"><b>${f.home_xg.toFixed(2)} – ${f.away_xg.toFixed(2)}</b><span>expected goals</span></div>
        <div class="stat"><b>${pct(f.p_home)} / ${pct(f.p_draw)} / ${pct(f.p_away)}</b><span>home / draw / away</span></div>
        <div class="stat"><b>${pct(f.p_over['2.5'])}</b><span>over 2.5 goals</span></div>
        <div class="stat"><b>${pct(f.p_btts)}</b><span>both teams score</span></div>
        <div class="stat"><b>${pct(f.p_00)}</b><span>0-0</span></div>
        <div class="stat"><b>${pct(f.p_goal_before['70'])}</b><span>goal before 70'</span></div>
        <div class="stat"><b>${pct(f.p_fav_scores_first)}</b><span>${f.favourite} score first</span></div>
        <div class="stat"><b>${f.top_scores.slice(0, 3).map(s => s.score + ' ' + pct(s.p)).join(' · ')}</b><span>most likely scores</span></div>
      </div>
      <div class="meta">Match odds</div><div class="bar"><i style="width:${f.p_home * 100}%;background:var(--accent)"></i><i style="width:${f.p_draw * 100}%;background:var(--muted)"></i><i style="width:${f.p_away * 100}%;background:var(--inplay)"></i></div>
    </div>
    <h3 style="margin:18px 0 8px;color:var(--muted)">Strategies for this match, best first</h3>
    ${m.ideas.length ? m.ideas.map(ideaCard).join('') : '<div class="card">No strategy passes its entry rules for this match. That is a legitimate answer: leave it.</div>'}`;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function ideaCard(i, m) {
  const settled = i.settled ? `<div class="result">Replay: this plan ${i.settled.hit >= 0.5 ? '<b class="pos">paid off</b>' : '<b class="neg">did not pay off</b>'} — ${spct(i.settled.pnl)} per unit risked${i.settled.hit > 0 && i.settled.hit < 1 ? ' (goal minutes not in the data, so this is an expectation)' : ''}</div>` : '';
  const hist = i.historical_strike_rate != null ? `<span>History: <b>${pct(i.historical_strike_rate)}</b> strike over ${i.historical_sample} similar trades</span>` : '';
  return `<div class="card idea ${cls(i.score)}">
    <div class="head"><span class="badge ${cls(i.score)}">${Math.round(i.score)}</span><h2>${i.strategy_label}</h2><span class="meta">${i.side.toUpperCase()} · ${i.market} · ${i.selection}</span></div>
    <div class="verdict">${i.verdict}</div>
    <div class="kv">
      <span>Pays off: <b>${pct(i.calibrated_hit_prob)}</b></span>
      <span>Return: <b>${spct(i.calibrated_roi)}</b> per unit <span title="before adjusting for this strategy's real track record in this league">(model ${spct(i.expected_roi)})</span></span>
      <span>Win <b class="pos">${spct(i.win_return)}</b> / lose <b class="neg">${spct(i.loss_return)}</b></span>
      <span>Entry price: <b>${price(i.market_price || i.model_price)}</b>${i.market_price ? ' (exchange)' : ' (model)'}</span>
      <span>Edge: <b>${spct(i.edge)}</b></span>
      <span>Stake: <b>${i.stake_pct.toFixed(1)}%</b> of bank ≈ ${money(i.stake_money)}</span>
      ${hist}
    </div>
    ${settled}
    <h3>The plan</h3>
    <ul class="steps">${i.plan.map(p => `<li><span class="ph ${p.phase}">${PH[p.phase]}</span><span>${p.text}</span></li>`).join('')}</ul>
    <h3>Why</h3>
    <ul style="margin:4px 0 4px 18px">${i.rationale.map(r => `<li>${r}</li>`).join('')}</ul>
    <div class="meta" style="margin-top:6px"><b>Best for:</b> ${i.best_for}<br><b>Avoid when:</b> ${i.avoid_when}</div>
    ${i.warnings.map(w => `<div class="warn">! ${w}</div>`).join('')}
    <details style="margin-top:8px"><summary class="meta">How the match can go (scenario table)</summary>${scenarioTable(i)}</details>
  </div>`;
}

function scenarioTable(i) {
  // Scenarios are not shipped individually; show the summary the scorer used.
  return `<table class="sc"><tr><th>Outcome</th><th class="n">Chance</th><th class="n">Profit per unit</th></tr>
    <tr><td>Plan pays off</td><td class="n">${pct(i.calibrated_hit_prob)}</td><td class="n pos">${spct(i.win_return)}</td></tr>
    <tr><td>Plan fails (stop or settlement)</td><td class="n">${pct(1 - i.calibrated_hit_prob)}</td><td class="n neg">${spct(i.loss_return)}</td></tr>
    <tr><td><b>Expected</b></td><td class="n"></td><td class="n"><b>${spct(i.calibrated_roi)}</b></td></tr></table>`;
}

function allIdeas() { return data ? data.matches.flatMap(m => m.ideas.map(i => ({ ...i, m }))) : []; }

function renderPicks() {
  if (!data) return;
  const n = +$('#picksCount').value, perMatch = $('#picksPerMatch').checked, sort = $('#picksSort').value;
  const key = { score: (i) => i.score, hit: (i) => i.calibrated_hit_prob, roi: (i) => i.calibrated_roi, edge: (i) => i.edge ?? -9 }[sort];
  let ideas = allIdeas().sort((a, b) => key(b) - key(a));
  if (perMatch) { const seen = new Set(); ideas = ideas.filter(i => !seen.has(i.m.id) && seen.add(i.m.id)); }
  ideas = ideas.slice(0, n);
  if (!ideas.length) { $('#picks').innerHTML = '<div class="empty">Nothing to rank. Scan a day with fixtures.</div>'; return; }
  const hits = ideas.filter(i => i.settled).reduce((a, i) => a + i.settled.hit, 0), settledN = ideas.filter(i => i.settled).length;
  $('#picks').innerHTML = (settledN ? `<div class="result" style="margin-bottom:10px">Replay: ${hits.toFixed(1)} of ${settledN} shown picks paid off (model expected ${ideas.reduce((a, i) => a + i.calibrated_hit_prob, 0).toFixed(1)}).</div>` : '') +
    ideas.map((i, k) => `<div class="card pick" data-id="${i.m.id}">
      <span class="badge ${cls(i.score)}">${Math.round(i.score)}</span>
      <div><div class="t">${k + 1}. ${i.m.home} v ${i.m.away} <span class="s">· ${i.m.league_name} · ${i.m.kickoff || ''}</span></div>
        <div>${i.strategy_label}: <span class="s">${i.plan[0].text}</span></div>
        <div class="s">Pays off ${pct(i.calibrated_hit_prob)} · return ${spct(i.calibrated_roi)} · edge ${spct(i.edge)} · stake ≈ ${money(i.stake_money)}${i.settled ? ` · <b class="${i.settled.hit >= 0.5 ? 'pos' : 'neg'}">${i.settled.hit >= 0.5 ? 'paid off' : 'missed'} ${spct(i.settled.pnl)}</b>` : ''}</div></div>
      <div class="s">open ›</div></div>`).join('');
  document.querySelectorAll('.pick').forEach(el => el.onclick = () => showMatch(el.dataset.id));
}

function renderStrategy() {
  const key = $('#stratSelect').value; const s = strategies.find(x => x.key === key); if (!s) return;
  $('#stratInfo').innerHTML = `<h2>${s.label}</h2><p>${s.description}</p><p><b>Use it when:</b> ${s.best_for}</p><p><b>Avoid when:</b> ${s.avoid_when}</p>`;
  if (!data) return;
  const ideas = allIdeas().filter(i => i.strategy === key).sort((a, b) => b.score - a.score);
  $('#stratList').innerHTML = ideas.length ? ideas.map((i, k) => `<div class="card pick" data-id="${i.m.id}">
      <span class="badge ${cls(i.score)}">${Math.round(i.score)}</span>
      <div><div class="t">${k + 1}. ${i.m.home} v ${i.m.away} <span class="s">· ${i.m.league_name} · ${i.m.kickoff || ''}</span></div>
        <div class="s">${i.verdict}</div>
        <div class="s">${i.plan[0].text}</div></div><div class="s">open ›</div></div>`).join('')
    : '<div class="empty">No match on this day passes the entry rules for this strategy.</div>';
  document.querySelectorAll('#stratList .pick').forEach(el => el.onclick = () => showMatch(el.dataset.id));
}

init();
