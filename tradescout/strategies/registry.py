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


def get_strategy(key: str) -> Strategy:
    for s in ALL_STRATEGIES:
        if s.key == key:
            return s
    raise KeyError(key)
