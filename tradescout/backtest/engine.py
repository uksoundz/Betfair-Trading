"""Walk-forward backtest.

For every match day in the range the model is refitted using only matches *before* that day
(refit cadence configurable), each strategy is evaluated exactly as the daily scan does, and then
settled against the real result. No look-ahead: the ranking a trader would have seen that morning
is the one that is scored.

Outputs
  * per strategy / league: n, strike rate, predicted strike rate, ROI per unit risked
  * by score band: does a higher rank score actually mean a better trade? (the honesty check)
  * a Calibration object that the live scorer consumes
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Optional

from ..data.base import NoPrices, PriceProvider
from ..data.openfootball import OpenFootballProvider
from ..model import Forecaster
from ..models import MatchResult, TradeIdea
from ..ranking import Calibration, Scorer
from ..strategies import ALL_STRATEGIES, Strategy

SCORE_BANDS = [(0, 40), (40, 50), (50, 60), (60, 70), (70, 101)]


@dataclass
class Trade:
    idea: TradeIdea
    result: MatchResult
    hit: float
    pnl: float


@dataclass
class BacktestReport:
    trades: list[Trade] = field(default_factory=list)
    calibration: Calibration = field(default_factory=Calibration)
    days: int = 0

    def by_strategy(self) -> list[dict]:
        rows = defaultdict(lambda: {"n": 0, "hits": 0, "pnl": 0.0, "pred": 0.0})
        for t in self.trades:
            r = rows[t.idea.strategy_label]
            r["n"] += 1
            r["hits"] += t.hit
            r["pnl"] += t.pnl
            r["pred"] += t.idea.hit_prob
        return [{"strategy": k, "n": v["n"], "strike": v["hits"] / v["n"], "predicted": v["pred"] / v["n"], "roi": v["pnl"] / v["n"]}
                for k, v in sorted(rows.items())]

    def by_league(self) -> list[dict]:
        rows = defaultdict(lambda: {"n": 0, "hits": 0, "pnl": 0.0})
        for t in self.trades:
            r = rows[t.idea.fixture.league]
            r["n"] += 1
            r["hits"] += t.hit
            r["pnl"] += t.pnl
        return [{"league": k, "n": v["n"], "strike": v["hits"] / v["n"], "roi": v["pnl"] / v["n"]} for k, v in sorted(rows.items())]

    def by_score_band(self) -> list[dict]:
        out = []
        for lo, hi in SCORE_BANDS:
            ts = [t for t in self.trades if lo <= t.idea.score < hi]
            if not ts:
                continue
            out.append({"band": f"{lo}-{min(hi, 100)}", "n": len(ts), "strike": sum(t.hit for t in ts) / len(ts),
                        "roi": sum(t.pnl for t in ts) / len(ts)})
        return out

    def top_n_daily(self, n: int = 3) -> dict:
        """What if you only took the n highest-scored ideas each day?"""
        by_day: dict[date, list[Trade]] = defaultdict(list)
        for t in self.trades:
            by_day[t.idea.fixture.date].append(t)
        picked = []
        for ts in by_day.values():
            picked += sorted(ts, key=lambda t: -t.idea.score)[:n]
        if not picked:
            return {"n": 0, "strike": 0.0, "roi": 0.0}
        return {"n": len(picked), "strike": sum(t.hit for t in picked) / len(picked), "roi": sum(t.pnl for t in picked) / len(picked)}


class Backtester:
    def __init__(self, provider: OpenFootballProvider, strategies: Iterable[Strategy] | None = None,
                 prices: Optional[PriceProvider] = None, refit_every_days: int = 7, **model_kw):
        self.provider = provider
        self.strategies = list(strategies or ALL_STRATEGIES)
        self.prices = prices or NoPrices()
        self.refit_every = refit_every_days
        self.model_kw = model_kw

    def run(self, start: date, end: date, leagues: Iterable[str] | None = None,
            progress: Optional[Callable[[date, int], None]] = None, scorer: Optional[Scorer] = None) -> BacktestReport:
        report = BacktestReport()
        report.calibration.generated = datetime.utcnow().isoformat(timespec="seconds")
        report.calibration.price_source = "model" if isinstance(self.prices, NoPrices) else "exchange"
        scorer = scorer or Scorer(Calibration())  # no calibration feedback inside the backtest itself
        all_results = self.provider.results(leagues=leagues)
        fcaster: Forecaster | None = None
        fitted_on: date | None = None
        for day in self.provider.match_days(start, end, leagues):
            if fcaster is None or (day - fitted_on).days >= self.refit_every:
                hist = [r for r in all_results if r.date < day]
                if len(hist) < 200:
                    continue
                fcaster = Forecaster.fit(hist, day, **self.model_kw)
                fitted_on = day
            fixtures = self.provider.fixtures(day, leagues)
            for fx in fixtures:
                result = self.provider.result_for(fx)
                if result is None:
                    continue
                fc = fcaster.forecast(fx)
                prices = self.prices.prices(fx)
                for strat in self.strategies:
                    r = strat.evaluate(fc, prices)
                    if r is None:
                        continue
                    idea = scorer.score(fx, fc, strat, r)
                    hit, pnl = strat.settle(fc, result)
                    report.trades.append(Trade(idea, result, hit, pnl))
                    report.calibration.add(strat.key, fx.league, hit, pnl, r.hit_prob)
            report.days += 1
            if progress:
                progress(day, len(report.trades))
        return report
