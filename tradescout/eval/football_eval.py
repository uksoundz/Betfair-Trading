"""Football forecast evaluation: log-loss and Brier for 1X2 and Over 2.5 against naive baselines,
walk-forward, by season, over a grid of Dixon-Coles settings.

Baselines
  * league base rates: P(H/D/A) and P(Over 2.5) from the same league in the training window
  * Elo-goals: not needed; base rates are the honest floor a model must beat

Protocol
  * tune seasons: everything before the holdout season
  * holdout: the most recent complete season, used once, after the grid is chosen
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Sequence

import numpy as np

from ..data.openfootball import OpenFootballProvider
from ..model import Forecaster
from ..models import MatchResult


def season_of(d: date) -> str:
    y = d.year if d.month >= 7 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def _clip(p: float) -> float:
    return min(max(p, 1e-6), 1 - 1e-6)


@dataclass
class EvalRow:
    date: date
    league: str
    season: str
    outcome: str          # H / D / A
    over25: bool
    p: dict               # model probs: H, D, A, over
    base: dict            # baseline probs


@dataclass
class EvalSummary:
    n: int
    logloss_1x2: float
    logloss_1x2_base: float
    brier_over: float
    brier_over_base: float
    cal_over: list = field(default_factory=list)   # (bucket, predicted, actual, n)
    cal_draw: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"n": self.n, "logloss_1x2": round(self.logloss_1x2, 4), "logloss_1x2_base": round(self.logloss_1x2_base, 4),
                "brier_over": round(self.brier_over, 4), "brier_over_base": round(self.brier_over_base, 4),
                "skill_1x2": round(1 - self.logloss_1x2 / self.logloss_1x2_base, 4), "skill_over": round(1 - self.brier_over / self.brier_over_base, 4)}


def _calibration(pairs: list[tuple[float, float]], buckets=(0, .2, .3, .4, .5, .6, .7, .8, 1.01)) -> list:
    out = []
    for lo, hi in zip(buckets[:-1], buckets[1:]):
        sel = [(p, y) for p, y in pairs if lo <= p < hi]
        if len(sel) >= 20:
            out.append((f"{lo:.1f}-{min(hi, 1):.1f}", round(float(np.mean([p for p, _ in sel])), 3), round(float(np.mean([y for _, y in sel])), 3), len(sel)))
    return out


def summarise(rows: Sequence[EvalRow]) -> EvalSummary:
    ll = llb = br = brb = 0.0
    over_pairs, draw_pairs = [], []
    for r in rows:
        ll -= math.log(_clip(r.p[r.outcome]))
        llb -= math.log(_clip(r.base[r.outcome]))
        y = 1.0 if r.over25 else 0.0
        br += (r.p["over"] - y) ** 2
        brb += (r.base["over"] - y) ** 2
        over_pairs.append((r.p["over"], y))
        draw_pairs.append((r.p["D"], 1.0 if r.outcome == "D" else 0.0))
    n = max(len(rows), 1)
    return EvalSummary(len(rows), ll / n, llb / n, br / n, brb / n, _calibration(over_pairs), _calibration(draw_pairs))


def walk_forward(provider: OpenFootballProvider, start: date, end: date, refit_days: int = 7, **model_kw) -> list[EvalRow]:
    results = provider.results()
    rows: list[EvalRow] = []
    fcaster = None
    fitted_on = None
    base_cache: dict = {}
    for day in provider.match_days(start, end):
        if fcaster is None or (day - fitted_on).days >= refit_days:
            hist = [r for r in results if r.date < day]
            if len(hist) < 300:
                continue
            fcaster = Forecaster.fit(hist, day, **model_kw)
            fitted_on = day
            # league base rates over the last 2 seasons of history
            base_cache = {}
            cutoff = day - timedelta(days=730)
            by_lg: dict[str, list[MatchResult]] = defaultdict(list)
            for r in hist:
                if r.date >= cutoff:
                    by_lg[r.league].append(r)
            for lg, rs in by_lg.items():
                n = len(rs)
                base_cache[lg] = {"H": sum(r.outcome == "H" for r in rs) / n, "D": sum(r.outcome == "D" for r in rs) / n,
                                  "A": sum(r.outcome == "A" for r in rs) / n, "over": sum(r.total_goals > 2.5 for r in rs) / n}
        for fx in provider.fixtures(day):
            res = provider.result_for(fx)
            if res is None:
                continue
            fc = fcaster.forecast(fx)
            base = base_cache.get(fx.league, {"H": 0.45, "D": 0.26, "A": 0.29, "over": 0.52})
            rows.append(EvalRow(day, fx.league, season_of(day), res.outcome, res.total_goals > 2.5,
                                {"H": fc.p_home, "D": fc.p_draw, "A": fc.p_away, "over": fc.p_over[2.5]}, base))
    return rows


def by_season(rows: Iterable[EvalRow]) -> dict[str, EvalSummary]:
    groups: dict[str, list[EvalRow]] = defaultdict(list)
    for r in rows:
        groups[r.season].append(r)
    return {s: summarise(rs) for s, rs in sorted(groups.items())}


def grid_search(provider: OpenFootballProvider, tune_start: date, tune_end: date, xis=(0.002, 0.003, 0.0045, 0.006, 0.009), history_days=(900,)) -> list[dict]:
    out = []
    for xi in xis:
        for hd in history_days:
            rows = walk_forward(provider, tune_start, tune_end, xi=xi, history_days=hd)
            s = summarise(rows)
            out.append({"xi": xi, "history_days": hd, **s.as_dict()})
    return sorted(out, key=lambda d: d["logloss_1x2"])
