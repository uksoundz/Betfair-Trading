"""Surface-blended Elo for tennis.

One overall rating and one per surface (hard, clay, grass), updated chronologically. The K factor
shrinks as a player accumulates matches (FiveThirtyEight style), so newcomers move fast and
established players move slowly. Match-win probability uses a 50/50 blend of overall and surface
rating when the surface rating has enough history behind it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from .data import TennisResult

SURFACES = ("Hard", "Clay", "Grass", "Carpet")


def _k(n: int) -> float:
    return 250.0 / ((n + 5) ** 0.4)


@dataclass
class PlayerRating:
    overall: float = 1500.0
    surface: dict = field(default_factory=lambda: {s: 1500.0 for s in SURFACES})
    n: int = 0
    n_surface: dict = field(default_factory=lambda: {s: 0 for s in SURFACES})
    last_played: Optional[date] = None
    wins: int = 0

    def rating_on(self, surface: str, min_surface_matches: int = 10) -> float:
        s = surface if surface in self.surface else "Hard"
        if self.n_surface.get(s, 0) >= min_surface_matches:
            return 0.5 * self.overall + 0.5 * self.surface[s]
        return self.overall


def expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


class TennisElo:
    # Defaults chosen on 2024 and confirmed on the 2025 holdout (eval/tennis_eval.py): a slower K
    # beats the FiveThirtyEight default, and the surface blend did not help on this data.
    def __init__(self, k_scale: float = 0.5, surface_weight: float = 0.0, min_surface_matches: int = 10):
        self.players: dict[str, PlayerRating] = {}
        self.n_matches = 0
        self.fitted_on: Optional[date] = None
        self.k_scale = k_scale
        self.surface_weight = surface_weight
        self.min_surface_matches = min_surface_matches

    def _rating(self, name: str, surface: str) -> float:
        if not self.knows(name):
            return 1500.0
        p = self.players[name]
        s = surface if surface in p.surface else "Hard"
        if p.n_surface.get(s, 0) >= self.min_surface_matches and self.surface_weight > 0:
            return (1 - self.surface_weight) * p.overall + self.surface_weight * p.surface[s]
        return p.overall

    def get(self, name: str) -> PlayerRating:
        return self.players.setdefault(name, PlayerRating())

    def fit(self, results: Iterable[TennisResult], as_of: date) -> "TennisElo":
        rows = sorted((r for r in results if r.date < as_of), key=lambda r: (r.date, r.tourney_date, r.match_id))
        for r in rows:
            if "W/O" in r.score or "DEF" in r.score:
                continue  # walkovers carry no information
            w, l = self.get(r.winner), self.get(r.loser)
            surf = r.surface if r.surface in SURFACES else "Hard"
            # overall
            e = expected(w.overall, l.overall)
            weight = (1.0 if r.best_of == 3 else 1.1) * self.k_scale  # best-of-five results are a little more informative
            dw = weight * _k(w.n) * (1 - e)
            dl = weight * _k(l.n) * (e - 1)
            w.overall += dw
            l.overall += dl
            # surface
            es = expected(w.surface[surf], l.surface[surf])
            w.surface[surf] += weight * _k(w.n_surface[surf]) * (1 - es)
            l.surface[surf] += weight * _k(l.n_surface[surf]) * (es - 1)
            w.n += 1
            l.n += 1
            w.n_surface[surf] += 1
            l.n_surface[surf] += 1
            w.wins += 1
            w.last_played = r.date
            l.last_played = r.date
            self.n_matches += 1
        self.fitted_on = as_of
        return self

    def knows(self, name: str) -> bool:
        return name in self.players

    def p_win(self, a: str, b: str, surface: str) -> float:
        return expected(self._rating(a, surface), self._rating(b, surface))

    def rating_on(self, name: str, surface: str) -> float:
        return self._rating(name, surface)

    def confidence(self, a: str, b: str, as_of: date) -> tuple[float, list[str]]:
        notes: list[str] = []
        conf = 1.0
        for name in (a, b):
            if not self.knows(name):
                notes.append(f"No rating history for {name}: tour-average rating assumed")
                conf *= 0.3
                continue
            p = self.players[name]
            conf *= min(1.0, p.n / 30.0)
            if p.last_played and (as_of - p.last_played).days > 120:
                notes.append(f"{name} has not played for {(as_of - p.last_played).days} days (injury or layoff): rating may be stale")
                conf *= 0.7
        return float(max(0.0, min(1.0, math.sqrt(conf)))), notes

    def table(self, surface: str = "Hard", top: int = 30) -> list[tuple[str, float, float, int]]:
        rows = [(n, p.overall, p.rating_on(surface), p.n) for n, p in self.players.items() if p.n >= 10]
        return sorted(rows, key=lambda r: -r[2])[:top]
