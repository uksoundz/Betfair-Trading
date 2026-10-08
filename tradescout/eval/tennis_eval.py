"""Tennis forecast evaluation, walk-forward by season.

Checks three things the strategies depend on:
  1. match-win log-loss of the Elo model vs. two baselines (coin flip, 'better-ranked player wins' at
     the empirical rate for the ranking gap)
  2. calibration of the Markov model's set-1, straight-sets and total-games probabilities against
     what actually happened, by surface and format
  3. the best Elo settings (K scale, surface weight) chosen on tuning years and confirmed on a holdout
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

import numpy as np

from .. import tennis as _t  # noqa: F401
from ..tennis.data import TennisProvider, TennisResult
from ..tennis.elo import TennisElo, expected
from ..tennis.forecast import TennisForecaster


def _clip(p: float) -> float:
    return min(max(p, 1e-6), 1 - 1e-6)


@dataclass
class TRow:
    date: date
    year: int
    surface: str
    best_of: int
    league: str
    p_a: float            # model P(player A wins), A = better-ranked
    a_won: bool
    p_set1_a: float
    set1_a: bool
    p_straight_fav: float
    straight_fav: bool
    exp_games: float
    games: int
    p_over_med: float
    over_med: bool
    retired: bool
    rank_gap_prob: float  # baseline from ranking gap


def _rank_baseline(hist: Sequence[TennisResult]) -> dict:
    """P(better-ranked wins) by log ranking ratio bucket, from history."""
    buckets: dict[int, list[int]] = defaultdict(list)
    for r in hist:
        if r.winner_rank and r.loser_rank:
            hi, lo = min(r.winner_rank, r.loser_rank), max(r.winner_rank, r.loser_rank)
            b = min(int(math.log2(lo / hi) * 2), 9)
            buckets[b].append(1 if r.winner_rank == hi else 0)
    return {b: (sum(v) + 1) / (len(v) + 2) for b, v in buckets.items()}


def walk_forward(provider: TennisProvider, start: date, end: date, refit_days: int = 7, elo_kwargs: dict | None = None) -> list[TRow]:
    results = provider.results()
    rows: list[TRow] = []
    fcaster = None
    fitted_on = None
    baseline: dict = {}
    for day in provider.match_days(start, end):
        if fcaster is None or (day - fitted_on).days >= refit_days:
            hist = [r for r in results if r.date < day]
            if len(hist) < 500:
                continue
            elo = TennisElo(**(elo_kwargs or {})).fit(hist, day)
            fcaster = TennisForecaster(elo)
            fitted_on = day
            baseline = _rank_baseline(hist)
        for fx in provider.fixtures(day):
            res = provider.result_for(fx)
            if res is None or not res.sets():
                continue
            fc = fcaster.forecast(fx)
            a_won = res.winner == fx.home
            fav_home = fc.p_a >= 0.5
            straight_key = ("2-0" if fav_home else "0-2") if fc.best_of == 3 else ("3-0" if fav_home else "0-3")
            w, l = res.set_score
            straight = (res.winner == fc.fav_name()) and l == 0
            med_line, p_over = min(fc.p_over.items(), key=lambda kv: abs(kv[1] - 0.5))
            # ranking baseline
            ra = res.winner_rank if res.winner == fx.home else res.loser_rank
            rb = res.loser_rank if res.winner == fx.home else res.winner_rank
            if ra and rb:
                hi, lo = min(ra, rb), max(ra, rb)
                b = min(int(math.log2(lo / hi) * 2), 9)
                p_better = baseline.get(b, 0.6)
                base = p_better if ra <= rb else 1 - p_better
            else:
                base = 0.5
            rows.append(TRow(day, day.year, fc.surface, fc.best_of, fx.league, fc.p_a, a_won, fc.p_set1_a, res.first_set_winner == fx.home,
                             fc.p_sets.get(straight_key, 0.0), straight, fc.expected_games, res.total_games, p_over, res.total_games > med_line,
                             res.retired, base))
    return rows


def summarise(rows: Sequence[TRow]) -> dict:
    full = [r for r in rows if not r.retired]
    n = len(full)
    if n == 0:
        return {"n": 0}
    ll = -sum(math.log(_clip(r.p_a if r.a_won else 1 - r.p_a)) for r in full) / n
    ll_coin = math.log(2)
    ll_rank = -sum(math.log(_clip(r.rank_gap_prob if r.a_won else 1 - r.rank_gap_prob)) for r in full) / n
    acc = sum((r.p_a >= 0.5) == r.a_won for r in full) / n
    return {
        "n": n, "logloss": round(ll, 4), "logloss_coin": round(ll_coin, 4), "logloss_rank_baseline": round(ll_rank, 4),
        "skill_vs_rank": round(1 - ll / ll_rank, 4), "accuracy": round(acc, 4),
        "set1_pred": round(float(np.mean([r.p_set1_a for r in full])), 4), "set1_actual": round(float(np.mean([r.set1_a for r in full])), 4),
        "straight_pred": round(float(np.mean([r.p_straight_fav for r in full])), 4), "straight_actual": round(float(np.mean([r.straight_fav for r in full])), 4),
        "games_pred": round(float(np.mean([r.exp_games for r in full])), 2), "games_actual": round(float(np.mean([r.games for r in full])), 2),
        "over_med_pred": round(float(np.mean([r.p_over_med for r in full])), 4), "over_med_actual": round(float(np.mean([r.over_med for r in full])), 4),
        "retired_share": round(sum(r.retired for r in rows) / len(rows), 4),
    }


def by_group(rows: Iterable[TRow], key) -> dict:
    groups: dict = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    return {k: summarise(v) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}


def games_calibration(provider: TennisProvider, before: date) -> dict:
    """Observed average games and set-level facts by (surface, best_of) up to `before`, used to
    recalibrate the serve-point averages so the Markov model's game counts match reality."""
    out: dict = defaultdict(lambda: {"n": 0, "games": 0, "sets": 0, "tiebreaks": 0, "breaks_per_set": 0.0})
    for r in provider.results(before=before):
        if r.retired or not r.sets():
            continue
        k = (r.surface, r.best_of)
        d = out[k]
        d["n"] += 1
        d["games"] += r.total_games
        d["sets"] += len(r.sets())
        d["tiebreaks"] += sum(1 for a, b in r.sets() if {a, b} == {7, 6})
    return {f"{s}|bo{b}": {"n": v["n"], "avg_games": round(v["games"] / v["n"], 2), "avg_sets": round(v["sets"] / v["n"], 3),
                            "tiebreak_rate_per_set": round(v["tiebreaks"] / max(v["sets"], 1), 4)} for (s, b), v in out.items() if v["n"] >= 50}
