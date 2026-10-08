"""Bet slip: turns a plan's pre-match selections into concrete, reviewable lines.

For each OrderLeg it works out the size (stake for backs, backer's stake for lays from the
liability), rounds prices onto Betfair's tick ladder and sizes to pence, flags lines below the
exchange minimum, and, when a Betfair connection exists, looks up the market and selection ids
and the best price currently available so the user can see whether the plan price is there.

`build_slip` never sends anything. `place_slip` does, and only after the server has checked that the
user's betting mode is "live" and the user has confirmed the slip on screen. It refuses lines that
are unresolved or below the exchange minimum, enforces the daily cap, and sends LIMIT orders at
the plan price that lapse at kick-off if unmatched.
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


@dataclass
class PlacedLine:
    market_label: str
    runner_name: str
    side: str
    price: float
    size: float
    status: str            # SUCCESS | FAILURE | SKIPPED
    bet_id: Optional[str] = None
    size_matched: float = 0.0
    avg_price_matched: Optional[float] = None
    order_status: Optional[str] = None  # EXECUTABLE (waiting at the plan price) | EXECUTION_COMPLETE (fully matched)
    error: Optional[str] = None


@dataclass
class PlacementResult:
    ok: bool
    lines: list[PlacedLine]
    committed: float          # back stakes + lay liabilities actually accepted by the exchange
    bet_ids: list[str]
    message: str

    def to_dict(self) -> dict:
        return {**asdict(self), "lines": [asdict(l) for l in self.lines]}


def place_slip(slip: BetSlip, bf, customer_ref: str, daily_cap: float, committed_today: float) -> PlacementResult:
    """Send the slip's valid lines to the exchange. The caller must already hold the user's confirmation."""
    sendable = [l for l in slip.lines if l.market_id and l.selection_id and not l.below_minimum]
    skipped = [l for l in slip.lines if l not in sendable]
    if not sendable:
        return PlacementResult(False, [], 0.0, [], "Nothing to place: every line is unresolved or below the exchange minimum.")
    committed = round(sum(l.liability for l in sendable), 2)
    if committed_today + committed > daily_cap + 1e-9:
        return PlacementResult(False, [], 0.0, [],
                               f"Daily cap would be exceeded: £{committed_today:.2f} already committed today, this slip adds "
                               f"£{committed:.2f}, cap is £{daily_cap:.2f}. Raise the cap in Settings or reduce the stake.")
    placed: list[PlacedLine] = [PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "SKIPPED",
                                           error="; ".join(l.warnings) or "not sent") for l in skipped]
    bet_ids: list[str] = []
    by_market: dict[str, list[SlipLine]] = {}
    for l in sendable:
        by_market.setdefault(l.market_id, []).append(l)
    sent_total = 0.0
    for n, (market_id, lines) in enumerate(by_market.items()):
        instructions = [{"selectionId": l.selection_id, "side": l.side, "price": l.plan_price, "size": l.size} for l in lines]
        try:
            report = bf.place_orders(market_id, instructions, f"{customer_ref}-{n}")
        except Exception as exc:
            for l in lines:
                placed.append(PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "FAILURE", error=str(exc)))
            continue
        reports = report.get("instructionReports", [])
        for k, l in enumerate(lines):
            r = reports[k] if k < len(reports) else {}
            status = r.get("status", report.get("status", "FAILURE"))
            line = PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, status, r.get("betId"),
                              float(r.get("sizeMatched") or 0.0), r.get("averagePriceMatched"), r.get("orderStatus"),
                              r.get("errorCode") or report.get("errorCode"))
            placed.append(line)
            if status == "SUCCESS":
                sent_total += l.liability
                if line.bet_id:
                    bet_ids.append(str(line.bet_id))
    ok = any(p.status == "SUCCESS" for p in placed)
    n_ok = sum(1 for p in placed if p.status == "SUCCESS")
    msg = f"{n_ok} of {len(sendable)} lines placed, £{sent_total:.2f} committed." if ok else "No lines were accepted by the exchange."
    return PlacementResult(ok, placed, round(sent_total, 2), bet_ids, msg)
