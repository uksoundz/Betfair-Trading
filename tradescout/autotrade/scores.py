"""Live match state for auto-trading: minute and score (football), sets and games (tennis).

Source 1 is Betfair's in-play score service (the one behind the scoreboards on the Betfair site; public,
not part of the documented betting API, so it is treated as best effort). Source 2, for the clock only,
is the exchange itself: minutes since the match odds market turned in play, less 15 for half-time. When
neither gives the score, score-driven rules wait; protective clock-driven exits still fire.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

import requests

DEFAULT_SCORES_URL = "https://ips.betfair.com/inplayservice/v1"


def _int(x) -> Optional[int]:
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


POINT_STEPS = {"0": 0, "00": 0, "love": 0, "15": 1, "30": 2, "40": 3, "a": 4, "ad": 4, "adv": 4}


def _point(x) -> Optional[int]:
    """Points won in the current game as a count: 0, 1, 2, 3 (40), 4 (advantage). Tiebreak counts pass through as numbers
    above 3 only when they are not game-score labels."""
    if x is None:
        return None
    t = str(x).strip().lower()
    if t == "":
        return None
    return POINT_STEPS.get(t, _int(t))


class ScoreFeed:
    def __init__(self, timeout: float = 6.0):
        self.base = os.getenv("BETFAIR_SCORES_URL", DEFAULT_SCORES_URL).rstrip("/")
        self.timeout = timeout
        self.last_ok: Optional[float] = None
        self.last_error: Optional[str] = None

    def _get(self, path: str, event_ids: list[str]) -> list[dict]:
        r = requests.get(f"{self.base}/{path}", params={"eventIds": ",".join(event_ids), "alt": "json", "regionCode": "UK", "locale": "en_GB"},
                         timeout=self.timeout, headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else [data]

    @staticmethod
    def parse_football(t: dict) -> dict:
        sc = t.get("score") or {}
        home, away = _int((sc.get("home") or {}).get("score")), _int((sc.get("away") or {}).get("score"))
        minute = t.get("elapsedRegularTime", t.get("timeElapsed"))
        first = None
        for u in t.get("updateDetails") or []:
            if "goal" in str(u.get("type", "")).lower() and u.get("team") in ("home", "away"):
                first = u["team"]
                break
        return {"minute": float(minute) if minute is not None else None, "home": home, "away": away, "first_goal": first,
                "status": t.get("inPlayMatchStatus") or t.get("status"), "source": "betfair scores"}

    @staticmethod
    def parse_tennis(t: dict) -> dict:
        sc = t.get("score") or {}
        h, a = sc.get("home") or {}, sc.get("away") or {}
        hs, as_ = _int(h.get("sets")), _int(a.get("sets"))
        seq_h, seq_a = h.get("gameSequence") or [], a.get("gameSequence") or []
        sets = [[_int(x) or 0, _int(y) or 0] for x, y in zip(seq_h, seq_a)]
        winners = ["home" if x > y else "away" for x, y in sets]
        cur_h, cur_a = _int(h.get("games")), _int(a.get("games"))
        if cur_h is not None and cur_a is not None:
            sets = sets + [[cur_h, cur_a]]
        if not winners and hs is not None and as_ is not None and hs + as_ == 1:
            winners = ["home" if hs else "away"]  # first set winner from the set count alone
        # points in the current game ("0", "15", "30", "40", "A"/"AD", or tiebreak counts) and who is serving, when the
        # service gives them; point-level rules wait when it does not
        ph, pa = _point(h.get("score")), _point(a.get("score"))
        server = "home" if h.get("isServing") is True else "away" if a.get("isServing") is True else None
        tiebreak = cur_h == 6 and cur_a == 6
        return {"home": hs, "away": as_, "sets": sets or None, "set_winners": winners if (hs is not None) else None,
                "sets_done": len(winners), "minute": None, "status": t.get("status"), "source": "betfair scores",
                "points": [ph, pa] if (ph is not None and pa is not None) else None, "server": server, "tiebreak": tiebreak,
                "game_key": f"{len(winners)}:{cur_h}-{cur_a}" if cur_h is not None and cur_a is not None else None}

    def states(self, events: Iterable[tuple[str, str]]) -> dict[str, dict]:
        """{event_id: state} for (event_id, sport) pairs. Missing events are simply absent."""
        events = list(events)
        out: dict[str, dict] = {}
        fb = [e for e, s in events if s == "football"]
        tn = [e for e, s in events if s == "tennis"]
        try:
            if fb:
                for t in self._get("eventTimelines", fb):
                    if t.get("eventId") is not None:
                        out[str(t["eventId"])] = self.parse_football(t)
            if tn:
                for t in self._get("scores", tn):
                    if t.get("eventId") is not None:
                        out[str(t["eventId"])] = self.parse_tennis(t)
            self.last_ok, self.last_error = time.time(), None
        except Exception as exc:  # best effort: rules that need the score wait
            self.last_error = str(exc)[:200]
        return out


def market_clock(market_start: Optional[str], inplay: bool, now: Optional[datetime] = None) -> Optional[float]:
    """Approximate football minute from the market's start time: elapsed minutes, less 15 for half-time
    after the 47th. Only used when the score service does not answer."""
    if not inplay or not market_start:
        return None
    try:
        t = datetime.fromisoformat(market_start.replace("Z", "+00:00"))
    except ValueError:
        return None
    el = ((now or datetime.now(timezone.utc)) - t).total_seconds() / 60
    if el < 0:
        return 0.0
    if el <= 47:
        return el
    if el <= 62:
        return 45.0
    return min(120.0, el - 15)
