"""Local web app: the point-and-click front end. Start with `tradescout app` (or app.bat).

GET  /                           the single-page UI
GET  /api/status                 connections, data freshness, model info
GET  /api/calendar?start=&days=  fixture counts per day (clickable day strip)
GET  /api/scan?date=YYYY-MM-DD   fixtures, forecasts, ranked ideas for a day
GET  /api/strategies             strategy library with plain-English guidance
GET  /api/settings  POST /api/settings          read / save keys, bank, stake style (written to .env)
POST /api/test/fixtures  POST /api/test/betfair  connection tests
GET  /api/journal  POST /api/journal  DELETE /api/journal/{id}  POST /api/journal/settle
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import LEAGUE_NAMES, settings, write_env
from ..data.base import NoPrices
from ..data.combined import CombinedResults
from ..data.openfootball import OpenFootballProvider
from ..betting import build_slip
from ..journal import Journal
from ..report.narrative import idea_verdict, match_summary
from ..scout import ScanResult, Scout
from ..strategies import ALL_STRATEGIES, get_strategy

STATIC = Path(__file__).parent / "static"
CACHE_TTL = 600  # seconds


class Runtime:
    """Everything that depends on settings, rebuilt when settings change."""

    def __init__(self):
        self.sample = OpenFootballProvider()
        self.live = None
        self.live_error: Optional[str] = None
        self.prices = NoPrices()
        self.betfair_error: Optional[str] = None
        if settings.football_data_org_key:
            from ..data.football_data_org import FootballDataOrgProvider
            self.live = FootballDataOrgProvider(settings.football_data_org_key)
        self.results = CombinedResults(self.sample, self.live, settings.cache_dir)
        if settings.has_betfair:
            try:
                from ..data.betfair import BetfairPrices
                self.prices = BetfairPrices(settings.betfair_app_key, settings.betfair_session_token, settings.betfair_username, settings.betfair_password)
            except Exception as exc:
                self.betfair_error = str(exc)
        self.scout = Scout(self.results, self.live or self.sample, self.prices, xi=settings.time_decay_xi, history_days=settings.history_days,
                           max_goals=settings.max_goals)
        self.scout.scorer.kelly_fraction = settings.kelly_fraction
        self.cache: dict[str, tuple[float, dict]] = {}
        self.lock = threading.Lock()
        self.results.refresh_in_background()

    @property
    def live_fixtures(self) -> bool:
        return self.live is not None

    def result_for(self, fx):
        return self.results.result_for(fx)


app = FastAPI(title="TradeScout")
rt = Runtime()
journal = Journal()


# ----------------------------------------------------------------------------------- helpers
def _idea_json(i, fc, result) -> dict:
    strat = get_strategy(i.strategy)
    d = {
        "strategy": i.strategy, "strategy_label": i.strategy_label, "market": i.market, "side": i.side, "selection": i.selection,
        "score": round(i.score, 1), "stars": i.stars, "hit_prob": i.hit_prob, "calibrated_hit_prob": i.calibrated_hit_prob,
        "model_price": i.model_price, "market_price": i.market_price, "edge": i.edge, "expected_roi": i.expected_roi,
        "calibrated_roi": i.calibrated_roi, "win_return": i.win_return, "loss_return": i.loss_return, "stake_pct": round(i.stake_pct, 2),
        "stake_money": round(settings.bank * i.stake_pct / 100, 2), "confidence": fc.confidence, "liquidity": i.liquidity,
        "historical_strike_rate": i.historical_strike_rate, "historical_sample": i.historical_sample,
        "plan": [asdict(p) for p in i.plan], "rationale": i.rationale, "warnings": i.warnings, "scenarios": i.scenarios,
        "orders": [asdict(o) for o in i.orders],
        "verdict": idea_verdict(i.calibrated_hit_prob, i.calibrated_roi, i.edge, i.score),
        "best_for": strat.best_for, "avoid_when": strat.avoid_when, "description": strat.description,
        "tracked": any(e.date == i.fixture.date.isoformat() and e.home == i.fixture.home and e.away == i.fixture.away and e.strategy == i.strategy
                       for e in journal.entries),
    }
    if result is not None:
        hit, pnl = strat.settle(fc, result)
        d["settled"] = {"hit": hit, "pnl": pnl}
    return d


def _scan_json(scan: ScanResult) -> dict:
    matches = []
    for fx in scan.fixtures:
        fc = scan.forecasts[fx.label]
        result = rt.result_for(fx)
        ideas = [i for i in scan.ideas if i.fixture.label == fx.label]
        top_cs = sorted(fc.p_cs.items(), key=lambda kv: -kv[1])[:6]
        matches.append({
            "id": fx.fixture_id or fx.label, "home": fx.home, "away": fx.away, "league": fx.league,
            "league_name": LEAGUE_NAMES.get(fx.league, fx.league),
            "kickoff": fx.kickoff.strftime("%H:%M") if fx.kickoff else None,
            "best_score": round(ideas[0].score, 1) if ideas else None, "best_stars": ideas[0].stars if ideas else 0,
            "best_strategy": ideas[0].strategy_label if ideas else None,
            "forecast": {
                "home_xg": fc.home_xg, "away_xg": fc.away_xg, "p_home": fc.p_home, "p_draw": fc.p_draw, "p_away": fc.p_away,
                "p_over": {str(k): v for k, v in fc.p_over.items()}, "p_btts": fc.p_btts, "p_00": fc.p_cs.get("0-0", 0),
                "p_ht_00": fc.p_ht_00, "p_goal_before": {str(k): v for k, v in fc.p_goal_before.items()},
                "p_fav_scores_first": fc.p_fav_scores_first, "favourite": fx.home if fc.favourite == "home" else fx.away,
                "confidence": fc.confidence, "top_scores": [{"score": s, "p": p} for s, p in top_cs],
                "home_rating": {"attack": fc.home_strength.attack, "defence": fc.home_strength.defence, "games": fc.home_strength.matches_in_window},
                "away_rating": {"attack": fc.away_strength.attack, "defence": fc.away_strength.defence, "games": fc.away_strength.matches_in_window},
            },
            "summary": match_summary(fc),
            "result": None if result is None else {"home": result.home_goals, "away": result.away_goals, "ht_home": result.ht_home, "ht_away": result.ht_away},
            "ideas": [_idea_json(i, fc, result) for i in ideas],
        })
    return {
        "date": scan.date.isoformat(), "weekday": scan.date.strftime("%A %d %B %Y"), "fixtures": len(scan.fixtures),
        "ideas": len(scan.ideas), "model_matches": scan.model_matches, "price_source": scan.price_source,
        "live_fixtures": rt.live_fixtures, "matches": matches, "skipped": scan.skipped,
        "bank": settings.bank, "generated": datetime.now().strftime("%H:%M:%S"),
    }


def _upcoming(on: date) -> dict[str, int]:
    try:
        if rt.live is not None:
            return {d.isoformat(): n for d, n in rt.live.upcoming(on + timedelta(days=1), 10).items()}
    except Exception:
        pass
    return {d.isoformat(): len(rt.sample.fixtures(d)) for d in rt.sample.match_days(on + timedelta(days=1), on + timedelta(days=21))[:7]}


def _scan_cached(date_str: str, refresh: bool = False) -> dict:
    try:
        on = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    with rt.lock:
        hit = rt.cache.get(date_str)
        if hit and not refresh and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        if on >= date.today():
            rt.sample.refresh_current_if_stale()
        try:
            result = _scan_json(rt.scout.scan(on))
        except Exception as exc:
            name = type(exc).__name__
            code = 429 if name == "RateLimited" else 502
            raise HTTPException(code, str(exc) if name in ("RateLimited", "BadApiKey") else f"{name}: {exc}")
        if not result["matches"]:
            result["upcoming"] = _upcoming(on)
        rt.cache[date_str] = (time.time(), result)
        return result


# ----------------------------------------------------------------------------------- routes
@app.get("/api/status")
def status():
    leagues = dict(LEAGUE_NAMES) if rt.live_fixtures else {k: LEAGUE_NAMES.get(k, k) for k in rt.sample.leagues}
    return {
        "live_fixtures": rt.live_fixtures, "betfair": settings.has_betfair and rt.betfair_error is None, "betfair_error": rt.betfair_error,
        "bank": settings.bank, "kelly_fraction": settings.kelly_fraction, "seasons": rt.sample.available_seasons(), "leagues": leagues,
        "today": date.today().isoformat(), "live_results": rt.results.status, "journal": journal.summary(),
    }


@app.get("/api/calendar")
def calendar(start: str, days: int = 10):
    try:
        s = date.fromisoformat(start)
    except ValueError:
        raise HTTPException(400, "start must be YYYY-MM-DD")
    key = f"cal:{start}:{days}"
    with rt.lock:
        hit = rt.cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        counts: dict[str, int] = {}
        source = "sample"
        if rt.live is not None:
            try:
                counts = {d.isoformat(): n for d, n in rt.live.upcoming(s, days).items()}
                source = "live"
            except Exception as exc:
                source = f"live feed error: {exc}"
        if not counts:
            for d in rt.sample.match_days(s, s + timedelta(days=days - 1)):
                counts[d.isoformat()] = len(rt.sample.fixtures(d))
        out = {"start": start, "days": days, "counts": counts, "source": source}
        rt.cache[key] = (time.time(), out)
        return out


@app.get("/api/strategies")
def strategies():
    return [{"key": s.key, "label": s.label, "description": s.description, "best_for": s.best_for, "avoid_when": s.avoid_when}
            for s in ALL_STRATEGIES]


@app.get("/api/scan")
def scan(date_str: str = Query(alias="date"), refresh: bool = False):
    return _scan_cached(date_str, refresh)


# ----- settings -----------------------------------------------------------------------
def _mask(v: Optional[str]) -> str:
    if not v:
        return ""
    return v[:3] + "•" * max(0, len(v) - 6) + v[-3:] if len(v) > 8 else "•" * len(v)


class SettingsIn(BaseModel):
    football_data_org_key: Optional[str] = None
    betfair_app_key: Optional[str] = None
    betfair_username: Optional[str] = None
    betfair_password: Optional[str] = None
    bank: Optional[float] = None
    kelly_fraction: Optional[float] = None
    clear_football: bool = False
    clear_betfair: bool = False


@app.get("/api/settings")
def get_settings():
    return {
        "football_data_org_key": _mask(settings.football_data_org_key), "has_football_key": bool(settings.football_data_org_key),
        "betfair_app_key": _mask(settings.betfair_app_key), "betfair_username": settings.betfair_username or "",
        "has_betfair_password": bool(settings.betfair_password), "bank": settings.bank, "kelly_fraction": settings.kelly_fraction,
        "env_path": str(Path(settings.cache_dir).parent / ".env"),
    }


@app.post("/api/settings")
def post_settings(body: SettingsIn):
    global rt
    values: dict[str, Optional[str]] = {}
    if body.clear_football:
        values["FOOTBALL_DATA_API_KEY"] = None
    elif body.football_data_org_key:
        values["FOOTBALL_DATA_API_KEY"] = body.football_data_org_key.strip()
    if body.clear_betfair:
        values.update({"BETFAIR_APP_KEY": None, "BETFAIR_USERNAME": None, "BETFAIR_PASSWORD": None, "BETFAIR_SESSION_TOKEN": None})
    else:
        if body.betfair_app_key:
            values["BETFAIR_APP_KEY"] = body.betfair_app_key.strip()
        if body.betfair_username:
            values["BETFAIR_USERNAME"] = body.betfair_username.strip()
        if body.betfair_password:
            values["BETFAIR_PASSWORD"] = body.betfair_password
    if body.bank is not None and body.bank > 0:
        values["TRADESCOUT_BANK"] = str(body.bank)
    if body.kelly_fraction is not None and 0 < body.kelly_fraction <= 1:
        values["TRADESCOUT_KELLY"] = str(body.kelly_fraction)
    write_env(values)
    rt = Runtime()
    return {"ok": True, **get_settings(), "status": status()}


@app.post("/api/test/fixtures")
def test_fixtures():
    if rt.live is None:
        return {"ok": False, "error": "No football-data.org key saved yet."}
    try:
        return rt.live.ping()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/test/betfair")
def test_betfair():
    if not settings.has_betfair:
        return {"ok": False, "error": "Betfair application key, username and password are all needed."}
    try:
        from ..data.betfair import BetfairPrices
        bf = BetfairPrices(settings.betfair_app_key, settings.betfair_session_token, settings.betfair_username, settings.betfair_password)
        events = bf._rpc("listEvents", {"filter": {"eventTypeIds": ["1"], "marketStartTime": {
            "from": date.today().isoformat() + "T00:00:00Z", "to": (date.today() + timedelta(days=2)).isoformat() + "T00:00:00Z"}}})
        return {"ok": True, "football_events_next_2_days": len(events)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ----- bet slip (review + paper record; no orders are sent) ----------------------------
class SlipIn(BaseModel):
    date: str
    match_id: str
    strategy: str
    stake_money: Optional[float] = None


def _rebuild_idea(body) -> tuple:
    with rt.lock:
        on = date.fromisoformat(body.date)
        fcaster = rt.scout.forecaster(on)
        fx = next((f for f in rt.scout.fixtures.fixtures(on) if (f.fixture_id or f.label) == body.match_id), None)
        if fx is None:
            raise HTTPException(404, "match not found for that date")
        fc = fcaster.forecast(fx)
        strat = get_strategy(body.strategy)
        r = strat.evaluate(fc, rt.scout.prices.prices(fx))
        if r is None:
            raise HTTPException(404, "strategy not available for that match")
        idea = rt.scout.scorer.score(fx, fc, strat, r)
    stake = body.stake_money if body.stake_money else round(settings.bank * idea.stake_pct / 100, 2)
    return idea, stake


@app.post("/api/betslip/preview")
def betslip_preview(body: SlipIn):
    idea, stake = _rebuild_idea(body)
    bf = rt.prices if (settings.has_betfair and rt.betfair_error is None and hasattr(rt.prices, "resolve")) else None
    return build_slip(idea, stake, bf).to_dict()


@app.post("/api/betslip/paper")
def betslip_paper(body: SlipIn):
    """Record the reviewed slip as a paper bet in the journal. Nothing is sent to the exchange."""
    idea, stake = _rebuild_idea(body)
    bf = rt.prices if (settings.has_betfair and rt.betfair_error is None and hasattr(rt.prices, "resolve")) else None
    slip = build_slip(idea, stake, bf)
    e = journal.add(idea, stake, note="paper slip")
    journal.attach_slip(e.id, [asdict(l) for l in slip.lines])
    rt.cache.pop(body.date, None)
    return {"ok": True, "entry": asdict(e), "slip": slip.to_dict(), "summary": journal.summary()}


# ----- journal -------------------------------------------------------------------------
class TrackIn(BaseModel):
    date: str
    match_id: str
    strategy: str
    stake_money: Optional[float] = None
    note: str = ""


@app.get("/api/journal")
def get_journal():
    return {"summary": journal.summary(), "entries": [asdict(e) for e in journal.entries]}


@app.post("/api/journal")
def track(body: TrackIn):
    scan_data = _scan_cached(body.date)
    match = next((m for m in scan_data["matches"] if m["id"] == body.match_id), None)
    if not match:
        raise HTTPException(404, "match not found for that date")
    idea_json = next((i for i in match["ideas"] if i["strategy"] == body.strategy), None)
    if not idea_json:
        raise HTTPException(404, "strategy not available for that match")
    # rebuild the TradeIdea from the live scan so the journal stores exactly what was shown
    with rt.lock:
        on = date.fromisoformat(body.date)
        fcaster = rt.scout.forecaster(on)
        fx = next(f for f in rt.scout.fixtures.fixtures(on) if (f.fixture_id or f.label) == body.match_id)
        fc = fcaster.forecast(fx)
        r = get_strategy(body.strategy).evaluate(fc, rt.scout.prices.prices(fx))
        idea = rt.scout.scorer.score(fx, fc, get_strategy(body.strategy), r)
    stake = body.stake_money if body.stake_money is not None else round(settings.bank * idea.stake_pct / 100, 2)
    e = journal.add(idea, stake, body.note)
    rt.cache.pop(body.date, None)
    return {"ok": True, "entry": asdict(e), "summary": journal.summary()}


@app.delete("/api/journal/{entry_id}")
def untrack(entry_id: str):
    ok = journal.remove(entry_id)
    rt.cache.clear()
    return {"ok": ok, "summary": journal.summary()}


@app.post("/api/journal/settle")
def settle():
    rt.sample.refresh_current_if_stale(max_age_hours=6)
    n = journal.settle(rt.scout, rt.result_for)
    return {"ok": True, "settled": n, "summary": journal.summary(), "entries": [asdict(e) for e in journal.entries]}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def _free_port(host: str, preferred: int) -> int:
    import socket
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port found")


def _open_when_ready(url: str, host: str, port: int, timeout: float = 20.0) -> None:
    import socket
    import webbrowser
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.3)
    webbrowser.open(url)


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn
    if not (STATIC / "index.html").exists():
        raise SystemExit(f"TradeScout UI files are missing from {STATIC}. Re-run install.bat (or: py -m pip install -e .)")
    port = _free_port(host, port)
    url = f"http://{host}:{port}/"
    print("=" * 64, flush=True)
    print(f"  TradeScout is starting at {url}")
    print("  If the browser does not open, copy that address into it yourself.")
    print(f"  Live fixtures: {'ON' if settings.football_data_org_key else 'off (add a key in Settings)'}   Betfair prices: {'ON' if settings.has_betfair else 'off'}")
    print("  Keep this window open while you use the app. Press Ctrl+C to stop.")
    print("=" * 64, flush=True)
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url, host, port), daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
