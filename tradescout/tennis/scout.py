"""Tennis daily pipeline and walk-forward backtest, mirroring the football Scout."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Iterable, Optional

from ..data.base import NoPrices, PriceProvider, fetch_prices
from ..models import Fixture, MarketPrices, TradeIdea
from ..ranking import Calibration, Scorer
from ..scout import ScanResult
from .data import TennisProvider, TennisResult
from .forecast import TennisForecast, TennisForecaster
from ..strategies.registry import active_strategies
from .strategies import TENNIS_STRATEGIES

TENNIS_LIQUIDITY = {"atp.gs": 1.0, "atp.1000": 0.85, "atp.500": 0.65, "atp.finals": 0.9, "atp.250": 0.5, "atp.olympics": 0.7, "atp.tour": 0.5}


class TennisScout:
    def __init__(self, results: TennisProvider, fixtures, prices: Optional[PriceProvider] = None, calibration: Optional[Calibration] = None):
        self.results = results
        self.fixtures = fixtures
        self.prices = prices or NoPrices()
        self.strategies = active_strategies(TENNIS_STRATEGIES)
        self.scorer = Scorer(calibration or Calibration.load())

    def forecaster(self, as_of: date) -> TennisForecaster:
        return TennisForecaster.fit(self.results.results(before=as_of), as_of)

    def scan(self, on: date, leagues: Iterable[str] | None = None, forecaster: TennisForecaster | None = None) -> ScanResult:
        fixtures = self.fixtures.fixtures(on, leagues)
        fcaster = forecaster or self.forecaster(on)
        forecasts: dict[str, TennisForecast] = {}
        ideas: list[TradeIdea] = []
        skipped: list[str] = []
        price_status: dict[str, dict] = {}
        source = "none"
        per_fixture, feed = fetch_prices(self.prices, fixtures)
        for fx in fixtures:
            fc = fcaster.forecast(fx)
            forecasts[fx.label] = fc
            prices = per_fixture.get(fx.label) or MarketPrices()
            if prices.status == "error":
                skipped.append(f"{fx.label}: {prices.note}")
            if prices.available:
                source = prices.source
            price_status[fx.label] = prices.diagnostics()
            for strat in self.strategies:
                r = strat.evaluate(fc, prices)
                if r is None:
                    continue
                ideas.append(self.scorer.score(fx, fc, strat, r, prices=prices, sport="tennis"))
        ideas.sort(key=lambda i: -i.score)
        return ScanResult(on, fixtures, forecasts, ideas, fcaster.elo.n_matches, source, skipped, feed, price_status)


@dataclass
class TennisTrade:
    idea: TradeIdea
    result: TennisResult
    hit: float
    pnl: float


@dataclass
class TennisBacktestReport:
    trades: list[TennisTrade] = field(default_factory=list)
    calibration: Calibration = field(default_factory=Calibration)
    days: int = 0

    def by_strategy(self) -> list[dict]:
        rows = defaultdict(lambda: {"n": 0, "hits": 0.0, "pnl": 0.0, "pred": 0.0})
        for t in self.trades:
            r = rows[t.idea.strategy_label]
            r["n"] += 1
            r["hits"] += t.hit
            r["pnl"] += t.pnl
            r["pred"] += t.idea.hit_prob
        return [{"strategy": k, "n": v["n"], "strike": v["hits"] / v["n"], "predicted": v["pred"] / v["n"], "roi": v["pnl"] / v["n"]}
                for k, v in sorted(rows.items())]

    def by_score_band(self) -> list[dict]:
        out = []
        for lo, hi in [(0, 40), (40, 50), (50, 60), (60, 101)]:
            ts = [t for t in self.trades if lo <= t.idea.score < hi]
            if ts:
                out.append({"band": f"{lo}-{min(hi, 100)}", "n": len(ts), "strike": sum(t.hit for t in ts) / len(ts), "roi": sum(t.pnl for t in ts) / len(ts)})
        return out


class TennisBacktester:
    def __init__(self, provider: TennisProvider, refit_every_days: int = 7):
        self.provider = provider
        self.refit_every = refit_every_days

    def run(self, start: date, end: date, progress: Optional[Callable[[date, int], None]] = None,
            scorer: Optional[Scorer] = None) -> TennisBacktestReport:
        rep = TennisBacktestReport()
        rep.calibration.generated = datetime.utcnow().isoformat(timespec="seconds")
        scorer = scorer or Scorer(Calibration())
        all_results = self.provider.results()
        fcaster = None
        fitted_on = None
        for day in self.provider.match_days(start, end):
            if fcaster is None or (day - fitted_on).days >= self.refit_every:
                hist = [r for r in all_results if r.date < day]
                if len(hist) < 500:
                    continue
                fcaster = TennisForecaster.fit(hist, day)
                fitted_on = day
            for fx in self.provider.fixtures(day):
                result = self.provider.result_for(fx)
                if result is None:
                    continue
                fc = fcaster.forecast(fx)
                for strat in TENNIS_STRATEGIES:
                    r = strat.evaluate(fc, NoPrices().prices(fx))
                    if r is None:
                        continue
                    idea = scorer.score(fx, fc, strat, r)
                    hit, pnl = strat.settle(fc, result)
                    rep.trades.append(TennisTrade(idea, result, hit, pnl))
                    rep.calibration.add(strat.key, fx.league, hit, pnl, r.hit_prob)
            rep.days += 1
            if progress:
                progress(day, len(rep.trades))
        return rep


def merge_calibration(new: Calibration, path=None) -> Calibration:
    """Write tennis entries into the shared calibration file without touching football's."""
    from ..ranking.calibration import DEFAULT_PATH
    existing = Calibration.load(path or DEFAULT_PATH)
    for k in list(existing.entries):
        if k.startswith("tn_"):
            del existing.entries[k]
    existing.entries.update(new.entries)
    existing.generated = new.generated
    existing.save(path or DEFAULT_PATH)
    return existing
