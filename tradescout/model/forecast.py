"""Turn a fitted goal model into a MatchForecast with every market probability the strategies need."""
from __future__ import annotations

from datetime import date
from typing import Sequence

import numpy as np

from ..models import Fixture, MatchForecast, MatchResult
from .dixon_coles import DixonColesModel
from .timing import half_time_matrix, p_goal_before, p_team_scores_first

MINUTES = (15, 30, 45, 60, 70, 75, 80, 85)


def matrix_markets(m: np.ndarray) -> dict:
    n = m.shape[0]
    i, j = np.indices(m.shape)
    total = i + j
    out = {
        "p_home": float(m[i > j].sum()),
        "p_draw": float(np.trace(m)),
        "p_away": float(m[i < j].sum()),
        "p_over": {line: float(m[total > line].sum()) for line in (0.5, 1.5, 2.5, 3.5, 4.5)},
        "p_btts": float(m[(i > 0) & (j > 0)].sum()),
        "p_cs": {f"{a}-{b}": float(m[a, b]) for a in range(min(n, 5)) for b in range(min(n, 5))},
    }
    return out


class Forecaster:
    def __init__(self, model: DixonColesModel):
        self.model = model

    @classmethod
    def fit(cls, results: Sequence[MatchResult], as_of: date, **kw) -> "Forecaster":
        return cls(DixonColesModel(**kw).fit(results, as_of))

    def forecast(self, fixture: Fixture) -> MatchForecast:
        mdl = self.model
        matrix, lam, mu = mdl.score_matrix(fixture.home, fixture.away)
        mk = matrix_markets(matrix)
        ht = half_time_matrix(lam, mu, mdl.rho, mdl.max_goals)
        p_hf, p_af, _ = p_team_scores_first(lam, mu)
        fav = "home" if mk["p_home"] >= mk["p_away"] else "away"
        hs, as_ = mdl.strength(fixture.home), mdl.strength(fixture.away)

        notes: list[str] = []
        # Confidence: how much time-weighted evidence sits behind both ratings. 20 effective
        # matches each ~ a full season of form -> 1.0. Unknown team -> heavy penalty.
        eff = min(hs.effective_matches, as_.effective_matches)
        confidence = float(np.clip(eff / 20.0, 0.0, 1.0))
        if not mdl.knows(fixture.home):
            notes.append(f"No rating for {fixture.home}: league-average placeholder used")
            confidence *= 0.3
        if not mdl.knows(fixture.away):
            notes.append(f"No rating for {fixture.away}: league-average placeholder used")
            confidence *= 0.3
        if 0 < min(hs.matches_in_window, as_.matches_in_window) < 8:
            notes.append("Fewer than 8 matches for one side in the rating window (early season / promoted)")

        return MatchForecast(
            fixture=fixture, score_matrix=matrix, home_xg=lam, away_xg=mu,
            home_strength=hs, away_strength=as_, rho=mdl.rho, ht_matrix=ht,
            p_home=mk["p_home"], p_draw=mk["p_draw"], p_away=mk["p_away"], p_over=mk["p_over"],
            p_btts=mk["p_btts"], p_cs=mk["p_cs"], p_ht_00=float(ht[0, 0]),
            p_goal_before={mnt: p_goal_before(lam + mu, mnt) for mnt in MINUTES},
            p_fav_scores_first=p_hf if fav == "home" else p_af, favourite=fav,
            confidence=confidence, notes=notes,
        )
