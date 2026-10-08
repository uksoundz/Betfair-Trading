"""The daily pipeline: fixtures -> forecasts -> strategies -> ranked ideas."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from .data.base import FixtureProvider, NoPrices, PriceProvider, ResultProvider
from .model import Forecaster
from .models import Fixture, MatchForecast, TradeIdea
from .ranking import Calibration, Scorer
from .strategies import ALL_STRATEGIES, Strategy


@dataclass
class ScanResult:
    date: date
    fixtures: list[Fixture]
    forecasts: dict[str, MatchForecast]
    ideas: list[TradeIdea]
    model_matches: int
    price_source: str
    skipped: list[str] = field(default_factory=list)

    def top(self, n: int = 20, min_score: float = 0.0) -> list[TradeIdea]:
        return [i for i in self.ideas if i.score >= min_score][:n]

    def best_per_match(self) -> list[TradeIdea]:
        seen: set[str] = set()
        out = []
        for i in self.ideas:
            if i.fixture.label not in seen:
                seen.add(i.fixture.label)
                out.append(i)
        return out


class Scout:
    def __init__(self, results: ResultProvider, fixtures: FixtureProvider, prices: Optional[PriceProvider] = None,
                 strategies: Iterable[Strategy] | None = None, calibration: Optional[Calibration] = None, **model_kw):
        self.results = results
        self.fixtures = fixtures
        self.prices = prices or NoPrices()
        self.strategies = list(strategies or ALL_STRATEGIES)
        self.scorer = Scorer(calibration or Calibration.load())
        self.model_kw = model_kw

    def forecaster(self, as_of: date, leagues: Iterable[str] | None = None) -> Forecaster:
        return Forecaster.fit(self.results.results(leagues=leagues, before=as_of), as_of, **self.model_kw)

    def scan(self, on: date, leagues: Iterable[str] | None = None, forecaster: Forecaster | None = None) -> ScanResult:
        fixtures = self.fixtures.fixtures(on, leagues)
        fcaster = forecaster or self.forecaster(on)
        forecasts: dict[str, MatchForecast] = {}
        ideas: list[TradeIdea] = []
        skipped: list[str] = []
        source = "none"
        for fx in fixtures:
            fc = fcaster.forecast(fx)
            forecasts[fx.label] = fc
            try:
                prices = self.prices.prices(fx)
            except Exception as exc:  # a price feed hiccup must never kill the scan
                skipped.append(f"{fx.label}: price feed error {exc}")
                prices = NoPrices().prices(fx)
            if prices.available:
                source = prices.source
            for strat in self.strategies:
                r = strat.evaluate(fc, prices)
                if r is None:
                    continue
                ideas.append(self.scorer.score(fx, fc, strat, r))
        ideas.sort(key=lambda i: -i.score)
        return ScanResult(on, fixtures, forecasts, ideas, fcaster.model.n_matches, source, skipped)
