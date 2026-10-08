"""football-data.co.uk historical CSVs: results plus closing odds (Bet365, Pinnacle, Betfair Exchange
and the market maximum/average). Free, no key. These are the files used to backtest strategy ROI
against *real* prices rather than model prices.

Columns we use: Date, HomeTeam, AwayTeam, FTHG, FTAG, HTHG, HTAG, B365H/D/A, PSH/D/A, B365>2.5,
B365<2.5, P>2.5, P<2.5, BFEH/BFED/BFEA (exchange), MaxH/MaxD/MaxA.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

from ..models import MatchResult
from .names import canonical

URL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
CODES = {"en.1": "E0", "en.2": "E1", "de.1": "D1", "es.1": "SP1", "it.1": "I1", "fr.1": "F1",
         "nl.1": "N1", "pt.1": "P1", "sco.1": "SC0", "be.1": "B1", "tr.1": "T1"}


def season_code(label: str) -> str:
    """'2024-25' -> '2425'."""
    a, b = label.split("-")
    return a[2:] + b


def _parse_date(s: str) -> date:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(s)


class FootballDataCoUk:
    def __init__(self, cache_dir: Path | str, timeout: int = 30):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    def frame(self, season: str, league: str, refresh: bool = False) -> pd.DataFrame:
        code = CODES[league]
        path = self.cache_dir / f"fdcouk_{season}_{code}.csv"
        if refresh or not path.exists():
            r = requests.get(URL.format(season=season_code(season), code=code), timeout=self.timeout)
            r.raise_for_status()
            path.write_bytes(r.content)
        df = pd.read_csv(io.BytesIO(path.read_bytes()), encoding="latin-1")
        df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
        df["date"] = df["Date"].astype(str).map(_parse_date)
        df["league"] = league
        df["home"] = df["HomeTeam"].map(canonical)
        df["away"] = df["AwayTeam"].map(canonical)
        return df

    def results(self, seasons: Iterable[str], leagues: Iterable[str]) -> list[MatchResult]:
        out = []
        for s in seasons:
            for l in leagues:
                df = self.frame(s, l)
                for row in df.itertuples(index=False):
                    out.append(MatchResult(row.date, l, row.home, row.away, int(row.FTHG), int(row.FTAG),
                                           int(row.HTHG) if pd.notna(getattr(row, "HTHG", None)) else None,
                                           int(row.HTAG) if pd.notna(getattr(row, "HTAG", None)) else None))
        out.sort(key=lambda r: r.date)
        return out

    @staticmethod
    def closing_prices(row) -> dict[str, float | None]:
        """Pick exchange odds where present, else Pinnacle, else Bet365."""
        def pick(*names):
            for n in names:
                v = getattr(row, n, None)
                if v is not None and pd.notna(v) and float(v) > 1.0:
                    return float(v)
            return None
        return {
            "home": pick("BFEH", "PSH", "B365H"),
            "draw": pick("BFED", "PSD", "B365D"),
            "away": pick("BFEA", "PSA", "B365A"),
            "over_25": pick("BFE_2_5", "P_2_5", "B365_2_5"),  # pandas mangles '>' into '_'
            "under_25": pick("BFE_2_5_1", "P_2_5_1", "B365_2_5_1"),
        }
