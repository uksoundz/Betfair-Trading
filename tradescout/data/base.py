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
