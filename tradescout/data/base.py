"""Provider protocols. The scout only ever talks to these three interfaces, so a new data feed
(API-Football, Sofascore, a CSV dump) is one class away."""
from __future__ import annotations

from datetime import date
from typing import Iterable, Protocol, runtime_checkable

from ..models import Fixture, MarketPrices, MatchResult


@runtime_checkable
class ResultProvider(Protocol):
    def results(self, leagues: Iterable[str] | None = None, before: date | None = None) -> list[MatchResult]:
        """Completed matches, optionally restricted to leagues and strictly before a date."""


@runtime_checkable
class FixtureProvider(Protocol):
    def fixtures(self, on: date, leagues: Iterable[str] | None = None) -> list[Fixture]:
        """Fixtures kicking off on the given calendar day."""


@runtime_checkable
class PriceProvider(Protocol):
    def prices(self, fixture: Fixture) -> MarketPrices:
        """Best exchange prices for a fixture. Return MarketPrices() when unknown."""


class NoPrices:
    """Default price provider: no market feed connected."""

    def prices(self, fixture: Fixture) -> MarketPrices:  # noqa: D401
        return MarketPrices()


def fetch_prices(provider, fixtures: list[Fixture]) -> tuple[dict[str, MarketPrices], dict | None]:
    """Prices for a day's fixtures keyed by fixture label, plus the feed report when the provider
    produces one (the Betfair client does). A feed failure never raises: every fixture comes back with
    status 'error' and the reason, so the scan can show it instead of silently reporting no prices."""
    if isinstance(provider, NoPrices) or provider is None:
        return {fx.label: MarketPrices() for fx in fixtures}, None
    if hasattr(provider, "prices_for_day"):
        try:
            per, report = provider.prices_for_day(fixtures)
            return per, (report.to_dict() if hasattr(report, "to_dict") else report)
        except Exception as exc:  # the feed must never kill the scan
            note = f"Price feed error: {exc}"
            return {fx.label: MarketPrices(status="error", note=note) for fx in fixtures}, {"source": "betfair", "error": str(exc), "fixtures": len(fixtures)}
    out: dict[str, MarketPrices] = {}
    for fx in fixtures:
        try:
            out[fx.label] = provider.prices(fx)
        except Exception as exc:
            out[fx.label] = MarketPrices(status="error", note=f"Price feed error: {exc}")
    return out, None
