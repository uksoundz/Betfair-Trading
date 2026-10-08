"""Trade-level statistics for a backtest, with the evidence class stated on every row.

Given a list of trades (each with .idea, .hit, .pnl per unit risked, and a date), produce the numbers
a trading-system review needs: count, strike rate, average win/loss, ROI, profit factor, maximum
drawdown, longest losing streak, volatility, and a bootstrap confidence interval on ROI. Trades are
treated as unit-risk flat stakes in chronological order.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

EVIDENCE_LABELS = {
    "model-synthetic": "Model-synthetic: entry at model-fair prices, in-play exits at modelled prices. Not market performance.",
    "model-fair-static": "Model-fair prices at entry, settled on the real result. Not market performance.",
    "bookmaker-odds": "Historical bookmaker closing odds at entry, settled on the real result.",
    "exchange-odds": "Historical exchange closing odds at entry, settled on the real result.",
    "paper": "Forward paper trading on observed live exchange prices.",
    "real": "Real matched bets.",
}


@dataclass
class TradeStats:
    n: int
    strike: float
    predicted: float
    avg_win: float
    avg_loss: float
    roi: float
    profit_factor: float
    max_drawdown: float
    longest_losing_streak: int
    volatility: float
    roi_ci_low: float
    roi_ci_high: float
    evidence: str

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        for k in ("strike", "predicted", "avg_win", "avg_loss", "roi", "profit_factor", "max_drawdown", "volatility", "roi_ci_low", "roi_ci_high"):
            d[k] = round(float(d[k]), 4)
        return d


def compute(pnls: Sequence[float], hits: Sequence[float], predicted: Sequence[float], evidence: str, seed: int = 11) -> TradeStats:
    p = np.asarray(pnls, dtype=float)
    n = len(p)
    if n == 0:
        return TradeStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, evidence)
    wins = p[p > 0]
    losses = p[p <= 0]
    cum = np.cumsum(p)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    dd = peak - cum
    streak = longest = 0
    for x in p:
        streak = streak + 1 if x <= 0 else 0
        longest = max(longest, streak)
    rng = np.random.default_rng(seed)
    boots = [p[rng.integers(0, n, n)].mean() for _ in range(1000)] if n >= 10 else [p.mean()]
    pf = float(wins.sum() / abs(losses.sum())) if losses.sum() < 0 else float("inf")
    # strike = mean hit probability: exact 0/1 where the outcome is observed, an expectation where the
    # event timing is inferred (never rounded, so a 62% expected hit is not counted as a win)
    return TradeStats(n, float(np.mean(hits)) if len(hits) else 0.0, float(np.mean(predicted)) if len(predicted) else 0.0,
                      float(wins.mean()) if len(wins) else 0.0, float(losses.mean()) if len(losses) else 0.0, float(p.mean()),
                      min(pf, 99.0), float(dd.max()), longest, float(p.std()), float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)), evidence)


def by_key(trades: Iterable, key, evidence_for) -> dict[str, TradeStats]:
    groups: dict[str, list] = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    out = {}
    for k, ts in sorted(groups.items()):
        ev = evidence_for(ts[0])
        out[k] = compute([t.pnl for t in ts], [t.hit for t in ts], [t.idea.hit_prob for t in ts], ev)
    return out


def season_of(d) -> str:
    y = d.year if d.month >= 7 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"
