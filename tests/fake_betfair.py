"""A local stand-in for the Betfair Exchange API, for end-to-end tests and demos.

It speaks the same two surfaces the app uses:

  POST /api/login, /api/keepAlive            (identitysso: form login, session tokens)
  POST /exchange/betting/json-rpc/v1         (SportsAPING: listEvents, listMarketCatalogue,
                                              listMarketBook, placeOrders, listCurrentOrders,
                                              cancelOrders)

Football events are generated from the bundled openfootball fixtures for the day asked for, named
the way Betfair names them ("Man Utd v Nottm Forest", "Paris St-G v Lille", "Sheff Wed v QPR"),
priced from the app's own forecast with a small overround, optionally with a deliberate
mispricing on some events (``edge``) so that the value engine has something to call a TRADE.
Tennis events are synthetic ATP matches between rated players ("C Alcaraz v J Sinner").

Failure modes can be switched on at runtime through POST /__control:
  {"expire_sessions": true}      every issued token becomes invalid (INVALID_SESSION_INFORMATION)
  {"fail_login": true|false}     logins return INVALID_USERNAME_OR_PASSWORD
  {"inplay": ["Man Utd v ..."]}  these events' markets report inplay=true
  {"suspend": ["..."]}           these events' markets report status SUSPENDED
  {"delay_ms": 300}              latency per call
GET /__state reports call counts, logins and the orders received.

Run standalone:  python -m tests.fake_betfair --port 8900 --edge 0.5
Then start the app with BETFAIR_LOGIN_URL=http://127.0.0.1:8900/api/login
BETFAIR_BETTING_URL=http://127.0.0.1:8900/exchange/betting/json-rpc/v1 and any app key,
username and password (password "wrong" is rejected).
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs

# openfootball spelling -> the short name Betfair uses on the exchange
BETFAIR_SPELLING = {
    "Arsenal FC": "Arsenal", "Aston Villa FC": "Aston Villa", "AFC Bournemouth": "Bournemouth", "Brentford FC": "Brentford",
    "Brighton & Hove Albion FC": "Brighton", "Chelsea FC": "Chelsea", "Coventry City FC": "Coventry", "Crystal Palace FC": "Crystal Palace",
    "Everton FC": "Everton", "Fulham FC": "Fulham", "Hull City AFC": "Hull", "Ipswich Town FC": "Ipswich", "Leeds United FC": "Leeds",
    "Liverpool FC": "Liverpool", "Manchester City FC": "Man City", "Manchester United FC": "Man Utd", "Newcastle United FC": "Newcastle",
    "Nottingham Forest FC": "Nottm Forest", "Sunderland AFC": "Sunderland", "Tottenham Hotspur FC": "Tottenham",
    "Birmingham City FC": "Birmingham", "Blackburn Rovers FC": "Blackburn", "Bolton Wanderers FC": "Bolton", "Bristol City FC": "Bristol City",
    "Burnley FC": "Burnley", "Cardiff City FC": "Cardiff", "Charlton Athletic FC": "Charlton", "Derby County FC": "Derby",
    "Lincoln City FC": "Lincoln", "Middlesbrough FC": "Middlesbrough", "Millwall FC": "Millwall", "Norwich City FC": "Norwich",
    "Portsmouth FC": "Portsmouth", "Preston North End FC": "Preston", "Queens Park Rangers FC": "QPR", "Sheffield United FC": "Sheff Utd",
    "Sheffield Wednesday FC": "Sheff Wed", "Southampton FC": "Southampton", "Stoke City FC": "Stoke", "Swansea City AFC": "Swansea",
    "Watford FC": "Watford", "West Bromwich Albion FC": "West Brom", "West Ham United FC": "West Ham", "Wolverhampton Wanderers FC": "Wolves",
    "Wrexham AFC": "Wrexham", "Luton Town FC": "Luton", "Leicester City FC": "Leicester", "Oxford United FC": "Oxford Utd", "Plymouth Argyle FC": "Plymouth",
    "Hull City FC": "Hull", "Sheffield Wednesday": "Sheff Wed",
    "1. FC Köln": "FC Koln", "1. FC Union Berlin": "Union Berlin", "1. FSV Mainz 05": "Mainz", "Bayer 04 Leverkusen": "Bayer Leverkusen",
    "Borussia Dortmund": "Dortmund", "Borussia Mönchengladbach": "Mgladbach", "Eintracht Frankfurt": "Eintracht Frankfurt", "FC Augsburg": "Augsburg",
    "FC Bayern München": "Bayern Munich", "FC Schalke 04": "Schalke 04", "Hamburger SV": "Hamburg", "RB Leipzig": "RB Leipzig", "SC Freiburg": "Freiburg",
    "SC Paderborn 07": "Paderborn", "SV 07 Elversberg": "Elversberg", "SV Werder Bremen": "Werder Bremen", "TSG 1899 Hoffenheim": "Hoffenheim",
    "VfB Stuttgart": "Stuttgart", "VfL Wolfsburg": "Wolfsburg", "VfL Bochum 1848": "Bochum", "FC St. Pauli": "St Pauli", "1. FC Heidenheim 1846": "Heidenheim",
    "Holstein Kiel": "Holstein Kiel",
    "Athletic Club": "Athletic Bilbao", "CA Osasuna": "Osasuna", "Club Atlético de Madrid": "Atletico Madrid", "Atlético de Madrid": "Atletico Madrid",
    "Deportivo Alavés": "Alaves", "Elche CF": "Elche", "FC Barcelona": "Barcelona", "Getafe CF": "Getafe", "Levante UD": "Levante", "Málaga CF": "Malaga",
    "RC Celta de Vigo": "Celta Vigo", "RC Deportivo La Coruña": "Dep La Coruna", "RCD Espanyol de Barcelona": "Espanyol",
    "Rayo Vallecano de Madrid": "Rayo Vallecano", "Real Betis Balompié": "Real Betis", "Real Madrid CF": "Real Madrid",
    "Real Racing Club de Santander": "Racing Santander", "Real Sociedad de Fútbol": "Real Sociedad", "Sevilla FC": "Sevilla", "Valencia CF": "Valencia",
    "Villarreal CF": "Villarreal", "Girona FC": "Girona", "RCD Mallorca": "Mallorca", "UD Las Palmas": "Las Palmas", "CD Leganés": "Leganes",
    "Real Valladolid CF": "Valladolid", "Real Oviedo": "Real Oviedo",
    "AJ Auxerre": "Auxerre", "AS Monaco FC": "Monaco", "Angers SCO": "Angers", "ES Troyes AC": "Troyes", "FC Lorient": "Lorient", "Le Havre AC": "Le Havre",
    "Le Mans FC": "Le Mans", "Lille OSC": "Lille", "OGC Nice": "Nice", "Olympique Lyonnais": "Lyon", "Olympique de Marseille": "Marseille",
    "Paris FC": "Paris FC", "Paris Saint-Germain FC": "Paris St-G", "RC Strasbourg Alsace": "Strasbourg", "Racing Club de Lens": "Lens", "RC Lens": "Lens",
    "Stade Brestois 29": "Brest", "Stade Rennais FC 1901": "Rennes", "Toulouse FC": "Toulouse", "FC Nantes": "Nantes", "Stade de Reims": "Reims",
    "AS Saint-Étienne": "St Etienne", "Montpellier HSC": "Montpellier", "FC Metz": "Metz",
    "AC Milan": "AC Milan", "AC Monza": "Monza", "ACF Fiorentina": "Fiorentina", "AS Roma": "Roma", "Atalanta BC": "Atalanta", "Bologna FC 1909": "Bologna",
    "Cagliari Calcio": "Cagliari", "Como 1907": "Como", "FC Internazionale Milano": "Inter", "Frosinone Calcio": "Frosinone", "Genoa CFC": "Genoa",
    "Juventus FC": "Juventus", "Parma Calcio 1913": "Parma", "SS Lazio": "Lazio", "SSC Napoli": "Napoli", "Torino FC": "Torino", "US Lecce": "Lecce",
    "US Sassuolo Calcio": "Sassuolo", "Udinese Calcio": "Udinese", "Venezia FC": "Venezia", "Hellas Verona FC": "Verona", "Empoli FC": "Empoli",
    "AC Pisa 1909": "Pisa", "US Cremonese": "Cremonese",
}
CS_RUNNERS = ["0 - 0", "1 - 0", "0 - 1", "1 - 1", "2 - 0", "0 - 2", "2 - 1", "1 - 2", "2 - 2", "3 - 0", "0 - 3", "3 - 1", "1 - 3", "3 - 2", "2 - 3", "3 - 3",
              "Any Other Home Win", "Any Other Away Win", "Any Other Draw"]
TENNIS_PLAYERS = ["Carlos Alcaraz", "Jannik Sinner", "Novak Djokovic", "Alexander Zverev", "Taylor Fritz", "Daniil Medvedev", "Holger Rune",
                  "Jack Draper", "Ben Shelton", "Lorenzo Musetti", "Felix Auger-Aliassime", "Francisco Cerundolo", "Stefanos Tsitsipas", "Sebastian Baez"]


def betfair_name(team: str) -> str:
    if team in BETFAIR_SPELLING:
        return BETFAIR_SPELLING[team]
    for suffix in (" FC", " AFC", " CF", " SC", " BC", " UD"):
        if team.endswith(suffix):
            return team[: -len(suffix)]
    return team


def betfair_player(full: str) -> str:
    parts = full.split()
    return f"{parts[0][0]} {' '.join(parts[1:])}" if len(parts) > 1 else full


def _ladder(price: float, side: str, rnd: random.Random, levels: int = 3) -> list[dict]:
    from tradescout.betting import round_to_tick
    out = []
    p = price
    for k in range(levels):
        p = round_to_tick(p * (1 - 0.006 * k) if side == "back" else p * (1 + 0.006 * k), side)
        out.append({"price": p, "size": round(rnd.uniform(20, 400), 2)})
    return out


class FakeExchange:
    def __init__(self, app_key: str = "testkey", password: str = "secret", edge: float = 0.0, seed: int = 7, session_ttl: Optional[float] = None):
        self.app_key = app_key
        self.password = password
        self.edge = edge
        self.seed = seed
        self.session_ttl = session_ttl
        self.tokens: dict[str, float] = {}
        self.expired: set[str] = set()
        self.orders: list[dict] = []
        self.calls: Counter = Counter()
        self.logins = 0
        self.fail_login = False
        self.inplay: set[str] = set()
        self.suspend: set[str] = set()
        self.delay_ms = 0
        self.reject_market_types: set[str] = set()   # placeOrders on these market types fails (e.g. MARKET_SUSPENDED)
        self.timeout_market_types: set[str] = set()  # placeOrders on these answers TIMEOUT but still places the order
        self.recent_refs: dict[str, float] = {}      # customerRef -> time, for DUPLICATE_TRANSACTION within 60 s
        self.catalogue_requests: list[dict] = []
        self._days: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._next_id = 10_000

    # ------------------------------------------------------------------ data generation
    def _day(self, day: date) -> dict:
        key = day.isoformat()
        with self._lock:
            if key in self._days:
                return self._days[key]
            built = self._build_day(day)
            self._days[key] = built
            return built

    def _tennis_forecaster(self, day: date):
        """Fitted once per process (ratings as of the first day asked for): a fake does not need daily refits."""
        if not hasattr(self, "_tf"):
            try:
                from tradescout.tennis.data import TennisProvider
                from tradescout.tennis.forecast import TennisForecaster
                tp = TennisProvider()
                self._tf = TennisForecaster.fit(tp.results(before=day), day)
            except Exception:
                self._tf = None
        return self._tf

    def _build_day(self, day: date) -> dict:
        from tradescout.data.openfootball import OpenFootballProvider
        from tradescout.model import Forecaster
        from tradescout.models import Fixture
        rnd = random.Random(f"{self.seed}:{day}")
        events: list[dict] = []
        markets: dict[str, dict] = {}
        prov = OpenFootballProvider()
        fixtures = prov.fixtures(day)
        fcaster = Forecaster.fit(prov.results(before=day), day) if fixtures else None
        h = 0.015  # half-spread around fair

        def fair(p, mult=1.0):
            p = max(0.005, min(0.995, p))
            return (mult / (p * (1 + h)), mult / (p * (1 - h)))

        for k, fx in enumerate(sorted(fixtures, key=lambda f: (f.kickoff or datetime.min, f.label))):
            fc = fcaster.forecast(fx)
            ev_id = str(33_000_000 + k + day.toordinal() % 1000 * 100)
            ko = (fx.kickoff or datetime.combine(day, datetime.min.time().replace(hour=15))).replace(tzinfo=timezone.utc)
            name = f"{betfair_name(fx.home)} v {betfair_name(fx.away)}"
            mispriced = self.edge > 0 and k % 3 == 0
            events.append({"event": {"id": ev_id, "name": name, "countryCode": "GB", "timezone": "GMT", "openDate": ko.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                           "marketCount": 6, "_sport": "football", "_fixture": fx.label, "_league": fx.league})

            def add_market(mtype, mname, runners, matched):
                mid = f"1.{self._next_id}"
                self._next_id += 1
                rs, book = [], []
                for n, (rname, fair_p, mult) in enumerate(runners):
                    sel = 1_000_000 + self._next_id * 10 + n
                    rs.append({"selectionId": sel, "runnerName": rname, "handicap": 0.0, "sortPriority": n + 1})
                    b, l = fair(fair_p, mult)
                    book.append({"selectionId": sel, "handicap": 0.0, "status": "ACTIVE", "lastPriceTraded": round(b, 2), "totalMatched": round(matched / len(runners), 2),
                                 "ex": {"availableToBack": _ladder(b, "back", rnd), "availableToLay": _ladder(l, "lay", rnd), "tradedVolume": []}})
                markets[mid] = {"marketId": mid, "marketName": mname, "marketStartTime": ko.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "totalMatched": matched,
                                "description": {"marketType": mtype, "bettingType": "ODDS", "persistenceEnabled": True, "bspMarket": False, "turnInPlayEnabled": True,
                                                "marketTime": ko.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "marketBaseRate": 5.0},
                                "runners": rs, "event": {k2: v for k2, v in events[-1]["event"].items()}, "competition": {"id": "1", "name": fx.league},
                                "_book": book, "_event": ev_id}

            draw_mult = 1 / (1 + self.edge / 2) if mispriced else 1.0  # market overrates the draw: lay price below fair
            add_market("MATCH_ODDS", "Match Odds", [(betfair_name(fx.home), fc.p_home, 1.0), (betfair_name(fx.away), fc.p_away, 1.0), ("The Draw", fc.p_draw, draw_mult)],
                       round(rnd.uniform(40_000, 900_000), 2))
            over_mult = (1 + self.edge) if mispriced else 1.0
            for line in (1.5, 2.5, 3.5):
                po = fc.p_over[line]
                add_market(f"OVER_UNDER_{str(line).replace('.', '')}", f"Over/Under {line} Goals",
                           [(f"Under {line} Goals", 1 - po, 1.0), (f"Over {line} Goals", po, over_mult if line == 2.5 else 1.0)], round(rnd.uniform(5_000, 120_000), 2))
            add_market("BOTH_TEAMS_TO_SCORE", "Both teams to Score?", [("Yes", fc.p_btts, 1.0), ("No", 1 - fc.p_btts, 1.0)], round(rnd.uniform(3_000, 60_000), 2))
            cs = []
            other = {"h": 0.0, "a": 0.0, "d": 0.0}
            for s, p in fc.p_cs.items():
                hg, ag = (int(x) for x in s.split("-"))
                if hg <= 3 and ag <= 3:
                    continue
                other["h" if hg > ag else "a" if ag > hg else "d"] += p
            for rname in CS_RUNNERS:
                if rname.startswith("Any Other"):
                    p = other[{"Home": "h", "Away": "a", "Draw": "d"}[rname.split()[2]]]
                else:
                    p = fc.p_cs.get(rname.replace(" ", ""), 0.001)
                cs.append((rname, p, over_mult if rname == "1 - 1" else 1.0))
            add_market("CORRECT_SCORE", "Correct Score", cs, round(rnd.uniform(4_000, 80_000), 2))
        # an outright-style event with no " v " in the name, as the real list contains
        events.append({"event": {"id": str(29_000_000 + day.toordinal() % 1000), "name": "English Premier League 2026/27", "countryCode": "GB", "timezone": "GMT",
                                 "openDate": f"{day.isoformat()}T17:00:00.000Z"}, "marketCount": 1, "_sport": "football"})
        # tennis: synthetic ATP singles between rated players
        tf = self._tennis_forecaster(day)
        players = TENNIS_PLAYERS[:]
        rnd.shuffle(players)
        for k in range(0, len(players) - 1, 2):
            a, b = players[k], players[k + 1]
            ev_id = str(34_000_000 + k + day.toordinal() % 1000 * 100)
            ko = datetime(day.year, day.month, day.day, 9 + k, 30, tzinfo=timezone.utc)
            name = f"{betfair_player(a)} v {betfair_player(b)}"
            events.append({"event": {"id": ev_id, "name": name, "countryCode": "CN", "timezone": "GMT", "openDate": ko.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                           "marketCount": 4, "_sport": "tennis"})
            if tf is not None:
                fc = tf.forecast(Fixture(day, "atp.1000", a, b, ko.replace(tzinfo=None), f"bf:{ev_id}", {"surface": "Hard", "best_of": 3, "tourney": "ATP Shanghai Masters 2026", "sport": "tennis"}))
                p_a, p_sets, p_over, p_set1 = fc.p_a, fc.p_sets, fc.p_over, fc.p_set1_a
            else:
                p_a, p_sets, p_over, p_set1 = 0.6, {"2-0": 0.4, "2-1": 0.2, "0-2": 0.25, "1-2": 0.15}, {22.5: 0.5}, 0.58
            comp = {"id": "12", "name": "ATP Shanghai Masters 2026"}

            def add_tmarket(mtype, mname, runners, matched):
                mid = f"1.{self._next_id}"
                self._next_id += 1
                rs, book = [], []
                for n, (rname, fair_p) in enumerate(runners):
                    sel = 2_000_000 + self._next_id * 10 + n
                    rs.append({"selectionId": sel, "runnerName": rname, "handicap": 0.0, "sortPriority": n + 1})
                    bb, ll = fair(fair_p)
                    book.append({"selectionId": sel, "handicap": 0.0, "status": "ACTIVE", "lastPriceTraded": round(bb, 2), "totalMatched": round(matched / len(runners), 2),
                                 "ex": {"availableToBack": _ladder(bb, "back", rnd), "availableToLay": _ladder(ll, "lay", rnd), "tradedVolume": []}})
                markets[mid] = {"marketId": mid, "marketName": mname, "marketStartTime": ko.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "totalMatched": matched,
                                "description": {"marketType": mtype, "bettingType": "ODDS", "persistenceEnabled": True, "bspMarket": False, "turnInPlayEnabled": True,
                                                "marketTime": ko.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "marketBaseRate": 5.0},
                                "runners": rs, "event": dict(events[-1]["event"]), "competition": comp, "_book": book, "_event": ev_id}

            pa, pb = betfair_player(a), betfair_player(b)
            sa, sb = a.split()[-1], b.split()[-1]
            add_tmarket("MATCH_ODDS", "Match Odds", [(pa, p_a), (pb, 1 - p_a)], round(rnd.uniform(50_000, 600_000), 2))
            add_tmarket("SET_BETTING", "Set Betting", [(f"{sa} 2-0", p_sets.get("2-0", 0.3)), (f"{sa} 2-1", p_sets.get("2-1", 0.2)),
                                                       (f"{sb} 2-0", p_sets.get("0-2", 0.3)), (f"{sb} 2-1", p_sets.get("1-2", 0.2))], round(rnd.uniform(2_000, 40_000), 2))
            line = min(p_over, key=lambda L: abs(p_over[L] - 0.5))
            add_tmarket("TOTAL_GAMES", f"Over/Under {line:g} Games", [(f"Under {line:g} Games", 1 - p_over[line]), (f"Over {line:g} Games", p_over[line])],
                        round(rnd.uniform(1_000, 20_000), 2))
            add_tmarket("SET_1_WINNER", "Set 1 Winner", [(pa, p_set1), (pb, 1 - p_set1)], round(rnd.uniform(1_000, 30_000), 2))
        return {"events": events, "markets": markets}

    # ------------------------------------------------------------------ SSO
    def login(self, username: str, password: str, app_key: str) -> dict:
        self.calls["login"] += 1
        if app_key != self.app_key:
            return {"token": "", "product": app_key, "status": "FAIL", "error": "INVALID_APP_KEY"}
        if self.fail_login or password != self.password or not username:
            return {"token": "", "product": app_key, "status": "FAIL", "error": "INVALID_USERNAME_OR_PASSWORD"}
        tok = uuid.uuid4().hex
        self.tokens[tok] = time.time()
        self.logins += 1
        return {"token": tok, "product": app_key, "status": "SUCCESS", "error": ""}

    def keep_alive(self, token: Optional[str], app_key: str) -> dict:
        self.calls["keepAlive"] += 1
        if not self._valid(token):
            return {"token": "", "product": app_key, "status": "FAIL", "error": "NO_SESSION"}
        self.tokens[token] = time.time()
        return {"token": token, "product": app_key, "status": "SUCCESS", "error": ""}

    def _valid(self, token: Optional[str]) -> bool:
        if not token or token not in self.tokens or token in self.expired:
            return False
        if self.session_ttl and time.time() - self.tokens[token] > self.session_ttl:
            return False
        return True

    # ------------------------------------------------------------------ JSON-RPC
    @staticmethod
    def _aping_error(code: str, rid):
        return {"jsonrpc": "2.0", "error": {"code": -32099, "message": "ANGX-0003", "data": {"exceptionname": "APINGException",
                "APINGException": {"requestUUID": uuid.uuid4().hex, "errorCode": code, "errorDetails": ""}}}, "id": rid}

    def rpc(self, body: dict, token: Optional[str], app_key: Optional[str]) -> dict:
        rid = body.get("id", 1)
        method = str(body.get("method", "")).split("/")[-1]
        self.calls[method] += 1
        if not app_key:
            return self._aping_error("NO_APP_KEY", rid)
        if app_key != self.app_key:
            return self._aping_error("INVALID_APP_KEY", rid)
        if not self._valid(token):
            return self._aping_error("INVALID_SESSION_INFORMATION", rid)
        params = body.get("params") or {}
        fn = getattr(self, f"_m_{method}", None)
        if fn is None:
            return {"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": rid}
        return {"jsonrpc": "2.0", "result": fn(params), "id": rid}

    @staticmethod
    def _window(params: dict) -> tuple[datetime, datetime]:
        mst = (params.get("filter") or {}).get("marketStartTime") or {}
        f = datetime.fromisoformat(mst.get("from", "2000-01-01T00:00:00Z").replace("Z", "+00:00"))
        t = datetime.fromisoformat(mst.get("to", "2100-01-01T00:00:00Z").replace("Z", "+00:00"))
        return f, t

    def _events_in(self, params: dict) -> list[dict]:
        f, t = self._window(params)
        types = set((params.get("filter") or {}).get("eventTypeIds") or [])
        out = []
        d = f.date() - timedelta(days=1)
        while d <= t.date():
            for e in self._day(d)["events"]:
                od = datetime.fromisoformat(e["event"]["openDate"].replace("Z", "+00:00"))
                if f <= od < t and (not types or ("1" in types and e["_sport"] == "football") or ("2" in types and e["_sport"] == "tennis")):
                    out.append(e)
            d += timedelta(days=1)
        return out

    def _m_listEventTypes(self, params: dict) -> list[dict]:
        return [{"eventType": {"id": "1", "name": "Soccer"}, "marketCount": 5000}, {"eventType": {"id": "2", "name": "Tennis"}, "marketCount": 800}]

    def _m_getDeveloperAppKeys(self, params: dict) -> list[dict]:
        return [{"appName": "tradescout", "appId": 1, "appVersions": [
            {"owner": "u", "versionId": 1, "version": "1.0-DELAY", "applicationKey": self.app_key, "delayData": True, "subscriptionRequired": False, "ownerManaged": True, "active": True},
            {"owner": "u", "versionId": 2, "version": "1.0", "applicationKey": self.app_key + "live", "delayData": False, "subscriptionRequired": True, "ownerManaged": True, "active": True}]}]

    def _m_listEvents(self, params: dict) -> list[dict]:
        return [{"event": e["event"], "marketCount": e["marketCount"]} for e in self._events_in(params)]

    def _all_markets(self) -> dict[str, dict]:
        out = {}
        for d in list(self._days.values()):
            out.update(d["markets"])
        return out

    def _m_listMarketCatalogue(self, params: dict) -> list[dict]:
        flt = params.get("filter") or {}
        ids = set(flt.get("eventIds") or [])
        mtypes = set(flt.get("marketTypeCodes") or [])
        mids = set(flt.get("marketIds") or [])
        if flt.get("marketStartTime") or flt.get("eventTypeIds"):
            for e in self._events_in(params):
                ids.add(e["event"]["id"])
        proj = set(params.get("marketProjection") or [])
        weight = (1 if "MARKET_DESCRIPTION" in proj else 0) + (1 if "RUNNER_METADATA" in proj else 0)
        max_results = int(params.get("maxResults") or 1000)
        self.catalogue_requests.append({"maxResults": max_results, "weight": weight, "events": len(ids)})
        if weight * max_results > 200:
            raise ValueError("TOO_MUCH_DATA")
        out = []
        for m in self._all_markets().values():
            if ids and m["_event"] not in ids:
                continue
            if mids and m["marketId"] not in mids:
                continue
            if mtypes and m["description"]["marketType"] not in mtypes:
                continue
            row = {"marketId": m["marketId"], "marketName": m["marketName"], "totalMatched": m["totalMatched"]}
            if "MARKET_START_TIME" in proj:
                row["marketStartTime"] = m["marketStartTime"]
            if "MARKET_DESCRIPTION" in proj:
                row["description"] = m["description"]
            if "RUNNER_DESCRIPTION" in proj:
                row["runners"] = m["runners"]
            if "EVENT" in proj:
                row["event"] = m["event"]
            if "COMPETITION" in proj:
                row["competition"] = m["competition"]
            out.append(row)
        out.sort(key=lambda r: r["marketId"])
        return out[:max_results]

    def _m_listMarketBook(self, params: dict) -> list[dict]:
        mids = params.get("marketIds") or []
        if len(mids) > 40:
            raise ValueError("TOO_MUCH_DATA")
        allm = self._all_markets()
        out = []
        for mid in mids:
            m = allm.get(mid)
            if not m:
                continue
            ev_name = m["event"]["name"]
            inplay = ev_name in self.inplay
            status = "SUSPENDED" if ev_name in self.suspend else "OPEN"
            out.append({"marketId": mid, "isMarketDataDelayed": True, "status": status, "betDelay": 5 if inplay else 0, "bspReconciled": False, "complete": True,
                        "inplay": inplay, "numberOfWinners": 1, "numberOfRunners": len(m["runners"]), "numberOfActiveRunners": len(m["runners"]),
                        "totalMatched": m["totalMatched"], "totalAvailable": round(m["totalMatched"] * 0.1, 2), "crossMatching": True, "runnersVoidable": False,
                        "version": 1, "runners": m["_book"]})
        return out

    def _m_placeOrders(self, params: dict) -> dict:
        mid = params.get("marketId")
        m = self._all_markets().get(mid)
        ref = str(params.get("customerRef") or "")
        if ref and time.time() - self.recent_refs.get(ref, 0) < 60:
            return {"customerRef": ref, "status": "FAILURE", "errorCode": "DUPLICATE_TRANSACTION", "marketId": mid,
                    "instructionReports": [{"status": "FAILURE", "errorCode": "DUPLICATE_TRANSACTION", "instruction": i} for i in params.get("instructions") or []]}
        if ref:
            self.recent_refs[ref] = time.time()
        mtype = m["description"]["marketType"] if m else None
        if mtype in self.reject_market_types:
            return {"customerRef": ref, "status": "FAILURE", "errorCode": "BET_ACTION_ERROR", "marketId": mid,
                    "instructionReports": [{"status": "FAILURE", "errorCode": "MARKET_SUSPENDED", "instruction": i} for i in params.get("instructions") or []]}
        timeout = mtype in self.timeout_market_types
        reports = []
        ok_all = True
        for ins in params.get("instructions") or []:
            lo = ins.get("limitOrder") or {}
            size, price = float(lo.get("size", 0)), float(lo.get("price", 0))
            sel = ins.get("selectionId")
            if m is None or not any(r["selectionId"] == sel for r in m["runners"]):
                reports.append({"status": "FAILURE", "errorCode": "INVALID_MARKET_ID" if m is None else "INVALID_RUNNER", "instruction": ins})
                ok_all = False
                continue
            if size < 2.0:
                reports.append({"status": "FAILURE", "errorCode": "INVALID_BET_SIZE", "instruction": ins})
                ok_all = False
                continue
            # matched if the limit is at or worse than the best available price
            br = next(b for b in m["_book"] if b["selectionId"] == sel)
            best = br["ex"]["availableToBack"][0]["price"] if ins["side"] == "BACK" else br["ex"]["availableToLay"][0]["price"]
            matched = (price <= best) if ins["side"] == "BACK" else (price >= best)
            bet_id = str(300_000_000 + len(self.orders))
            order = {"betId": bet_id, "marketId": mid, "selectionId": sel, "handicap": 0.0, "priceSize": {"price": price, "size": size}, "bspLiability": 0.0,
                     "side": ins["side"], "status": "EXECUTION_COMPLETE" if matched else "EXECUTABLE", "persistenceType": lo.get("persistenceType", "LAPSE"),
                     "orderType": "LIMIT", "placedDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                     "averagePriceMatched": best if matched else 0.0, "sizeMatched": size if matched else 0.0, "sizeRemaining": 0.0 if matched else size,
                     "sizeLapsed": 0.0, "sizeCancelled": 0.0, "sizeVoided": 0.0, "regulatorCode": "GIBRALTAR REGULATOR",
                     "customerStrategyRef": params.get("customerStrategyRef"), "customerRef": params.get("customerRef"),
                     "customerOrderRef": ins.get("customerOrderRef")}
            self.orders.append(order)
            if timeout:
                reports.append({"status": "TIMEOUT", "instruction": ins})
                continue
            reports.append({"status": "SUCCESS", "instruction": ins, "betId": bet_id, "placedDate": order["placedDate"], "averagePriceMatched": order["averagePriceMatched"],
                            "sizeMatched": order["sizeMatched"], "orderStatus": order["status"]})
        out = {"customerRef": params.get("customerRef"), "status": "SUCCESS" if ok_all else "FAILURE", "marketId": mid, "instructionReports": reports}
        if not ok_all:
            out["errorCode"] = "BET_ACTION_ERROR"
        return out

    def _m_listCurrentOrders(self, params: dict) -> dict:
        refs = set(params.get("customerStrategyRefs") or [])
        mids = set(params.get("marketIds") or [])
        rows = [o for o in self.orders if (not refs or o.get("customerStrategyRef") in refs) and (not mids or o["marketId"] in mids)]
        return {"currentOrders": rows, "moreAvailable": False}

    def _m_cancelOrders(self, params: dict) -> dict:
        mid = params.get("marketId")
        wanted = {i["betId"] for i in (params.get("instructions") or [])}
        reports = []
        for o in self.orders:
            if o["marketId"] == mid and (not wanted or o["betId"] in wanted) and o["sizeRemaining"] > 0:
                reports.append({"status": "SUCCESS", "instruction": {"betId": o["betId"]}, "sizeCancelled": o["sizeRemaining"],
                                "cancelledDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")})
                o["sizeCancelled"] = o["sizeRemaining"]
                o["sizeRemaining"] = 0.0
                o["status"] = "EXECUTION_COMPLETE"
        return {"status": "SUCCESS", "marketId": mid, "instructionReports": reports}

    # ------------------------------------------------------------------ control
    def control(self, body: dict) -> dict:
        if body.get("expire_sessions"):
            self.expired.update(self.tokens)
        if "fail_login" in body:
            self.fail_login = bool(body["fail_login"])
        if "inplay" in body:
            self.inplay = set(body["inplay"] or [])
            # a market in play has started: move its start time into the past (and back when cleared), as Betfair would show
            for m in self._all_markets().values():
                orig = m.setdefault("_start_orig", m["marketStartTime"])
                m["marketStartTime"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S.000Z") if m["event"]["name"] in self.inplay else orig
                m["description"]["marketTime"] = m["marketStartTime"]
        if "suspend" in body:
            self.suspend = set(body["suspend"] or [])
        if "delay_ms" in body:
            self.delay_ms = int(body["delay_ms"])
        if body.get("reset_orders"):
            self.orders.clear()
            self.recent_refs.clear()
        if "reject_market_types" in body:
            self.reject_market_types = set(body["reject_market_types"] or [])
        if "timeout_market_types" in body:
            self.timeout_market_types = set(body["timeout_market_types"] or [])
        if body.get("reset_calls"):
            self.calls.clear()
        return self.state()

    def state(self) -> dict:
        return {"calls": dict(self.calls), "logins": self.logins, "orders": self.orders, "fail_login": self.fail_login, "inplay": sorted(self.inplay),
                "suspend": sorted(self.suspend), "live_tokens": len([t for t in self.tokens if self._valid(t)]), "edge": self.edge}


class _Handler(BaseHTTPRequestHandler):
    exchange: FakeExchange = None  # set by serve()

    def log_message(self, *a):  # silence
        pass

    def _send(self, payload, code=200):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        if self.path.startswith("/__state"):
            return self._send(self.exchange.state())
        if self.path.startswith("/api/keepAlive"):
            return self._send(self.exchange.keep_alive(self.headers.get("X-Authentication"), self.headers.get("X-Application", "")))
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        ex = self.exchange
        if ex.delay_ms:
            time.sleep(ex.delay_ms / 1000)
        raw = self._body()
        if self.path.startswith("/api/login"):
            form = parse_qs(raw.decode())
            return self._send(ex.login((form.get("username") or [""])[0], (form.get("password") or [""])[0], self.headers.get("X-Application", "")))
        if self.path.startswith("/api/keepAlive"):
            return self._send(ex.keep_alive(self.headers.get("X-Authentication"), self.headers.get("X-Application", "")))
        if self.path.startswith("/__control"):
            return self._send(ex.control(json.loads(raw or b"{}")))
        if self.path.startswith("/exchange/betting/json-rpc/v1") or self.path.startswith("/exchange/account/json-rpc/v1"):
            body = json.loads(raw)
            tok, key = self.headers.get("X-Authentication"), self.headers.get("X-Application")
            try:
                if isinstance(body, list):
                    return self._send([ex.rpc(b, tok, key) for b in body])
                return self._send(ex.rpc(body, tok, key))
            except ValueError as exc:
                return self._send(FakeExchange._aping_error(str(exc), body.get("id", 1) if isinstance(body, dict) else 1))
        self._send({"error": "not found"}, 404)


def serve(port: int = 0, host: str = "127.0.0.1", **kw) -> tuple[ThreadingHTTPServer, str, FakeExchange]:
    """Start the fake in a daemon thread. Returns (server, base_url, exchange)."""
    ex = FakeExchange(**kw)
    handler = type("Handler", (_Handler,), {"exchange": ex})
    srv = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://{host}:{srv.server_address[1]}", ex


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Fake Betfair Exchange API for TradeScout tests and demos")
    p.add_argument("--port", type=int, default=8900)
    p.add_argument("--app-key", default="testkey")
    p.add_argument("--password", default="secret")
    p.add_argument("--edge", type=float, default=0.0, help="mispricing on every third event so some ideas become TRADE (e.g. 0.5)")
    p.add_argument("--session-ttl", type=float, default=None, help="seconds before a session token expires")
    a = p.parse_args(argv)
    srv, url, ex = serve(a.port, app_key=a.app_key, password=a.password, edge=a.edge, session_ttl=a.session_ttl)
    print(f"Fake Betfair at {url}  (app key {a.app_key}, password {a.password}, edge {a.edge})", flush=True)
    print(f"  BETFAIR_LOGIN_URL={url}/api/login\n  BETFAIR_BETTING_URL={url}/exchange/betting/json-rpc/v1", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
