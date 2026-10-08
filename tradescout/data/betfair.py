"""Betfair Exchange prices via the JSON-RPC Betting API. Needs BETFAIR_APP_KEY plus either a
session token or username/password (interactive login endpoint; cert login is left to the
user's own setup). Soccer event type id is 1.

Only the markets the strategies consume are fetched: MATCH_ODDS, OVER_UNDER_15/25/35,
BOTH_TEAMS_TO_SCORE, CORRECT_SCORE. Best back price is returned for back-side selections and
best lay price for lay-side ones is exposed through `lay_prices`.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import requests

from ..models import Fixture, MarketPrices
from .names import names_match

LOGIN = "https://identitysso.betfair.com/api/login"
BETTING = "https://api.betfair.com/exchange/betting/json-rpc/v1"
MARKETS = ["MATCH_ODDS", "OVER_UNDER_15", "OVER_UNDER_25", "OVER_UNDER_35", "BOTH_TEAMS_TO_SCORE", "CORRECT_SCORE"]


class BetfairPrices:
    def __init__(self, app_key: str, session_token: str | None = None, username: str | None = None,
                 password: str | None = None, timeout: int = 20):
        self.app_key = app_key
        self.timeout = timeout
        self.token = session_token or self._login(username or "", password or "")
        self._event_cache: dict[str, list[dict]] = {}

    def _login(self, username: str, password: str) -> str:
        r = requests.post(LOGIN, data={"username": username, "password": password},
                          headers={"X-Application": self.app_key, "Accept": "application/json"}, timeout=self.timeout)
        r.raise_for_status()
        payload = r.json()
        if payload.get("status") != "SUCCESS":
            raise RuntimeError(f"Betfair login failed: {payload}")
        return payload["token"]

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        body = {"jsonrpc": "2.0", "method": f"SportsAPING/v1.0/{method}", "params": params, "id": 1}
        r = requests.post(BETTING, json=body, timeout=self.timeout,
                          headers={"X-Application": self.app_key, "X-Authentication": self.token, "Content-Type": "application/json"})
        r.raise_for_status()
        payload = r.json()
        if "error" in payload:
            raise RuntimeError(payload["error"])
        return payload["result"]

    def _events_for_day(self, fixture: Fixture) -> list[dict]:
        key = fixture.date.isoformat()
        if key not in self._event_cache:
            start = fixture.date.isoformat() + "T00:00:00Z"
            end = (fixture.date + timedelta(days=1)).isoformat() + "T00:00:00Z"
            self._event_cache[key] = self._rpc("listEvents", {"filter": {"eventTypeIds": ["1"], "marketStartTime": {"from": start, "to": end}}})
        return self._event_cache[key]

    def _find_event(self, fixture: Fixture) -> str | None:
        for ev in self._events_for_day(fixture):
            name = ev["event"]["name"]
            if " v " not in name:
                continue
            h, a = name.split(" v ", 1)
            if names_match(h, fixture.home) and names_match(a, fixture.away):
                return ev["event"]["id"]
        return None

    # ----- read-only lookups used by the bet slip -------------------------------------------
    def _catalogue(self, fixture: Fixture) -> list[dict]:
        event_id = self._find_event(fixture)
        if not event_id:
            return []
        key = f"cat:{event_id}"
        if key not in self._event_cache:
            self._event_cache[key] = self._rpc("listMarketCatalogue", {
                "filter": {"eventIds": [event_id], "marketTypeCodes": MARKETS},
                "maxResults": 20, "marketProjection": ["RUNNER_DESCRIPTION", "MARKET_DESCRIPTION"]})
        return self._event_cache[key]

    @staticmethod
    def _runner_matches(runner: dict, fixture: Fixture, market: str, selection: str) -> bool:
        name = runner["runnerName"]
        if market == "MATCH_ODDS":
            if selection == "draw":
                return name == "The Draw"
            target = fixture.home if selection == "home" else fixture.away
            if names_match(name, target):
                return True
            # Betfair convention: sortPriority 1 = home, 2 = away, 3 = draw
            return runner.get("sortPriority") == (1 if selection == "home" else 2) and name != "The Draw"
        if market == "CORRECT_SCORE":
            return name.replace(" ", "") == selection.replace(" ", "")
        return name.lower() == selection.lower()

    def resolve(self, fixture: Fixture, market: str, selection: str):
        """(market_id, selection_id, best_back, best_lay) for one selection, or None."""
        for cat in self._catalogue(fixture):
            if cat["description"]["marketType"] != market:
                continue
            for r in cat["runners"]:
                if self._runner_matches(r, fixture, market, selection):
                    book = self._rpc("listMarketBook", {"marketIds": [cat["marketId"]], "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}})
                    best_back = best_lay = None
                    for br in (book[0].get("runners", []) if book else []):
                        if br["selectionId"] == r["selectionId"]:
                            best_back = (br.get("ex", {}).get("availableToBack") or [{}])[0].get("price")
                            best_lay = (br.get("ex", {}).get("availableToLay") or [{}])[0].get("price")
                    return cat["marketId"], r["selectionId"], best_back, best_lay
        return None

    def prices(self, fixture: Fixture) -> MarketPrices:
        event_id = self._find_event(fixture)
        if not event_id:
            return MarketPrices()
        cats = self._rpc("listMarketCatalogue", {
            "filter": {"eventIds": [event_id], "marketTypeCodes": MARKETS},
            "maxResults": 20, "marketProjection": ["RUNNER_DESCRIPTION", "MARKET_DESCRIPTION"]})
        if not cats:
            return MarketPrices()
        books = self._rpc("listMarketBook", {"marketIds": [c["marketId"] for c in cats],
                                             "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}})
        by_id = {b["marketId"]: b for b in books}
        mp = MarketPrices(source="betfair")
        self.lay_prices: dict[str, float] = {}
        for cat in cats:
            book = by_id.get(cat["marketId"])
            if not book:
                continue
            mtype = cat["description"]["marketType"]
            runners = {r["selectionId"]: r["runnerName"] for r in cat["runners"]}
            if mtype == "MATCH_ODDS":
                mp.total_matched = book.get("totalMatched")
            for r in book.get("runners", []):
                name = runners.get(r["selectionId"], "")
                back = (r.get("ex", {}).get("availableToBack") or [{}])[0].get("price")
                lay = (r.get("ex", {}).get("availableToLay") or [{}])[0].get("price")
                if lay:
                    self.lay_prices[f"{mtype}:{name}"] = lay
                if not back:
                    continue
                if mtype == "MATCH_ODDS":
                    if names_match(name, fixture.home):
                        mp.home = back
                    elif names_match(name, fixture.away):
                        mp.away = back
                    elif name == "The Draw":
                        mp.draw = lay or back  # strategies lay the draw: use lay price
                elif mtype.startswith("OVER_UNDER_"):
                    line = mtype.split("_")[-1]
                    attr = ("over_" if name.startswith("Over") else "under_") + line
                    setattr(mp, attr, back)
                elif mtype == "BOTH_TEAMS_TO_SCORE":
                    if name == "Yes":
                        mp.btts_yes = back
                    else:
                        mp.btts_no = back
                elif mtype == "CORRECT_SCORE":
                    mp.correct_scores[name.replace(" ", "")] = back
        return mp
