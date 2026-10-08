"""Bet slip: turns a plan's pre-match selections into concrete, reviewable lines.

For each OrderLeg it works out the size (stake for backs, backer's stake for lays from the
liability), rounds prices onto Betfair's tick ladder and sizes to pence, flags lines below the
exchange minimum, and, when a Betfair connection exists, looks up the market and selection ids
and the best price currently available so the user can see whether the plan price is there.

Nothing in this module sends orders. The slip is for the user to review; paper mode records it in
the journal so the strategy can be judged on the prices that were actually available.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

from .models import Fixture, TradeIdea

MIN_STAKE = 2.00  # Betfair GBP minimum per bet
BETFAIR_MARKET_URL = "https://www.betfair.com/exchange/plus/football/market/{market_id}"

# Betfair price ladder: (upper bound, tick)
_LADDER = [(2.0, 0.01), (3.0, 0.02), (4.0, 0.05), (6.0, 0.1), (10.0, 0.2), (20.0, 0.5), (30.0, 1.0), (50.0, 2.0), (100.0, 5.0), (1000.0, 10.0)]


def round_to_tick(price: float, side: str) -> float:
    """Snap a price onto the exchange ladder. Backs round down (easier to match, slightly worse
    price); lays round up (same idea). Clamped to 1.01-1000."""
    price = min(max(price, 1.01), 1000.0)
    lo = 1.0
    for hi, tick in _LADDER:
        if price <= hi + 1e-9:
            steps = round((price - lo) / tick, 6)
            snapped = lo + (math.floor(steps) if side == "back" else math.ceil(steps)) * tick
            snapped = min(max(round(snapped, 2), max(lo, 1.01)), hi)
            return float(snapped)
        lo = hi
    return 1000.0


@dataclass
class SlipLine:
    market: str
    market_label: str
    selection: str
    runner_name: str
    side: str
    plan_price: float
    size: float            # backer's stake placed on the exchange
    liability: float       # what you can lose on this line
    payout: float          # what you win if it lands (before commission)
    fraction: float
    note: str = ""
    live_price: Optional[float] = None  # best price available right now on the exchange
    price_ok: Optional[bool] = None     # live price at least as good as the plan price
    market_id: Optional[str] = None
    selection_id: Optional[int] = None
    betfair_url: Optional[str] = None  # opens this market on the Betfair website
    below_minimum: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class BetSlip:
    fixture: str
    date: str
    strategy: str
    strategy_label: str
    stake_money: float
    lines: list[SlipLine]
    total_staked: float
    total_liability: float
    price_source: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {**asdict(self), "lines": [asdict(l) for l in self.lines]}


MARKET_LABELS = {"MATCH_ODDS": "Match Odds", "OVER_UNDER_25": "Over/Under 2.5 Goals", "OVER_UNDER_15": "Over/Under 1.5 Goals",
                 "CORRECT_SCORE": "Correct Score", "BOTH_TEAMS_TO_SCORE": "Both Teams to Score"}


def runner_label(fixture: Fixture, leg) -> str:
    if leg.market == "MATCH_ODDS":
        return {"home": fixture.home, "away": fixture.away, "draw": "The Draw"}.get(leg.selection, leg.selection)
    return leg.selection


def build_slip(idea: TradeIdea, stake_money: float, bf=None, min_stake: float = MIN_STAKE) -> BetSlip:
    """Compose the slip for one plan. `bf` is an optional Betfair client exposing
    `resolve(fixture, market, selection)` -> (market_id, selection_id, best_back, best_lay) or None."""
    fx = idea.fixture
    lines: list[SlipLine] = []
    warnings: list[str] = []
    source = "model"
    for leg in idea.orders:
        price = round_to_tick(leg.price, leg.side)
        unit = stake_money * leg.fraction
        if leg.side == "back":
            size = unit
            liability = size
            payout = size * (price - 1)
        else:  # lay: unit is the liability
            size = unit / (price - 1)
            liability = size * (price - 1)
            payout = size
        line = SlipLine(leg.market, MARKET_LABELS.get(leg.market, leg.market), leg.selection, runner_label(fx, leg), leg.side,
                        price, round(size, 2), round(liability, 2), round(payout, 2), leg.fraction, leg.note)
        if line.size < min_stake:
            line.below_minimum = True
            line.warnings.append(f"Exchange minimum is £{min_stake:.2f} per bet; this line is £{line.size:.2f}. Raise the stake or drop the line.")
        if bf is not None:
            try:
                found = bf.resolve(fx, leg.market, leg.selection)
            except Exception as exc:  # a price lookup failure must not kill the slip
                found = None
                line.warnings.append(f"Price lookup failed: {exc}")
            if found:
                line.market_id, line.selection_id, best_back, best_lay = found
                line.betfair_url = BETFAIR_MARKET_URL.format(market_id=line.market_id)
                live = best_back if leg.side == "back" else best_lay
                line.live_price = live
                source = "betfair"
                if live:
                    line.price_ok = (live >= price) if leg.side == "back" else (live <= price)
                    if not line.price_ok:
                        line.warnings.append(f"Plan needs {price:.2f} or {'better' if leg.side == 'back' else 'lower'}; exchange currently offers {live:.2f}.")
            else:
                line.warnings.append("Market or selection not found on the exchange for this fixture.")
        lines.append(line)
    if not lines:
        warnings.append("This plan has no pre-match selections (it is traded in play).")
    if any(l.below_minimum for l in lines):
        warnings.append("One or more lines are below the exchange minimum stake.")
    if bf is None:
        warnings.append("Betfair is not connected: prices shown are the plan's model prices.")
    return BetSlip(fx.label, fx.date.isoformat(), idea.strategy, idea.strategy_label, round(stake_money, 2), lines,
                   round(sum(l.size for l in lines if l.side == "back"), 2),
                   round(sum(l.liability for l in lines), 2), source, warnings)
