"""Signals log: every idea the app shows, with the prices it saw, so the system can be judged on
what it said at the time rather than on backtests. Append-only JSONL in data/signals.jsonl.

A signal is written when a scan runs with live fixtures (today or future dates), one row per idea,
with the decision, the conservative edge, the entry quote and a timestamp. `settle_signals` later
attaches the real result and, for plans settled at the result, the realised P/L at the recorded
entry price. In-play plans are settled with the model's exits and flagged as simulated, exactly as
the backtest does, because the exchange's in-play prices are not recorded.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from .config import REPO_ROOT
from .models import TradeIdea

SIGNALS_PATH = REPO_ROOT / "data" / "signals.jsonl"


def record(ideas: Iterable[TradeIdea], price_source: str, path: Path = SIGNALS_PATH) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow().isoformat(timespec="seconds")
    n = 0
    with path.open("a") as fh:
        for i in ideas:
            row = {
                "ts": now, "sport": i.sport, "date": i.fixture.date.isoformat(), "league": i.fixture.league, "home": i.fixture.home,
                "away": i.fixture.away, "fixture_id": i.fixture.fixture_id, "strategy": i.strategy, "decision": i.decision,
                "evidence": i.evidence, "score": round(i.score, 1), "hit_prob": round(i.hit_prob, 4), "calibrated_hit_prob": round(i.calibrated_hit_prob, 4),
                "p_market": i.p_market, "p_conservative": i.p_conservative, "ev_conservative": i.ev_conservative, "ev_model": i.ev_model,
                "execution": i.execution, "price_source": price_source,
                "legs": [{"market": o.market, "selection": o.selection, "side": o.side, "plan_price": o.price, "fraction": o.fraction,
                          "p_model": o.p_model, "quote": (l.get("price") if l else None)} for o, l in zip(i.orders, i.legs or [{}] * len(i.orders))],
                "settled": None,
            }
            fh.write(json.dumps(row) + "\n")
            n += 1
    return n


def load(path: Path = SIGNALS_PATH) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def summary(rows: list[dict]) -> dict:
    """What the log shows so far, by decision and sport."""
    out: dict = {"total": len(rows), "by_decision": {}, "by_sport": {}, "settled": 0, "settled_pnl": 0.0, "trade_signals": 0,
                 "trade_settled": 0, "trade_hits": 0, "trade_pnl": 0.0}
    for r in rows:
        out["by_decision"][r["decision"]] = out["by_decision"].get(r["decision"], 0) + 1
        out["by_sport"][r["sport"]] = out["by_sport"].get(r["sport"], 0) + 1
        if r.get("settled"):
            out["settled"] += 1
            out["settled_pnl"] += r["settled"].get("pnl", 0.0)
            if r["decision"] == "TRADE":
                out["trade_settled"] += 1
                out["trade_hits"] += 1 if r["settled"].get("hit", 0) >= 0.5 else 0
                out["trade_pnl"] += r["settled"].get("pnl", 0.0)
        if r["decision"] == "TRADE":
            out["trade_signals"] += 1
    return out


def settle_signals(result_lookup, forecaster_for_date, path: Path = SIGNALS_PATH) -> int:
    """Attach results to unsettled rows whose match has finished. result_lookup(sport, fixture-like dict) -> result or None;
    forecaster_for_date(sport, date) -> forecaster. Returns the number of rows settled."""
    from .models import Fixture
    from .strategies import get_strategy
    rows = load(path)
    changed = 0
    cache: dict = {}
    for r in rows:
        if r.get("settled") or date.fromisoformat(r["date"]) > date.today():
            continue
        fx = Fixture(date.fromisoformat(r["date"]), r["league"], r["home"], r["away"], None, r.get("fixture_id"), {"sport": r["sport"]})
        res = result_lookup(r["sport"], fx)
        if res is None:
            continue
        key = (r["sport"], r["date"])
        if key not in cache:
            try:
                cache[key] = forecaster_for_date(r["sport"], fx.date)
            except Exception:
                cache[key] = None
        fc_aster = cache[key]
        if fc_aster is None:
            continue
        fc = fc_aster.forecast(fx)
        hit, pnl = get_strategy(r["strategy"]).settle(fc, res)
        r["settled"] = {"hit": hit, "pnl": pnl, "simulated_exits": r["evidence"] != "exchange-priced-static",
                        "result": getattr(res, "score", None) or f"{getattr(res, 'home_goals', '?')}-{getattr(res, 'away_goals', '?')}"}
        changed += 1
    if changed:
        path.write_text("\n".join(json.dumps(x) for x in rows) + "\n")
    return changed
