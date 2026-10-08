"""Honest calibration + holdout protocol, both sports.

  tuning window  -> strategy strike rates (calibration.json)   never sees the holdout
  holdout window -> trade statistics with the tuning calibration applied, by strategy and season

Everything produced here is MODEL-SYNTHETIC evidence: entry at model-fair prices, in-play exits at
modelled prices, settlement on real results. It measures whether the model's probabilities and the
plans' payoff structure hold up out of sample. It is not, and is never presented as, market
performance. Results are written to data/strategy_stats.json for the app and the report.

Run:  python -m tradescout.eval.holdout
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime

from ..backtest import Backtester
from ..config import REPO_ROOT
from ..data.openfootball import OpenFootballProvider
from ..ranking import Calibration, Scorer
from ..ranking.calibration import DEFAULT_PATH
from ..strategies import ALL_STRATEGIES
from ..tennis.data import TennisProvider
from ..tennis.scout import TennisBacktester
from ..tennis.strategies import TENNIS_STRATEGIES
from .backtest_stats import by_key, compute, season_of

STATS_PATH = REPO_ROOT / "data" / "strategy_stats.json"

FOOTBALL_TUNE = (date(2024, 8, 1), date(2025, 6, 30))
FOOTBALL_HOLD = (date(2025, 8, 1), date(2026, 6, 30))
TENNIS_TUNE = (date(2024, 1, 1), date(2024, 12, 31))
TENNIS_HOLD = (date(2025, 1, 1), date(2026, 1, 31))


def _evidence(strategy_key: str) -> str:
    strat = next((s for s in ALL_STRATEGIES + TENNIS_STRATEGIES if s.key == strategy_key), None)
    if strat is None:
        return "model-synthetic"
    if getattr(strat, "settlement", "exact") == "unverifiable":
        return "unverifiable"
    return "model-fair-static" if not getattr(strat, "inplay", True) else "model-synthetic"


def _stats_block(trades, label_fn) -> dict:
    out = {}
    for k, st in by_key(trades, label_fn, lambda t: _evidence(t.idea.strategy)).items():
        out[k] = st.as_dict()
    return out


def run(verbose: bool = True) -> dict:
    log = (lambda *a: print(*a, flush=True)) if verbose else (lambda *a: None)
    report: dict = {"generated": datetime.utcnow().isoformat(timespec="seconds"), "football": {}, "tennis": {},
                    "evidence_note": "All figures are model-synthetic or model-fair: entry at the model's own fair price, in-play exits at modelled prices, settled on real results. They are not exchange performance."}

    # ---------------- football
    fb = OpenFootballProvider()
    bt = Backtester(fb)
    log("football: tuning backtest", FOOTBALL_TUNE)
    tune = bt.run(*FOOTBALL_TUNE)
    cal = Calibration(generated=report["generated"], price_source="model")
    for t in tune.trades:
        cal.add(t.idea.strategy, t.idea.fixture.league, t.hit, t.pnl, t.idea.hit_prob)
    log("football: holdout backtest", FOOTBALL_HOLD)
    hold = bt.run(*FOOTBALL_HOLD, scorer=Scorer(cal))
    report["football"]["tuning"] = {"window": [d.isoformat() for d in FOOTBALL_TUNE], "by_strategy": _stats_block(tune.trades, lambda t: t.idea.strategy)}
    report["football"]["holdout"] = {"window": [d.isoformat() for d in FOOTBALL_HOLD], "by_strategy": _stats_block(hold.trades, lambda t: t.idea.strategy),
                                     "by_league": _stats_block(hold.trades, lambda t: t.idea.fixture.league),
                                     "by_strategy_league": _stats_block(hold.trades, lambda t: f"{t.idea.strategy}|{t.idea.fixture.league}")}
    # calibration check on holdout: calibrated hit vs actual, per strategy
    chk = {}
    for s in ALL_STRATEGIES:
        ts = [t for t in hold.trades if t.idea.strategy == s.key]
        if ts:
            chk[s.key] = {"n": len(ts), "calibrated_pred": round(sum(t.idea.calibrated_hit_prob for t in ts) / len(ts), 4),
                          "raw_pred": round(sum(t.idea.hit_prob for t in ts) / len(ts), 4), "actual": round(sum(t.hit for t in ts) / len(ts), 4)}
    report["football"]["holdout"]["calibration_check"] = chk

    # ---------------- tennis
    tp = TennisProvider()
    tbt = TennisBacktester(tp)
    log("tennis: tuning backtest", TENNIS_TUNE)
    ttune = tbt.run(*TENNIS_TUNE)
    for t in ttune.trades:
        cal.add(t.idea.strategy, t.idea.fixture.league, t.hit, t.pnl, t.idea.hit_prob)
    log("tennis: holdout backtest", TENNIS_HOLD)
    thold = tbt.run(*TENNIS_HOLD, scorer=Scorer(cal))
    report["tennis"]["tuning"] = {"window": [d.isoformat() for d in TENNIS_TUNE], "by_strategy": _stats_block(ttune.trades, lambda t: t.idea.strategy)}
    report["tennis"]["holdout"] = {"window": [d.isoformat() for d in TENNIS_HOLD], "by_strategy": _stats_block(thold.trades, lambda t: t.idea.strategy),
                                   "by_league": _stats_block(thold.trades, lambda t: t.idea.fixture.league),
                                   "by_surface": _stats_block(thold.trades, lambda t: t.idea.fixture.meta.get("surface", "?"))}
    chk = {}
    for s in TENNIS_STRATEGIES:
        ts = [t for t in thold.trades if t.idea.strategy == s.key]
        if ts:
            chk[s.key] = {"n": len(ts), "calibrated_pred": round(sum(t.idea.calibrated_hit_prob for t in ts) / len(ts), 4),
                          "raw_pred": round(sum(t.idea.hit_prob for t in ts) / len(ts), 4), "actual": round(sum(t.hit for t in ts) / len(ts), 4)}
    report["tennis"]["holdout"]["calibration_check"] = chk

    # ---------------- persist: calibration from tuning windows only; stats for the app
    cal.save(DEFAULT_PATH)
    STATS_PATH.write_text(json.dumps(report, indent=1))
    log("written", DEFAULT_PATH, STATS_PATH)
    return report


if __name__ == "__main__":
    rep = run()
    for sport in ("football", "tennis"):
        print(f"\n== {sport.upper()} HOLDOUT {rep[sport]['holdout']['window']} ==")
        for k, v in rep[sport]["holdout"]["by_strategy"].items():
            print(f"  {k:22s} n={v['n']:5d} strike {v['strike']:.3f} pred {v['predicted']:.3f} roi {v['roi']:+.4f} [{v['roi_ci_low']:+.3f},{v['roi_ci_high']:+.3f}] PF {v['profit_factor']:.2f} maxDD {v['max_drawdown']:.1f} streak {v['longest_losing_streak']} | {v['evidence']}")
        print("  calibration check:", rep[sport]["holdout"]["calibration_check"])
