"""Tennis on the exchange: fixtures from Betfair's own event list (event type 2) and market lookup
by name, because Betfair's tennis market type codes vary by market and are not documented in one
place. Only ATP singles are kept, since the ratings cover ATP only.

Player names on Betfair are usually "N Surname" or "Surname" while the ratings use full names, so
matching is on surname plus first initial against the rating pool.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from ..models import Fixture, MarketPrices
from ..value import Quote
from .betfair import BetfairPrices

TENNIS_EVENT_TYPE = "2"
GRAND_SLAMS = ("australian open", "roland garros", "french open", "wimbledon", "us open")
EXCLUDE = ("wta", "itf", "challenger", "doubles", "women", "juniors", "wheelchair", "utr", "exhibition", "boys", "girls")
MARKET_NAME_TYPES = {"match odds": "MATCH_ODDS", "set betting": "SET_BETTING", "set 1 winner": "SET_1_WINNER", "first set winner": "SET_1_WINNER",
                     "total games": "TOTAL_GAMES"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def surname_initial(name: str) -> tuple[str, str]:
    """('djokovic', 'n') from 'N Djokovic', 'Novak Djokovic', 'Djokovic, N' or 'Djokovic'."""
    name = name.replace(".", " ").replace(",", " ").strip()
    parts = [p for p in name.split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return _norm(parts[0]), ""
    # Betfair: initial first ("N Djokovic"); ratings: first name first ("Novak Djokovic")
    if len(parts[0]) <= 2:
        return _norm(" ".join(parts[1:])), _norm(parts[0])[:1]
    return _norm(" ".join(parts[1:])), _norm(parts[0])[:1]


class PlayerMatcher:
    def __init__(self, known_players: Iterable[str]):
        self.index: dict[tuple[str, str], str] = {}
        self.by_surname: dict[str, list[str]] = {}
        for p in known_players:
            s, i = surname_initial(p)
            self.index[(s, i)] = p
            self.by_surname.setdefault(s, []).append(p)

    def resolve(self, betfair_name: str) -> str:
        s, i = surname_initial(betfair_name)
        if (s, i) in self.index:
            return self.index[(s, i)]
        cands = self.by_surname.get(s, [])
        if len(cands) == 1:
            return cands[0]
        return betfair_name  # unknown: the forecaster will use a default rating and low confidence

    def same(self, a: str, b: str) -> bool:
        return surname_initial(a)[0] == surname_initial(b)[0]


def classify_competition(name: str) -> Optional[tuple[str, int]]:
    """(league code, best_of) for an ATP singles competition name, or None to skip."""
    low = name.lower()
    if any(x in low for x in EXCLUDE):
        return None
    if any(gs in low for gs in GRAND_SLAMS):
        return ("atp.gs", 5) if "men" in low or "atp" in low or not any(w in low for w in ("women", "wta")) else None
    if "atp" not in low:
        return None
    if "masters" in low or "1000" in low:
        return "atp.1000", 3
    if "500" in low:
        return "atp.500", 3
    if "finals" in low:
        return "atp.finals", 3
    if "250" in low:
        return "atp.250", 3
    return "atp.tour", 3


class BetfairTennis:
    """FixtureProvider + PriceProvider for ATP singles via the exchange."""

    def __init__(self, bf: BetfairPrices, tennis_provider):
        self.bf = bf
        self.provider = tennis_provider
        self.matcher = PlayerMatcher(tennis_provider.players())
        self._events: dict[str, list[dict]] = {}
        self._fixture_event: dict[str, str] = {}

    def _events_on(self, on: date) -> list[dict]:
        key = on.isoformat()
        if key not in self._events:
            start = (on - timedelta(days=1)).isoformat() + "T00:00:00Z"
            end = (on + timedelta(days=2)).isoformat() + "T00:00:00Z"
            self._events[key] = self.bf._rpc("listEvents", {"filter": {"eventTypeIds": [TENNIS_EVENT_TYPE], "marketStartTime": {"from": start, "to": end}}})
        return self._events[key]

    def _competitions(self, event_ids: list[str]) -> dict[str, str]:
        """event id -> competition name (one catalogue call, match odds only)."""
        out: dict[str, str] = {}
        for i in range(0, len(event_ids), 100):
            cats = self.bf._rpc("listMarketCatalogue", {"filter": {"eventIds": event_ids[i:i + 100], "marketTypeCodes": ["MATCH_ODDS"]},
                                                       "maxResults": 200, "marketProjection": ["COMPETITION", "EVENT", "MARKET_START_TIME"]})
            for c in cats:
                out[c["event"]["id"]] = (c.get("competition") or {}).get("name", ""), c.get("marketStartTime")
        return out

    def fixtures(self, on: date, leagues: Iterable[str] | None = None) -> list[Fixture]:
        events = self._events_on(on)
        ids = [e["event"]["id"] for e in events if " v " in e["event"]["name"] and "/" not in e["event"]["name"]]
        comps = self._competitions(ids)
        out: list[Fixture] = []
        wanted = set(leagues) if leagues else None
        for e in events:
            ev = e["event"]
            if ev["id"] not in comps:
                continue
            comp, start = comps[ev["id"]]
            cls = classify_competition(comp or "")
            if cls is None:
                continue
            league, best_of = cls
            if wanted and league not in wanted:
                continue
            try:
                ko = datetime.fromisoformat((start or ev.get("openDate", "")).replace("Z", "+00:00")).astimezone()
            except ValueError:
                continue
            if ko.date() != on:
                continue
            a, b = ev["name"].split(" v ", 1)
            home, away = self.matcher.resolve(a.strip()), self.matcher.resolve(b.strip())
            surface = self.provider.surface_for(comp or "", "Hard")
            fx = Fixture(on, league, home, away, ko.replace(tzinfo=None), f"bf:{ev['id']}",
                         {"surface": surface, "best_of": best_of, "tourney": comp, "sport": "tennis", "betfair_names": (a.strip(), b.strip())})
            self._fixture_event[fx.fixture_id] = ev["id"]
            out.append(fx)
        out.sort(key=lambda f: (f.kickoff, f.league))
        return out

    # ----- prices ------------------------------------------------------------------------
    def _event_id(self, fixture: Fixture) -> Optional[str]:
        if fixture.fixture_id in self._fixture_event:
            return self._fixture_event[fixture.fixture_id]
        for e in self._events_on(fixture.date):
            name = e["event"]["name"]
            if " v " not in name:
                continue
            a, b = name.split(" v ", 1)
            if self.matcher.same(a, fixture.home) and self.matcher.same(b, fixture.away):
                return e["event"]["id"]
        return None

    def _markets(self, fixture: Fixture) -> list[dict]:
        event_id = self._event_id(fixture)
        if not event_id:
            return []
        key = f"tcat:{event_id}"
        if key not in self.bf._event_cache:
            cats = self.bf._rpc("listMarketCatalogue", {"filter": {"eventIds": [event_id]}, "maxResults": 60,
                                                       "marketProjection": ["RUNNER_DESCRIPTION", "MARKET_DESCRIPTION"]})
            for c in cats:
                low = c.get("marketName", "").lower()
                c["_type"] = c["description"].get("marketType") or next((t for n, t in MARKET_NAME_TYPES.items() if n in low), None)
                if "total games" in low or ("over/under" in low and "games" in low):
                    c["_type"] = "TOTAL_GAMES"
            self.bf._event_cache[key] = cats
        return self.bf._event_cache[key]

    def prices(self, fixture: Fixture) -> MarketPrices:
        cats = [c for c in self._markets(fixture) if c.get("_type") in ("MATCH_ODDS", "SET_BETTING", "TOTAL_GAMES", "SET_1_WINNER")]
        if not cats:
            return MarketPrices()
        books = self.bf._rpc("listMarketBook", {"marketIds": [c["marketId"] for c in cats], "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}})
        by_id = {b["marketId"]: b for b in books}
        mp = MarketPrices(source="betfair", as_of=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        for cat in cats:
            book = by_id.get(cat["marketId"])
            if not book:
                continue
            if book.get("status") not in (None, "OPEN") or book.get("inplay"):
                continue  # suspended, closed or in play: not a pre-match price
            mtype = cat["_type"]
            runners = {r["selectionId"]: r for r in cat["runners"]}
            if mtype == "MATCH_ODDS":
                mp.total_matched = book.get("totalMatched")
            for r in book.get("runners", []):
                meta = runners.get(r["selectionId"], {})
                name = meta.get("runnerName", "")
                ex = r.get("ex", {})
                back_l = [(x["price"], x["size"]) for x in (ex.get("availableToBack") or []) if x.get("price")]
                lay_l = [(x["price"], x["size"]) for x in (ex.get("availableToLay") or []) if x.get("price")]
                q = Quote(back_l, lay_l, book.get("totalMatched"), mp.as_of)
                if mtype == "MATCH_ODDS":
                    skey = "home" if self.matcher.same(name, fixture.home) else "away"
                    mp.quotes[f"MATCH_ODDS:{skey}"] = q
                    if back_l:
                        setattr(mp, skey, back_l[0][0])
                elif mtype == "SET_BETTING":
                    # runner names like "Djokovic 2-0" or "2 - 0": keep the score part
                    m = re.search(r"(\d)\s*-\s*(\d)", name)
                    if not m:
                        continue
                    score = f"{m.group(1)}-{m.group(2)}"
                    # orient as home-away: Betfair names the player; if it is the away player, flip
                    if not self.matcher.same(name, fixture.home) and self.matcher.same(name, fixture.away):
                        score = f"{m.group(2)}-{m.group(1)}"
                    mp.quotes[f"SET_BETTING:{score}"] = q
                    if back_l:
                        mp.correct_scores[score] = back_l[0][0]
                elif mtype == "TOTAL_GAMES":
                    mp.quotes[f"TOTAL_GAMES:{name}"] = q
                elif mtype == "SET_1_WINNER":
                    skey = "home" if self.matcher.same(name, fixture.home) else "away"
                    mp.quotes[f"SET_1_WINNER:{skey}"] = q
        return mp

    # ----- slip resolution (same contract as BetfairPrices.resolve) ------------------------
    def resolve(self, fixture: Fixture, market: str, selection: str):
        for cat in self._markets(fixture):
            if cat.get("_type") != market:
                continue
            for r in cat["runners"]:
                name = r["runnerName"]
                if market == "MATCH_ODDS":
                    ok = self.matcher.same(name, fixture.home if selection == "home" else fixture.away)
                elif market == "SET_BETTING":
                    m = re.search(r"(\d)\s*-\s*(\d)", name)
                    ok = bool(m) and (f"{m.group(1)}-{m.group(2)}" == selection and not self.matcher.same(name, fixture.away)
                                      or f"{m.group(2)}-{m.group(1)}" == selection and self.matcher.same(name, fixture.away))
                else:
                    ok = name.lower() == selection.lower()
                if ok:
                    book = self.bf._rpc("listMarketBook", {"marketIds": [cat["marketId"]], "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}})
                    bb = bl = None
                    for br in (book[0].get("runners", []) if book else []):
                        if br["selectionId"] == r["selectionId"]:
                            bb = (br.get("ex", {}).get("availableToBack") or [{}])[0].get("price")
                            bl = (br.get("ex", {}).get("availableToLay") or [{}])[0].get("price")
                    return cat["marketId"], r["selectionId"], bb, bl
        return None

    # orders go through the underlying exchange client
    def place_orders(self, market_id, instructions, customer_ref):
        return self.bf.place_orders(market_id, instructions, customer_ref)

    def current_orders(self, market_ids=None):
        return self.bf.current_orders(market_ids)

    def cancel_orders(self, market_id, bet_ids=None):
        return self.bf.cancel_orders(market_id, bet_ids)
