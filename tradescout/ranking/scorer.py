"""Composite 0-100 score for a trade idea.

    score = clip( 50 + 150 * expected_roi_adjusted
                  + 100 * market_edge (when an exchange feed is connected)
                  , 0, 100 )
            * (0.5 + 0.5 * confidence)      # how much data sits behind both team ratings
            * (0.7 + 0.3 * liquidity)       # can you actually get matched at the price?

`expected_roi_adjusted` re-prices the strategy's scenarios with a *calibrated* hit probability:
the model's number shrunk towards the strategy's historical strike rate in that league
(weight n / (n + 150)), and multiplied by the historical calibration ratio (actual / predicted).
A strategy that has returned 40% of its predicted hits is punished no matter how good today looks.

Stake is quarter-Kelly on the win/loss-averaged two-outcome approximation, capped at 5% of bank.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..config import DEFAULT_LIQUIDITY, LEAGUE_LIQUIDITY, settings
from ..models import Fixture, MatchForecast, TradeIdea
from ..strategies.base import Strategy, StrategyResult
from .calibration import Calibration

SHRINK_N = 150.0


class Scorer:
    def __init__(self, calibration: Optional[Calibration] = None, kelly_fraction: float | None = None):
        self.calibration = calibration or Calibration()
        self.kelly_fraction = settings.kelly_fraction if kelly_fraction is None else kelly_fraction

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
        """Fraction of bank for a bet winning `win` with prob p and losing |loss| otherwise."""
        if win <= 0 or loss >= 0:
            return 0.0
        b = win / abs(loss)
        f = (p * b - (1 - p)) / b
        return max(0.0, f * abs(loss))  # scale back to the unit actually risked

    def score(self, fixture: Fixture, fc: MatchForecast, strategy: Strategy, r: StrategyResult) -> TradeIdea:
        cal_hit, hist_rate, n_hist = self.calibrated_hit(strategy, fixture.league, r.hit_prob)
        win, loss = r.win_return, r.loss_return
        roi_adj = cal_hit * win + (1 - cal_hit) * loss
        liquidity = LEAGUE_LIQUIDITY.get(fixture.league, DEFAULT_LIQUIDITY)
        base = 50 + 150 * roi_adj
        if r.edge is not None:
            base += 100 * r.edge
        score = float(np.clip(base, 0, 100) * (0.5 + 0.5 * fc.confidence) * (0.7 + 0.3 * liquidity))
        stake = self.kelly(cal_hit, win, loss) * self.kelly_fraction
        stake_pct = float(min(stake, 0.05)) * 100
        warnings = list(r.warnings) + list(fc.notes)
        if r.edge is None:
            warnings.append("No exchange feed: prices are model-fair minus a typical overround; edge not measured")
        if fc.confidence < 0.5:
            warnings.append("Low data confidence: fewer than ~10 effective matches for one side")
        return TradeIdea(
            fixture=fixture, strategy=strategy.key, strategy_label=strategy.label, market=r.market, side=r.side,
            selection=r.selection, hit_prob=r.hit_prob, model_price=r.model_price, market_price=r.market_price, edge=r.edge,
            expected_roi=r.expected_roi, win_return=win, loss_return=loss, calibrated_hit_prob=cal_hit, calibrated_roi=float(roi_adj),
            historical_strike_rate=hist_rate, historical_sample=n_hist, confidence=fc.confidence, liquidity=liquidity,
            score=score, stake_pct=stake_pct, plan=r.plan, rationale=r.rationale, warnings=warnings,
            scenarios=[{"label": s.label, "prob": s.prob, "profit": s.profit} for s in r.scenarios],
        )
