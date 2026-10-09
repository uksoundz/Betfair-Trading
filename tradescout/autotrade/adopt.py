"""Adopting a bet the user placed on Betfair (website or app) into auto-trading.

The user's matched bets are grouped per selection; each is offered the plans whose entry it matches
(a lay of the draw can follow Lay the Draw, a back of Over 2.5 the Over 2.5 free-bet rule, and so on).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from ..strategies import get_strategy
from . import position as P

TENNIS_TOTALS = ("TOTAL_GAMES", "COMBINED_TOTAL")


def plans_for(market_type: str, runner_name: str, side: str, sport: str) -> list[str]:
    """Strategy keys whose pre-match entry is this bet."""
    name = (runner_name or "").lower()
    out: list[str] = []
    if sport == "tennis":
        if market_type == "MATCH_ODDS" and side == "back":
            out.append("tn_b2l_fav")
        elif market_type == "SET_BETTING" and side == "back":
            out.append("tn_straight_sets")
        elif market_type in TENNIS_TOTALS and side == "back" and name.startswith("over"):
            out.append("tn_over_games")
        return out
    if market_type == "MATCH_ODDS":
        if name == "the draw" and side == "lay":
            out.append("ltd")
        elif name != "the draw" and side == "back":
            out.append("b2l_fav")
    elif market_type == "OVER_UNDER_25":
        if name.startswith("over") and side == "back":
            out.append("over25_ins")
        elif name.startswith("under") and side == "lay":
            out.append("lay_under25_staged")
        elif name.startswith("under") and side == "back":
            out.append("under25_tradeout")
    elif market_type == "CORRECT_SCORE" and name.replace(" ", "") == "0-0" and side == "lay":
        out.append("lay_00")
    return [k for k in out if get_strategy(k).trade_rules()]


def positions(orders: Iterable[dict], catalogue: dict[str, dict]) -> list[dict]:
    """One row per (market, selection, handicap) with matched money, from the account's current orders."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for o in orders:
        groups[(o.get("marketId"), o.get("selectionId"), float(o.get("handicap") or 0.0))].append(o)
    out = []
    for (mid, sid, hc), os_ in groups.items():
        cat = catalogue.get(mid)
        if not cat:
            continue
        e = P.exposure(os_, sid, hc)
        if e.backed + e.laid < 0.01:
            continue  # nothing matched yet
        runner = next((r for r in cat.get("runners", []) if r.get("selectionId") == sid and float(r.get("handicap") or 0.0) == hc), {})
        mtype = (cat.get("description") or {}).get("marketType", "")
        sport = "tennis" if str((cat.get("eventType") or {}).get("id")) == "2" else "football"
        side = "back" if e.backed >= e.laid else "lay"
        net = round(abs(e.backed - e.laid), 2)
        matched = [o for o in os_ if float(o.get("sizeMatched") or 0) > 0 and o.get("side", "").lower() == side]
        stake = sum(float(o["sizeMatched"]) for o in matched)
        avg = sum(float(o["sizeMatched"]) * float(o.get("averagePriceMatched") or 0) for o in matched) / stake if stake else 0.0
        name = runner.get("runnerName", str(sid))
        if mtype in TENNIS_TOTALS and hc and not any(ch.isdigit() for ch in name):
            name = f"{name} {abs(hc):g}"
        ev = cat.get("event") or {}
        home, _, away = (ev.get("name") or "").partition(" v ")
        out.append({"key": f"{mid}:{sid}:{hc:g}", "market_id": mid, "market_type": mtype, "market_name": cat.get("marketName", mtype),
                    "event_id": ev.get("id"), "event_name": ev.get("name"), "home": home.strip(), "away": away.strip(), "start": cat.get("marketStartTime"),
                    "sport": sport, "selection_id": sid, "handicap": hc, "runner_name": name, "sort_priority": runner.get("sortPriority"),
                    "side": side, "size": net, "avg_price": round(avg, 2), "liability": round(-e.worst, 2), "win": round(e.win, 2), "lose": round(e.lose, 2),
                    "bet_ids": [str(o.get("betId")) for o in os_], "plans": [{"key": k, "label": get_strategy(k).label} for k in plans_for(mtype, name, side, sport)]})
    out.sort(key=lambda r: (r.get("start") or "", r["event_name"] or ""))
    return out


def fav_side(row: dict) -> str:
    """Which side the plan's 'favourite' is for an adopted bet: the player or team the bet is on."""
    if row["market_type"] == "MATCH_ODDS" and row["runner_name"].lower() != "the draw":
        if row.get("sort_priority") in (1, 2):
            return "home" if row["sort_priority"] == 1 else "away"
    if row["sport"] == "tennis" and row["market_type"] == "SET_BETTING":
        who = "".join(ch for ch in row["runner_name"] if not ch.isdigit() and ch not in "- ").lower()
        return "away" if who and who in row.get("away", "").lower().replace(" ", "") else "home"
    return "home"
