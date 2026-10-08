"""Local web app: the point-and-click front end. Start with `tradescout app` (or app.bat).

GET /                          the single-page UI
GET /api/scan?date=YYYY-MM-DD  fixtures, forecasts, ideas (grouped per match) for that day
GET /api/strategies            strategy library with plain-English guidance
GET /api/status                data sources, keys present, model info
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import LEAGUE_NAMES, settings
from ..data.base import NoPrices
from ..data.openfootball import OpenFootballProvider
from ..report.narrative import idea_verdict, match_summary
from ..scout import ScanResult, Scout
from ..strategies import ALL_STRATEGIES, get_strategy

STATIC = Path(__file__).parent / "static"
CACHE_TTL = 600  # seconds


def build_scout() -> tuple[Scout, OpenFootballProvider]:
    sample = OpenFootballProvider()
    fixtures = sample
    prices = NoPrices()
    if settings.football_data_org_key:
        from ..data.football_data_org import FootballDataOrgProvider
        fixtures = FootballDataOrgProvider(settings.football_data_org_key)
    if settings.has_betfair:
        from ..data.betfair import BetfairPrices
        prices = BetfairPrices(settings.betfair_app_key, settings.betfair_session_token, settings.betfair_username, settings.betfair_password)
    return Scout(sample, fixtures, prices, xi=settings.time_decay_xi, history_days=settings.history_days), sample


app = FastAPI(title="TradeScout")
_scout, _sample = build_scout()
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _idea_json(i, fc, result) -> dict:
    d = {
        "strategy": i.strategy, "strategy_label": i.strategy_label, "market": i.market, "side": i.side, "selection": i.selection,
        "score": round(i.score, 1), "hit_prob": i.hit_prob, "calibrated_hit_prob": i.calibrated_hit_prob,
        "model_price": i.model_price, "market_price": i.market_price, "edge": i.edge, "expected_roi": i.expected_roi,
        "calibrated_roi": i.calibrated_roi,
        "win_return": i.win_return, "loss_return": i.loss_return, "stake_pct": round(i.stake_pct, 2),
        "stake_money": round(settings.bank * i.stake_pct / 100, 2), "confidence": fc.confidence, "liquidity": i.liquidity,
        "historical_strike_rate": i.historical_strike_rate, "historical_sample": i.historical_sample,
        "plan": [asdict(p) for p in i.plan], "rationale": i.rationale, "warnings": i.warnings,
        "verdict": idea_verdict(i.calibrated_hit_prob, i.calibrated_roi, i.edge, i.score),
    }
    strat = get_strategy(i.strategy)
    d["best_for"] = strat.best_for
    d["avoid_when"] = strat.avoid_when
    if result is not None:
        hit, pnl = strat.settle(fc, result)
        d["settled"] = {"hit": hit, "pnl": pnl}
    return d


def _scan_json(scan: ScanResult) -> dict:
    matches = []
    for fx in scan.fixtures:
        fc = scan.forecasts[fx.label]
        result = _sample.result_for(fx)
        ideas = [i for i in scan.ideas if i.fixture.label == fx.label]
        top_cs = sorted(fc.p_cs.items(), key=lambda kv: -kv[1])[:6]
        matches.append({
            "id": fx.fixture_id or fx.label, "home": fx.home, "away": fx.away, "league": fx.league,
            "league_name": LEAGUE_NAMES.get(fx.league, fx.league),
            "kickoff": fx.kickoff.strftime("%H:%M") if fx.kickoff else None,
            "best_score": round(ideas[0].score, 1) if ideas else None,
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
        "live_fixtures": _scout.fixtures is not _sample, "matches": matches, "skipped": scan.skipped,
        "bank": settings.bank, "generated": datetime.now().strftime("%H:%M:%S"),
    }


@app.get("/api/status")
def status():
    return {
        "live_fixtures": bool(settings.football_data_org_key), "betfair": settings.has_betfair, "bank": settings.bank,
        "seasons": _sample.available_seasons(), "leagues": {k: LEAGUE_NAMES.get(k, k) for k in _sample.leagues},
        "today": date.today().isoformat(),
    }


@app.get("/api/calendar")
def calendar(start: str, days: int = 10):
    """Fixture counts per day so the UI can show clickable match days."""
    from datetime import timedelta
    try:
        s = date.fromisoformat(start)
    except ValueError:
        raise HTTPException(400, "start must be YYYY-MM-DD")
    key = f"cal:{start}:{days}"
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        counts: dict[str, int] = {}
        source = "sample"
        if _scout.fixtures is not _sample:
            try:
                counts = {d.isoformat(): n for d, n in _scout.fixtures.upcoming(s, days).items()}  # type: ignore[attr-defined]
                source = "live"
            except Exception as exc:
                source = f"live feed error: {exc}"
        if not counts:
            for d in _sample.match_days(s, s + timedelta(days=days - 1)):
                counts[d.isoformat()] = len(_sample.fixtures(d))
        out = {"start": start, "days": days, "counts": counts, "source": source}
        _cache[key] = (time.time(), out)
        return out


@app.get("/api/strategies")
def strategies():
    return [{"key": s.key, "label": s.label, "description": s.description, "best_for": s.best_for, "avoid_when": s.avoid_when}
            for s in ALL_STRATEGIES]


@app.get("/api/scan")
def scan(date_str: str = Query(alias="date"), refresh: bool = False):
    try:
        on = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    with _lock:
        hit = _cache.get(date_str)
        if hit and not refresh and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        if on >= date.today():
            _sample.refresh_current_if_stale()
        try:
            result = _scan_json(_scout.scan(on))
        except Exception as exc:  # feed error: surface it to the UI instead of a 500 page
            raise HTTPException(502, f"{type(exc).__name__}: {exc}")
        if not result["matches"]:
            upcoming = {}
            try:
                if _scout.fixtures is not _sample:
                    from datetime import timedelta
                    upcoming = {d.isoformat(): n for d, n in _scout.fixtures.upcoming(on + timedelta(days=1), 10).items()}  # type: ignore[attr-defined]
            except Exception:
                upcoming = {}
            if not upcoming:
                from datetime import timedelta
                upcoming = {d.isoformat(): len(_sample.fixtures(d)) for d in _sample.match_days(on + timedelta(days=1), on + timedelta(days=21))[:7]}
            result["upcoming"] = upcoming
        _cache[date_str] = (time.time(), result)
        return result


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
    """Open the browser only once the server answers, so the user never lands on another program."""
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
    print(f"  Live fixtures: {'ON' if settings.football_data_org_key else 'off'}   Betfair prices: {'ON' if settings.has_betfair else 'off'}")
    print("  Keep this window open while you use the app. Press Ctrl+C to stop.")
    print("=" * 64, flush=True)
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url, host, port), daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
