"""Goal timing. The scoreline model says how many goals; this says *when*.

We use a non-homogeneous Poisson process whose intensity rises through the match (goals cluster
late: tiring legs, chasing games, added time). The shape is the well-documented empirical
profile - roughly 45% of goals in the first half, 55% in the second, with the final 15 minutes
the most prolific. Given total expected goals `xg`, the probability of at least one goal before
minute m is 1 - exp(-xg * F(m)) where F is the cumulative intensity share.

The half-time scoreline matrix is derived by thinning: each team's xG is split by the first-half
share, with the same DC rho applied (low HT scores are also under-dispersed).
"""
from __future__ import annotations

import numpy as np
from scipy.stats import poisson

# cumulative share of goals scored by the end of each 15-minute band (0, 15, 30, 45, 60, 75, 90+)
_BANDS = np.array([0, 15, 30, 45, 60, 75, 90])
_CUM_SHARE = np.array([0.0, 0.125, 0.275, 0.455, 0.625, 0.795, 1.0])
FIRST_HALF_SHARE = float(_CUM_SHARE[3])


def cumulative_share(minute: float) -> float:
    """Fraction of a match's expected goals that arrive before `minute` (piecewise linear)."""
    minute = float(np.clip(minute, 0, 90))
    return float(np.interp(minute, _BANDS, _CUM_SHARE))


def p_goal_before(total_xg: float, minute: float) -> float:
    return 1.0 - float(np.exp(-total_xg * cumulative_share(minute)))


def p_goal_between(total_xg: float, start: float, end: float) -> float:
    share = cumulative_share(end) - cumulative_share(start)
    return 1.0 - float(np.exp(-total_xg * share))


def half_time_matrix(lam: float, mu: float, rho: float, max_goals: int) -> np.ndarray:
    l1, m1 = lam * FIRST_HALF_SHARE, mu * FIRST_HALF_SHARE
    g = np.arange(max_goals + 1)
    m = np.outer(poisson.pmf(g, l1), poisson.pmf(g, m1))
    m[0, 0] *= 1 - l1 * m1 * rho
    m[1, 0] *= 1 + m1 * rho
    m[0, 1] *= 1 + l1 * rho
    m[1, 1] *= 1 - rho
    m = np.clip(m, 0, None)
    return m / m.sum()


def p_team_scores_first(lam: float, mu: float) -> tuple[float, float, float]:
    """(P home first, P away first, P no goal). For competing Poisson processes the first
    arrival belongs to team i with probability lam_i / (lam + mu)."""
    total = lam + mu
    if total <= 0:
        return 0.0, 0.0, 1.0
    p_any = 1 - float(np.exp(-total))
    return p_any * lam / total, p_any * mu / total, 1 - p_any
