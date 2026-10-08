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

MIN_STAKE = 2.00        # Betfair GBP minimum stake per bet
MIN_STAKE_SMALL = 1.00  # Betfair accepts smaller stakes when the potential payout reaches MIN_PAYOUT
MIN_PAYOUT = 10.00
BETFAIR_MARKET_URL = "https://www.betfair.com/exchange/plus/football/market/{market_id}"
RECONCILE_WAIT_SECONDS = 2.0  # Betfair asks for up to 15 s before a timed-out order shows; we check once after this


def min_bet_ok(size: float, price: float, min_stake: float = MIN_STAKE) -> bool:
    """Betfair's minimum bet rule for GBP: the stake must reach the minimum, or a smaller stake is fine
    when the payout (stake x price) reaches the minimum payout."""
    return size >= min_stake - 1e-9 or (size >= MIN_STAKE_SMALL - 1e-9 and size * price >= MIN_PAYOUT - 1e-9)


def min_plan_stake(idea: TradeIdea, min_stake: float = MIN_STAKE) -> float:
    """Smallest plan stake (unit risked) at which every pre-match leg clears the exchange minimum:
    a back leg needs unit x fraction >= min; a lay leg's backer stake is unit x fraction / (price - 1)."""
    need = 0.0
    for leg in idea.orders:
        if leg.fraction <= 0:
            continue
        price = round_to_tick(leg.price, leg.side)
        per_unit = leg.fraction if leg.side == "back" else leg.fraction / max(price - 1, 0.01)
        need = max(need, min_stake / per_unit)
    return math.ceil(need * 100) / 100 if need else min_stake


def customer_ref(*parts: object) -> str:
    """Deterministic de-duplication reference (<= 32 chars, [A-Za-z0-9-]) for one slip line group: the same
    plan on the same match and market gives the same ref, so an accidental resubmission within Betfair's
    de-duplication window is rejected by the exchange instead of doubling the stake."""
    import hashlib
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()
    return f"ts-{digest[:24]}"

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
        if not min_bet_ok(line.size, price, min_stake):
            line.below_minimum = True
            line.warnings.append(f"Exchange minimum is £{min_stake:.2f} per bet (or £{MIN_STAKE_SMALL:.0f} when the payout reaches £{MIN_PAYOUT:.0f}); "
                                 f"this line is £{line.size:.2f}. Raise the stake or drop the line.")
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
                    line.warnings.append("No price on offer for this selection right now (market may be suspended or in play).")
            else:
                line.warnings.append("Market or selection not found on the exchange for this fixture (no matching exchange event, or the market is not listed yet).")
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
    unwound: bool = False     # a later market failed, so the unmatched part of earlier lines was cancelled
    pending: bool = False     # Betfair timed out on a line and it could not be confirmed either way

    def to_dict(self) -> dict:
        return {**asdict(self), "lines": [asdict(l) for l in self.lines]}


def _reconcile_timeout(bf, market_id: str, lines: list[SlipLine]) -> dict[int, dict]:
    """After a TIMEOUT, ask the exchange which of our orders on this market exist (Betfair says allow up to
    15 s). Returns selectionId -> current order for the lines that did get through."""
    import time as _time
    _time.sleep(RECONCILE_WAIT_SECONDS)
    found: dict[int, dict] = {}
    try:
        for o in bf.current_orders([market_id]):
            for l in lines:
                ps = o.get("priceSize") or {}
                if o.get("selectionId") == l.selection_id and str(o.get("side", "")).lower() == l.side \
                        and abs(float(ps.get("price") or 0) - l.plan_price) < 1e-6 and abs(float(ps.get("size") or 0) - l.size) < 0.011:
                    found[l.selection_id] = o
    except Exception:
        pass
    return found


def place_slip(slip: BetSlip, bf, customer_ref: str, daily_cap: float, committed_today: float) -> PlacementResult:
    """Send the slip's valid lines to the exchange. The caller must already hold the user's confirmation.
    One placeOrders call per market (Betfair places all-or-nothing within a call). If a later market is
    rejected after an earlier one was accepted, the unmatched part of the accepted lines is cancelled so
    the user is not left holding half a plan; anything already matched is reported as such."""
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
    pending = False
    accepted: dict[str, list[str]] = {}  # market -> bet ids accepted so far (for unwinding)
    failed_after_success = False
    for n, (market_id, lines) in enumerate(by_market.items()):
        instructions = [{"selectionId": l.selection_id, "side": l.side, "price": l.plan_price, "size": l.size} for l in lines]
        ref = f"{customer_ref}-{n}"[:32]
        try:
            report = bf.place_orders(market_id, instructions, ref)
        except Exception as exc:
            text = str(exc)
            if "TIMEOUT" in text.upper():
                found = _reconcile_timeout(bf, market_id, lines)
                for l in lines:
                    o = found.get(l.selection_id)
                    if o:
                        line = PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "SUCCESS", str(o.get("betId")),
                                          float(o.get("sizeMatched") or 0.0), o.get("averagePriceMatched"), o.get("status"), None)
                        sent_total += l.liability
                        bet_ids.append(str(o.get("betId")))
                        accepted.setdefault(market_id, []).append(str(o.get("betId")))
                    else:
                        pending = True
                        line = PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "PENDING",
                                          error="Betfair timed out and the order could not be confirmed. Check Open orders before placing again.")
                    placed.append(line)
                continue
            if accepted:
                failed_after_success = True
            for l in lines:
                placed.append(PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "FAILURE", error=text))
            continue
        reports = report.get("instructionReports", [])
        market_ok = False
        for k, l in enumerate(lines):
            r = reports[k] if k < len(reports) else {}
            status = r.get("status", report.get("status", "FAILURE"))
            err = r.get("errorCode") or report.get("errorCode")
            if status == "TIMEOUT" or err == "TIMEOUT_ERROR":
                found = _reconcile_timeout(bf, market_id, [l])
                o = found.get(l.selection_id)
                if o:
                    status, err = "SUCCESS", None
                    r = {"betId": o.get("betId"), "sizeMatched": o.get("sizeMatched"), "averagePriceMatched": o.get("averagePriceMatched"), "orderStatus": o.get("status")}
                else:
                    status, err, pending = "PENDING", "Betfair timed out and the order could not be confirmed. Check Open orders before placing again.", True
            elif err == "DUPLICATE_TRANSACTION":
                status, err = "DUPLICATE", "This slip was already sent a moment ago; the exchange refused the duplicate. Check Open orders."
            line = PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, status, r.get("betId"),
                              float(r.get("sizeMatched") or 0.0), r.get("averagePriceMatched"), r.get("orderStatus"), err)
            placed.append(line)
            if status == "SUCCESS":
                market_ok = True
                sent_total += l.liability
                if line.bet_id:
                    bet_ids.append(str(line.bet_id))
                    accepted.setdefault(market_id, []).append(str(line.bet_id))
        if not market_ok and accepted and market_id not in accepted:
            failed_after_success = True
    unwound = False
    if failed_after_success and accepted:
        # cancel whatever is still unmatched on the markets that went through; matched stake cannot be undone
        for market_id, ids in accepted.items():
            try:
                bf.cancel_orders(market_id, ids)
                unwound = True
            except Exception:
                pass
        for p in placed:
            if p.status == "SUCCESS" and p.order_status != "EXECUTION_COMPLETE":
                p.error = "cancelled: another leg of the plan was rejected"
    ok = any(p.status == "SUCCESS" for p in placed)
    n_ok = sum(1 for p in placed if p.status == "SUCCESS")
    if unwound:
        matched = sum(p.size_matched for p in placed if p.status == "SUCCESS")
        msg = (f"Plan not completed: a later leg was rejected, so the unmatched part of the accepted lines was cancelled."
               + (f" £{matched:.2f} had already matched and stands." if matched else ""))
    elif ok:
        msg = f"{n_ok} of {len(sendable)} lines placed, £{sent_total:.2f} committed."
        if pending:
            msg += " One or more lines timed out: check Open orders."
    elif pending:
        msg = "Betfair timed out; nothing could be confirmed. Check Open orders in My picks before trying again."
    else:
        msg = "No lines were accepted by the exchange: " + "; ".join(sorted({p.error for p in placed if p.error and p.status != 'SKIPPED'})) if any(p.error for p in placed if p.status != "SKIPPED") else "No lines were accepted by the exchange."
    return PlacementResult(ok, placed, round(sent_total, 2), bet_ids, msg, unwound, pending)
