from __future__ import annotations

from .back_to_lay import BackToLayFavourite
from .base import Strategy
from .correct_score import CorrectScoreBasket
from .lay_the_draw import LayTheDraw
from .overs import BackOversWithInsurance, LayUndersStaged
from .unders import BackUndersTradeOut
from .zero_zero import LayZeroZero

ALL_STRATEGIES: list[Strategy] = [
    LayTheDraw(),
    BackOversWithInsurance(),
    LayUndersStaged(),
    BackToLayFavourite(),
    LayZeroZero(),
    BackUndersTradeOut(),
    CorrectScoreBasket(),
]


def active_strategies(strategies: list[Strategy] | None = None) -> list[Strategy]:
    """Strategies enabled for scanning: the shipped defaults, overridden by TRADESCOUT_ENABLED /
    TRADESCOUT_DISABLED (comma-separated keys) from settings."""
    import os
    pool = strategies if strategies is not None else ALL_STRATEGIES
    enabled = {k.strip() for k in os.getenv("TRADESCOUT_ENABLED", "").split(",") if k.strip()}
    disabled = {k.strip() for k in os.getenv("TRADESCOUT_DISABLED", "").split(",") if k.strip()}
    return [s for s in pool if (s.key in enabled) or (s.enabled_default and s.key not in disabled)]


def get_strategy(key: str) -> Strategy:
    for s in ALL_STRATEGIES:
        if s.key == key:
            return s
    if key.startswith("tn_"):
        from ..tennis.strategies import get_tennis_strategy
        return get_tennis_strategy(key)
    raise KeyError(key)
