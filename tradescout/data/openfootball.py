"""openfootball/football.json - free, public-domain results for the big European leagues.

Two modes:
  * bundled sample in data/sample (works offline, used by tests and the demo)
  * refresh from GitHub raw URLs (no key needed)

A season file is a dict with "name" and "matches"; each match has date, team1, team2 and an
optional score {"ft": [h, a], "ht": [h, a]}. Fixtures with no "ft" have not been played.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable

import requests

from ..config import SAMPLE_DATA_DIR
from ..models import Fixture, MatchResult

RAW_URL = "https://raw.githubusercontent.com/openfootball/football.json/master/{season}/{league}.json"
DEFAULT_LEAGUES = ("en.1", "en.2", "de.1", "es.1", "it.1", "fr.1")


def _season_label(d: date) -> str:
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start}-{str(start + 1)[2:]}"


def _seasons_covering(start: date, end: date) -> list[str]:
    out: list[str] = []
    y = start.year if start.month >= 7 else start.year - 1
    while date(y, 7, 1) <= end:
        out.append(f"{y}-{str(y + 1)[2:]}")
        y += 1
    return out


def _ft(m: dict) -> list[int] | None:
    s = m.get("score")
    if isinstance(s, dict) and s.get("ft"):
        return s["ft"]
    return None


def _ht(m: dict) -> list[int] | None:
    s = m.get("score")
    if isinstance(s, dict) and s.get("ht"):
        return s["ht"]
    return None


class OpenFootballProvider:
    """Implements ResultProvider and FixtureProvider from openfootball JSON files."""

    def __init__(self, data_dir: Path | str = SAMPLE_DATA_DIR, leagues: Iterable[str] = DEFAULT_LEAGUES):
        self.data_dir = Path(data_dir)
        self.leagues = tuple(leagues)
        self._raw: dict[tuple[str, str], list[dict]] = {}

    # ----- loading -------------------------------------------------------------------------
    def _load(self, season: str, league: str) -> list[dict]:
        key = (season, league)
        if key in self._raw:
            return self._raw[key]
        path = self.data_dir / f"{season}_{league}.json"
        if not path.exists():
            self._raw[key] = []
            return []
        with path.open() as fh:
            payload = json.load(fh)
        matches = payload.get("matches", []) if isinstance(payload, dict) else []
        self._raw[key] = matches
        return matches

    @staticmethod
    def current_season(today: date | None = None) -> str:
        return _season_label(today or date.today())

    def refresh_current_if_stale(self, max_age_hours: int = 24, timeout: int = 30) -> bool:
        """Download this season's files if missing or older than max_age_hours. Never raises."""
        import time
        season = self.current_season()
        sample = self.data_dir / f"{season}_{self.leagues[0]}.json"
        if sample.exists() and (time.time() - sample.stat().st_mtime) < max_age_hours * 3600:
            return False
        try:
            return bool(self.refresh([season], timeout=timeout))
        except Exception:
            return False

    def available_seasons(self) -> list[str]:
        return sorted({p.name.split("_")[0] for p in self.data_dir.glob("*_*.json")})

    def refresh(self, seasons: Iterable[str], leagues: Iterable[str] | None = None, timeout: int = 30) -> list[Path]:
        """Download season files from GitHub into data_dir. Returns written paths."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for season in seasons:
            for league in leagues or self.leagues:
                url = RAW_URL.format(season=season, league=league)
                r = requests.get(url, timeout=timeout)
                if r.status_code != 200:
                    continue
                path = self.data_dir / f"{season}_{league}.json"
                path.write_bytes(r.content)
                self._raw.pop((season, league), None)
                written.append(path)
        return written

    # ----- ResultProvider --------------------------------------------------------------------
    def results(self, leagues: Iterable[str] | None = None, before: date | None = None) -> list[MatchResult]:
        out: list[MatchResult] = []
        for season in self.available_seasons():
            for league in leagues or self.leagues:
                for m in self._load(season, league):
                    ft = _ft(m)
                    if not ft:
                        continue
                    d = date.fromisoformat(m["date"])
                    if before is not None and d >= before:
                        continue
                    ht = _ht(m)
                    out.append(
                        MatchResult(
                            date=d,
                            league=league,
                            home=m["team1"],
                            away=m["team2"],
                            home_goals=int(ft[0]),
                            away_goals=int(ft[1]),
                            ht_home=int(ht[0]) if ht else None,
                            ht_away=int(ht[1]) if ht else None,
                        )
                    )
        out.sort(key=lambda r: r.date)
        return out

    # ----- FixtureProvider -------------------------------------------------------------------
    def fixtures(self, on: date, leagues: Iterable[str] | None = None) -> list[Fixture]:
        """All matches scheduled on `on`, played or not. Replaying a past date therefore shows
        the fixtures as they looked that morning; the result is only used for evaluation."""
        season = _season_label(on)
        out: list[Fixture] = []
        for league in leagues or self.leagues:
            for m in self._load(season, league):
                if m.get("date") != on.isoformat():
                    continue
                ko = None
                if m.get("time"):
                    try:
                        ko = datetime.combine(on, time.fromisoformat(m["time"]))
                    except ValueError:
                        ko = None
                out.append(Fixture(date=on, league=league, home=m["team1"], away=m["team2"], kickoff=ko,
                                   fixture_id=f"{league}:{on.isoformat()}:{m['team1']}:{m['team2']}"))
        out.sort(key=lambda f: (f.kickoff or datetime.combine(on, time.min), f.league))
        return out

    def result_for(self, fixture: Fixture) -> MatchResult | None:
        """Actual result of a fixture if it has been played (used for replay evaluation)."""
        for m in self._load(_season_label(fixture.date), fixture.league):
            if m.get("date") == fixture.date.isoformat() and m["team1"] == fixture.home and m["team2"] == fixture.away:
                ft = _ft(m)
                if not ft:
                    return None
                ht = _ht(m)
                return MatchResult(fixture.date, fixture.league, fixture.home, fixture.away, int(ft[0]), int(ft[1]),
                                   int(ht[0]) if ht else None, int(ht[1]) if ht else None)
        return None

    def match_days(self, start: date, end: date, leagues: Iterable[str] | None = None) -> list[date]:
        days: set[date] = set()
        for season in _seasons_covering(start, end):
            for league in leagues or self.leagues:
                for m in self._load(season, league):
                    d = date.fromisoformat(m["date"])
                    if start <= d <= end:
                        days.add(d)
        return sorted(days)
