"""Turn Elo + Markov into a TennisForecast with every number the strategies and the UI need."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from ..models import Fixture
from .data import TennisResult
from .elo import TennisElo
from .markov import match_distribution, p_first_break, p_match_from, solve_serve_probs

GAME_LINES_BO3 = (18.5, 19.5, 20.5, 21.5, 22.5, 23.5, 24.5)
GAME_LINES_BO5 = (30.5, 32.5, 34.5, 36.5, 38.5, 40.5)


@dataclass
class TennisForecast:
    fixture: Fixture
    a: str
    b: str
    surface: str
    best_of: int
    p_a: float                 # match win probability for player A (the listed "home")
    elo_a: float
    elo_b: float
    pa_serve: float
    pb_serve: float
    p_set1_a: float
    p_sets: dict               # "2-0" -> prob (A's sets first)
    p_games: dict              # total games -> prob
    expected_games: float
    p_over: dict               # line -> P(total games > line)
    p_first_break_a: float
    p_first_break_b: float
    cond: dict                 # named in-play states -> P(A wins) e.g. "set1_won", "set1_lost", "a_break_up", "b_break_up"
    confidence: float
    notes: list[str] = field(default_factory=list)
    rank_a: Optional[int] = None
    rank_b: Optional[int] = None

    @property
    def favourite(self) -> str:
        return "home" if self.p_a >= 0.5 else "away"

    @property
    def p_fav(self) -> float:
        return max(self.p_a, 1 - self.p_a)

    def fav_name(self) -> str:
        return self.a if self.p_a >= 0.5 else self.b

    def dog_name(self) -> str:
        return self.b if self.p_a >= 0.5 else self.a

    def p_fav_from(self, sets_fav: int, sets_dog: int, games_fav: int, games_dog: int, server: str) -> float:
        """P(favourite wins) from an in-play state expressed from the favourite's point of view;
        server is 'fav' or 'dog'."""
        if self.p_a >= 0.5:
            return p_match_from(self.pa_serve, self.pb_serve, self.best_of, sets_fav, sets_dog, games_fav, games_dog, "A" if server == "fav" else "B")
        return 1 - p_match_from(self.pa_serve, self.pb_serve, self.best_of, sets_dog, sets_fav, games_dog, games_fav, "A" if server == "dog" else "B")


# 5-point Gauss-Hermite quadrature for a standard normal: (abscissa, weight)
_GH = [(-2.0201828705, 0.0199532421), (-0.9585724646, 0.3936193232), (0.0, 0.9453087205), (0.9585724646, 0.3936193232), (2.0201828705, 0.0199532421)]
_GH = [(z * math.sqrt(2), w / math.sqrt(math.pi)) for z, w in _GH]


class TennisForecaster:
    """mixture_sigma: standard deviation (logit scale) of day-to-day uncertainty in the true match
    probability around the Elo estimate. The point-level model assumes one fixed strength all match;
    real matches are more lopsided than that, so without the mixture it predicts too few straight-sets
    wins and too many games (eval/tennis_eval.py). The value is chosen on 2024 and confirmed on 2025."""

    def __init__(self, elo: TennisElo, mixture_sigma: float = 0.0, calibration=None):
        self.elo = elo
        self.mixture_sigma = mixture_sigma  # tested on 2024: no gain, so off by default
        if calibration is None:
            from .calibration import TennisCalibration
            calibration = TennisCalibration.load()
        self.calibration = calibration

    @classmethod
    def fit(cls, results: Iterable[TennisResult], as_of: date, mixture_sigma: float = 0.0, calibration=None, **elo_kwargs) -> "TennisForecaster":
        return cls(TennisElo(**elo_kwargs).fit(results, as_of), mixture_sigma, calibration)

    def _components(self, p_a: float) -> list[tuple[float, float]]:
        """[(weight, p)] mixture components around p_a on the logit scale."""
        if self.mixture_sigma <= 0:
            return [(1.0, p_a)]
        p_a = min(max(p_a, 1e-4), 1 - 1e-4)
        lo = math.log(p_a / (1 - p_a))
        out = []
        for z, w in _GH:
            l = lo + self.mixture_sigma * z
            out.append((w, 1 / (1 + math.exp(-l))))
        s = sum(w for w, _ in out)
        return [(w / s, p) for w, p in out]

    def forecast(self, fx: Fixture) -> TennisForecast:
        surface = fx.meta.get("surface", "Hard")
        best_of = int(fx.meta.get("best_of", 3))
        p_elo = self.elo.p_win(fx.home, fx.away, surface)
        lines = GAME_LINES_BO3 if best_of == 3 else GAME_LINES_BO5
        # mixture over strength uncertainty
        p_a = 0.0
        sets: dict = {}
        games: dict = {}
        p_set1 = 0.0
        fb_a = fb_b = 0.0
        cond = {"set1_won": 0.0, "set1_lost": 0.0, "a_break_up": 0.0, "b_break_up": 0.0}
        pa_mix = pb_mix = 0.0
        for w, p in self._components(p_elo):
            pa, pb = solve_serve_probs(p, best_of, surface)
            dist = match_distribution(pa, pb, best_of, "A")
            p_a += w * dist["p_match"]
            p_set1 += w * dist["p_set1"]
            for k, v in dist["sets"].items():
                key = f"{k[0]}-{k[1]}"
                sets[key] = sets.get(key, 0.0) + w * v
            for n, v in dist["games"].items():
                games[n] = games.get(n, 0.0) + w * v
            a_fb, b_fb = p_first_break(pa, pb, "A")
            fb_a += w * a_fb
            fb_b += w * b_fb
            cond["set1_won"] += w * p_match_from(pa, pb, best_of, 1, 0, 0, 0, "A")
            cond["set1_lost"] += w * p_match_from(pa, pb, best_of, 0, 1, 0, 0, "A")
            cond["a_break_up"] += w * p_match_from(pa, pb, best_of, 0, 0, 2, 1, "A")
            cond["b_break_up"] += w * p_match_from(pa, pb, best_of, 0, 0, 1, 2, "B")
            pa_mix += w * pa
            pb_mix += w * pb
        # empirical calibration (fitted on 2024, validated on 2025): the point model is too even
        if self.calibration is not None and self.calibration.fitted:
            sets = self.calibration.apply_sets(sets, p_a, best_of)
            games = self.calibration.shift_games(games, best_of)
        games = dict(sorted(games.items()))
        exp_games = sum(n * p for n, p in games.items())
        p_over = {line: sum(p for n, p in games.items() if n > line) for line in lines}
        conf, notes = self.elo.confidence(fx.home, fx.away, self.elo.fitted_on or fx.date)
        ra = self.elo.rating_on(fx.home, surface)
        rb = self.elo.rating_on(fx.away, surface)
        return TennisForecast(fx, fx.home, fx.away, surface, best_of, p_a, ra, rb, round(pa_mix, 4), round(pb_mix, 4), p_set1, sets, games,
                              exp_games, p_over, fb_a, fb_b, cond, conf, notes)


def summary(fc: TennisForecast) -> list[str]:
    fav, dog = fc.fav_name(), fc.dog_name()
    pf = fc.p_fav
    out = []
    if pf >= 0.75:
        out.append(f"{fav} is a heavy favourite at {pf:.0%} (fair price {1/pf:.2f}) on {fc.surface.lower()}; Elo {fc.elo_a:.0f} v {fc.elo_b:.0f}.")
    elif pf >= 0.6:
        out.append(f"{fav} is the favourite at {pf:.0%} (fair {1/pf:.2f}) against {dog}; Elo {fc.elo_a:.0f} v {fc.elo_b:.0f} on {fc.surface.lower()}.")
    else:
        out.append(f"Close match: {fc.a} {fc.p_a:.0%}, {fc.b} {1-fc.p_a:.0%} on {fc.surface.lower()}. Expect swings; set-by-set trading suits it.")
    out.append(f"Serve model: {fc.a} wins {fc.pa_serve:.0%} of points on serve, {fc.b} {fc.pb_serve:.0%}. Expected {fc.expected_games:.1f} games; "
               f"straight sets for {fav} {fc.p_sets.get('2-0' if fc.p_a >= 0.5 else '0-2', fc.p_sets.get('3-0', 0)):.0%}.")
    fav_set1 = fc.p_set1_a if fc.p_a >= 0.5 else 1 - fc.p_set1_a
    fav_after_loss = fc.cond["set1_lost"] if fc.p_a >= 0.5 else 1 - fc.cond["set1_won"]
    out.append(f"{fav} wins the first set {fav_set1:.0%} of the time; after losing it the chance drops to {fav_after_loss:.0%}, "
               f"which is where the trading value sits.")
    if fc.confidence < 0.6:
        out.append("Caution: thin rating history or a long layoff for one player; treat the numbers with care.")
    out += [n + "." for n in fc.notes]
    return out
