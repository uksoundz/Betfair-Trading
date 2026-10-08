"""Time-weighted Dixon-Coles (1997) goal model.

Each team gets an attack and a defence rating; there is a home-advantage term and the
Dixon-Coles rho correction for the under-dispersion of low scores (0-0, 1-0, 0-1, 1-1).
Older matches are down-weighted exponentially (xi per day) so form matters more than history.

Fitted by maximum likelihood with scipy. On ~2,000 matches this takes well under a second.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

from ..models import MatchResult, TeamStrength


def _tau(x: np.ndarray, y: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float) -> np.ndarray:
    """Dixon-Coles low-score adjustment factor, vectorised."""
    out = np.ones_like(lam)
    m00 = (x == 0) & (y == 0)
    m10 = (x == 1) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m11 = (x == 1) & (y == 1)
    out[m00] = 1 - lam[m00] * mu[m00] * rho
    out[m10] = 1 + mu[m10] * rho
    out[m01] = 1 + lam[m01] * rho
    out[m11] = 1 - rho
    return out


@dataclass
class DixonColesModel:
    xi: float = 0.0045  # time decay per day
    max_goals: int = 8
    history_days: int = 900

    def __post_init__(self):
        self.teams: list[str] = []
        self.index: dict[str, int] = {}
        self.attack: np.ndarray | None = None
        self.defence: np.ndarray | None = None
        self.home_adv: float = 0.25
        self.rho: float = -0.05
        self.matches_in_window: dict[str, int] = {}
        self.effective_matches: dict[str, float] = {}
        self.league_avg_goals: dict[str, float] = {}
        self.fitted_on: date | None = None
        self.n_matches: int = 0

    # ------------------------------------------------------------------------------------
    def fit(self, results: Sequence[MatchResult], as_of: date) -> "DixonColesModel":
        rows = [r for r in results if r.date < as_of and (as_of - r.date).days <= self.history_days]
        if len(rows) < 50:
            raise ValueError(f"need at least 50 historical matches, got {len(rows)}")
        self.teams = sorted({r.home for r in rows} | {r.away for r in rows})
        self.index = {t: i for i, t in enumerate(self.teams)}
        n = len(self.teams)
        h = np.array([self.index[r.home] for r in rows])
        a = np.array([self.index[r.away] for r in rows])
        hg = np.array([r.home_goals for r in rows], dtype=float)
        ag = np.array([r.away_goals for r in rows], dtype=float)
        days = np.array([(as_of - r.date).days for r in rows], dtype=float)
        w = np.exp(-self.xi * days)

        # per-league averages (used as prior for unseen teams and for reporting)
        by_league: dict[str, list[float]] = {}
        for r in rows:
            by_league.setdefault(r.league, []).append(r.total_goals)
        self.league_avg_goals = {k: float(np.mean(v)) for k, v in by_league.items()}

        self.matches_in_window = {t: 0 for t in self.teams}
        self.effective_matches = {t: 0.0 for t in self.teams}
        for r, wi in zip(rows, w):
            self.matches_in_window[r.home] += 1
            self.matches_in_window[r.away] += 1
            self.effective_matches[r.home] += float(wi)
            self.effective_matches[r.away] += float(wi)

        def unpack(p):
            att = p[:n]
            dfn = p[n:2 * n]
            return att, dfn, p[2 * n], p[2 * n + 1]

        ridge = 0.01
        is00 = (hg == 0) & (ag == 0)
        is10 = (hg == 1) & (ag == 0)
        is01 = (hg == 0) & (ag == 1)
        is11 = (hg == 1) & (ag == 1)

        def nll_and_grad(p):
            att, dfn, home_adv, rho = unpack(p)
            lam = np.exp(att[h] + dfn[a] + home_adv)
            mu = np.exp(att[a] + dfn[h])
            tau = np.clip(_tau(hg, ag, lam, mu, rho), 1e-6, None)
            ll = w * (np.log(tau) + hg * np.log(lam) - lam + ag * np.log(mu) - mu)
            value = -np.sum(ll) + ridge * (np.sum(att ** 2) + np.sum(dfn ** 2))
            # derivatives of log tau
            dlt_dlam = np.where(is00, -mu * rho, np.where(is01, rho, 0.0)) / tau
            dlt_dmu = np.where(is00, -lam * rho, np.where(is10, rho, 0.0)) / tau
            dlt_drho = np.where(is00, -lam * mu, np.where(is10, mu, np.where(is01, lam, np.where(is11, -1.0, 0.0)))) / tau
            g_lam = w * ((hg - lam) + lam * dlt_dlam)  # d ll / d log(lam)
            g_mu = w * ((ag - mu) + mu * dlt_dmu)
            g_att = np.bincount(h, g_lam, minlength=n) + np.bincount(a, g_mu, minlength=n)
            g_def = np.bincount(a, g_lam, minlength=n) + np.bincount(h, g_mu, minlength=n)
            grad = np.concatenate([-g_att + 2 * ridge * att, -g_def + 2 * ridge * dfn, [-np.sum(g_lam)], [-np.sum(w * dlt_drho)]])
            return value, grad

        x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25, -0.05]])
        bounds = [(-3, 3)] * (2 * n) + [(-1, 1), (-0.45, 0.45)]
        res = minimize(nll_and_grad, x0, jac=True, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 500})
        att, dfn, self.home_adv, self.rho = unpack(res.x)
        # gauge: centre attack on zero (shifting attack by c and defence by -c leaves predictions unchanged)
        c = float(np.mean(att))
        self.attack, self.defence = att - c, dfn + c
        self.home_adv = float(self.home_adv)
        self.rho = float(self.rho)
        self.fitted_on = as_of
        self.n_matches = len(rows)
        return self

    # ------------------------------------------------------------------------------------
    def knows(self, team: str) -> bool:
        return team in self.index

    def strength(self, team: str) -> TeamStrength:
        if not self.knows(team):
            # league-average placeholder: attack 0 (mean), defence = mean defence
            return TeamStrength(attack=0.0, defence=float(np.mean(self.defence)), matches_in_window=0, effective_matches=0.0)
        i = self.index[team]
        return TeamStrength(float(self.attack[i]), float(self.defence[i]), self.matches_in_window[team], self.effective_matches[team])

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        hs, as_ = self.strength(home), self.strength(away)
        lam = float(np.exp(hs.attack + as_.defence + self.home_adv))
        mu = float(np.exp(as_.attack + hs.defence))
        return lam, mu

    def score_matrix(self, home: str, away: str) -> tuple[np.ndarray, float, float]:
        lam, mu = self.expected_goals(home, away)
        g = np.arange(self.max_goals + 1)
        ph = poisson.pmf(g, lam)
        pa = poisson.pmf(g, mu)
        m = np.outer(ph, pa)
        # DC correction on the four low-score cells
        m[0, 0] *= 1 - lam * mu * self.rho
        m[1, 0] *= 1 + mu * self.rho
        m[0, 1] *= 1 + lam * self.rho
        m[1, 1] *= 1 - self.rho
        m = np.clip(m, 0, None)
        m /= m.sum()
        return m, lam, mu

    def ratings_table(self) -> list[tuple[str, float, float, int]]:
        return sorted(((t, float(self.attack[i]), float(self.defence[i]), self.matches_in_window[t]) for t, i in self.index.items()),
                      key=lambda r: -(r[1] - r[2]))
