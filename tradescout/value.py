"""Exchange-aware value engine.

A good forecast is not a trade. This module decides whether a selection is worth taking on the
exchange *at the prices and sizes actually on offer*, after commission, spread and the chance the
order is not matched, and with the model's probability shrunk towards what the market itself
implies. It produces one of three decisions:

  TRADE      conservative net expected value clears the threshold and the order is executable
  NO TRADE   priced, but no proven advantage (edge too small, spread too wide, size too thin)
  RESEARCH   no exchange price at all: the model's view is shown but nothing can be called value

Every number here is per 1 unit risked (stake for backs, liability for lays).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# How much weight the model's probability gets against the market-implied probability, by market
# family. These are priors, not measured skill: the evaluation harness shows the football 1X2
# model has modest skill over base rates and the goals model none, and the tennis Elo barely beats
# a ranking baseline, while exchange prices are known to be sharp. They are deliberately small and
# are exposed in settings so they can be raised once the signals log shows the model beating the
# market on a sample.
STALE_AFTER_SECONDS = 300      # flag and penalise execution
STALE_BLOCK_SECONDS = 900      # a signal older than this is never a TRADE
MODEL_WEIGHT = {"MATCH_ODDS": 0.30, "OVER_UNDER_25": 0.15, "OVER_UNDER_15": 0.15, "CORRECT_SCORE": 0.25,
                "BOTH_TEAMS_TO_SCORE": 0.15, "SET_BETTING": 0.25, "TOTAL_GAMES": 0.15, "DEFAULT": 0.2}


@dataclass
class Quote:
    """Best offers for one selection. Ladders are [(price, size)] best first (up to 3 levels)."""

    back: list = field(default_factory=list)   # available to back (you back at these)
    lay: list = field(default_factory=list)    # available to lay (you lay at these)
    total_matched: Optional[float] = None
    as_of: Optional[str] = None                # ISO timestamp of the snapshot

    @property
    def best_back(self) -> Optional[float]:
        return self.back[0][0] if self.back else None

    @property
    def best_lay(self) -> Optional[float]:
        return self.lay[0][0] if self.lay else None

    @property
    def implied(self) -> Optional[float]:
        """Market-implied probability from the back/lay midpoint (exchange spreads are tight, so this
        is close to the overround-free price)."""
        b, l = self.best_back, self.best_lay
        if b and l:
            return 2.0 / (b + l)
        if b:
            return 1.0 / b
        if l:
            return 1.0 / l
        return None

    @property
    def spread(self) -> Optional[float]:
        b, l = self.best_back, self.best_lay
        return (l - b) / b if (b and l) else None

    def age_seconds(self, now=None) -> Optional[float]:
        if not self.as_of:
            return None
        from datetime import datetime, timezone
        try:
            ts = datetime.fromisoformat(self.as_of.replace("Z", "+00:00"))
        except ValueError:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - ts).total_seconds())


@dataclass
class Fill:
    requested: float          # size we want (backer's stake)
    fillable: float           # size available at or better than the limit
    avg_price: Optional[float]
    fraction: float           # fillable / requested, capped at 1


def simulate_fill(ladder: list, limit: float, size: float, side: str) -> Fill:
    """Walk the ladder taking everything at or better than `limit` until `size` is filled."""
    got = 0.0
    cost = 0.0
    for price, avail in ladder:
        ok = price >= limit if side == "back" else price <= limit
        if not ok:
            break
        take = min(avail, size - got)
        if take <= 0:
            break
        got += take
        cost += take * price
        if got >= size - 1e-9:
            break
    avg = cost / got if got > 0 else None
    return Fill(size, round(got, 2), avg, min(1.0, got / size) if size > 0 else 0.0)


@dataclass
class ValueAssessment:
    decision: str                   # TRADE | NO TRADE | RESEARCH
    reasons: list
    p_model: float
    p_market: Optional[float]
    p_blend: Optional[float]
    p_conservative: Optional[float]
    price: Optional[float]          # effective entry price (size-weighted) or best available
    ev_model: Optional[float]       # net EV per unit risked using the raw model probability
    ev_blend: Optional[float]
    ev_conservative: Optional[float]
    spread: Optional[float]
    fill_fraction: Optional[float]
    execution: float                # 0..1 quality of execution (fill x spread x liquidity)
    liability_per_unit: float       # what 1 unit of stake puts at risk (1 for backs, price-1 for lays per stake)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def net_ev(p: float, price: float, side: str, commission: float) -> float:
    """Net expected value per unit risked. Back: risk = stake. Lay: risk = liability = stake*(price-1)."""
    if side == "back":
        return p * (price - 1) * (1 - commission) - (1 - p)
    # lay: win the backer's stake (1/(price-1) per unit liability) when the selection loses
    win = (1.0 / (price - 1)) * (1 - commission)
    return (1 - p) * win - p * 1.0


def assess(p_model: float, side: str, quote: Optional[Quote], limit_price: Optional[float], size: float,
           market: str, confidence: float, commission: float, min_edge: float, max_spread: float,
           min_total_matched: float = 2000.0, model_weight: Optional[float] = None, model_weight_scale: float = 1.0) -> ValueAssessment:
    """Assess one selection. `limit_price` is the plan's minimum (back) / maximum (lay) price;
    when None the best available price is used."""
    reasons: list[str] = []
    if quote is None or (side == "back" and not quote.back) or (side == "lay" and not quote.lay):
        return ValueAssessment("RESEARCH", ["No exchange price for this selection: model view only, no proven advantage."],
                               p_model, None, None, None, None, None, None, None, None, None, 0.0, 1.0)
    ladder = quote.back if side == "back" else quote.lay
    best = ladder[0][0]
    limit = limit_price if limit_price else best
    # if the plan price is better than the market, we would rest an order: fill is uncertain
    fill = simulate_fill(ladder, limit, size, side)
    price = fill.avg_price or best
    if fill.fraction == 0:
        reasons.append(f"Plan price {limit:.2f} is not on offer right now (best {best:.2f}); the order rests at the plan price until kick-off.")
    elif fill.fraction < 1:
        reasons.append(f"Only £{fill.fillable:.0f} of £{size:.0f} available at the plan price; expect a partial fill.")
    p_mkt = quote.implied
    w = MODEL_WEIGHT.get(market, MODEL_WEIGHT["DEFAULT"]) if model_weight is None else model_weight
    w = min(0.7, w * max(0.1, model_weight_scale))
    p_blend = w * p_model + (1 - w) * p_mkt if p_mkt is not None else p_model
    # conservative: pull the blend back towards the market by the model's own uncertainty
    u = 1.0 - max(0.0, min(1.0, confidence))
    p_cons = p_mkt + (1 - u) * (p_blend - p_mkt) if p_mkt is not None else p_blend
    eff_price = price if fill.fraction > 0 else limit
    ev_model = net_ev(p_model, eff_price, side, commission)
    ev_blend = net_ev(p_blend, eff_price, side, commission)
    ev_cons = net_ev(p_cons, eff_price, side, commission)
    spread = quote.spread
    # Orders are resting limit orders at the plan price that lapse at kick-off, so today's depth and
    # today's spread are not execution blockers: the money arrives by kick-off and the exit happens in
    # play where the big football markets are deep. They do say how reliable the market's own
    # probability is right now, so they shade execution quality and, when the spread is so wide that
    # the mid-point means nothing, hold the decision back until closer to the start.
    liquidity_ok = (quote.total_matched or 0) >= min_total_matched
    spread_wide = spread is not None and spread > max_spread
    spread_unreliable = spread is not None and spread > max(3 * max_spread, 0.10)
    execution = (fill.fraction if fill.fraction > 0 else 0.3) * (0.7 if spread_wide else 1.0) * (0.85 if not liquidity_ok else 1.0)
    decision = "TRADE"
    if ev_cons < min_edge:
        decision = "NO TRADE"
        reasons.append(f"Conservative net edge {ev_cons:+.1%} per unit risked is below the {min_edge:.0%} threshold after {commission:.0%} commission.")
    if spread_unreliable:
        decision = "NO TRADE"
        reasons.append(f"Back/lay spread {spread:.0%} is too wide for the market to tell us anything yet; check again nearer kick-off.")
    elif spread_wide:
        reasons.append(f"Spread {spread:.1%} is wide right now (above {max_spread:.0%}): the market's own probability is uncertain, so the edge estimate is rough.")
    if not liquidity_ok:
        reasons.append(f"Only £{(quote.total_matched or 0):,.0f} matched so far: thin now. A resting order fills only if the price is on offer by kick-off; the exit is in play where the market is deep.")
    age = quote.age_seconds()
    if age is not None and age > STALE_AFTER_SECONDS:
        reasons.append(f"Prices are {age / 60:.0f} minutes old: refresh before acting.")
        execution *= 0.7
        if age > STALE_BLOCK_SECONDS:
            decision = "NO TRADE"
            reasons.append("Price snapshot too old to act on; the signal is invalid until refreshed.")
    if fill.fraction == 0 and decision == "TRADE":
        reasons.append("Value exists only if the market comes to the plan price: the order rests at that price and lapses at kick-off if it never arrives.")
    if decision == "TRADE" and not reasons:
        reasons.append(f"Conservative net edge {ev_cons:+.1%} per unit risked after commission; market implies {p_mkt:.1%}, model {p_model:.1%}.")
    liability = 1.0 if side == "back" else (eff_price - 1)
    return ValueAssessment(decision, reasons, p_model, p_mkt, p_blend, p_cons, round(eff_price, 2), ev_model, ev_blend, ev_cons,
                           spread, fill.fraction, round(execution, 3), liability)
