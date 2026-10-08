"""Live fixtures from football-data.org (free tier: 10 req/min, 12 competitions incl. PL, PD, BL1,
SA, FL1, ELC, CL). Set FOOTBALL_DATA_API_KEY. Team names are normalised to the openfootball
spelling so the model's ratings line up with the live fixture list."""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

import requests

from ..models import Fixture, MatchResult
from .names import canonical

BASE = "https://api.football-data.org/v4"
COMPETITIONS = {  # openfootball code -> football-data.org code
    "en.1": "PL",
    "en.2": "ELC",
    "de.1": "BL1",
    "es.1": "PD",
    "it.1": "SA",
    "fr.1": "FL1",
    "nl.1": "DED",
    "pt.1": "PPL",
    "uefa.cl": "CL",
}
REVERSE = {v: k for k, v in COMPETITIONS.items()}


class FootballDataOrgProvider:
    def __init__(self, api_key: str, timeout: int = 20):
        self.session = requests.Session()
        self.session.headers["X-Auth-Token"] = api_key
        self.timeout = timeout

    def _get(self, path: str, **params) -> dict:
        r = self.session.get(f"{BASE}{path}", params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def fixtures(self, on: date, leagues: Iterable[str] | None = None) -> list[Fixture]:
        codes = [COMPETITIONS[l] for l in (leagues or COMPETITIONS) if l in COMPETITIONS]
        payload = self._get("/matches", dateFrom=on.isoformat(), dateTo=on.isoformat(), competitions=",".join(codes))
        out: list[Fixture] = []
        for m in payload.get("matches", []):
            if m.get("status") in {"FINISHED", "POSTPONED", "CANCELLED"}:
                continue
            league = REVERSE.get(m["competition"]["code"])
            if not league:
                continue
            ko = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
            out.append(Fixture(date=on, league=league, home=canonical(m["homeTeam"]["name"]),
                               away=canonical(m["awayTeam"]["name"]), kickoff=ko, fixture_id=str(m["id"])))
        return out

    def results(self, leagues: Iterable[str] | None = None, before: date | None = None) -> list[MatchResult]:
        out: list[MatchResult] = []
        for league in leagues or COMPETITIONS:
            code = COMPETITIONS.get(league)
            if not code:
                continue
            payload = self._get(f"/competitions/{code}/matches", status="FINISHED")
            for m in payload.get("matches", []):
                d = date.fromisoformat(m["utcDate"][:10])
                if before and d >= before:
                    continue
                ft = m["score"]["fullTime"]
                ht = m["score"].get("halfTime", {})
                out.append(MatchResult(d, league, canonical(m["homeTeam"]["name"]), canonical(m["awayTeam"]["name"]),
                                       ft["home"], ft["away"], ht.get("home"), ht.get("away")))
        out.sort(key=lambda r: r.date)
        return out
