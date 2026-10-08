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


SMALL_STAKE_JURISDICTIONS = ("com", "com.au")  # where the "£1 if the payout reaches £10" exception applies


def min_bet_ok(size: float, price: float, min_stake: float = MIN_STAKE, jurisdiction: str = "com") -> bool:
    """Betfair's minimum bet rule for GBP: the stake must reach the minimum, or (UK/international only) a
    smaller stake is fine when the payout (stake x price) reaches the minimum payout."""
    if size >= min_stake - 1e-9:
        return True
    return jurisdiction in SMALL_STAKE_JURISDICTIONS and size >= MIN_STAKE_SMALL - 1e-9 and size * price >= MIN_PAYOUT - 1e-9


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
    blocked: bool = False              # market in play / suspended / closed: cannot be sent as a pre-match order
    market_status: Optional[str] = None
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
                 "OVER_UNDER_35": "Over/Under 3.5 Goals", "CORRECT_SCORE": "Correct Score", "BOTH_TEAMS_TO_SCORE": "Both Teams to Score",
                 "SET_BETTING": "Set Betting", "TOTAL_GAMES": "Total Games", "SET_1_WINNER": "Set 1 Winner"}


def runner_label(fixture: Fixture, leg) -> str:
    if leg.market in ("MATCH_ODDS", "SET_1_WINNER"):
        return {"home": fixture.home, "away": fixture.away, "draw": "The Draw"}.get(leg.selection, leg.selection)
    if leg.market == "SET_BETTING" and "-" in leg.selection:  # home-away sets -> "Player 2-0" as the exchange names it
        a, b = leg.selection.split("-", 1)
        return f"{fixture.home} {a}-{b}" if int(a) > int(b) else f"{fixture.away} {b}-{a}"
    if leg.market == "TOTAL_GAMES":
        return f"{leg.selection} games"
    return leg.selection


def build_slip(idea: TradeIdea, stake_money: float, bf=None, min_stake: float = MIN_STAKE, jurisdiction: str = "com") -> BetSlip:
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
        if not min_bet_ok(line.size, price, min_stake, jurisdiction):
            line.below_minimum = True
            line.warnings.append(f"Exchange minimum is £{min_stake:.2f} per bet (or £{MIN_STAKE_SMALL:.0f} when the payout reaches £{MIN_PAYOUT:.0f}); "
                                 f"this line is £{line.size:.2f}. Raise the stake or drop the line.")
        if bf is not None:
            full = None
            try:
                if hasattr(bf, "resolve_full"):
                    full = bf.resolve_full(fx, leg.market, leg.selection)
                    found = (full["market_id"], full["selection_id"], full["best_back"], full["best_lay"]) if full else None
                else:
                    found = bf.resolve(fx, leg.market, leg.selection)
            except Exception as exc:  # a price lookup failure must not kill the slip
                found = None
                line.warnings.append(f"Price lookup failed: {exc}")
            if found:
                line.market_id, line.selection_id, best_back, best_lay = found
                if full and full.get("status") not in (None, "OPEN"):
                    line.blocked = True
                    line.market_status = full["status"]
                    line.warnings.append(f"Market is {full['status'].lower().replace('inplay', 'in play')}: a pre-match order cannot be placed now.")
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
    status: str            # SUCCESS | FAILURE | SKIPPED | PENDING | DUPLICATE | CANCELLED
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


RECONCILE_POLL_SECONDS = (2.0, 3.0, 5.0, 5.0)  # Betfair: allow up to 15 s for a timed-out order to appear


def order_ref(customer_ref: str, line: SlipLine) -> str:
    """Per-line customerOrderRef (<= 32 chars): the only safe way to recognise our own order afterwards."""
    return f"{customer_ref[:20]}-{line.selection_id}"[:32]


def _reconcile_unconfirmed(bf, market_id: str, lines: list[SlipLine], customer_ref: str, polls=RECONCILE_POLL_SECONDS) -> dict[int, dict]:
    """After a timeout or a dropped connection, ask the exchange which of these lines exist, recognised by
    their customerOrderRef (never by price and size, which an earlier order could share). Polls for up to
    ~15 s. Returns selectionId -> order."""
    import time as _time
    wanted = {order_ref(customer_ref, l): l for l in lines}
    found: dict[int, dict] = {}
    for wait in polls:
        _time.sleep(wait)
        try:
            for o in bf.current_orders([market_id]):
                l = wanted.get(str(o.get("customerOrderRef") or ""))
                if l is not None:
                    found[l.selection_id] = o
        except Exception:
            continue
        if len(found) == len(lines):
            break
    return found


def _standing(lines: list[PlacedLine], slip_lines: dict[tuple, SlipLine]) -> float:
    """Money actually at risk on the exchange: matched stakes (backs) or matched liabilities (lays) plus
    the unmatched remainder of orders still waiting (EXECUTABLE)."""
    total = 0.0
    for p in lines:
        if p.status != "SUCCESS":
            continue
        l = slip_lines.get((p.market_label, p.runner_name, p.side))
        liability_per_stake = (l.plan_price - 1) if (l and l.side == "lay") else 1.0
        if p.order_status == "EXECUTION_COMPLETE":
            total += p.size * liability_per_stake
        elif p.order_status == "CANCELLED":
            total += (p.size_matched or 0.0) * liability_per_stake
        else:
            total += p.size * liability_per_stake
    return round(total, 2)


def place_slip(slip: BetSlip, bf, customer_ref: str, daily_cap: float, committed_today: float) -> PlacementResult:
    """Send the slip's valid lines to the exchange. The caller must already hold the user's confirmation.
    One placeOrders call per market (Betfair places all-or-nothing within a call). The plan is all or
    nothing across markets too: the first rejected market stops the rest from being sent, and whatever
    an earlier market accepted is cancelled (matched stake cannot be undone and is reported). A timeout
    or dropped connection is 'unconfirmed', never 'failed': the exchange is asked what it holds."""
    sendable = [l for l in slip.lines if l.market_id and l.selection_id and not l.below_minimum and not l.blocked]
    skipped = [l for l in slip.lines if l not in sendable]
    if not sendable:
        return PlacementResult(False, [], 0.0, [], "Nothing to place: every line is unresolved, below the exchange minimum, or on a market that is not open.")
    committed = round(sum(l.liability for l in sendable), 2)
    if committed_today + committed > daily_cap + 1e-9:
        return PlacementResult(False, [], 0.0, [],
                               f"Daily cap would be exceeded: £{committed_today:.2f} already committed today, this slip adds "
                               f"£{committed:.2f}, cap is £{daily_cap:.2f}. Raise the cap in Settings or reduce the stake.")
    placed: list[PlacedLine] = [PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "SKIPPED",
                                           error="; ".join(l.warnings) or "not sent") for l in skipped]
    by_market: dict[str, list[SlipLine]] = {}
    for l in sendable:
        by_market.setdefault(l.market_id, []).append(l)
    slip_index = {(l.market_label, l.runner_name, l.side): l for l in slip.lines}
    pending = False
    accepted: dict[str, list[str]] = {}  # market -> bet ids accepted (for unwinding)
    failed = False
    markets = list(by_market.items())
    for n, (market_id, lines) in enumerate(markets):
        if failed:
            for l in lines:
                placed.append(PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "SKIPPED", error="not sent: an earlier leg of the plan was rejected"))
            continue
        ref = f"{customer_ref}-{n}"[:32]
        instructions = [{"selectionId": l.selection_id, "side": l.side, "price": l.plan_price, "size": l.size, "customerOrderRef": order_ref(customer_ref, l)} for l in lines]
        try:
            report = bf.place_orders(market_id, instructions, ref)
        except Exception as exc:
            code = getattr(exc, "code", "")
            if code in ("TIMEOUT_ERROR", "NETWORK") or "TIMEOUT" in str(exc).upper():
                found = _reconcile_unconfirmed(bf, market_id, lines, customer_ref)
                for l in lines:
                    o = found.get(l.selection_id)
                    if o:
                        placed.append(PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "SUCCESS", str(o.get("betId")),
                                                 float(o.get("sizeMatched") or 0.0), o.get("averagePriceMatched"), o.get("status"), None))
                        accepted.setdefault(market_id, []).append(str(o.get("betId")))
                    else:
                        pending = True
                        placed.append(PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "PENDING",
                                                 error="Betfair did not answer and the order could not be found afterwards. Check Open orders before placing again."))
                continue
            failed = True
            for l in lines:
                placed.append(PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "FAILURE", error=str(exc)))
            continue
        reports = report.get("instructionReports", [])
        market_ok = False
        unconfirmed = []
        for k, l in enumerate(lines):
            r = reports[k] if k < len(reports) else {}
            status = r.get("status", report.get("status", "FAILURE"))
            err = r.get("errorCode") or report.get("errorCode")
            if status == "TIMEOUT" or err == "TIMEOUT_ERROR":
                unconfirmed.append((k, l))
                continue
            if err == "DUPLICATE_TRANSACTION":
                status, err = "DUPLICATE", "This slip was already sent a moment ago; the exchange refused the duplicate. Check Open orders."
            line = PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, status, r.get("betId"),
                              float(r.get("sizeMatched") or 0.0), r.get("averagePriceMatched"), r.get("orderStatus"), err)
            placed.append(line)
            if status == "SUCCESS":
                market_ok = True
                if line.bet_id:
                    accepted.setdefault(market_id, []).append(str(line.bet_id))
        if unconfirmed:
            found = _reconcile_unconfirmed(bf, market_id, [l for _, l in unconfirmed], customer_ref)
            for _, l in unconfirmed:
                o = found.get(l.selection_id)
                if o:
                    market_ok = True
                    placed.append(PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "SUCCESS", str(o.get("betId")),
                                             float(o.get("sizeMatched") or 0.0), o.get("averagePriceMatched"), o.get("status"), None))
                    accepted.setdefault(market_id, []).append(str(o.get("betId")))
                else:
                    pending = True
                    placed.append(PlacedLine(l.market_label, l.runner_name, l.side, l.plan_price, l.size, "PENDING",
                                             error="Betfair timed out and the order could not be found afterwards. Check Open orders before placing again."))
        if not market_ok and not any(p.status == "PENDING" for p in placed if p.market_label == lines[0].market_label):
            failed = True
    unwound = False
    if failed and accepted:
        # cancel whatever is still unmatched on the markets that went through; matched stake cannot be undone
        for market_id, ids in accepted.items():
            try:
                rep = bf.cancel_orders(market_id, ids)
                unwound = True
                cancelled = {str(i.get("instruction", {}).get("betId")): float(i.get("sizeCancelled") or 0.0) for i in rep.get("instructionReports", [])}
            except Exception:
                cancelled = {}
            for p in placed:
                if p.status == "SUCCESS" and str(p.bet_id) in ids and p.order_status != "EXECUTION_COMPLETE":
                    p.order_status = "CANCELLED"
                    p.size_matched = round(max(0.0, p.size - cancelled.get(str(p.bet_id), p.size)), 2) if cancelled else p.size_matched
                    p.error = "cancelled: another leg of the plan was rejected" + (f"; £{p.size_matched:.2f} had already matched" if p.size_matched else "")
    sent_total = _standing(placed, slip_index)
    bet_ids = [str(p.bet_id) for p in placed if p.status == "SUCCESS" and p.bet_id and not (p.order_status == "CANCELLED" and not p.size_matched)]
    live = [p for p in placed if p.status == "SUCCESS" and not (p.order_status == "CANCELLED" and not p.size_matched)]
    ok = bool(live)
    n_ok = len(live)
    if unwound:
        matched = sum(p.size_matched for p in placed if p.status == "SUCCESS" and p.order_status == "CANCELLED")
        msg = ("Plan not completed: a leg was rejected, so the unmatched part of the accepted lines was cancelled."
               + (f" £{matched:.2f} had already matched and stands." if matched else " Nothing is on the exchange."))
    elif ok:
        msg = f"{n_ok} of {len(sendable)} lines placed, £{sent_total:.2f} committed."
        if pending:
            msg += " One or more lines could not be confirmed: check Open orders."
    elif pending:
        msg = "Betfair did not confirm the order and it could not be found afterwards. Check Open orders in My picks before trying again."
    else:
        errs = sorted({p.error for p in placed if p.error and p.status not in ("SKIPPED",)})
        msg = "No lines were accepted by the exchange" + (": " + "; ".join(errs) if errs else ".")
    return PlacementResult(ok, placed, sent_total, bet_ids, msg, unwound, pending)
