"""Tennis on the exchange: fixtures from Betfair's own event list (event type 2) and market lookup
by name, because Betfair's tennis market type codes vary by market and are not documented in one
place. Only ATP singles are kept, since the ratings cover ATP only.

Player names on Betfair come as "N Djokovic", "Djokovic N", "Djokovic, N", "J M Cerundolo" or just
"Djokovic", while the ratings use full names, so matching is on the surname phrase (one to three
trailing tokens of the full name, accents folded) with initials to split namesakes.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Iterable, Optional

from ..models import Fixture, MarketPrices
from ..value import Quote
from .betfair import BetfairPrices, FeedReport, _now
from .matching import fold

TENNIS_EVENT_TYPE = "2"
GRAND_SLAMS = ("australian open", "roland garros", "french open", "wimbledon", "us open")
EXCLUDE = ("wta", "itf", "challenger", "doubles", "women", "juniors", "wheelchair", "utr", "exhibition", "boys", "girls", "legends")
MARKET_NAME_TYPES = {"match odds": "MATCH_ODDS", "set betting": "SET_BETTING", "set 1 winner": "SET_1_WINNER", "first set winner": "SET_1_WINNER",
                     "total games": "TOTAL_GAMES"}
TENNIS_MARKETS = ("MATCH_ODDS", "SET_BETTING", "TOTAL_GAMES", "SET_1_WINNER")


def parse_player(name: str) -> tuple[list[str], str]:
    """('djokovic',), 'n' style split of a feed name into surname tokens and initials."""
    toks = fold(name.replace(",", " ")).split()
    if not toks:
        return [], ""
    if len(toks) >= 2 and len(toks[-1]) <= 2 and all(len(t) > 2 for t in toks[:-1]):
        return toks[:-1], toks[-1]                      # "Djokovic N", "Bautista Agut R"
    lead = []
    i = 0
    while i < len(toks) - 1 and len(toks[i]) <= 2:      # "N Djokovic", "J M Cerundolo", "JM Cerundolo"
        lead.append(toks[i])
        i += 1
    if lead:
        return toks[i:], "".join(lead)
    return toks, ""                                     # "Djokovic", "Novak Djokovic"


def surname_initial(name: str) -> tuple[str, str]:
    """('djokovic', 'n') from any feed form (kept for callers of the old helper)."""
    sur, ini = parse_player(name)
    if not sur:
        return "", ""
    if not ini and len(sur) > 1:  # a full name: given names first
        return "".join(sur[1:]), sur[0][:1]
    return "".join(sur), ini[:1]


class PlayerMatcher:
    def __init__(self, known_players: Iterable[str]):
        self.players: list[str] = []
        self.index: dict[str, list[str]] = {}
        seen: set[str] = set()
        for p in known_players:
            key = fold(p)
            if key in seen:
                continue
            seen.add(key)
            self.players.append(p)
            toks = key.split()
            for n in (1, 2, 3):
                if len(toks) >= n:
                    self.index.setdefault(" ".join(toks[-n:]), []).append(p)

    @staticmethod
    def _initials(full: str, surname_tokens: int) -> str:
        toks = fold(full).split()
        given = toks[:max(0, len(toks) - surname_tokens)]
        return "".join(t[0] for t in given)

    def resolve(self, betfair_name: str) -> str:
        sur, ini = parse_player(betfair_name)
        for n in range(min(3, len(sur)), 0, -1):
            key = " ".join(sur[-n:])
            cands = self.index.get(key, [])
            if not cands:
                continue
            if len(cands) == 1 and (not ini or self._initials(cands[0], n).startswith(ini[:1])):
                return cands[0]
            if ini:
                by_ini = [c for c in cands if self._initials(c, n).startswith(ini)] or [c for c in cands if self._initials(c, n)[:1] == ini[:1]]
                if len(by_ini) == 1:
                    return by_ini[0]
            if len(cands) == 1:
                return cands[0]
        return betfair_name  # unknown: the forecaster will use a default rating and low confidence

    def same(self, a: str, b: str) -> bool:
        """Same player under either feed's spelling."""
        if fold(a) == fold(b):
            return True
        sa, ia = parse_player(a)
        sb, ib = parse_player(b)
        if not sa or not sb:
            return False
        if sa == sb:
            return not (ia and ib) or ia[:1] == ib[:1]
        # one side is a full name, the other a surname phrase: compare the tail
        short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
        return long_[-len(short):] == short


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


def player_part(runner_name: str) -> str:
    """'Alcaraz 2-0' / 'Alcaraz 2 - 0' / '2-0 Alcaraz' -> 'Alcaraz' (set-betting runners carry the score)."""
    return re.sub(r"\d\s*-\s*\d", " ", runner_name).strip(" -:")


def games_key(runner_name: str) -> str:
    """'Over 22.5 Games' / 'Over 22.5' -> 'Over 22.5' (the strategies' selection key)."""
    m = re.match(r"\s*(over|under)\s+(\d+(?:\.\d+)?)", runner_name, re.I)
    return f"{m.group(1).title()} {float(m.group(2)):g}" if m else runner_name


class BetfairTennis:
    """FixtureProvider + PriceProvider for ATP singles via the exchange."""

    def __init__(self, bf: BetfairPrices, tennis_provider):
        self.bf = bf
        self.provider = tennis_provider
        self.matcher = PlayerMatcher(tennis_provider.players())
        self._fixture_event: dict[str, str] = {}

    @property
    def health(self):
        return self.bf.health

    def _competitions(self, event_ids: list[str]) -> dict[str, tuple[str, Optional[str]]]:
        """event id -> (competition name, market start) in one catalogue call per 100 events (match odds only)."""
        out: dict[str, tuple[str, Optional[str]]] = {}
        for i in range(0, len(event_ids), 100):
            cats = self.bf._rpc("listMarketCatalogue", {"filter": {"eventIds": event_ids[i:i + 100], "marketTypeCodes": ["MATCH_ODDS"]},
                                                       "maxResults": 200, "marketProjection": ["COMPETITION", "EVENT", "MARKET_START_TIME"]}) or []
            for c in cats:
                out[c["event"]["id"]] = (c.get("competition") or {}).get("name", ""), c.get("marketStartTime")
        return out

    def fixtures(self, on: date, leagues: Iterable[str] | None = None) -> list[Fixture]:
        events = self.bf.events_on(on, TENNIS_EVENT_TYPE)
        ids = [e["event"]["id"] for e in events if " v " in e["event"]["name"] and "/" not in e["event"]["name"]]
        comps = self.bf._cached(f"tcomp:{on.isoformat()}", 600, lambda: self._competitions(ids)) if ids else {}
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
        if fixture.fixture_id and str(fixture.fixture_id).startswith("bf:"):
            return str(fixture.fixture_id)[3:]
        if fixture.fixture_id in self._fixture_event:
            return self._fixture_event[fixture.fixture_id]
        for e in self.bf.events_on(fixture.date, TENNIS_EVENT_TYPE):
            name = e["event"]["name"]
            if " v " not in name:
                continue
            a, b = name.split(" v ", 1)
            if self.matcher.same(a, fixture.home) and self.matcher.same(b, fixture.away):
                return e["event"]["id"]
        return None

    @staticmethod
    def _classify(cats: list[dict]) -> list[dict]:
        for c in cats:
            low = c.get("marketName", "").lower()
            t = (c.get("description") or {}).get("marketType")
            if "total games" in low or ("over/under" in low and "games" in low):
                t = "TOTAL_GAMES"
            elif t not in TENNIS_MARKETS:
                t = next((v for n, v in MARKET_NAME_TYPES.items() if n in low), t)
            c["_type"] = t
        return [c for c in cats if c.get("_type") in TENNIS_MARKETS]

    def _markets(self, fixture: Fixture) -> list[dict]:
        event_id = self._event_id(fixture)
        if not event_id:
            return []
        return self._classify(self.bf.catalogue([event_id]).get(event_id, []))

    def _build(self, fixture: Fixture, cats: list[dict], by_id: dict[str, dict], event_name: str) -> MarketPrices:
        mp = MarketPrices(source="betfair", as_of=_now(), event_id=self._event_id(fixture), event_name=event_name)
        if not cats:
            mp.status, mp.note = "no_markets", "The exchange lists this match but none of the markets the strategies use yet."
            return mp
        for cat in cats:
            book = by_id.get(cat["marketId"])
            mtype = cat["_type"]
            if not book:
                mp.market_status[mtype] = "NO_BOOK"
                continue
            status = book.get("status") or "OPEN"
            inplay = bool(book.get("inplay"))
            mp.market_status[mtype] = "INPLAY" if inplay else status
            if mtype == "MATCH_ODDS":
                mp.total_matched = book.get("totalMatched")
                mp.inplay = inplay
                if book.get("isMarketDataDelayed") is not None:
                    mp.delayed = bool(book.get("isMarketDataDelayed"))
            if status != "OPEN" or inplay:
                continue  # suspended, closed or in play: not a pre-match price
            runners = {r["selectionId"]: r for r in cat["runners"]}
            for r in book.get("runners", []):
                if r.get("status") not in (None, "ACTIVE"):
                    continue
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
                    m = re.search(r"(\d)\s*-\s*(\d)", name)
                    if not m:
                        continue
                    score = f"{m.group(1)}-{m.group(2)}"
                    who = player_part(name)
                    if who and not self.matcher.same(who, fixture.home) and self.matcher.same(who, fixture.away):
                        score = f"{m.group(2)}-{m.group(1)}"  # Betfair names the player: orient as home-away
                    mp.quotes[f"SET_BETTING:{score}"] = q
                    if back_l:
                        mp.correct_scores[score] = back_l[0][0]
                elif mtype == "TOTAL_GAMES":
                    mp.quotes[f"TOTAL_GAMES:{games_key(name)}"] = q
                elif mtype == "SET_1_WINNER":
                    skey = "home" if self.matcher.same(name, fixture.home) else "away"
                    mp.quotes[f"SET_1_WINNER:{skey}"] = q
        if mp.quotes:
            mp.status, mp.note = "ok", f"Priced from exchange event '{event_name}'."
        elif mp.inplay or any(v == "INPLAY" for v in mp.market_status.values()):
            mp.status, mp.note = "inplay", "The match has started (markets in play): pre-match plans no longer apply."
        elif any(v == "SUSPENDED" for v in mp.market_status.values()):
            mp.status, mp.note = "suspended", "Every market is suspended at the moment. Refresh in a minute."
        elif any(v == "CLOSED" for v in mp.market_status.values()):
            mp.status, mp.note = "closed", "The exchange markets for this match are closed."
        else:
            mp.status, mp.note = "no_markets", "The exchange has no prices on offer for this match yet."
        return mp

    def prices_for_day(self, fixtures: Iterable[Fixture]) -> tuple[dict[str, MarketPrices], FeedReport]:
        import time as _time
        fixtures = list(fixtures)
        rep = FeedReport(day=fixtures[0].date.isoformat() if fixtures else "", fixtures=len(fixtures))
        t0, calls0 = _time.time(), self.bf.health.calls
        out: dict[str, MarketPrices] = {}
        if not fixtures:
            return out, rep
        try:
            ids = {fx.label: self._event_id(fx) for fx in fixtures}
            rep.events_on_day = len(self.bf.events_on(fixtures[0].date, TENNIS_EVENT_TYPE))
            cats = {e: self._classify(c) for e, c in self.bf.catalogue([e for e in ids.values() if e]).items()}
            by_id = self.bf.books([c["marketId"] for cs in cats.values() for c in cs])
        except Exception as exc:
            rep.error, rep.error_code = str(exc), getattr(exc, "code", None)
            for fx in fixtures:
                out[fx.label] = MarketPrices(status="error", note=str(exc))
            rep.fetched_at, rep.elapsed_ms, rep.calls = _now(), int((_time.time() - t0) * 1000), self.bf.health.calls - calls0
            return out, rep
        for fx in fixtures:
            e = ids.get(fx.label)
            if not e:
                mp = MarketPrices(status="no_event", note=f"No exchange event found for '{fx.label}'.")
                rep.unmatched.append({"fixture": fx.label, "reason": mp.note, "candidates": []})
            else:
                rep.matched += 1
                names = fx.meta.get("betfair_names") if isinstance(fx.meta, dict) else None
                mp = self._build(fx, cats.get(e, []), by_id, f"{names[0]} v {names[1]}" if names else fx.label)
                if mp.status == "ok":
                    rep.priced += 1
                elif mp.status == "inplay":
                    rep.inplay.append(fx.label)
                elif mp.status == "suspended":
                    rep.suspended.append(fx.label)
                if mp.delayed is not None and rep.delayed is None:
                    rep.delayed = mp.delayed
            out[fx.label] = mp
        rep.fetched_at, rep.elapsed_ms, rep.calls = _now(), int((_time.time() - t0) * 1000), self.bf.health.calls - calls0
        return out, rep

    def prices(self, fixture: Fixture) -> MarketPrices:
        out, _ = self.prices_for_day([fixture])
        return out.get(fixture.label, MarketPrices())

    def diagnose(self, day: date, fixtures: Iterable[Fixture]) -> dict:
        fixtures = list(fixtures)
        per, report = self.prices_for_day(fixtures)
        rows = [{"fixture": fx.label, "league": fx.league, "kickoff": fx.kickoff.strftime("%H:%M") if fx.kickoff else None, **per.get(fx.label, MarketPrices()).diagnostics()}
                for fx in fixtures]
        return {"day": day.isoformat(), "health": self.bf.health.to_dict(), "report": report.to_dict(), "fixtures": rows, "error": None,
                "login_url": self.bf.login_url, "betting_url": self.bf.betting_url}

    # ----- slip resolution (same contract as BetfairPrices.resolve) ------------------------
    def resolve(self, fixture: Fixture, market: str, selection: str):
        full = self.resolve_full(fixture, market, selection)
        if not full:
            return None
        return full["market_id"], full["selection_id"], full["best_back"], full["best_lay"]

    def resolve_full(self, fixture: Fixture, market: str, selection: str) -> Optional[dict]:
        for cat in self._markets(fixture):
            if cat.get("_type") != market:
                continue
            for r in cat["runners"]:
                name = r["runnerName"]
                if market == "MATCH_ODDS" or market == "SET_1_WINNER":
                    ok = self.matcher.same(name, fixture.home if selection == "home" else fixture.away)
                elif market == "SET_BETTING":
                    m = re.search(r"(\d)\s*-\s*(\d)", name)
                    who = player_part(name)
                    is_away = bool(who) and not self.matcher.same(who, fixture.home) and self.matcher.same(who, fixture.away)
                    ok = bool(m) and (f"{m.group(1)}-{m.group(2)}" == selection and not is_away
                                      or f"{m.group(2)}-{m.group(1)}" == selection and is_away)
                elif market == "TOTAL_GAMES":
                    ok = games_key(name).lower() == games_key(selection).lower()
                else:
                    ok = name.lower() == selection.lower()
                if ok:
                    book = self.bf.books([cat["marketId"]]).get(cat["marketId"], {})
                    bb = bl = None
                    for br in book.get("runners", []):
                        if br["selectionId"] == r["selectionId"]:
                            bb = (br.get("ex", {}).get("availableToBack") or [{}])[0].get("price")
                            bl = (br.get("ex", {}).get("availableToLay") or [{}])[0].get("price")
                    return {"market_id": cat["marketId"], "selection_id": r["selectionId"], "best_back": bb, "best_lay": bl,
                            "status": "INPLAY" if book.get("inplay") else (book.get("status") or "OPEN"), "runner_name": name,
                            "event_name": fixture.label, "delayed": book.get("isMarketDataDelayed")}
        return None

    # orders go through the underlying exchange client
    def place_orders(self, market_id, instructions, customer_ref):
        return self.bf.place_orders(market_id, instructions, customer_ref)

    def current_orders(self, market_ids=None):
        return self.bf.current_orders(market_ids)

    def cancel_orders(self, market_id, bet_ids=None):
        return self.bf.cancel_orders(market_id, bet_ids)
