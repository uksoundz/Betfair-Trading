"""ATP results in the Sackmann CSV layout (bundled files come from the TML-Database mirror, which
is published for research and educational use; a commercial release needs a licensed feed, and
this loader is the one class to swap).

The files give a tournament start date, not a match date, so each match is assigned an
approximate calendar day from its round and the draw size. That is good enough for replaying a
tournament week and for walk-forward backtests (the model is always fitted as of the tournament
start, so no result inside the tournament leaks into its own ratings).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from ..config import REPO_ROOT
from ..models import Fixture

TENNIS_DATA_DIR = REPO_ROOT / "data" / "sample" / "tennis"
TML_URL = "https://raw.githubusercontent.com/Tennismylife/TML-Database/master/{year}.csv"

LEVELS = {"G": "atp.gs", "M": "atp.1000", "1000": "atp.1000", "500": "atp.500", "250": "atp.250", "A": "atp.250",
          "F": "atp.finals", "O": "atp.olympics", "D": "davis"}
LEVEL_NAMES = {"atp.gs": "Grand Slam", "atp.1000": "ATP Masters 1000", "atp.500": "ATP 500", "atp.250": "ATP 250",
               "atp.finals": "ATP Finals", "atp.olympics": "Olympics", "atp.tour": "ATP Tour"}

# day offset from tournament start by (draw size band, round); pairs alternate by match number parity
_OFFSETS_128 = {"R128": (0, 1), "R64": (2, 3), "R32": (4, 5), "R16": (6, 7), "QF": (8, 9), "SF": (10, 11), "F": (13, 13)}
_OFFSETS_96 = {"R128": (0, 1), "R64": (2, 3), "R32": (4, 5), "R16": (6, 7), "QF": (8, 9), "SF": (10, 10), "F": (11, 11)}
_OFFSETS_64 = {"R64": (0, 1), "R32": (1, 2), "R16": (3, 3), "QF": (4, 4), "SF": (5, 5), "F": (6, 6)}
_OFFSETS_32 = {"R32": (0, 1), "R16": (2, 3), "QF": (4, 4), "SF": (5, 5), "F": (6, 6), "RR": (0, 4), "BR": (6, 6)}


def approx_match_date(tourney_date: date, draw_size: int, rnd: str, match_num: int) -> date:
    table = _OFFSETS_128 if draw_size >= 112 else _OFFSETS_96 if draw_size >= 80 else _OFFSETS_64 if draw_size >= 48 else _OFFSETS_32
    lo, hi = table.get(rnd, (0, 0))
    if rnd == "RR":
        return tourney_date + timedelta(days=min(hi, match_num % 5))
    return tourney_date + timedelta(days=lo if match_num % 2 == 0 else hi)


@dataclass(frozen=True)
class TennisResult:
    date: date
    league: str
    tourney: str
    surface: str
    best_of: int
    winner: str
    loser: str
    score: str
    winner_rank: Optional[int]
    loser_rank: Optional[int]
    tourney_date: date
    match_id: str

    @property
    def retired(self) -> bool:
        return "RET" in self.score or "W/O" in self.score or "DEF" in self.score

    def sets(self) -> list[tuple[int, int]]:
        """[(winner games, loser games)] per completed set, tiebreak detail stripped."""
        out = []
        for part in self.score.replace("[", " ").replace("]", " ").split():
            m = re.match(r"^(\d+)-(\d+)", part)
            if m:
                out.append((int(m.group(1)), int(m.group(2))))
        return out

    @property
    def set_score(self) -> tuple[int, int]:
        w = l = 0
        for a, b in self.sets():
            if a > b:
                w += 1
            elif b > a:
                l += 1
        return w, l

    @property
    def total_games(self) -> int:
        return sum(a + b for a, b in self.sets())

    @property
    def first_set_winner(self) -> Optional[str]:
        s = self.sets()
        if not s:
            return None
        return self.winner if s[0][0] > s[0][1] else self.loser


def _int(v: str) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


class TennisProvider:
    """ResultProvider + FixtureProvider over the bundled CSVs (replay mode)."""

    def __init__(self, data_dir: Path | str = TENNIS_DATA_DIR):
        self.data_dir = Path(data_dir)
        self._rows: list[TennisResult] | None = None
        self.surfaces: dict[str, str] = {}  # tourney name -> last known surface

    def refresh(self, years: Iterable[int], timeout: int = 30) -> list[Path]:
        import requests
        self.data_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for y in years:
            r = requests.get(TML_URL.format(year=y), timeout=timeout)
            if r.status_code == 200 and r.content:
                p = self.data_dir / f"atp_{y}.csv"
                p.write_bytes(r.content)
                written.append(p)
        self._rows = None
        if hasattr(self, "_by_id"):
            del self._by_id
        return written

    def _load(self) -> list[TennisResult]:
        if self._rows is not None:
            return self._rows
        rows: list[TennisResult] = []
        for path in sorted(self.data_dir.glob("atp_*.csv")):
            with path.open(encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    level = LEVELS.get(r.get("tourney_level", ""), "atp.tour")
                    if level == "davis" or not r.get("score"):
                        continue
                    try:
                        td = datetime.strptime(r["tourney_date"], "%Y%m%d").date()
                    except (KeyError, ValueError):
                        continue
                    surface = (r.get("surface") or "Hard").strip() or "Hard"
                    self.surfaces[r["tourney_name"].strip().lower()] = surface
                    draw = _int(r.get("draw_size", "")) or 32
                    mnum = _int(r.get("match_num", ""))
                    winner, loser = r["winner_name"].strip(), r["loser_name"].strip()
                    # match_num is missing in a share of rows (recorded as 0/blank), so the id must
                    # carry the players too or different matches collide on the same key
                    match_id = f"{r['tourney_id']}:{mnum or 0}:{winner}|{loser}"
                    rows.append(TennisResult(
                        approx_match_date(td, draw, r.get("round", ""), mnum or (len(rows) % 2)), level, r["tourney_name"].strip(), surface,
                        _int(r.get("best_of", "")) or 3, winner, loser, r["score"].strip(),
                        _int(r.get("winner_rank", "")), _int(r.get("loser_rank", "")), td, match_id))
        # drop exact duplicates (same tournament, round and players)
        seen: set = set()
        unique: list[TennisResult] = []
        for x in rows:
            k = (x.tourney_date, x.tourney, x.winner, x.loser, x.score)
            if k in seen:
                continue
            seen.add(k)
            unique.append(x)
        unique.sort(key=lambda x: (x.date, x.tourney_date, x.match_id))
        self._rows = unique
        return unique

    def results(self, leagues: Iterable[str] | None = None, before: date | None = None) -> list[TennisResult]:
        wanted = set(leagues) if leagues else None
        return [r for r in self._load() if (wanted is None or r.league in wanted) and (before is None or r.date < before)]

    @staticmethod
    def _fixture(r: TennisResult) -> Fixture:
        # order players by ranking so the fixture never reveals the winner
        wr, lr = r.winner_rank or 9999, r.loser_rank or 9999
        a, b = (r.winner, r.loser) if (wr, r.winner) <= (lr, r.loser) else (r.loser, r.winner)
        return Fixture(r.date, r.league, a, b, datetime.combine(r.date, datetime.min.time()), r.match_id,
                       {"surface": r.surface, "best_of": r.best_of, "tourney": r.tourney, "sport": "tennis"})

    def fixtures(self, on: date, leagues: Iterable[str] | None = None) -> list[Fixture]:
        wanted = set(leagues) if leagues else None
        out = [self._fixture(r) for r in self._load() if r.date == on and (wanted is None or r.league in wanted) and not r.retired]
        out.sort(key=lambda f: (f.meta.get("tourney", ""), f.home))
        return out

    def result_for(self, fixture: Fixture) -> TennisResult | None:
        if not hasattr(self, "_by_id"):
            self._by_id = {r.match_id: r for r in self._load()}
        hit = self._by_id.get(fixture.fixture_id or "")
        if hit is not None and {hit.winner, hit.loser} == {fixture.home, fixture.away}:
            return hit
        for r in self._load():
            if r.date == fixture.date and {r.winner, r.loser} == {fixture.home, fixture.away}:
                return r
        return None

    def match_days(self, start: date, end: date, leagues: Iterable[str] | None = None) -> list[date]:
        wanted = set(leagues) if leagues else None
        return sorted({r.date for r in self._load() if start <= r.date <= end and (wanted is None or r.league in wanted)})

    def surface_for(self, tourney_name: str, default: str = "Hard") -> str:
        key = tourney_name.strip().lower()
        if key in self.surfaces:
            return self.surfaces[key]
        for name, surf in self.surfaces.items():
            if name in key or key in name:
                return surf
        return default

    def players(self) -> set[str]:
        return {r.winner for r in self._load()} | {r.loser for r in self._load()}
