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
GET  /api/betfair/diagnose?date=&sport=  which fixtures the exchange prices today and why not; POST /api/betfair/reconnect
POST /api/betslip/preview|paper|place    (sport in body; place takes confirm and an explicit override for non-TRADE ideas)
GET  /api/bets/open  POST /api/bets/cancel
GET  /api/journal  POST /api/journal  DELETE /api/journal/{id}  POST /api/journal/settle
"""
from __future__ import annotations

import json
import os
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
from ..betting import build_slip, customer_ref, min_plan_stake, place_slip
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
CACHE_TTL_LIVE = 45     # seconds, when exchange prices are attached (stale-signal protection)
AUTO_REFRESH_SECONDS = 60  # the UI re-pulls prices this often while Betfair is connected
KEEPALIVE_SECONDS = 600    # Betfair sessions need a keepAlive within 20 minutes on some exchanges
SIGNAL_INTERVAL = 600      # do not log the same day's signals more often than this
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
        self.betfair_setup_error: Optional[str] = None
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
                # lazy: no network at start-up; the connect thread below logs in and probes the key
                self.betfair = BetfairPrices(settings.betfair_app_key, settings.betfair_session_token, settings.betfair_username, settings.betfair_password,
                                             jurisdiction=settings.betfair_jurisdiction, cert_file=settings.betfair_cert_file, key_file=settings.betfair_key_file)
                prices = self.betfair
                bt = BetfairTennis(self.betfair, self.tennis)
                tennis_fixtures, tennis_prices = bt, bt
            except Exception as exc:
                self.betfair_setup_error = str(exc)
        self.scout = Scout(self.results, self.live or self.sample, prices, xi=settings.time_decay_xi, history_days=settings.history_days,
                           max_goals=settings.max_goals)
        self.tennis_scout = TennisScout(self.tennis, tennis_fixtures, tennis_prices)
        self.limits = RiskLimits(kelly_fraction=settings.kelly_fraction)
        for sc in (self.scout, self.tennis_scout):
            sc.scorer.limits = self.limits
            sc.scorer.kelly_fraction = settings.kelly_fraction
        self.cache: dict[str, tuple[float, dict]] = {}
        self.fixture_cache: dict[str, list] = {}  # "sport:date" -> fixtures of the last scan (the slip reuses them: no refetch)
        self.lock = threading.Lock()
        self.last_signal: dict[str, float] = {}
        self.generation = time.time()
        self.results.refresh_in_background()
        if self.betfair is not None:
            threading.Thread(target=self._connect_betfair, daemon=True).start()
            threading.Thread(target=self._keepalive_loop, daemon=True).start()

    def _connect_betfair(self) -> None:
        """Log in, prove the key works on the betting API, find out whether it is a Delayed key."""
        try:
            self.betfair.ensure_session()
            self.betfair.probe()
            self.betfair.app_key_delayed()
        except Exception:
            pass  # recorded in health; shown in the UI

    def _keepalive_loop(self) -> None:
        bf = self.betfair
        while bf is not None:
            time.sleep(KEEPALIVE_SECONDS)
            if globals().get("rt") is not self:  # settings were saved and a new Runtime took over
                return
            try:
                if bf.token:
                    bf.keep_alive()
            except Exception:
                pass

    @property
    def live_fixtures(self) -> bool:
        return self.live is not None

    @property
    def betfair_configured(self) -> bool:
        return self.betfair is not None

    @property
    def betfair_ok(self) -> bool:
        return self.betfair is not None and self.betfair.health.connected

    @property
    def betfair_error(self) -> Optional[str]:
        if self.betfair_setup_error:
            return self.betfair_setup_error
        if self.betfair is None:
            return None
        return self.betfair.health.last_error

    def betfair_state(self) -> dict:
        h = self.betfair.health.to_dict() if self.betfair is not None else None
        return {"configured": self.betfair_configured, "connected": self.betfair_ok, "error": self.betfair_error, "health": h,
                "delayed": (h or {}).get("delayed"), "can_login": (h or {}).get("can_login", False) or settings.betfair_can_login,
                "jurisdiction": settings.betfair_jurisdiction, "cert_login": bool(settings.betfair_cert_file)}

    @property
    def tennis_live(self) -> bool:
        return self.betfair_configured

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


# ----------------------------------------------------------------------------------- auto-trading
from ..autotrade.engine import AutoTrader, new_job  # noqa: E402
from ..autotrade import rules as RULES  # noqa: E402

AUTOTRADE_PATH = Path(os.getenv("TRADESCOUT_AUTOTRADE_PATH", str(REPO_ROOT / "data" / "autotrade.json")))


def _can_commit(amount: float) -> Optional[str]:
    if journal.committed_today("live") + amount > settings.daily_cap + 1e-9:
        return f"the daily cap of £{settings.daily_cap:.2f} would be exceeded."
    return None


def _autotrade_done(job) -> None:
    for e in journal.entries:
        if e.id == job.entry_id:
            e.note = (e.note + "; " if e.note else "") + f"auto-trade {job.state}: {job.status}"
            journal.save()
            break


autotrader = AutoTrader(lambda: rt.betfair, AUTOTRADE_PATH, jurisdiction=lambda: settings.betfair_jurisdiction,
                        can_commit=_can_commit, on_done=_autotrade_done)
autotrader.enabled = lambda: settings.autotrade and settings.betting_mode in ("live", "paper")


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
        ps = scan.price_status.get(fx.label) or {}
        matches.append({
            "price_status": ps.get("status", "none"), "price_note": ps.get("note", ""), "price_as_of": ps.get("as_of"),
            "exchange_event": ps.get("event_name"), "price_candidates": ps.get("candidates") or [], "inplay": bool(ps.get("inplay")),
            "delayed": ps.get("delayed"), "markets": ps.get("markets") or {}, "raw_markets": ps.get("raw_markets") or [], "price_flags": ps.get("flags") or [],
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
    feed = dict(scan.feed) if scan.feed else None
    if feed is not None:
        feed.pop("event_names", None)  # the diagnose route carries the full list
    priced = sum(1 for m in matches if m["price_status"] == "ok")
    return {
        "sport": sport, "date": scan.date.isoformat(), "weekday": scan.date.strftime("%A %d %B %Y"), "fixtures": len(scan.fixtures),
        "ideas": len(scan.ideas), "decisions": decisions, "model_matches": scan.model_matches, "price_source": scan.price_source,
        "live_fixtures": rt.live_fixtures if sport == "football" else rt.tennis_live, "matches": matches, "skipped": scan.skipped,
        "feed": feed, "priced": priced, "betfair": rt.betfair_state(),
        "bank": settings.bank, "generated": datetime.now().strftime("%H:%M:%S"),
        "prices_as_of": (feed or {}).get("fetched_at") if scan.price_source != "none" else None,
        "auto_refresh_seconds": AUTO_REFRESH_SECONDS if rt.betfair_configured else None,
    }


def _upcoming(sport: str, on: date) -> dict[str, int]:
    try:
        if sport == "football" and rt.live is not None:
            return {d.isoformat(): n for d, n in rt.live.upcoming(on + timedelta(days=1), 10).items()}
    except Exception:
        pass
    prov = rt.tennis if sport == "tennis" else rt.sample
    return {d.isoformat(): len(prov.fixtures(d)) for d in prov.match_days(on + timedelta(days=1), on + timedelta(days=21))[:7]}


def _scan_cached(date_str: str, sport: str, refresh: str | bool = False) -> dict:
    """refresh: False (serve the cache while fresh), 'prices' (re-pull prices now, keep the exchange's
    event/catalogue cache) or 'full' / True (also drop the exchange caches so newly listed events appear)."""
    try:
        on = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    mode = "full" if refresh is True or str(refresh).lower() in ("true", "1", "full") else ("prices" if str(refresh).lower() == "prices" else "")
    key = f"{sport}:{date_str}"
    with rt.lock:
        hit = rt.cache.get(key)
        ttl = CACHE_TTL_LIVE if (hit and hit[1].get("price_source") not in (None, "none")) else CACHE_TTL_MODEL
        if hit and not mode and time.time() - hit[0] < ttl:
            return hit[1]
        if mode == "full" and rt.betfair is not None:
            rt.betfair.invalidate()
        if on >= date.today() and sport == "football":
            rt.sample.refresh_current_if_stale()
        rt.refresh_exposure()
        try:
            scan = rt.scout_for(sport).scan(on)
            result = _scan_json(scan, sport)
        except Exception as exc:
            name = type(exc).__name__
            code = 429 if name == "RateLimited" else 502
            raise HTTPException(code, str(exc) if name in ("RateLimited", "BadApiKey", "BetfairError") else f"{name}: {exc}")
        if not result["matches"]:
            result["upcoming"] = _upcoming(sport, on)
        elif on >= date.today() and result["live_fixtures"] and time.time() - rt.last_signal.get(key, 0) > SIGNAL_INTERVAL:
            try:
                signals.record(scan.ideas, scan.price_source)
                rt.last_signal[key] = time.time()
            except Exception:
                pass
        rt.cache[key] = (time.time(), result)
        rt.fixture_cache[key] = list(scan.fixtures)
        return result


# ----------------------------------------------------------------------------------- routes
@app.get("/api/status")
def status():
    ex = rt.refresh_exposure()
    leagues_fb = dict((k, LEAGUE_NAMES[k]) for k in FOOTBALL_LEAGUES) if rt.live_fixtures else {k: LEAGUE_NAMES.get(k, k) for k in rt.sample.leagues}
    bf = rt.betfair_state()
    return {
        "sports": SPORTS, "live_fixtures": rt.live_fixtures, "tennis_live": rt.tennis_live,
        "betfair": rt.betfair_ok, "betfair_error": rt.betfair_error, "betfair_configured": rt.betfair_configured, "betfair_state": bf,
        "betfair_delayed": bf.get("delayed"), "auto_refresh_seconds": AUTO_REFRESH_SECONDS if rt.betfair_configured else None,
        "autotrade": {"enabled": settings.autotrade, "active": len(autotrader.active()), "live": sum(1 for j in autotrader.active() if j.state == "live")},
        "bank": settings.bank, "kelly_fraction": settings.kelly_fraction, "seasons": rt.sample.available_seasons(),
        "leagues": {"football": leagues_fb, "tennis": {k: LEAGUE_NAMES[k] for k in TENNIS_LEAGUES}},
        "today": date.today().isoformat(), "live_results": rt.results.status, "journal": journal.summary(),
        "betting_mode": settings.betting_mode, "daily_cap": settings.daily_cap, "committed_today": journal.committed_today("live"),
        "commission": settings.commission, "min_edge": settings.min_edge, "max_spread": settings.max_spread, "model_weight_scale": settings.model_weight_scale,
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
def scan(date_str: str = Query(alias="date"), sport: str = "football", refresh: str = ""):
    return _scan_cached(date_str, _sport(sport), refresh)


@app.get("/api/betfair/diagnose")
def betfair_diagnose(date_str: str = Query(alias="date"), sport: str = "football"):
    """Everything needed to see why prices are or are not attached: login state, key type, the exchange's
    event names for the day, and per fixture the matched event or the nearest misses."""
    sport = _sport(sport)
    if rt.betfair is None:
        return {"ok": False, "configured": False, "error": rt.betfair_setup_error or "Betfair is not set up: add the application key, username and password in Settings.",
                "betfair": rt.betfair_state()}
    try:
        on = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    scout = rt.scout_for(sport)
    with rt.lock:
        try:
            fixtures = scout.fixtures.fixtures(on)
        except Exception as exc:
            return {"ok": False, "configured": True, "error": f"Could not load fixtures: {exc}", "betfair": rt.betfair_state()}
        client = scout.prices if hasattr(scout.prices, "diagnose") else rt.betfair
        diag = client.diagnose(on, fixtures)
    return {"ok": not diag.get("error") and not (diag.get("report") or {}).get("error"), "configured": True, "sport": sport, **diag, "betfair": rt.betfair_state()}


@app.post("/api/betfair/reconnect")
def betfair_reconnect():
    """Force a fresh login and key check now (after fixing credentials, or when the session died)."""
    if rt.betfair is None:
        return {"ok": False, "error": "Betfair is not set up.", "betfair": rt.betfair_state()}
    bf = rt.betfair
    try:
        if bf.can_login:
            bf.token = None
            bf.login(force=True)
        probe = bf.probe()
        bf.app_key_delayed()
        bf.invalidate()
        rt.cache.clear()
        return {"ok": True, "probe": probe, "betfair": rt.betfair_state()}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "betfair": rt.betfair_state()}


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
    betfair_jurisdiction: Optional[str] = None
    betfair_cert_file: Optional[str] = None
    betfair_key_file: Optional[str] = None
    bank: Optional[float] = None
    kelly_fraction: Optional[float] = None
    betting_mode: Optional[str] = None
    daily_cap: Optional[float] = None
    commission: Optional[float] = None
    min_edge: Optional[float] = None
    model_weight_scale: Optional[float] = None
    autotrade: Optional[bool] = None
    enabled_strategies: Optional[list[str]] = None
    clear_football: bool = False
    clear_betfair: bool = False


@app.get("/api/settings")
def get_settings():
    import os
    return {
        "football_data_org_key": _mask(settings.football_data_org_key), "has_football_key": bool(settings.football_data_org_key),
        "betfair_app_key": _mask(settings.betfair_app_key), "betfair_username": settings.betfair_username or "",
        "has_betfair_password": bool(settings.betfair_password), "betfair_jurisdiction": settings.betfair_jurisdiction,
        "betfair_cert_file": settings.betfair_cert_file or "", "betfair_key_file": settings.betfair_key_file or "",
        "has_session_token": bool(settings.betfair_session_token), "betfair": rt.betfair_state(),
        "bank": settings.bank, "kelly_fraction": settings.kelly_fraction,
        "betting_mode": settings.betting_mode, "daily_cap": settings.daily_cap, "committed_today": journal.committed_today("live"),
        "commission": settings.commission, "min_edge": settings.min_edge, "max_spread": settings.max_spread, "model_weight_scale": settings.model_weight_scale,
        "autotrade": settings.autotrade,
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
        values.update({"BETFAIR_APP_KEY": None, "BETFAIR_USERNAME": None, "BETFAIR_PASSWORD": None, "BETFAIR_SESSION_TOKEN": None,
                       "BETFAIR_CERT_FILE": None, "BETFAIR_KEY_FILE": None})
    else:
        if body.betfair_app_key:
            values["BETFAIR_APP_KEY"] = body.betfair_app_key.strip()
        if body.betfair_username:
            values["BETFAIR_USERNAME"] = body.betfair_username.strip()
        if body.betfair_password:
            values["BETFAIR_PASSWORD"] = body.betfair_password
            values["BETFAIR_SESSION_TOKEN"] = None  # a pasted token must never outrank fresh credentials
        if body.betfair_jurisdiction is not None:
            values["BETFAIR_JURISDICTION"] = body.betfair_jurisdiction.strip().lower() or None
        if body.betfair_cert_file is not None:
            values["BETFAIR_CERT_FILE"] = body.betfair_cert_file.strip() or None
        if body.betfair_key_file is not None:
            values["BETFAIR_KEY_FILE"] = body.betfair_key_file.strip() or None
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
    if body.autotrade is not None:
        values["TRADESCOUT_AUTOTRADE"] = "1" if body.autotrade else "0"
        if not body.autotrade:
            autotrader.stop_all()
    if body.model_weight_scale is not None and 0.5 <= body.model_weight_scale <= 3:
        values["TRADESCOUT_MODEL_WEIGHT_SCALE"] = str(body.model_weight_scale)
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
        bf = BetfairPrices(settings.betfair_app_key, settings.betfair_session_token, settings.betfair_username, settings.betfair_password,
                           jurisdiction=settings.betfair_jurisdiction, cert_file=settings.betfair_cert_file, key_file=settings.betfair_key_file)
        bf.ensure_session()
        bf.probe()
        delayed = bf.app_key_delayed()
        events = bf.events_on(date.today(), "1") + bf.events_on(date.today() + timedelta(days=1), "1")
        tennis = bf.events_on(date.today(), "2") + bf.events_on(date.today() + timedelta(days=1), "2")
        if rt.betfair is not None:  # share the fresh session with the running app
            rt.betfair.token = bf.token
            rt.betfair.health.connected = True
            rt.betfair.health.last_error = rt.betfair.health.last_error_code = None
            rt.betfair.health.delayed = delayed
        return {"ok": True, "football_events_next_2_days": len(events), "tennis_events_next_2_days": len(tennis), "delayed": delayed,
                "login_host": bf.login_url}
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
    override: bool = False  # user explicitly accepts placing an idea the app did not call a TRADE


def _rebuild_idea(body) -> tuple:
    sport = _sport(body.sport)
    scout = rt.scout_for(sport)
    try:
        on = date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    with rt.lock:
        try:
            fixtures = rt.fixture_cache.get(f"{sport}:{body.date}") or scout.fixtures.fixtures(on)
            fx = next((f for f in fixtures if (f.fixture_id or f.label) == body.match_id), None)
            if fx is None:
                raise HTTPException(404, "match not found for that date")
            fcaster = scout.forecaster(on)
            fc = fcaster.forecast(fx)
            strat = get_strategy(body.strategy)
            prices = scout.prices.prices(fx)  # never raises: feed problems come back as status 'error'
            r = strat.evaluate(fc, prices)
            if r is None:
                raise HTTPException(404, "strategy not available for that match")
            rt.refresh_exposure()
            idea = scout.scorer.score(fx, fc, strat, r, prices=prices, sport=sport)
        except HTTPException:
            raise
        except Exception as exc:
            name = type(exc).__name__
            code = 429 if name == "RateLimited" else 400 if name == "BadApiKey" else 502
            raise HTTPException(code, str(exc) if name in ("RateLimited", "BadApiKey", "BetfairError") else f"Could not rebuild the plan: {exc}")
    floor = min_plan_stake(idea)
    if body.stake_money:
        stake = body.stake_money
    elif getattr(body, "for_slip", True):
        # the risk engine's stake, raised to the smallest plan stake at which every leg clears the
        # exchange minimum (a £2 plan split 80/20 cannot be placed); the slip says when this happened
        stake = max(idea.stake_money or 0.0, floor)
    else:
        stake = idea.stake_money or 2.0  # tracking for the record keeps the advised unit
    return idea, stake, sport


def _price_client(sport: str):
    scout = rt.scout_for(sport)
    return scout.prices if (rt.betfair_configured and hasattr(scout.prices, "resolve")) else None


def _placement_gate(idea, slip, stake: float = 0.0) -> dict:
    """Why the Place button is on or off, spelled out. Placement is always user-confirmed per slip.
    Hard blocks cannot be overridden (mode, connection, nothing sendable, no exchange price, the risk
    engine saying no). Soft blocks (not a TRADE, above the per-trade cap, already placed today) need the
    explicit override tick-box and are logged as the user's call."""
    sendable = [l for l in slip.lines if l.market_id and l.selection_id and not l.below_minimum and not l.blocked]
    hard, soft = [], []
    if settings.betting_mode != "live":
        hard.append("Betting mode is " + ("Paper" if settings.betting_mode == "paper" else "Off") + ": switch it to Live in Settings > Betting to send orders.")
    if not rt.betfair_configured:
        hard.append("Betfair is not set up: add the application key, username and password in Settings.")
    elif not rt.betfair_ok:
        hard.append("Betfair is not connected: " + (rt.betfair_error or "no session yet") + ". Use Reconnect in Settings.")
    if not slip.lines:
        hard.append("This plan has no pre-match selections to place.")
    elif not sendable:
        if any(l.blocked for l in slip.lines):
            hard.append("The market is " + ", ".join(sorted({(l.market_status or "").lower().replace("inplay", "in play") for l in slip.lines if l.blocked})) + ": pre-match orders cannot be placed now.")
        else:
            hard.append("None of the lines could be found on the exchange (or they are below the minimum stake), so there is nothing to send.")
    if idea.decision == "RESEARCH":
        hard.append("No exchange price was available when this idea was scored, so it cannot be placed from here. Refresh prices first.")
    if idea.decision == "TRADE" and (idea.stake_money or 0) <= 0:
        hard.append("The risk engine says no stake for this trade: " + (idea.risk_notes[0] if idea.risk_notes else "a loss limit or exposure cap is reached") + ".")
    cap = round(rt.limits.max_per_trade * settings.bank, 2)
    risk = round(sum(l.liability for l in sendable), 2)
    if sendable and risk > cap + 1e-9:
        soft.append(f"This slip risks £{risk:.2f}, above your per-trade cap of £{cap:.2f} ({rt.limits.max_per_trade:.0%} of bank). Tick the override to place it anyway, or lower the stake.")
    already = journal.live_entry(idea.fixture, idea.strategy)
    if already is not None:
        soft.append(f"Already placed on Betfair today (bet ids {', '.join(already.bet_refs[:3]) or 'see Open orders'}). Cancel it under My picks > Open orders first, or tick the override to place it again.")
    if idea.decision == "NO TRADE":
        soft.append(f"The app's decision is NO TRADE, not TRADE: " + " ".join(idea.decision_reasons[:2]) +
                    " You can still place it by ticking the override, which is logged as your call, not the app's.")
    hard_block = bool(hard)
    return {"can_place": not hard_block and not soft, "override_allowed": not hard_block and bool(soft), "override_needed": bool(soft),
            "place_block_reasons": hard + soft, "sendable_lines": len(sendable), "betting_mode": settings.betting_mode, "betfair_connected": rt.betfair_ok,
            "delayed": rt.betfair_state().get("delayed"), "per_trade_cap": cap, "risk_money": risk, "already_placed": already is not None}


@app.post("/api/betslip/preview")
def betslip_preview(body: SlipIn):
    idea, stake, sport = _rebuild_idea(body)
    slip = build_slip(idea, stake, _price_client(sport), jurisdiction=settings.betfair_jurisdiction)
    floor = min_plan_stake(idea)
    advised = round(idea.stake_money or 0.0, 2)
    note = None
    if not body.stake_money and advised and stake > advised + 1e-9:
        note = (f"The risk engine advises £{advised:.2f} for this plan, but every leg must clear the exchange minimum, so the stake has been "
                f"raised to £{stake:.2f}. Lower it if you prefer; legs under the minimum are then left out.")
    elif not body.stake_money and not advised and idea.decision != "TRADE":
        note = f"No stake is advised for a {idea.decision} idea. £{stake:.2f} is the smallest plan stake at which every leg clears the exchange minimum."
    return slip.to_dict() | {"decision": idea.decision, "decision_reasons": idea.decision_reasons, "stake_advised": advised, "stake_floor": floor,
                             "stake_note": note, **_placement_gate(idea, slip, stake)}


@app.post("/api/betslip/place")
def betslip_place(body: PlaceIn):
    if settings.betting_mode != "live":
        raise HTTPException(403, "Live betting is switched off. Turn it on in Settings > Betting when you are ready.")
    if not body.confirm:
        raise HTTPException(400, "Placement needs confirmation.")
    idea, stake, sport = _rebuild_idea(body)
    client = _price_client(sport)
    if client is None or not hasattr(client, "place_orders"):
        raise HTTPException(400, "Betfair is not set up. Add your application key, username and password in Settings.")
    if not rt.betfair_ok:
        try:
            client.bf.ensure_session() if hasattr(client, "bf") else client.ensure_session()
        except Exception as exc:
            raise HTTPException(400, f"Betfair is not connected: {exc}")
    slip = build_slip(idea, stake, client, jurisdiction=settings.betfair_jurisdiction)
    gate = _placement_gate(idea, slip, stake)
    if not gate["can_place"] and not (gate["override_allowed"] and body.override):
        raise HTTPException(409, " ".join(gate["place_block_reasons"]))
    override = gate["override_needed"]
    ref = customer_ref(body.date, body.match_id, body.strategy, sport, round(stake, 2))
    result = place_slip(slip, client, ref, settings.daily_cap, journal.committed_today("live"))
    entry_id = None
    if result.ok or result.pending:
        why = [r.split(":")[0] for r in gate["place_block_reasons"]] if override else []
        note = "placed on Betfair" + (f" (override: {'; '.join(why)})" if override else "") + (" (unconfirmed line, check open orders)" if result.pending else "")
        e = journal.add(idea, stake, note=note)
        e.sport = sport
        journal.attach_slip(e.id, [asdict(l) for l in slip.lines], placed="live", refs=result.bet_ids, total=result.committed, note=note, stake_money=stake)
        entry_id = e.id
        rt.cache.pop(f"{sport}:{body.date}", None)
    return {"ok": result.ok, "result": result.to_dict(), "slip": slip.to_dict(), "summary": journal.summary(), "override": override, "entry_id": entry_id,
            "can_autotrade": bool(idea.rules),
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
    slip = build_slip(idea, stake, _price_client(sport), jurisdiction=settings.betfair_jurisdiction)
    e = journal.add(idea, stake, note="paper slip")
    e.sport = sport
    journal.attach_slip(e.id, [asdict(l) for l in slip.lines], note="paper slip", stake_money=stake)
    rt.cache.pop(f"{sport}:{body.date}", None)
    return {"ok": True, "entry": asdict(e), "slip": slip.to_dict(), "summary": journal.summary()}


# ----- auto-trading ----------------------------------------------------------------------
class ArmIn(BaseModel):
    entry_id: str
    simulate: bool = False
    confirm: bool = False


def _job_view(j) -> dict:
    d = asdict(j)
    names = {"fav": j.home if j.fav == "home" else j.away, "dog": j.away if j.fav == "home" else j.home}
    d["rules_text"] = [RULES.describe(r, j.legs, names) for r in j.rules]
    d["fired_text"] = [r["text"] for r in j.rules if r["id"] in j.fired]
    return d


def _job_for_entry(entry_id: str, simulate: bool) -> tuple:
    """Build (without arming) the auto-trade job for a journal entry, or raise with the reason."""
    e = next((x for x in journal.entries if x.id == entry_id), None)
    if e is None:
        raise HTTPException(404, "That pick is not in My picks.")
    rules = list(e.rules or [])
    fav = e.fav or "home"
    if not rules:
        strat = get_strategy(e.strategy)
        rules = []
        try:
            from ..models import Fixture as _Fx, MarketPrices as _MP
            scout = rt.scout_for(e.sport or "football")
            fx = _Fx(date.fromisoformat(e.date), e.league, e.home, e.away)
            fc = scout.forecaster(fx.date).forecast(fx)
            r = strat.evaluate(fc, _MP())
            rules = list(getattr(r, "rules", []) or []) if r else []
            fav = getattr(fc, "favourite", None) or ("home" if getattr(fc, "p_a", 0.5) >= 0.5 else "away")
        except Exception:
            rules = []
    if not rules:
        raise HTTPException(400, f"{e.strategy_label}: this plan's in-play steps are not automated (they need a judgement call or data the app does not have). Trade it by hand.")
    legs = []
    for l in e.slip or []:
        if not l.get("market_id") or not l.get("selection_id"):
            continue
        legs.append({"market": l["market"], "market_label": l.get("market_label"), "market_id": l["market_id"], "selection": l["selection"],
                     "selection_id": l["selection_id"], "handicap": float(l.get("handicap") or 0.0), "side": l["side"], "entry_price": l["plan_price"],
                     "size": l["size"], "runner_name": l.get("runner_name")})
    if not legs:
        raise HTTPException(400, "This pick has no exchange markets on record. Place it through the bet slip (live, or paper with Betfair connected) first.")
    if simulate or e.placed != "live":
        if rt.betfair is None:
            raise HTTPException(400, "Simulation needs live prices: connect Betfair in Settings.")
        simulate = True
    else:
        if settings.betting_mode != "live":
            raise HTTPException(403, "Betting mode is not Live; arm it in simulate mode or switch to Live in Settings.")
        if not rt.betfair_ok:
            raise HTTPException(400, "Betfair is not connected.")
    if not settings.autotrade:
        raise HTTPException(403, "Auto-trading is switched off. Turn it on in Settings > Betting first.")
    names = {"fav": e.home if fav == "home" else e.away, "dog": e.away if fav == "home" else e.home}
    event_id, market_start = None, None
    try:
        fx = next((f for f in rt.fixture_cache.get(f"{e.sport or 'football'}:{e.date}", []) if f.home == e.home and f.away == e.away), None)
        if fx is not None and hasattr(rt.betfair, "match_fixture") and (e.sport or "football") == "football":
            m = rt.betfair.match_fixture(fx)
            event_id = m.event_id
        elif fx is not None and str(fx.fixture_id or "").startswith("bf:"):
            event_id = str(fx.fixture_id)[3:]
        if event_id:
            for c in rt.betfair.catalogue([event_id]).get(event_id, []):
                if c.get("marketId") == legs[0]["market_id"]:
                    market_start = c.get("marketStartTime")
    except Exception:
        pass
    unit = float(e.stake_money)
    job = new_job(entry_id=e.id, sport=e.sport or "football", date=e.date, home=e.home, away=e.away, fav=fav, strategy=e.strategy,
                  strategy_label=e.strategy_label, event_id=event_id, legs=legs, rules=rules, unit=unit,
                  max_liability=round(unit * 1.0 + 0.01, 2), simulate=simulate, market_start=market_start,
                  bet_ids=list(e.bet_refs or []))
    warnings = []
    if not event_id:
        warnings.append("The exchange event could not be identified, so there is no live score: only rules on the clock and prices can fire.")
    if e.decision and e.decision != "TRADE":
        warnings.append(f"The app's decision on this pick was {e.decision}; you placed it as an override.")
    return job, names, warnings


@app.get("/api/autotrade")
def autotrade_list():
    jobs = sorted(autotrader.jobs.values(), key=lambda j: j.created, reverse=True)
    return {"enabled": settings.autotrade, "betting_mode": settings.betting_mode, "running": bool(autotrader._thread and autotrader._thread.is_alive()),
            "last_tick": autotrader.last_tick, "score_feed_error": autotrader.scores.last_error, "jobs": [_job_view(j) for j in jobs[:50]]}


@app.post("/api/autotrade/preview")
def autotrade_preview(body: ArmIn):
    job, names, warnings = _job_for_entry(body.entry_id, body.simulate)
    return {"job": _job_view(job), "warnings": warnings, "simulate": job.simulate}


@app.post("/api/autotrade/arm")
def autotrade_arm(body: ArmIn):
    if not body.confirm:
        raise HTTPException(400, "Arming needs your confirmation.")
    job, names, warnings = _job_for_entry(body.entry_id, body.simulate)
    job = autotrader.arm(job)
    return {"ok": True, "job": _job_view(job), "warnings": warnings}


class JobIn(BaseModel):
    job_id: str
    leg: Optional[int] = None


@app.post("/api/autotrade/disarm")
def autotrade_disarm(body: JobIn):
    j = autotrader.disarm(body.job_id)
    if j is None:
        raise HTTPException(404, "No such auto-trade.")
    return {"ok": True, "job": _job_view(j)}


@app.post("/api/autotrade/green")
def autotrade_green(body: JobIn):
    if rt.betfair is None:
        raise HTTPException(400, "Betfair is not connected.")
    j = autotrader.green_now(body.job_id, body.leg)
    if j is None:
        raise HTTPException(404, "No such auto-trade.")
    return {"ok": True, "job": _job_view(j)}


@app.post("/api/autotrade/stop_all")
def autotrade_stop_all():
    return {"ok": True, "stopped": autotrader.stop_all()}


# ----- journal -------------------------------------------------------------------------
class TrackIn(BaseModel):
    date: str
    match_id: str
    strategy: str
    sport: str = "football"
    stake_money: Optional[float] = None
    note: str = ""
    for_slip: bool = False


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
    if autotrader.active():
        autotrader.start()
        print(f"  Auto-trading: {len(autotrader.active())} armed plan(s) resumed.", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
