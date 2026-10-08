"""In-play conditional pricing from the pre-match model.

Given the scoreline at minute t, the goals still to come are Poisson with the *remaining* share of
each side's expected goals. From that we get the fair price of any market at any point of the
match - which is exactly what a trader needs to plan exits before kick-off: "if the favourite
leads 1-0 on 30 minutes, where will the draw be trading?"
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from scipy.stats import poisson

from .timing import cumulative_share


@lru_cache(maxsize=65536)
def _remaining_matrix(lam_r: float, mu_r: float, rho: float, max_goals: int) -> np.ndarray:
    g = np.arange(max_goals + 1)
    m = np.outer(poisson.pmf(g, lam_r), poisson.pmf(g, mu_r))
    if rho:
        # Dixon-Coles low-score correction only applies while the game is still 0-0
        m[0, 0] *= 1 - lam_r * mu_r * rho
        m[1, 0] *= 1 + mu_r * rho
        m[0, 1] *= 1 + lam_r * rho
        m[1, 1] *= 1 - rho
        m = np.clip(m, 0, None)
    return m / m.sum()


def conditional_matrix(lam: float, mu: float, home_goals: int, away_goals: int, minute: float,
                       max_goals: int = 8, rho: float = 0.0) -> np.ndarray:
    """Final-score probability matrix given the score at `minute`. Shape (max_goals+1, max_goals+1),
    indices are final goals (clipped at max_goals)."""
    rem = 1.0 - cumulative_share(minute)
    use_rho = rho if (home_goals == 0 and away_goals == 0) else 0.0
    r = _remaining_matrix(round(lam * rem, 4), round(mu * rem, 4), round(use_rho, 4), max_goals)
    out = np.zeros_like(r)
    out[home_goals:, away_goals:] = r[: max_goals + 1 - home_goals, : max_goals + 1 - away_goals]
    # mass that fell off the edge is folded into the last cell
    out[max_goals, max_goals] += 1.0 - out.sum()
    return out


def market_probs(m: np.ndarray) -> dict:
    i, j = np.indices(m.shape)
    total = i + j
    return {
        "home": float(m[i > j].sum()),
        "draw": float(np.trace(m)),
        "away": float(m[i < j].sum()),
        "over_15": float(m[total > 1.5].sum()),
        "over_25": float(m[total > 2.5].sum()),
        "under_25": float(m[total < 2.5].sum()),
        "cs": m,
    }


def fair_price(p: float, floor: float = 1.01, cap: float = 1000.0) -> float:
    if p <= 0:
        return cap
    return float(np.clip(1.0 / p, floor, cap))


def exit_profit_back(entry: float, exit_price: float, friction: float = 0.03) -> float:
    """Profit per unit staked on a back bet that is greened up by laying at `exit_price`.
    Hedged equally across outcomes: profit = stake * (entry/exit - 1). Friction models the spread
    and commission paid on the exit."""
    exit_price = exit_price * (1 + friction)
    return entry / exit_price - 1.0


def exit_profit_lay(entry: float, exit_price: float, friction: float = 0.03) -> float:
    """Profit per unit of *liability* on a lay bet that is closed by backing at `exit_price`.
    Lay stake s at d (liability s(d-1)); back s*d/d' at d' -> equal profit s(1 - d/d')."""
    exit_price = exit_price / (1 + friction)
    s_per_liability = 1.0 / (entry - 1.0)
    return s_per_liability * (1.0 - entry / exit_price)
