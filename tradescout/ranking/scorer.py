"""Opportunity scoring: from a strategy's scenario tree to a ranked, exchange-aware trade idea.

Three layers, each visible separately in the output:

1. Calibrated probability of profit. The strategy's hit probability shrunk towards its historical
   strike rate in that league (from the walk-forward backtest), with the calibration ratio
   actual/predicted applied. A strategy that has delivered 85% of its predicted hits is marked down
   everywhere, today's numbers notwithstanding.

2. Value against the exchange (value.py). For each pre-match leg: the market-implied probability
   from the back/lay midpoint, the model probability shrunk towards it (the model gets a small,
   stated weight), the net EV after commission at the price actually available for the size wanted,
   spread and depth. The plan's entry edge is the stake-weighted sum. Without a quote the idea is
   RESEARCH: shown, never called value.

3. Rank score 0-100, transparent: 10 points per 1% of conservative net edge, multiplied by execution
   quality (0..1) and an evidence weight (1.0 for plans settled at the result on exchange prices,
   0.6 for plans whose exits are model-simulated in play). RESEARCH ideas get a separate
   model-only research score for ordering, and are never a TRADE.

Stake is the risk engine's (risk.py): fractional Kelly on the conservative probability, then every cap.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..config import DEFAULT_LIQUIDITY, LEAGUE_LIQUIDITY, settings
from ..models import Fixture, MarketPrices, TradeIdea
from ..risk import Exposure, RiskLimits, advise_stake
from ..strategies.base import Strategy, StrategyResult
from ..value import ValueAssessment, assess, net_ev
from .calibration import Calibration

SHRINK_N = 150.0
# strategies whose plan return is only established at the final result (no modelled in-play exits)
STATIC_STRATEGIES = {"over25_ins", "cs_basket", "tn_over_games", "tn_straight_sets"}
EVIDENCE_WEIGHT = {"exchange-priced-static": 1.0, "simulated-inplay": 0.6, "model-synthetic": 0.0}


class Scorer:
    def __init__(self, calibration: Optional[Calibration] = None, kelly_fraction: float | None = None, limits: RiskLimits | None = None):
        self.calibration = calibration or Calibration()
        self.limits = limits or RiskLimits(kelly_fraction=settings.kelly_fraction if kelly_fraction is None else kelly_fraction)
        self.kelly_fraction = self.limits.kelly_fraction
        self.exposure: Optional[Exposure] = None  # set by the app from the journal before a scan

    def calibrated_hit(self, strategy: Strategy, league: str, hit_prob: float) -> tuple[float, Optional[float], int]:
        e = self.calibration.get(strategy.key, league)
        if not e or e.n < 20:
            return hit_prob, None, 0
        w = e.n / (e.n + SHRINK_N)
        ratio = e.strike_rate / e.predicted_rate if e.predicted_rate > 0 else 1.0
        ratio = float(np.clip(ratio, 0.6, 1.3))
        blended = (1 - w) * hit_prob + w * (hit_prob * ratio)
        return float(np.clip(blended, 0.01, 0.99)), e.strike_rate, e.n

    @staticmethod
    def kelly(p: float, win: float, loss: float) -> float:
        if win <= 0 or loss >= 0:
            return 0.0
        b = win / abs(loss)
        f = (p * b - (1 - p)) / b
        return max(0.0, f * abs(loss))

    def _value(self, fixture: Fixture, fc, r: StrategyResult, prices: Optional[MarketPrices], unit_money: float) -> tuple[list[ValueAssessment], list]:
        out = []
        for leg in r.orders:
            quote = prices.quote(leg.market, leg.selection) if prices is not None else None
            size = max(2.0, unit_money * leg.fraction) if leg.sizing == "stake" else max(2.0, unit_money * leg.fraction / max(leg.price - 1, 0.01))
            out.append(assess(leg.p_model, leg.side, quote, leg.price, size, leg.market, fc.confidence, settings.commission,
                              settings.min_edge, settings.max_spread))
        return out, list(r.orders)

    def score(self, fixture: Fixture, fc, strategy: Strategy, r: StrategyResult, prices: Optional[MarketPrices] = None,
              sport: str = "football", exposure: Optional[Exposure] = None, bank: Optional[float] = None) -> TradeIdea:
        bank = settings.bank if bank is None else bank
        exposure = exposure or self.exposure or Exposure(current_bank=bank, peak_bank=bank)
        cal_hit, hist_rate, n_hist = self.calibrated_hit(strategy, fixture.league, r.hit_prob)
        win, loss = r.win_return, r.loss_return
        roi_adj = cal_hit * win + (1 - cal_hit) * loss
        liquidity = LEAGUE_LIQUIDITY.get(fixture.league, DEFAULT_LIQUIDITY)
        max_loss = float(min((s.profit for s in r.scenarios), default=-1.0))

        # ---- value against the exchange
        unit_money = max(2.0, bank * self.limits.max_per_trade)  # size used for the fill simulation
        live = prices if (prices is not None and prices.available) else None
        legs, orders = self._value(fixture, fc, r, live, unit_money)
        priced = [l for l in legs if l.decision != "RESEARCH"]
        started = prices is not None and prices.status in ("inplay", "suspended", "closed")
        if started:
            # the exchange has this match in play (or its markets suspended / closed): a pre-match plan
            # cannot be entered, so this is a NO TRADE with the reason, not a research idea
            ev_cons = ev_model = p_cons = p_mkt = None
            execution = 0.0
            decision = "NO TRADE"
            reasons = [prices.note or "Markets are not open for pre-match entry."]
            evidence = "model-synthetic"
        elif legs and len(priced) == len(legs):
            # stake-weighted entry edge across legs (fractions sum to 1 within a sizing type)
            ev_cons = float(sum(o.fraction * l.ev_conservative for o, l in zip(orders, legs)))
            ev_model = float(sum(o.fraction * l.ev_model for o, l in zip(orders, legs)))
            # structural cost or benefit of the plan beyond simply holding the entry bet: the model's
            # plan return at its own shaded prices (exits, friction, commission included) minus the
            # return of holding the same entry to settlement. In-play plans pay exit friction twice
            # over, so this is usually negative and a TRADE must clear it.
            ev_hold = float(sum(o.fraction * net_ev(o.p_model, o.price, o.side, settings.commission) for o in orders))
            plan_adjust = float(r.expected_roi - ev_hold) if strategy.inplay else 0.0
            ev_cons += plan_adjust
            ev_model += plan_adjust
            p_cons = legs[0].p_conservative
            p_mkt = legs[0].p_market
            execution = float(min(l.execution for l in legs))
            decision = "TRADE" if (all(l.decision == "TRADE" for l in legs) and ev_cons >= settings.min_edge) else "NO TRADE"
            reasons = [x for l in legs for x in l.reasons]
            if strategy.inplay and abs(plan_adjust) > 0.002:
                reasons.append(f"In-play plan structure {'costs' if plan_adjust < 0 else 'adds'} {abs(plan_adjust):.1%} per unit versus holding the entry bet (modelled exits, friction, commission).")
            if decision == "NO TRADE" and ev_cons < settings.min_edge and not any("below" in x for x in reasons):
                reasons.append(f"Combined conservative net edge {ev_cons:+.1%} is below the {settings.min_edge:.0%} threshold.")
            evidence = "exchange-priced-static" if strategy.key in STATIC_STRATEGIES else "simulated-inplay"
        else:
            ev_cons = ev_model = p_cons = p_mkt = None
            execution = 0.0
            decision = "RESEARCH"
            why = (prices.note + " ") if (prices is not None and prices.note and not prices.available) else ""
            reasons = [why + "No exchange price: model view only. NO TRADE until Betfair prices confirm an edge."]
            evidence = "model-synthetic"

        # ---- rank score (transparent): 10 points per 1% conservative edge x execution x evidence
        if decision in ("TRADE", "NO TRADE"):
            score = float(np.clip(1000.0 * max(ev_cons or 0.0, 0.0) * execution * EVIDENCE_WEIGHT[evidence], 0, 100))
            if decision == "NO TRADE":
                score = min(score, 39.0)  # never looks like a trade
        else:
            # research score: model-only ordering, capped below the trade band
            score = float(np.clip(20 + 150 * roi_adj, 0, 39) * (0.5 + 0.5 * fc.confidence) * (0.7 + 0.3 * liquidity))

        # ---- stake from the risk engine (conservative probability where we have one)
        liability_per_unit = legs[0].liability_per_unit if legs else 1.0
        if decision == "TRADE":
            # Kelly on the entry bet itself at the conservative probability: back wins (price-1)(1-c)
            # per unit staked, a lay wins (1-c)/(price-1) per unit of liability; both lose 1 unit.
            leg0, order0 = legs[0], orders[0]
            px = leg0.price or order0.price
            if order0.side == "back":
                k_win, k_loss, k_p = (px - 1) * (1 - settings.commission), -1.0, p_cons
            else:
                k_win, k_loss, k_p = (1 - settings.commission) / (px - 1), -1.0, 1 - p_cons
            advice = advise_stake(k_p, k_win, k_loss, 1.0, bank, self.limits, exposure, strategy.key, sport)
        else:
            from ..risk import StakeAdvice
            advice = StakeAdvice(0.0, 0.0, 0.0, 0.0, [], True,
                                 "No stake: not a TRADE (no proven advantage at an available exchange price)." if decision == "NO TRADE"
                                 else "No stake: research only, no exchange price.")
        stake_pct = float(advice.applied_pct)

        warnings = list(r.warnings) + list(fc.notes)
        if fc.confidence < 0.5:
            warnings.append("Low data confidence: thin rating history for one side")
        if live is not None and live.as_of:
            warnings.append(f"Prices as of {live.as_of}")
        if live is not None and live.delayed:
            warnings.append("Exchange prices are delayed (Delayed application key): up to three minutes old.")
        return TradeIdea(
            fixture=fixture, strategy=strategy.key, strategy_label=strategy.label, market=r.market, side=r.side,
            selection=r.selection, hit_prob=r.hit_prob, model_price=r.model_price, market_price=r.market_price, edge=r.edge,
            expected_roi=r.expected_roi, win_return=win, loss_return=loss, calibrated_hit_prob=cal_hit, calibrated_roi=float(roi_adj),
            historical_strike_rate=hist_rate, historical_sample=n_hist, confidence=fc.confidence, liquidity=liquidity,
            score=score, stake_pct=stake_pct, plan=r.plan, rationale=r.rationale, warnings=warnings,
            scenarios=[{"label": s.label, "prob": s.prob, "profit": s.profit} for s in r.scenarios],
            orders=list(r.orders), decision=decision, decision_reasons=reasons, evidence=evidence,
            p_conservative=p_cons, ev_conservative=ev_cons, ev_model=ev_model, p_market=p_mkt, execution=execution,
            legs=[l.to_dict() for l in legs], max_loss_per_unit=max_loss, stake_money=advice.stake_money, risk_money=advice.risk_money,
            risk_notes=([advice.reason] if advice.reason else []) + advice.caps_hit, sport=sport,
        )
