"""Machine-readable in-play rules for a plan.

Each strategy states its in-play steps twice: as text for a person and as rules for the engine. A rule
is a plain dict so it can be stored with an armed job and shown back to the user:

    {"id": "goal", "when": <condition>, "do": <action>, "final": True, "text": "..."}

Conditions read the match state (minute, score, who scored first, sets) and live prices:
    goals_at_least n | goals_at_most n | minute_at_least m | minute_before m | first_goal by fav|dog
    set_won set by fav|dog | set_tiebreak set | set_won_easily set by max_games
    price_ratio_at_most leg ratio      (the leg's selection now backs at <= ratio x the entry price)
    price_not_worse_than_entry leg     (scale-in only when the price has moved our way or stayed)
    all of [...] | any of [...]

Actions act on one leg of the plan (its market and selection):
    green leg     hedge the leg's selection so profit or loss is equal whatever happens (also used to close at a loss)
    free_bet leg  lay off the original back stake: no loss if it fails, profit if it lands
    scale_in leg fraction   add the same bet, fraction of the plan's unit, at the price now on offer
    hold          nothing to do; the leg runs to the result
    enter leg side limit fraction valid_seconds
                  open the position in play (plans with no pre-match bet): a limit order at the plan's own value
                  limit (the most for a lay, the least for a back), sized as `fraction` of the unit (a liability for
                  a lay), left on the exchange for `valid_seconds` and then cancelled; whatever matched is held

`final` ends the job once the action has gone through. Rules fire at most once, in order.
"""
from __future__ import annotations

from typing import Any


def goals_at_least(n: int) -> dict:
    return {"t": "goals_at_least", "n": n}


def goals_at_most(n: int) -> dict:
    return {"t": "goals_at_most", "n": n}


def minute_at_least(m: float) -> dict:
    return {"t": "minute_at_least", "m": m}


def minute_before(m: float) -> dict:
    return {"t": "minute_before", "m": m}


def first_goal(by: str) -> dict:
    return {"t": "first_goal", "by": by}


def set_won(set_no: int, by: str) -> dict:
    return {"t": "set_won", "set": set_no, "by": by}


def set_tiebreak(set_no: int) -> dict:
    return {"t": "set_tiebreak", "set": set_no}


def set_won_easily(set_no: int, by: str, max_games: int) -> dict:
    return {"t": "set_won_easily", "set": set_no, "by": by, "max": max_games}


def price_ratio_at_most(leg: int, ratio: float) -> dict:
    return {"t": "price_ratio_at_most", "leg": leg, "ratio": ratio}


def price_not_worse_than_entry(leg: int) -> dict:
    return {"t": "price_not_worse_than_entry", "leg": leg}


def all_of(*c: dict) -> dict:
    return {"t": "all", "of": list(c)}


def any_of(*c: dict) -> dict:
    return {"t": "any", "of": list(c)}


def green(leg: int = 0) -> dict:
    return {"a": "green", "leg": leg}


def free_bet(leg: int = 0) -> dict:
    return {"a": "free_bet", "leg": leg}


def scale_in(leg: int, fraction: float) -> dict:
    return {"a": "scale_in", "leg": leg, "fraction": fraction}


def hold(why: str = "") -> dict:
    return {"a": "hold", "why": why} if why else {"a": "hold"}


def enter(leg: int, side: str, limit: float, fraction: float = 1.0, valid_seconds: float = 120.0) -> dict:
    return {"a": "enter", "leg": leg, "side": side, "limit": round(float(limit), 2), "fraction": fraction, "valid_seconds": valid_seconds}


def rule(rid: str, when: dict, do: dict, text: str, final: bool = False) -> dict:
    """A protective exit tied to the clock (e.g. "still 0-0 on 70': close") still fires when the score feed
    is down, because a missed goal-triggered exit would already have greened the position; firing late
    is the safe side. Rules that add risk (scale-in) never fire on an unknown score."""
    r = {"id": rid, "when": when, "do": do, "final": final, "text": text}
    r["fire_if_score_unknown"] = bool(do.get("a") == "green" and final and "clock" in needs(when))
    return r


# ------------------------------------------------------------------ evaluation
NEEDS_SCORE = {"goals_at_least", "goals_at_most", "first_goal", "set_won", "set_tiebreak", "set_won_easily"}
NEEDS_CLOCK = {"minute_at_least", "minute_before"}


def needs(cond: dict) -> set[str]:
    """Which feeds a condition depends on: 'score', 'clock', 'price'."""
    t = cond.get("t")
    if t in ("all", "any"):
        out: set[str] = set()
        for c in cond.get("of", []):
            out |= needs(c)
        return out
    if t in NEEDS_SCORE:
        return {"score"}
    if t in NEEDS_CLOCK:
        return {"clock"}
    return {"price"}


def evaluate(cond: dict, state: dict, assume_score: bool = False) -> bool | None:
    """True / False, or None when the information is not available (the rule then does not fire).
    state: minute, home, away (goals or sets), first_goal ('home'|'away'|None), fav ('home'|'away'),
    sets: [[home_games, away_games], ...] completed and current, prices: {leg: {"back", "lay", "entry", "side"}}."""
    t = cond.get("t")
    if t == "all":
        vals = [evaluate(c, state, assume_score) for c in cond["of"]]
        if any(v is False for v in vals):
            return False
        return None if any(v is None for v in vals) else True
    if t == "any":
        vals = [evaluate(c, state, assume_score) for c in cond["of"]]
        if any(v is True for v in vals):
            return True
        return None if any(v is None for v in vals) else False
    minute, home, away = state.get("minute"), state.get("home"), state.get("away")
    fav = state.get("fav", "home")
    if assume_score and t in NEEDS_SCORE and (home is None or away is None):
        return True
    if t == "minute_at_least":
        return None if minute is None else minute >= cond["m"]
    if t == "minute_before":
        return None if minute is None else minute < cond["m"]
    if t in ("goals_at_least", "goals_at_most"):
        if home is None or away is None:
            return None
        total = home + away
        return total >= cond["n"] if t == "goals_at_least" else total <= cond["n"]
    if t == "first_goal":
        if home is None or away is None:
            return None
        if home + away == 0:
            return False
        first = state.get("first_goal")
        if first is None:  # scorer of the first goal unknown: infer only from a one-goal lead
            if abs(home - away) != 1 or home + away != 1:
                return None
            first = "home" if home > away else "away"
        return (first == fav) == (cond["by"] == "fav")
    if t in ("set_won", "set_won_easily", "set_tiebreak"):
        i = cond["set"] - 1
        winners = state.get("set_winners")
        sets = state.get("sets")
        if t == "set_tiebreak":
            if sets is None:
                return None
            if i >= len(sets):
                return False
            h, a = sets[i]
            return h >= 6 and a >= 6
        if winners is None:
            return None
        if i >= len(winners):  # the set is not finished yet
            return False
        won = (winners[i] == fav) == (cond["by"] == "fav")
        if t == "set_won":
            return won
        if sets is None or i >= len(sets):
            return None
        return won and min(sets[i]) <= cond["max"]
    if t in ("price_ratio_at_most", "price_not_worse_than_entry"):
        p = (state.get("prices") or {}).get(cond["leg"])
        if not p:
            return None
        if t == "price_ratio_at_most":
            return None if not p.get("back") else p["back"] <= cond["ratio"] * p["entry"]
        now = p.get("back") if p["side"] == "back" else p.get("lay")
        if not now:
            return None
        return now >= p["entry"] if p["side"] == "back" else now <= p["entry"]
    return None


# ------------------------------------------------------------------ plain English
def describe_condition(c: dict, names: dict[str, Any]) -> str:
    t = c.get("t")
    fav, dog = names.get("fav", "the favourite"), names.get("dog", "the underdog")
    if t == "all":
        return " and ".join(describe_condition(x, names) for x in c["of"])
    if t == "any":
        return " or ".join(describe_condition(x, names) for x in c["of"])
    return {
        "goals_at_least": lambda: "the first goal goes in" if c.get("n") == 1 else f"there have been {c.get('n')} goals",
        "goals_at_most": lambda: "it is still 0-0" if c.get("n") == 0 else f"there have been no more than {c.get('n')} goals",
        "minute_at_least": lambda: f"the clock reaches {c.get('m'):g} minutes",
        "minute_before": lambda: f"before {c.get('m'):g} minutes",
        "first_goal": lambda: f"{fav if c.get('by') == 'fav' else dog} score first",
        "set_won": lambda: f"{fav if c.get('by') == 'fav' else dog} win set {c.get('set')}",
        "set_tiebreak": lambda: f"set {c.get('set')} reaches 6-6",
        "set_won_easily": lambda: f"{fav if c.get('by') == 'fav' else dog} win set {c.get('set')} conceding {c.get('max')} games or fewer",
        "price_ratio_at_most": lambda: f"the price has fallen to {c.get('ratio'):.0%} of the entry price or less",
        "price_not_worse_than_entry": lambda: "the price is no worse than at entry",
    }.get(t, lambda: t)()


def describe_action(a: dict, legs: list[dict]) -> str:
    leg = legs[a.get("leg", 0)] if legs and a.get("a") != "hold" and a.get("leg", 0) < len(legs) else {}
    what = f"{leg.get('runner_name', leg.get('selection', ''))} ({leg.get('market_label', leg.get('market', ''))})" if leg else ""
    kind = a.get("a")
    if kind == "green":
        opp = "back" if leg.get("side") == "lay" else "lay"
        return f"{opp} {what} at the best price on offer so the result is the same whatever happens (green up, or close for the smaller loss)"
    if kind == "free_bet":
        return f"lay {what} for the original stake: nothing lost if it fails, profit if it lands"
    if kind == "enter":
        lim = "or lower" if a.get("side") == "lay" else "or higher"
        risk = "liability" if a.get("side") == "lay" else "stake"
        return (f"{a.get('side', '')} {what} at {a.get('limit', 0):.2f} {lim} with {a.get('fraction', 1):.0%} of the plan {risk}; "
                f"the order stays up for {a.get('valid_seconds', 120):.0f} seconds, then any unmatched part is cancelled and what matched runs to the result")
    if kind == "scale_in":
        return f"{leg.get('side', '')} {what} again with {a.get('fraction', 0):.0%} of the plan stake at the price on offer"
    return a.get("why") or "do nothing; let it run to the result"


def describe(r: dict, legs: list[dict], names: dict[str, Any]) -> str:
    return f"When {describe_condition(r['when'], names)}: {describe_action(r['do'], legs)}." + (" Then the trade is finished." if r.get("final") else "")
