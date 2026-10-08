"""Live fixtures from football-data.org (free tier: 10 req/min, 12 competitions incl. PL, PD, BL1,
SA, FL1, ELC, CL). Set FOOTBALL_DATA_API_KEY. Team names are normalised to the openfootball
spelling so the model's ratings line up with the live fixture list."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

import requests

from ..models import Fixture, MatchResult
from .names import canonical

BASE = "https://api.football-data.org/v4"
UK_TZ = ZoneInfo("Europe/London")
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


class RateLimited(RuntimeError):
    """The free plan allows 10 requests a minute."""


class BadApiKey(RuntimeError):
    pass


class FootballDataOrgProvider:
    def __init__(self, api_key: str, timeout: int = 20):
        self.session = requests.Session()
        self.session.headers["X-Auth-Token"] = api_key
        self.timeout = timeout

    def _get(self, path: str, **params) -> dict:
        r = self.session.get(f"{BASE}{path}", params=params, timeout=self.timeout)
        if r.status_code == 429:
            raise RateLimited("football-data.org limit reached (10 requests a minute on the free plan). Wait a minute and try again.")
        if r.status_code in (400, 401, 403):
            raise BadApiKey(f"football-data.org rejected the request ({r.status_code}). Check the API key in Settings.")
        r.raise_for_status()
        return r.json()

    def ping(self) -> dict:
        """Cheap connectivity test: fixtures over the next 3 days."""
        counts = self.upcoming(date.today(), 3)
        return {"ok": True, "fixtures_next_3_days": sum(counts.values()), "days": {d.isoformat(): n for d, n in counts.items()}}

    def finished_matches(self, league: str) -> list[MatchResult]:
        """All finished matches of the current season for one competition (one request)."""
        code = COMPETITIONS[league]
        payload = self._get(f"/competitions/{code}/matches", status="FINISHED")
        out: list[MatchResult] = []
        for m in payload.get("matches", []):
            ft = (m.get("score") or {}).get("fullTime") or {}
            if ft.get("home") is None or ft.get("away") is None:
                continue
            ht = (m.get("score") or {}).get("halfTime") or {}
            d = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(UK_TZ).date()
            out.append(MatchResult(d, league, canonical(m["homeTeam"]["name"]), canonical(m["awayTeam"]["name"]),
                                   int(ft["home"]), int(ft["away"]), ht.get("home"), ht.get("away")))
        return out

    @staticmethod
    def parse_fixtures(payload: dict, on: date) -> list[Fixture]:
        """Turn a /matches payload into Fixtures kicking off on `on` (UK local date). Postponed and
        cancelled games are dropped; finished ones are kept so past days can be replayed."""
        out: list[Fixture] = []
        for m in payload.get("matches", []):
            if m.get("status") in {"POSTPONED", "CANCELLED", "SUSPENDED"}:
                continue
            league = REVERSE.get((m.get("competition") or {}).get("code"))
            if not league:
                continue
            ko = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(UK_TZ)
            if ko.date() != on:
                continue
            out.append(Fixture(date=on, league=league, home=canonical(m["homeTeam"]["name"]),
                               away=canonical(m["awayTeam"]["name"]), kickoff=ko.replace(tzinfo=None), fixture_id=str(m["id"])))
        out.sort(key=lambda f: (f.kickoff, f.league))
        return out

    def fixtures(self, on: date, leagues: Iterable[str] | None = None) -> list[Fixture]:
        codes = [COMPETITIONS[l] for l in (leagues or COMPETITIONS) if l in COMPETITIONS]
        # dateTo is exclusive on football-data.org v4, so ask for [on, on + 2 days) and filter locally;
        # the extra day also catches late UK kick-offs that fall on the next UTC date.
        payload = self._get("/matches", dateFrom=(on - timedelta(days=1)).isoformat(), dateTo=(on + timedelta(days=2)).isoformat(),
                            competitions=",".join(codes))
        return self.parse_fixtures(payload, on)

    def upcoming(self, start: date, days: int = 10, leagues: Iterable[str] | None = None) -> dict[date, int]:
        """Number of fixtures per UK day from `start` for `days` days (free tier allows a 10-day window)."""
        codes = [COMPETITIONS[l] for l in (leagues or COMPETITIONS) if l in COMPETITIONS]
        end = start + timedelta(days=min(days, 10))
        payload = self._get("/matches", dateFrom=start.isoformat(), dateTo=end.isoformat(), competitions=",".join(codes))
        counts: dict[date, int] = {}
        for m in payload.get("matches", []):
            if m.get("status") in {"POSTPONED", "CANCELLED", "SUSPENDED"}:
                continue
            d = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(UK_TZ).date()
            if start <= d < end:
                counts[d] = counts.get(d, 0) + 1
        return dict(sorted(counts.items()))

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
