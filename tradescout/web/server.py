"""Local web app: the point-and-click front end. Start with `tradescout app` (or app.bat).

Every data route takes `sport` = football | tennis.

GET  /                                   the single-page UI
GET  /api/status                         connections, data freshness, model info, risk/exposure
GET  /api/calendar?start=&days=&sport=   fixture counts per day
GET  /api/scan?date=&sport=              fixtures, forecasts, ranked ideas (TRADE / NO TRADE / RESEARCH)
GET  /api/strategies?sport=              strategy library with guidance and holdout evidence
GET  /api/stats                          holdout statistics per strategy (model-synthetic evidence) + signals log summary
GET  /api/settings  POST /api/settings   keys, bank, staking, betting mode, enabled strategies (written to .env)
POST /api/test/fixtures  POST /api/test/betfair
POST /api/betslip/preview|paper|place    (sport in body)
GET  /api/bets/open  POST /api/bets/cancel
GET  /api/journal  POST /api/journal  DELETE /api/journal/{id}  POST /api/journal/settle
"""
from __future__ import annotations

import json
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

from .. import signals
from ..betting import build_slip, place_slip
from ..config import FOOTBALL_LEAGUES, LEAGUE_NAMES, REPO_ROOT, SPORTS, TENNIS_LEAGUES, settings, write_env
from ..data.base import NoPrices
from ..data.combined import CombinedResults
from ..data.openfootball import OpenFootballProvider
from ..journal import Journal
from ..report.narrative import idea_verdict, match_summary
from ..risk import RiskLimits, exposure_from_journal, risk_of_ruin
from ..scout import ScanResult, Scout
from ..strategies import ALL_STRATEGIES, active_strategies, get_strategy
from ..tennis.data import TennisProvider
from ..tennis.forecast import summary as tennis_summary
from ..tennis.scout import TennisScout
from ..tennis.strategies import TENNIS_STRATEGIES

STATIC = Path(__file__).parent / "static"
CACHE_TTL_MODEL = 600   # seconds, when prices are model-only
CACHE_TTL_LIVE = 90     # seconds, when exchange prices are attached (stale-signal protection)
STATS_PATH = REPO_ROOT / "data" / "strategy_stats.json"


def _sport(s: Optional[str]) -> str:
    s = (s or "football").lower()
    if s not in SPORTS:
        raise HTTPException(400, f"sport must be one of {list(SPORTS)}")
    return s


class Runtime:
    """Everything that depends on settings, rebuilt when settings change."""

    def __init__(self):
        self.sample = OpenFootballProvider()
        self.tennis = TennisProvider()
        self.live = None
        self.betfair = None
        self.betfair_error: Optional[str] = None
        if settings.football_data_org_key:
            from ..data.football_data_org import FootballDataOrgProvider
            self.live = FootballDataOrgProvider(settings.football_data_org_key)
        self.results = CombinedResults(self.sample, self.live, settings.cache_dir)
        prices = NoPrices()
        tennis_fixtures = self.tennis
        tennis_prices = NoPrices()
        if settings.has_betfair:
            try:
                from ..data.betfair import BetfairPrices
                from ..data.betfair_tennis import BetfairTennis
                self.betfair = BetfairPrices(settings.betfair_app_key, settings.betfair_session_token, settings.betfair_username, settings.betfair_password)
                prices = self.betfair
                bt = BetfairTennis(self.betfair, self.tennis)
                tennis_fixtures, tennis_prices = bt, bt
            except Exception as exc:
                self.betfair_error = str(exc)
        self.scout = Scout(self.results, self.live or self.sample, prices, xi=settings.time_decay_xi, history_days=settings.history_days,
                           max_goals=settings.max_goals)
        self.tennis_scout = TennisScout(self.tennis, tennis_fixtures, tennis_prices)
        self.limits = RiskLimits(kelly_fraction=settings.kelly_fraction)
        for sc in (self.scout, self.tennis_scout):
            sc.scorer.limits = self.limits
            sc.scorer.kelly_fraction = settings.kelly_fraction
        self.cache: dict[str, tuple[float, dict]] = {}
        self.lock = threading.Lock()
        self.results.refresh_in_background()

    @property
    def live_fixtures(self) -> bool:
        return self.live is not None

    @property
    def betfair_ok(self) -> bool:
        return self.betfair is not None and self.betfair_error is None

    @property
    def tennis_live(self) -> bool:
        return self.betfair_ok

    def scout_for(self, sport: str):
        return self.tennis_scout if sport == "tennis" else self.scout

    def result_for(self, sport: str, fx):
        return self.tennis.result_for(fx) if sport == "tennis" else self.results.result_for(fx)

    def refresh_exposure(self):
        ex = exposure_from_journal(journal.entries, settings.bank)
        self.scout.scorer.exposure = ex
        self.tennis_scout.scorer.exposure = ex
        return ex


app = FastAPI(title="TradeScout")
rt = Runtime()
journal = Journal()


# ----------------------------------------------------------------------------------- helpers
def _idea_json(i, fc, result, sport: str) -> dict:
    strat = get_strategy(i.strategy)
    d = {
        "strategy": i.strategy, "strategy_label": i.strategy_label, "market": i.market, "side": i.side, "selection": i.selection,
        "score": round(i.score, 1), "stars": i.stars, "hit_prob": i.hit_prob, "calibrated_hit_prob": i.calibrated_hit_prob,
        "model_price": i.model_price, "market_price": i.market_price, "edge": i.edge, "expected_roi": i.expected_roi,
        "calibrated_roi": i.calibrated_roi, "win_return": i.win_return, "loss_return": i.loss_return, "stake_pct": round(i.stake_pct, 2),
        "stake_money": round(i.stake_money, 2), "risk_money": round(i.risk_money, 2), "risk_notes": i.risk_notes,
        "confidence": fc.confidence, "liquidity": i.liquidity,
        "historical_strike_rate": i.historical_strike_rate, "historical_sample": i.historical_sample,
        "plan": [asdict(p) for p in i.plan], "rationale": i.rationale, "warnings": i.warnings, "scenarios": i.scenarios,
        "orders": [asdict(o) for o in i.orders],
        "decision": i.decision, "decision_reasons": i.decision_reasons, "evidence": i.evidence,
        "p_conservative": i.p_conservative, "ev_conservative": i.ev_conservative, "ev_model": i.ev_model, "p_market": i.p_market,
        "execution": i.execution, "legs": i.legs, "max_loss_per_unit": i.max_loss_per_unit,
        "verdict": idea_verdict(i.calibrated_hit_prob, i.calibrated_roi, i.edge, i.score, i.decision, i.ev_conservative),
        "best_for": strat.best_for, "avoid_when": strat.avoid_when, "description": strat.description,
        "settlement": strat.settlement, "inplay": strat.inplay, "sport": sport,
        "tracked": any(e.date == i.fixture.date.isoformat() and e.home == i.fixture.home and e.away == i.fixture.away and e.strategy == i.strategy
                       for e in journal.entries),
    }
    if result is not None:
        hit, pnl = strat.settle(fc, result)
        d["settled"] = {"hit": hit, "pnl": pnl}
    return d


def _forecast_json(sport: str, fx, fc) -> dict:
    if sport == "tennis":
        return {
            "sport": "tennis", "surface": fc.surface, "best_of": fc.best_of, "tourney": fx.meta.get("tourney", ""),
            "p_a": fc.p_a, "p_b": 1 - fc.p_a, "elo_a": fc.elo_a, "elo_b": fc.elo_b, "pa_serve": fc.pa_serve, "pb_serve": fc.pb_serve,
            "p_set1_a": fc.p_set1_a, "p_sets": fc.p_sets, "expected_games": fc.expected_games, "p_over": {str(k): v for k, v in fc.p_over.items()},
            "p_first_break_a": fc.p_first_break_a, "p_first_break_b": fc.p_first_break_b, "cond": fc.cond, "confidence": fc.confidence,
            "favourite": fc.fav_name(),
        }
    top_cs = sorted(fc.p_cs.items(), key=lambda kv: -kv[1])[:6]
    return {
        "sport": "football",
        "home_xg": fc.home_xg, "away_xg": fc.away_xg, "p_home": fc.p_home, "p_draw": fc.p_draw, "p_away": fc.p_away,
        "p_over": {str(k): v for k, v in fc.p_over.items()}, "p_btts": fc.p_btts, "p_00": fc.p_cs.get("0-0", 0),
        "p_ht_00": fc.p_ht_00, "p_goal_before": {str(k): v for k, v in fc.p_goal_before.items()},
        "p_fav_scores_first": fc.p_fav_scores_first, "favourite": fx.home if fc.favourite == "home" else fx.away,
        "confidence": fc.confidence, "top_scores": [{"score": s, "p": p} for s, p in top_cs],
        "home_rating": {"attack": fc.home_strength.attack, "defence": fc.home_strength.defence, "games": fc.home_strength.matches_in_window},
        "away_rating": {"attack": fc.away_strength.attack, "defence": fc.away_strength.defence, "games": fc.away_strength.matches_in_window},
    }


def _result_json(sport: str, result) -> Optional[dict]:
    if result is None:
        return None
    if sport == "tennis":
        w, l = result.set_score
        return {"winner": result.winner, "score": result.score, "sets": f"{w}-{l}", "games": result.total_games, "retired": result.retired}
    return {"home": result.home_goals, "away": result.away_goals, "ht_home": result.ht_home, "ht_away": result.ht_away}


def _scan_json(scan: ScanResult, sport: str) -> dict:
    matches = []
    for fx in scan.fixtures:
        fc = scan.forecasts[fx.label]
        result = rt.result_for(sport, fx)
        ideas = [i for i in scan.ideas if i.fixture.label == fx.label]
        trade_ideas = [i for i in ideas if i.decision == "TRADE"]
        best = trade_ideas[0] if trade_ideas else (ideas[0] if ideas else None)
        matches.append({
            "id": fx.fixture_id or fx.label, "home": fx.home, "away": fx.away, "league": fx.league,
            "league_name": LEAGUE_NAMES.get(fx.league, fx.league), "sport": sport,
            "kickoff": fx.kickoff.strftime("%H:%M") if fx.kickoff and fx.kickoff.strftime("%H:%M") != "00:00" else None,
            "best_score": round(best.score, 1) if best else None, "best_stars": best.stars if best else 0,
            "best_strategy": best.strategy_label if best else None, "best_decision": best.decision if best else "NO TRADE",
            "n_trades": len(trade_ideas),
            "forecast": _forecast_json(sport, fx, fc),
            "summary": tennis_summary(fc) if sport == "tennis" else match_summary(fc),
            "result": _result_json(sport, result),
            "ideas": [_idea_json(i, fc, result, sport) for i in ideas],
        })
    decisions = {"TRADE": 0, "NO TRADE": 0, "RESEARCH": 0}
    for i in scan.ideas:
        decisions[i.decision] = decisions.get(i.decision, 0) + 1
    return {
        "sport": sport, "date": scan.date.isoformat(), "weekday": scan.date.strftime("%A %d %B %Y"), "fixtures": len(scan.fixtures),
        "ideas": len(scan.ideas), "decisions": decisions, "model_matches": scan.model_matches, "price_source": scan.price_source,
        "live_fixtures": rt.live_fixtures if sport == "football" else rt.tennis_live, "matches": matches, "skipped": scan.skipped,
        "bank": settings.bank, "generated": datetime.now().strftime("%H:%M:%S"), "prices_as_of": datetime.utcnow().isoformat(timespec="seconds") if scan.price_source != "none" else None,
    }


def _upcoming(sport: str, on: date) -> dict[str, int]:
    try:
        if sport == "football" and rt.live is not None:
            return {d.isoformat(): n for d, n in rt.live.upcoming(on + timedelta(days=1), 10).items()}
    except Exception:
        pass
    prov = rt.tennis if sport == "tennis" else rt.sample
    return {d.isoformat(): len(prov.fixtures(d)) for d in prov.match_days(on + timedelta(days=1), on + timedelta(days=21))[:7]}


def _scan_cached(date_str: str, sport: str, refresh: bool = False) -> dict:
    try:
        on = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    key = f"{sport}:{date_str}"
    with rt.lock:
        hit = rt.cache.get(key)
        ttl = CACHE_TTL_LIVE if (hit and hit[1].get("price_source") not in (None, "none")) else CACHE_TTL_MODEL
        if hit and not refresh and time.time() - hit[0] < ttl:
            return hit[1]
        if on >= date.today() and sport == "football":
            rt.sample.refresh_current_if_stale()
        rt.refresh_exposure()
        try:
            scan = rt.scout_for(sport).scan(on)
            result = _scan_json(scan, sport)
        except Exception as exc:
            name = type(exc).__name__
            code = 429 if name == "RateLimited" else 502
            raise HTTPException(code, str(exc) if name in ("RateLimited", "BadApiKey") else f"{name}: {exc}")
        if not result["matches"]:
            result["upcoming"] = _upcoming(sport, on)
        elif on >= date.today() and result["live_fixtures"]:
            try:
                signals.record(scan.ideas, scan.price_source)
            except Exception:
                pass
        rt.cache[key] = (time.time(), result)
        return result


# ----------------------------------------------------------------------------------- routes
@app.get("/api/status")
def status():
    ex = rt.refresh_exposure()
    leagues_fb = dict((k, LEAGUE_NAMES[k]) for k in FOOTBALL_LEAGUES) if rt.live_fixtures else {k: LEAGUE_NAMES.get(k, k) for k in rt.sample.leagues}
    return {
        "sports": SPORTS, "live_fixtures": rt.live_fixtures, "tennis_live": rt.tennis_live,
        "betfair": rt.betfair_ok, "betfair_error": rt.betfair_error,
        "bank": settings.bank, "kelly_fraction": settings.kelly_fraction, "seasons": rt.sample.available_seasons(),
        "leagues": {"football": leagues_fb, "tennis": {k: LEAGUE_NAMES[k] for k in TENNIS_LEAGUES}},
        "today": date.today().isoformat(), "live_results": rt.results.status, "journal": journal.summary(),
        "betting_mode": settings.betting_mode, "daily_cap": settings.daily_cap, "committed_today": journal.committed_today("live"),
        "commission": settings.commission, "min_edge": settings.min_edge, "max_spread": settings.max_spread,
        "exposure": {"open_total": round(ex.open_total, 2), "by_strategy": ex.by_strategy, "by_sport": ex.by_sport,
                     "realised_today": round(ex.realised_today, 2), "realised_week": round(ex.realised_week, 2),
                     "drawdown": round(ex.drawdown, 4), "current_bank": round(ex.current_bank, 2), "peak_bank": round(ex.peak_bank, 2)},
        "limits": asdict(rt.limits),
        "tennis_data_to": max((r.date for r in rt.tennis.results()), default=None).isoformat() if rt.tennis.results() else None,
    }


@app.get("/api/calendar")
def calendar(start: str, days: int = 10, sport: str = "football"):
    sport = _sport(sport)
    try:
        s = date.fromisoformat(start)
    except ValueError:
        raise HTTPException(400, "start must be YYYY-MM-DD")
    key = f"cal:{sport}:{start}:{days}"
    with rt.lock:
        hit = rt.cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_MODEL:
            return hit[1]
        counts: dict[str, int] = {}
        source = "sample"
        if sport == "football" and rt.live is not None:
            try:
                counts = {d.isoformat(): n for d, n in rt.live.upcoming(s, days).items()}
                source = "live"
            except Exception as exc:
                source = f"live feed error: {exc}"
        elif sport == "tennis" and rt.tennis_live:
            try:
                for k in range(days):
                    d = s + timedelta(days=k)
                    n = len(rt.tennis_scout.fixtures.fixtures(d))
                    if n:
                        counts[d.isoformat()] = n
                source = "live"
            except Exception as exc:
                source = f"live feed error: {exc}"
        if not counts:
            prov = rt.tennis if sport == "tennis" else rt.sample
            for d in prov.match_days(s, s + timedelta(days=days - 1)):
                counts[d.isoformat()] = len(prov.fixtures(d))
        out = {"start": start, "days": days, "counts": counts, "source": source, "sport": sport}
        rt.cache[key] = (time.time(), out)
        return out


def _stats() -> dict:
    if STATS_PATH.exists():
        try:
            return json.loads(STATS_PATH.read_text())
        except Exception:
            return {}
    return {}


@app.get("/api/strategies")
def strategies(sport: str = "football"):
    sport = _sport(sport)
    pool = TENNIS_STRATEGIES if sport == "tennis" else ALL_STRATEGIES
    active = {s.key for s in active_strategies(pool)}
    stats = _stats().get(sport, {}).get("holdout", {}).get("by_strategy", {})
    return [{"key": s.key, "label": s.label, "description": s.description, "best_for": s.best_for, "avoid_when": s.avoid_when,
             "enabled": s.key in active, "enabled_default": s.enabled_default, "settlement": s.settlement, "inplay": s.inplay,
             "holdout": stats.get(s.key)} for s in pool]


@app.get("/api/stats")
def stats():
    rows = signals.load()
    return {"strategy_stats": _stats(), "signals": signals.summary(rows), "signals_recent": rows[-50:]}


@app.get("/api/risk")
def risk(p: float = 0.55, win: float = 1.0, loss: float = -1.0):
    ex = rt.refresh_exposure()
    frac = rt.limits.kelly_fraction * max(0.0, (p * win + (1 - p) * loss)) / max(win, 1e-9)
    return {"exposure": {"open_total": ex.open_total, "by_strategy": ex.by_strategy, "by_sport": ex.by_sport, "drawdown": ex.drawdown,
                         "realised_today": ex.realised_today, "realised_week": ex.realised_week},
            "limits": asdict(rt.limits), "risk_of_ruin": {"fraction": round(min(frac, rt.limits.max_per_trade), 4),
                                                           **risk_of_ruin(p, win, loss, min(frac, rt.limits.max_per_trade))}}


@app.get("/api/scan")
def scan(date_str: str = Query(alias="date"), sport: str = "football", refresh: bool = False):
    return _scan_cached(date_str, _sport(sport), refresh)


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
    betting_mode: Optional[str] = None
    daily_cap: Optional[float] = None
    commission: Optional[float] = None
    min_edge: Optional[float] = None
    enabled_strategies: Optional[list[str]] = None
    clear_football: bool = False
    clear_betfair: bool = False


@app.get("/api/settings")
def get_settings():
    import os
    return {
        "football_data_org_key": _mask(settings.football_data_org_key), "has_football_key": bool(settings.football_data_org_key),
        "betfair_app_key": _mask(settings.betfair_app_key), "betfair_username": settings.betfair_username or "",
        "has_betfair_password": bool(settings.betfair_password), "bank": settings.bank, "kelly_fraction": settings.kelly_fraction,
        "betting_mode": settings.betting_mode, "daily_cap": settings.daily_cap, "committed_today": journal.committed_today("live"),
        "commission": settings.commission, "min_edge": settings.min_edge, "max_spread": settings.max_spread,
        "enabled_strategies": [s.key for s in active_strategies(ALL_STRATEGIES + TENNIS_STRATEGIES)],
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
    if body.betting_mode in ("off", "paper", "live"):
        values["TRADESCOUT_BETTING_MODE"] = body.betting_mode
    if body.daily_cap is not None and body.daily_cap >= 0:
        values["TRADESCOUT_DAILY_CAP"] = str(body.daily_cap)
    if body.commission is not None and 0 <= body.commission <= 0.2:
        values["TRADESCOUT_COMMISSION"] = str(body.commission)
    if body.min_edge is not None and 0 <= body.min_edge <= 0.5:
        values["TRADESCOUT_MIN_EDGE"] = str(body.min_edge)
    if body.enabled_strategies is not None:
        allk = [s.key for s in ALL_STRATEGIES + TENNIS_STRATEGIES]
        chosen = {k for k in body.enabled_strategies if k in allk}
        values["TRADESCOUT_ENABLED"] = ",".join(sorted(chosen)) or None
        values["TRADESCOUT_DISABLED"] = ",".join(sorted(set(allk) - chosen)) or None
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
        tennis = bf._rpc("listEvents", {"filter": {"eventTypeIds": ["2"], "marketStartTime": {
            "from": date.today().isoformat() + "T00:00:00Z", "to": (date.today() + timedelta(days=2)).isoformat() + "T00:00:00Z"}}})
        return {"ok": True, "football_events_next_2_days": len(events), "tennis_events_next_2_days": len(tennis)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ----- bet slip ------------------------------------------------------------------------
class SlipIn(BaseModel):
    date: str
    match_id: str
    strategy: str
    sport: str = "football"
    stake_money: Optional[float] = None


class PlaceIn(SlipIn):
    confirm: bool = False


def _rebuild_idea(body) -> tuple:
    sport = _sport(body.sport)
    scout = rt.scout_for(sport)
    with rt.lock:
        on = date.fromisoformat(body.date)
        fcaster = scout.forecaster(on)
        fx = next((f for f in scout.fixtures.fixtures(on) if (f.fixture_id or f.label) == body.match_id), None)
        if fx is None:
            raise HTTPException(404, "match not found for that date")
        fc = fcaster.forecast(fx)
        strat = get_strategy(body.strategy)
        prices = scout.prices.prices(fx)
        r = strat.evaluate(fc, prices)
        if r is None:
            raise HTTPException(404, "strategy not available for that match")
        rt.refresh_exposure()
        idea = scout.scorer.score(fx, fc, strat, r, prices=prices if prices.available else None, sport=sport)
    stake = body.stake_money if body.stake_money else (idea.stake_money or 2.0)
    return idea, stake, sport


def _price_client(sport: str):
    scout = rt.scout_for(sport)
    return scout.prices if (rt.betfair_ok and hasattr(scout.prices, "resolve")) else None


@app.post("/api/betslip/preview")
def betslip_preview(body: SlipIn):
    idea, stake, sport = _rebuild_idea(body)
    return build_slip(idea, stake, _price_client(sport)).to_dict() | {"decision": idea.decision, "decision_reasons": idea.decision_reasons}


@app.post("/api/betslip/place")
def betslip_place(body: PlaceIn):
    if settings.betting_mode != "live":
        raise HTTPException(403, "Live betting is switched off. Turn it on in Settings > Betting when you are ready.")
    if not body.confirm:
        raise HTTPException(400, "Placement needs confirmation.")
    idea, stake, sport = _rebuild_idea(body)
    client = _price_client(sport)
    if client is None or not hasattr(client, "place_orders"):
        raise HTTPException(400, "Betfair is not connected. Add your application key, username and password in Settings.")
    if idea.decision != "TRADE":
        raise HTTPException(409, f"This idea is {idea.decision}, not a TRADE: " + " ".join(idea.decision_reasons))
    slip = build_slip(idea, stake, client)
    ref = f"ts{int(time.time())}"
    result = place_slip(slip, client, ref, settings.daily_cap, journal.committed_today("live"))
    if result.ok:
        e = journal.add(idea, stake, note="placed on Betfair")
        e.sport = sport
        journal.attach_slip(e.id, [asdict(l) for l in slip.lines], placed="live", refs=result.bet_ids, total=result.committed)
        rt.cache.pop(f"{sport}:{body.date}", None)
    return {"ok": result.ok, "result": result.to_dict(), "slip": slip.to_dict(), "summary": journal.summary(),
            "committed_today": journal.committed_today("live"), "daily_cap": settings.daily_cap}


@app.get("/api/bets/open")
def bets_open():
    if not rt.betfair_ok:
        return {"ok": False, "error": "Betfair is not connected.", "orders": []}
    try:
        orders = rt.betfair.current_orders()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "orders": []}
    out = []
    for o in orders:
        ps = o.get("priceSize", {})
        out.append({"bet_id": o.get("betId"), "market_id": o.get("marketId"), "selection_id": o.get("selectionId"), "side": o.get("side"),
                    "price": ps.get("price"), "size": ps.get("size"), "matched": o.get("sizeMatched"), "remaining": o.get("sizeRemaining"),
                    "status": o.get("status"), "placed": o.get("placedDate"), "avg_price": o.get("averagePriceMatched"),
                    "url": f"https://www.betfair.com/exchange/plus/market/{o.get('marketId')}"})
    return {"ok": True, "orders": out}


class CancelIn(BaseModel):
    market_id: str
    bet_ids: Optional[list[str]] = None


@app.post("/api/bets/cancel")
def bets_cancel(body: CancelIn):
    if not rt.betfair_ok:
        raise HTTPException(400, "Betfair is not connected.")
    try:
        return {"ok": True, "report": rt.betfair.cancel_orders(body.market_id, body.bet_ids)}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/betslip/paper")
def betslip_paper(body: SlipIn):
    idea, stake, sport = _rebuild_idea(body)
    slip = build_slip(idea, stake, _price_client(sport))
    e = journal.add(idea, stake, note="paper slip")
    e.sport = sport
    journal.attach_slip(e.id, [asdict(l) for l in slip.lines])
    rt.cache.pop(f"{sport}:{body.date}", None)
    return {"ok": True, "entry": asdict(e), "slip": slip.to_dict(), "summary": journal.summary()}


# ----- journal -------------------------------------------------------------------------
class TrackIn(BaseModel):
    date: str
    match_id: str
    strategy: str
    sport: str = "football"
    stake_money: Optional[float] = None
    note: str = ""


@app.get("/api/journal")
def get_journal():
    return {"summary": journal.summary(), "entries": [asdict(e) for e in journal.entries]}


@app.post("/api/journal")
def track(body: TrackIn):
    idea, stake, sport = _rebuild_idea(body)
    e = journal.add(idea, body.stake_money if body.stake_money is not None else stake, body.note)
    e.sport = sport
    journal.save()
    rt.cache.pop(f"{sport}:{body.date}", None)
    return {"ok": True, "entry": asdict(e), "summary": journal.summary()}


@app.delete("/api/journal/{entry_id}")
def untrack(entry_id: str):
    ok = journal.remove(entry_id)
    rt.cache.clear()
    return {"ok": ok, "summary": journal.summary()}


@app.post("/api/journal/settle")
def settle():
    rt.sample.refresh_current_if_stale(max_age_hours=6)
    n = journal.settle_multi({"football": rt.scout, "tennis": rt.tennis_scout}, rt.result_for)
    try:
        signals.settle_signals(rt.result_for, lambda sport, d: rt.scout_for(sport).forecaster(d))
    except Exception:
        pass
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
