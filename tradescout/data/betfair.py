"""Betfair Exchange client: prices, fixture matching, diagnostics and order placement.

Session handling (Betfair rules): a session token lasts 24 hours for UK/Ireland accounts, 12 hours
elsewhere and 20 minutes on the Italian/Spanish exchanges unless kept alive. The client therefore
logs in lazily, keeps the session alive on request, and re-logs-in once when the betting API answers
INVALID_SESSION_INFORMATION / NO_SESSION, so a price feed never dies quietly overnight.

Login: the interactive endpoint (username + password) or, for accounts with two-factor
authentication, the certificate endpoint (client certificate + key). The identity host follows the
account's jurisdiction (.com, .it, .es, .ro, .se, .com.au). Endpoints can be overridden with
BETFAIR_LOGIN_URL / BETFAIR_KEEPALIVE_URL / BETFAIR_BETTING_URL / BETFAIR_ACCOUNTS_URL (used by the
test double in tests/fake_betfair.py).

Prices for a day are fetched in three batched calls (events, catalogue, books in chunks) rather than
two calls per fixture, and every fixture gets a diagnosis: matched to which exchange event, or why
not (no event, ambiguous names, in play, suspended, feed error). Fixture-to-event matching is the
scored matcher in matching.py, not an exact alias table. `listMarketBook` asks for virtual
(cross-matched) prices, which is what the website shows.

Soccer event type id is 1, tennis 2. Only the markets the strategies consume are fetched.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import requests

from ..models import Fixture, MarketPrices
from ..value import Quote
from .matching import EventMatch, assign_match_odds, best_event, fold

IDENTITY_HOSTS = {"com": "https://identitysso.betfair.com", "it": "https://identitysso.betfair.it", "es": "https://identitysso.betfair.es",
                  "ro": "https://identitysso.betfair.ro", "se": "https://identitysso.betfair.se", "com.au": "https://identitysso.betfair.com.au"}
CERT_HOSTS = {k: v.replace("identitysso.", "identitysso-cert.") for k, v in IDENTITY_HOSTS.items()}
BETTING_DEFAULT = "https://api.betfair.com/exchange/betting/json-rpc/v1"
ACCOUNTS_DEFAULT = "https://api.betfair.com/exchange/account/json-rpc/v1"
MARKETS = ["MATCH_ODDS", "OVER_UNDER_15", "OVER_UNDER_25", "OVER_UNDER_35", "BOTH_TEAMS_TO_SCORE", "CORRECT_SCORE"]
UK_TZ = ZoneInfo("Europe/London")
EVENT_TTL = 600        # seconds an event list / catalogue is reused
BOOK_CHUNK = 25        # markets per listMarketBook call (EX_BEST_OFFERS weighs 5 each, limit 200)
CATALOGUE_CHUNK = 60   # events per listMarketCatalogue call (6 market types each, maxResults 1000)

LOGIN_ERRORS = {
    "INVALID_USERNAME_OR_PASSWORD": "Betfair rejected the username or password. Use your Betfair username (not e-mail). Accounts with two-factor authentication must use the certificate login.",
    "EMAIL_LOGIN_NOT_ALLOWED": "Use your Betfair username, not your e-mail address.",
    "ACCOUNT_NOW_LOCKED": "Betfair has just locked the account after failed logins. Unlock it on the Betfair website, then check the password here.",
    "ACCOUNT_ALREADY_LOCKED": "The Betfair account is locked. Unlock it on the Betfair website.",
    "PENDING_AUTH": "The account needs a verification step on the Betfair website before API access works.",
    "TEMPORARY_BAN_TOO_MANY_REQUESTS": "Too many login attempts. Wait a few minutes and try again.",
    "SECURITY_RESTRICTED_LOCATION": "Betfair does not allow access from your current location.",
    "SELF_EXCLUDED": "The account is self-excluded.", "SUSPENDED": "The Betfair account is suspended.", "CLOSED": "The Betfair account is closed.",
    "TRADING_MASTER_SUSPENDED": "The account is suspended.", "DUPLICATE_CARDS": "Betfair needs a card detail check on the website.",
    "CERT_AUTH_REQUIRED": "This account requires the certificate login (two-factor authentication).",
    "INVALID_APP_KEY": "The application key is not valid.", "NO_APP_KEY": "No application key was sent.",
    "SPAIN_MIGRATION_REQUIRED": "Spanish account: log in via the .es jurisdiction.", "ITALY_PROFILING_REQUIRED": "Italian account: complete profiling on betfair.it.",
    "DANISH_AUTHORIZATION_REQUIRED": "Danish account: authorise API access on the Betfair website.",
    "KYC_SUSPEND": "The account is awaiting an identity check on the Betfair website.", "ACTIONS_REQUIRED": "Betfair needs you to complete an action on the website first.",
}
API_ERRORS = {
    "INVALID_SESSION_INFORMATION": "The Betfair session expired and could not be renewed.", "NO_SESSION": "No Betfair session.",
    "INVALID_APP_KEY": "Betfair does not recognise this application key.", "NO_APP_KEY": "No application key was sent.",
    "ACCESS_DENIED": "Betfair refused this application key for the betting API: the key is restricted or not yet activated.",
    "TOO_MUCH_DATA": "Request too large for one call.", "TOO_MANY_REQUESTS": "Betfair rate limit hit; slow down.",
    "SERVICE_BUSY": "Betfair is busy; try again in a moment.", "TIMEOUT_ERROR": "Betfair timed out.", "UNEXPECTED_ERROR": "Betfair returned an unexpected error.",
    "INVALID_INPUT_DATA": "Betfair rejected the request parameters.", "REQUEST_SIZE_EXCEEDS_LIMIT": "Request too large.",
    "NETWORK": "Could not reach Betfair (network, proxy or firewall).",
}


class BetfairError(RuntimeError):
    def __init__(self, code: str, details: str = ""):
        self.code = code
        self.details = details
        text = LOGIN_ERRORS.get(code) or API_ERRORS.get(code) or f"Betfair error {code}"
        super().__init__(f"{text}{' (' + details + ')' if details and details != code else ''} [{code}]")


@dataclass
class FeedHealth:
    configured: bool = True
    connected: bool = False
    can_login: bool = False
    identity_host: str = ""
    delayed: Optional[bool] = None      # exchange flags the price data as delayed (Delayed app key)
    logins: int = 0
    calls: int = 0
    last_login: Optional[str] = None
    last_ok: Optional[str] = None
    last_error: Optional[str] = None
    last_error_code: Optional[str] = None
    last_error_at: Optional[str] = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class FeedReport:
    """What happened when prices were fetched for one day's fixtures."""
    source: str = "betfair"
    day: str = ""
    events_on_day: int = 0
    fixtures: int = 0
    matched: int = 0
    priced: int = 0
    unmatched: list = field(default_factory=list)   # [{"fixture", "reason", "candidates": [[name, score]]}]
    inplay: list = field(default_factory=list)
    suspended: list = field(default_factory=list)
    error: Optional[str] = None
    error_code: Optional[str] = None
    fetched_at: Optional[str] = None
    elapsed_ms: int = 0
    calls: int = 0
    delayed: Optional[bool] = None
    event_names: list = field(default_factory=list)  # how the exchange names today's events (for the user)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _aping_code(payload: dict) -> tuple[str, str]:
    err = payload.get("error") or {}
    data = err.get("data") or {}
    ex = data.get("APINGException") or data.get("AccountAPINGException") or {}
    code = ex.get("errorCode") or err.get("message") or "UNEXPECTED_ERROR"
    return str(code), str(ex.get("errorDetails") or err.get("message") or "")


class BetfairPrices:
    def __init__(self, app_key: str, session_token: str | None = None, username: str | None = None, password: str | None = None,
                 timeout: int = 20, jurisdiction: str = "com", cert_file: str | None = None, key_file: str | None = None, login_now: bool = False):
        self.app_key = app_key
        self.timeout = timeout
        self.username = username or ""
        self.password = password or ""
        self.cert = (cert_file, key_file) if cert_file and key_file else None
        j = (jurisdiction or "com").lower()
        host = IDENTITY_HOSTS.get(j, IDENTITY_HOSTS["com"])
        self.login_url = os.getenv("BETFAIR_LOGIN_URL") or (f"{CERT_HOSTS.get(j, CERT_HOSTS['com'])}/api/certlogin" if self.cert else f"{host}/api/login")
        self.keepalive_url = os.getenv("BETFAIR_KEEPALIVE_URL") or f"{host}/api/keepAlive"
        self.betting_url = os.getenv("BETFAIR_BETTING_URL", BETTING_DEFAULT)
        self.accounts_url = os.getenv("BETFAIR_ACCOUNTS_URL", ACCOUNTS_DEFAULT)
        self.token: Optional[str] = session_token or None
        self.token_from_settings = bool(session_token)
        self.health = FeedHealth(configured=bool(app_key), connected=bool(session_token), can_login=bool(self.username and self.password) or bool(self.cert),
                                 identity_host=host)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()
        self.lay_prices: dict[str, float] = {}
        if login_now:
            self.ensure_session()

    # ------------------------------------------------------------------ session
    @property
    def can_login(self) -> bool:
        return self.health.can_login

    def ensure_session(self) -> str:
        with self._lock:
            if self.token:
                return self.token
            return self.login()

    def _record_error(self, exc: BetfairError) -> None:
        self.health.last_error = str(exc)
        self.health.last_error_code = exc.code
        self.health.last_error_at = _now()

    def login(self) -> str:
        """Open a fresh session. Raises BetfairError with a plain-English message."""
        with self._lock:
            if not self.can_login:
                exc = BetfairError("NO_SESSION", "no username/password or certificate saved, so an expired session token cannot be renewed")
                self._record_error(exc)
                self.health.connected = False
                raise exc
            try:
                if self.cert:
                    r = requests.post(self.login_url, data={"username": self.username, "password": self.password}, cert=self.cert,
                                      headers={"X-Application": self.app_key, "Accept": "application/json"}, timeout=self.timeout)
                    r.raise_for_status()
                    payload = r.json()
                    ok, token, code = payload.get("loginStatus") == "SUCCESS", payload.get("sessionToken"), payload.get("loginStatus")
                else:
                    r = requests.post(self.login_url, data={"username": self.username, "password": self.password},
                                      headers={"X-Application": self.app_key, "Accept": "application/json"}, timeout=self.timeout)
                    r.raise_for_status()
                    payload = r.json()
                    ok, token, code = payload.get("status") == "SUCCESS", payload.get("token"), payload.get("error") or payload.get("status")
            except requests.RequestException as exc:
                err = BetfairError("NETWORK", str(exc)[:200])
                self._record_error(err)
                self.health.connected = False
                raise err from exc
            except ValueError as exc:
                err = BetfairError("UNEXPECTED_ERROR", "login response was not JSON")
                self._record_error(err)
                raise err from exc
            if not ok or not token:
                err = BetfairError(str(code or "UNEXPECTED_ERROR"))
                self._record_error(err)
                self.health.connected = False
                self.token = None
                raise err
            self.token = token
            self.token_from_settings = False
            self.health.connected = True
            self.health.logins += 1
            self.health.last_login = _now()
            self.health.last_error = self.health.last_error_code = self.health.last_error_at = None
            return token

    def keep_alive(self) -> bool:
        """Extend the session. False (and token dropped) when Betfair says the session is gone."""
        with self._lock:
            if not self.token:
                return False
            try:
                r = requests.post(self.keepalive_url, headers={"X-Application": self.app_key, "X-Authentication": self.token, "Accept": "application/json"},
                                  timeout=self.timeout)
                payload = r.json()
            except (requests.RequestException, ValueError):
                return bool(self.token)  # network blip: keep the token, the next call will tell
            if payload.get("status") == "SUCCESS":
                self.token = payload.get("token") or self.token
                self.health.last_ok = _now()
                self.health.connected = True
                return True
            self.token = None
            self.health.connected = False
            self._record_error(BetfairError(str(payload.get("error") or "NO_SESSION")))
            return False

    def _rpc(self, method: str, params: dict[str, Any], endpoint: str | None = None, prefix: str = "SportsAPING/v1.0/", _retry: bool = True) -> Any:
        token = self.ensure_session()
        body = {"jsonrpc": "2.0", "method": f"{prefix}{method}", "params": params, "id": 1}
        try:
            r = requests.post(endpoint or self.betting_url, json=body, timeout=self.timeout,
                              headers={"X-Application": self.app_key, "X-Authentication": token, "Content-Type": "application/json", "Accept": "application/json"})
        except requests.RequestException as exc:
            err = BetfairError("NETWORK", str(exc)[:200])
            self._record_error(err)
            raise err from exc
        try:
            payload = r.json()
        except ValueError:
            err = BetfairError("UNEXPECTED_ERROR", f"HTTP {r.status_code} without a JSON body")
            self._record_error(err)
            raise err
        if isinstance(payload, dict) and "error" in payload:
            code, details = _aping_code(payload)
            if code in ("INVALID_SESSION_INFORMATION", "NO_SESSION") and _retry:
                with self._lock:
                    self.token = None
                    self.health.connected = False
                if self.can_login:
                    self.login()
                    return self._rpc(method, params, endpoint, prefix, _retry=False)
            err = BetfairError(code, details)
            self._record_error(err)
            if code in ("INVALID_SESSION_INFORMATION", "NO_SESSION", "INVALID_APP_KEY", "NO_APP_KEY", "ACCESS_DENIED"):
                self.health.connected = False
            raise err
        if r.status_code >= 400:
            err = BetfairError("UNEXPECTED_ERROR", f"HTTP {r.status_code}")
            self._record_error(err)
            raise err
        self.health.calls += 1
        self.health.last_ok = _now()
        self.health.connected = True
        return payload.get("result") if isinstance(payload, dict) else payload

    def probe(self) -> dict:
        """Cheap end-to-end check of key + session: counts event types. Raises BetfairError."""
        types = self._rpc("listEventTypes", {"filter": {}})
        return {"ok": True, "event_types": len(types or [])}

    def app_key_delayed(self) -> Optional[bool]:
        """Whether this application key is a Delayed key (prices 1-180 s old), via the Accounts API. None if unknown."""
        try:
            rows = self._rpc("getDeveloperAppKeys", {}, endpoint=self.accounts_url, prefix="AccountAPING/v1.0/")
            for app in rows or []:
                for v in app.get("appVersions", []):
                    if v.get("applicationKey") == self.app_key:
                        self.health.delayed = bool(v.get("delayData"))
                        return self.health.delayed
        except BetfairError:
            return None
        return None

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def _cached(self, key: str, ttl: float, build):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        value = build()
        with self._lock:
            self._cache[key] = (time.time(), value)
        return value

    # ------------------------------------------------------------------ events and markets
    def events_on(self, day: date, event_type: str = "1") -> list[dict]:
        """Exchange events whose start falls on `day` (UK time). Cached for EVENT_TTL seconds."""
        def build():
            start = (day - timedelta(days=1)).isoformat() + "T00:00:00Z"
            end = (day + timedelta(days=2)).isoformat() + "T00:00:00Z"
            rows = self._rpc("listEvents", {"filter": {"eventTypeIds": [event_type], "marketStartTime": {"from": start, "to": end}}})
            keep = []
            for ev in rows or []:
                od = (ev.get("event") or {}).get("openDate")
                if od:
                    try:
                        if datetime.fromisoformat(od.replace("Z", "+00:00")).astimezone(UK_TZ).date() != day:
                            continue
                    except ValueError:
                        pass
                keep.append(ev)
            return keep
        return self._cached(f"ev:{event_type}:{day.isoformat()}", EVENT_TTL, build)

    def match_fixture(self, fixture: Fixture) -> EventMatch:
        if fixture.fixture_id and str(fixture.fixture_id).startswith("bf:"):
            return EventMatch(str(fixture.fixture_id)[3:], fixture.label, 1.0, 1.0, 1.0, [], "matched")
        return best_event(fixture.home, fixture.away, self.events_on(fixture.date), fixture.kickoff)

    def catalogue(self, event_ids: Iterable[str], market_types: Iterable[str] | None = None) -> dict[str, list[dict]]:
        """event id -> market catalogue rows (runners + description), batched and cached per event."""
        wanted = [e for e in dict.fromkeys(event_ids) if e]
        out: dict[str, list[dict]] = {}
        missing = []
        with self._lock:
            for e in wanted:
                hit = self._cache.get(f"cat:{e}")
                if hit and time.time() - hit[0] < EVENT_TTL:
                    out[e] = hit[1]
                else:
                    missing.append(e)
        for i in range(0, len(missing), CATALOGUE_CHUNK):
            chunk = missing[i:i + CATALOGUE_CHUNK]
            flt: dict = {"eventIds": chunk}
            if market_types:
                flt["marketTypeCodes"] = list(market_types)
            rows = self._rpc("listMarketCatalogue", {"filter": flt, "maxResults": 1000,
                                                    "marketProjection": ["RUNNER_DESCRIPTION", "MARKET_DESCRIPTION", "EVENT", "MARKET_START_TIME"]}) or []
            grouped: dict[str, list[dict]] = {e: [] for e in chunk}
            for c in rows:
                grouped.setdefault((c.get("event") or {}).get("id"), []).append(c)
            with self._lock:
                for e, cats in grouped.items():
                    self._cache[f"cat:{e}"] = (time.time(), cats)
                    out[e] = cats
        return out

    def books(self, market_ids: Iterable[str]) -> dict[str, dict]:
        ids = [m for m in dict.fromkeys(market_ids) if m]
        out: dict[str, dict] = {}
        for i in range(0, len(ids), BOOK_CHUNK):
            rows = self._rpc("listMarketBook", {"marketIds": ids[i:i + BOOK_CHUNK],
                                                "priceProjection": {"priceData": ["EX_BEST_OFFERS"], "virtualise": True}}) or []
            for b in rows:
                out[b["marketId"]] = b
        return out

    # ------------------------------------------------------------------ prices
    def _build_prices(self, fixture: Fixture, cats: list[dict], by_id: dict[str, dict], match: EventMatch) -> MarketPrices:
        mp = MarketPrices(source="betfair", as_of=_now(), event_id=match.event_id, event_name=match.event_name)
        if not cats:
            mp.status, mp.note = "no_markets", f"Matched exchange event '{match.event_name}' but it has none of the markets the strategies use yet."
            return mp
        any_book = False
        for cat in cats:
            book = by_id.get(cat["marketId"])
            mtype = (cat.get("description") or {}).get("marketType") or ""
            if not book:
                mp.market_status[mtype] = "NO_BOOK"
                continue
            any_book = True
            status = book.get("status") or "OPEN"
            inplay = bool(book.get("inplay"))
            mp.market_status[mtype] = "INPLAY" if inplay else status
            if mtype == "MATCH_ODDS":
                mp.total_matched = book.get("totalMatched")
                mp.inplay = inplay
                if book.get("isMarketDataDelayed") is not None:
                    mp.delayed = bool(book.get("isMarketDataDelayed"))
            if status != "OPEN" or inplay:
                continue  # suspended / closed / in play: never price a pre-match plan off it
            runners = {r["selectionId"]: r for r in cat.get("runners", [])}
            roles = assign_match_odds(cat.get("runners", []), fixture.home, fixture.away) if mtype == "MATCH_ODDS" else {}
            for r in book.get("runners", []):
                if r.get("status") not in (None, "ACTIVE"):
                    continue
                meta = runners.get(r["selectionId"], {})
                name = meta.get("runnerName", "")
                ex = r.get("ex", {})
                back_ladder = [(x["price"], x["size"]) for x in (ex.get("availableToBack") or []) if x.get("price")]
                lay_ladder = [(x["price"], x["size"]) for x in (ex.get("availableToLay") or []) if x.get("price")]
                quote = Quote(back_ladder, lay_ladder, book.get("totalMatched"), mp.as_of)
                if mtype == "MATCH_ODDS":
                    skey = roles.get(r["selectionId"], "away")
                elif mtype in ("CORRECT_SCORE", "SET_BETTING"):
                    skey = name.replace(" ", "")
                else:
                    skey = name
                mp.quotes[f"{mtype}:{skey}"] = quote
                back = back_ladder[0][0] if back_ladder else None
                lay = lay_ladder[0][0] if lay_ladder else None
                if lay:
                    self.lay_prices[f"{mtype}:{name}"] = lay
                if mtype == "MATCH_ODDS":
                    if skey == "home" and back:
                        mp.home = back
                    elif skey == "away" and back:
                        mp.away = back
                    elif skey == "draw":
                        mp.draw = lay or back  # strategies lay the draw: use lay price
                elif not back:
                    continue
                elif mtype.startswith("OVER_UNDER_"):
                    line = mtype.split("_")[-1]
                    setattr(mp, ("over_" if name.startswith("Over") else "under_") + line, back)
                elif mtype == "BOTH_TEAMS_TO_SCORE":
                    if name == "Yes":
                        mp.btts_yes = back
                    else:
                        mp.btts_no = back
                elif mtype == "CORRECT_SCORE":
                    mp.correct_scores[name.replace(" ", "")] = back
        if mp.quotes:
            mp.status = "ok"
            mp.note = f"Priced from exchange event '{match.event_name}'."
            if any(v == "SUSPENDED" for v in mp.market_status.values()):
                sus = [k for k, v in mp.market_status.items() if v == "SUSPENDED"]
                mp.note += f" Suspended right now: {', '.join(sus)}."
        elif mp.inplay or any(v == "INPLAY" for v in mp.market_status.values()):
            mp.status, mp.note = "inplay", "The match has started (markets in play): pre-match plans no longer apply."
        elif any(v == "SUSPENDED" for v in mp.market_status.values()):
            mp.status, mp.note = "suspended", "Every market is suspended at the moment (team news or a price reset). Refresh in a minute."
        elif any(v == "CLOSED" for v in mp.market_status.values()):
            mp.status, mp.note = "closed", "The exchange markets for this match are closed."
        elif not any_book:
            mp.status, mp.note = "no_markets", f"Exchange event '{match.event_name}' returned no price books."
        else:
            mp.status, mp.note = "no_markets", f"Exchange event '{match.event_name}' has no prices on offer yet."
        return mp

    def prices_for_day(self, fixtures: Iterable[Fixture]) -> tuple[dict[str, MarketPrices], FeedReport]:
        """Prices for every fixture of one day in batched calls, plus a report of what matched and what did not."""
        fixtures = list(fixtures)
        rep = FeedReport(day=fixtures[0].date.isoformat() if fixtures else "", fixtures=len(fixtures))
        t0 = time.time()
        calls0 = self.health.calls
        out: dict[str, MarketPrices] = {}
        if not fixtures:
            return out, rep
        try:
            matches = {fx.label: self.match_fixture(fx) for fx in fixtures}
            try:
                evs = self.events_on(fixtures[0].date)
                rep.events_on_day = len(evs)
                rep.event_names = sorted((e.get("event") or {}).get("name", "") for e in evs)
            except BetfairError:
                raise
            matched_ids = [m.event_id for m in matches.values() if m.event_id]
            cats = self.catalogue(matched_ids, MARKETS)
            by_id = self.books([c["marketId"] for e in matched_ids for c in cats.get(e, [])])
        except BetfairError as exc:
            rep.error, rep.error_code = str(exc), exc.code
            for fx in fixtures:
                out[fx.label] = MarketPrices(status="error", note=str(exc))
            rep.elapsed_ms = int((time.time() - t0) * 1000)
            rep.calls = self.health.calls - calls0
            rep.fetched_at = _now()
            return out, rep
        for fx in fixtures:
            m = matches[fx.label]
            if not m.event_id:
                mp = MarketPrices(status="no_event", note=m.reason, candidates=list(m.candidates))
                rep.unmatched.append({"fixture": fx.label, "reason": m.reason, "candidates": [list(c) for c in m.candidates]})
            else:
                rep.matched += 1
                mp = self._build_prices(fx, cats.get(m.event_id, []), by_id, m)
                if mp.status == "ok":
                    rep.priced += 1
                elif mp.status == "inplay":
                    rep.inplay.append(fx.label)
                elif mp.status == "suspended":
                    rep.suspended.append(fx.label)
                if mp.delayed is not None and rep.delayed is None:
                    rep.delayed = mp.delayed
            out[fx.label] = mp
        if rep.delayed is not None:
            self.health.delayed = rep.delayed
        rep.elapsed_ms = int((time.time() - t0) * 1000)
        rep.calls = self.health.calls - calls0
        rep.fetched_at = _now()
        return out, rep

    def prices(self, fixture: Fixture) -> MarketPrices:
        """Prices for one fixture (the bet slip and CLI use this). Errors come back as status 'error', never raised."""
        out, _ = self.prices_for_day([fixture])
        return out.get(fixture.label, MarketPrices())

    def diagnose(self, day: date, fixtures: Iterable[Fixture]) -> dict:
        """Everything a user needs to see why prices are or are not attached on a day."""
        fixtures = list(fixtures)
        rows = []
        report: Optional[FeedReport] = None
        error = None
        try:
            per, report = self.prices_for_day(fixtures)
            for fx in fixtures:
                mp = per.get(fx.label, MarketPrices())
                rows.append({"fixture": fx.label, "league": fx.league, "kickoff": fx.kickoff.strftime("%H:%M") if fx.kickoff else None, **mp.diagnostics()})
        except Exception as exc:  # pragma: no cover - defensive
            error = str(exc)
        return {"day": day.isoformat(), "health": self.health.to_dict(), "report": report.to_dict() if report else None, "fixtures": rows, "error": error,
                "login_url": self.login_url, "betting_url": self.betting_url}

    # ------------------------------------------------------------------ slip resolution
    @staticmethod
    def _runner_matches(runner: dict, roles: dict, market: str, selection: str) -> bool:
        name = runner.get("runnerName", "")
        if market == "MATCH_ODDS":
            return roles.get(runner["selectionId"]) == selection
        if market in ("CORRECT_SCORE", "SET_BETTING"):
            return name.replace(" ", "") == selection.replace(" ", "")
        a, b = fold(name), fold(selection)
        return a == b or a.startswith(b + " ") or b.startswith(a + " ")

    def resolve(self, fixture: Fixture, market: str, selection: str):
        """(market_id, selection_id, best_back, best_lay) for one selection, or None when not on the exchange."""
        full = self.resolve_full(fixture, market, selection)
        if not full:
            return None
        return full["market_id"], full["selection_id"], full["best_back"], full["best_lay"]

    def resolve_full(self, fixture: Fixture, market: str, selection: str) -> Optional[dict]:
        m = self.match_fixture(fixture)
        if not m.event_id:
            return None
        cats = self.catalogue([m.event_id], MARKETS if market in MARKETS else None).get(m.event_id, [])
        for cat in cats:
            if (cat.get("description") or {}).get("marketType") != market:
                continue
            roles = assign_match_odds(cat.get("runners", []), fixture.home, fixture.away) if market == "MATCH_ODDS" else {}
            for r in cat.get("runners", []):
                if self._runner_matches(r, roles, market, selection):
                    book = self.books([cat["marketId"]]).get(cat["marketId"], {})
                    best_back = best_lay = None
                    for br in book.get("runners", []):
                        if br["selectionId"] == r["selectionId"]:
                            best_back = (br.get("ex", {}).get("availableToBack") or [{}])[0].get("price")
                            best_lay = (br.get("ex", {}).get("availableToLay") or [{}])[0].get("price")
                    return {"market_id": cat["marketId"], "selection_id": r["selectionId"], "best_back": best_back, "best_lay": best_lay,
                            "status": "INPLAY" if book.get("inplay") else (book.get("status") or "OPEN"), "runner_name": r.get("runnerName"),
                            "event_name": m.event_name, "delayed": book.get("isMarketDataDelayed")}
        return None

    # ------------------------------------------------------------------ orders (only after the user confirms a slip on screen)
    def place_orders(self, market_id: str, instructions: list[dict], customer_ref: str) -> dict:
        """Send LIMIT orders for one market. Each instruction: selectionId, side (back|lay), price, size.
        persistenceType LAPSE means anything still unmatched is cancelled when the market turns in play,
        so a plan price that never arrives simply expires at kick-off. customerRef (<= 32 chars) lets the
        exchange reject an accidental resubmission of the same slip within its de-duplication window."""
        payload = {
            "marketId": market_id,
            "instructions": [{"selectionId": int(i["selectionId"]), "handicap": 0, "side": i["side"].upper(), "orderType": "LIMIT",
                              "limitOrder": {"size": round(float(i["size"]), 2), "price": float(i["price"]), "persistenceType": "LAPSE"}}
                             for i in instructions],
            "customerRef": customer_ref[:32],
            "customerStrategyRef": "tradescout",
        }
        return self._rpc("placeOrders", payload)

    def current_orders(self, market_ids: list[str] | None = None) -> list[dict]:
        """Open and recently matched orders this app placed (tagged with the tradescout strategy ref)."""
        params: dict = {"orderProjection": "ALL", "customerStrategyRefs": ["tradescout"], "fromRecord": 0, "recordCount": 200}
        if market_ids:
            params["marketIds"] = market_ids
        return (self._rpc("listCurrentOrders", params) or {}).get("currentOrders", [])

    def cancel_orders(self, market_id: str, bet_ids: list[str] | None = None) -> dict:
        """Cancel unmatched orders on a market (all of them when bet_ids is None)."""
        params: dict = {"marketId": market_id}
        if bet_ids:
            params["instructions"] = [{"betId": b} for b in bet_ids]
        return self._rpc("cancelOrders", params)
