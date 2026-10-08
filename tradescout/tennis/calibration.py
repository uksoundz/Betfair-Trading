"""Empirical calibration of the tennis point model.

The iid point model is known to be too "even": real matches are more decisive than independent
points imply (Klaassen & Magnus), so it under-predicts straight-sets wins and over-predicts total
games. Two small corrections, fitted on one season and validated on the next:

  * straight sets: logistic recalibration  p' = sigmoid(a + b * logit(p)); the mass taken from or
    added to the straight-sets outcome is rebalanced across the other set scores proportionally
  * total games: the games distribution is shifted by a constant per format (best-of-3 / best-of-5)

Parameters live in data/tennis_calibration.json with the season they were fitted on.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..config import REPO_ROOT

DEFAULT_PATH = REPO_ROOT / "data" / "tennis_calibration.json"


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


@dataclass
class TennisCalibration:
    straight_a: float = 0.0
    straight_b: float = 1.0
    games_shift: dict = field(default_factory=lambda: {"3": 0.0, "5": 0.0})
    fitted_on: str = ""
    n: int = 0

    @property
    def fitted(self) -> bool:
        return bool(self.fitted_on)

    # ----- apply --------------------------------------------------------------------------
    def straight(self, p: float) -> float:
        return _sigmoid(self.straight_a + self.straight_b * _logit(p))

    def apply_sets(self, sets: dict, p_a: float, best_of: int) -> dict:
        """Recalibrate the favourite's straight-sets probability and rebalance the rest."""
        need = best_of // 2 + 1
        fav_home = p_a >= 0.5
        key = f"{need}-0" if fav_home else f"0-{need}"
        p_old = sets.get(key, 0.0)
        if p_old <= 0 or p_old >= 1:
            return sets
        p_new = min(self.straight(p_old), 0.95)
        rest_old = 1 - p_old
        rest_new = 1 - p_new
        out = {}
        for k, v in sets.items():
            out[k] = p_new if k == key else v * rest_new / rest_old
        return out

    def shift_games(self, games: dict, best_of: int) -> dict:
        shift = float(self.games_shift.get(str(best_of), 0.0))
        if abs(shift) < 1e-9:
            return games
        # shift the pmf by a non-integer amount: split between floor and ceil
        lo = math.floor(shift)
        frac = shift - lo
        out: dict = {}
        min_games = 12 if best_of == 3 else 18
        for n, p in games.items():
            for d, w in ((lo, 1 - frac), (lo + 1, frac)):
                if w <= 0:
                    continue
                m = max(min_games, n + d)
                out[m] = out.get(m, 0.0) + p * w
        return out

    # ----- fit ----------------------------------------------------------------------------
    @classmethod
    def fit(cls, p_straight: list, y_straight: list, games_pred: list, games_actual: list, best_of: list, label: str) -> "TennisCalibration":
        x = np.array([_logit(p) for p in p_straight])
        y = np.array(y_straight, dtype=float)
        # 2-parameter logistic regression by Newton's method
        a, b = 0.0, 1.0
        for _ in range(50):
            z = a + b * x
            q = 1 / (1 + np.exp(-z))
            g = np.array([np.sum(q - y), np.sum((q - y) * x)])
            w = q * (1 - q)
            H = np.array([[np.sum(w), np.sum(w * x)], [np.sum(w * x), np.sum(w * x * x)]]) + 1e-6 * np.eye(2)
            step = np.linalg.solve(H, g)
            a, b = a - step[0], b - step[1]
            if np.abs(step).max() < 1e-8:
                break
        gp, ga, bo = np.array(games_pred), np.array(games_actual), np.array(best_of)
        shift = {}
        for fmt in (3, 5):
            m = bo == fmt
            shift[str(fmt)] = float(np.mean(ga[m] - gp[m])) if m.sum() >= 50 else 0.0
        return cls(float(a), float(b), shift, label, int(len(x)))

    def save(self, path: Path | str = DEFAULT_PATH) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.__dict__, indent=1))

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> "TennisCalibration":
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls(**json.loads(p.read_text()))
        except Exception:
            return cls()
