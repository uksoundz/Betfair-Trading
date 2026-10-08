"""Plain-English match summary generated from the model numbers. Deterministic: same numbers,
same words. This is the 'reasoning' a trader reads before looking at any strategy."""
from __future__ import annotations

from ..models import MatchForecast


def _pct(p: float) -> str:
    return f"{p:.0%}"


def match_summary(fc: MatchForecast) -> list[str]:
    f = fc.fixture
    fav = f.home if fc.favourite == "home" else f.away
    dog = f.away if fc.favourite == "home" else f.home
    p_fav = max(fc.p_home, fc.p_away)
    gap = abs(fc.p_home - fc.p_away)
    out: list[str] = []

    if p_fav >= 0.65:
        out.append(f"{fav} are strong favourites at {_pct(p_fav)} (fair price {1/p_fav:.2f}). The draw is only {_pct(fc.p_draw)} likely.")
    elif gap >= 0.12:
        out.append(f"{fav} are favourites at {_pct(p_fav)} against {dog} at {_pct(min(fc.p_home, fc.p_away))}, with the draw at {_pct(fc.p_draw)}.")
    else:
        out.append(f"This is close to a coin flip: {f.home} {_pct(fc.p_home)}, draw {_pct(fc.p_draw)}, {f.away} {_pct(fc.p_away)}. Equalisers are likely, so match-odds trades need insurance.")

    tg = fc.total_xg
    if tg >= 3.0:
        out.append(f"It projects as a high-scoring game: {fc.home_xg:.1f} - {fc.away_xg:.1f} expected goals, Over 2.5 at {_pct(fc.p_over[2.5])} and both teams to score at {_pct(fc.p_btts)}. Goals strategies suit it.")
    elif tg >= 2.3:
        out.append(f"Goals should flow at a normal rate: {fc.home_xg:.1f} - {fc.away_xg:.1f} expected, Over 2.5 at {_pct(fc.p_over[2.5])}, both to score {_pct(fc.p_btts)}. Neither overs nor unders has an obvious edge here.")
    else:
        out.append(f"It projects as a tight, low-scoring game: {fc.home_xg:.1f} - {fc.away_xg:.1f} expected goals, Under 2.5 at {_pct(1-fc.p_over[2.5])} and a {_pct(fc.p_cs.get('0-0', 0))} chance of 0-0. Unders and patience suit it; lay-the-draw does not.")

    out.append(f"A goal before half-time is {_pct(fc.p_goal_before[45])} likely and before 70 minutes {_pct(fc.p_goal_before[70])}. {fav} score first {_pct(fc.p_fav_scores_first)} of the time.")

    top = sorted(fc.p_cs.items(), key=lambda kv: -kv[1])[:3]
    out.append("Most likely scores: " + ", ".join(f"{s} ({p:.0%})" for s, p in top) + ".")

    hs, as_ = fc.home_strength, fc.away_strength
    bits = []
    if hs.attack > 0.25:
        bits.append(f"{f.home} have an above-average attack")
    if hs.defence < -0.25:
        bits.append(f"{f.home} defend well")
    if hs.defence > 0.2:
        bits.append(f"{f.home} concede a lot")
    if as_.attack > 0.25:
        bits.append(f"{f.away} have an above-average attack")
    if as_.defence < -0.25:
        bits.append(f"{f.away} defend well")
    if as_.defence > 0.2:
        bits.append(f"{f.away} concede a lot")
    if bits:
        out.append("Form ratings: " + "; ".join(bits) + ".")
    if fc.confidence < 0.6:
        out.append("Caution: one side has little recent data (new to the league or early season), so these numbers are less reliable than usual.")
    for n in fc.notes:
        out.append(n + ".")
    return out


def idea_verdict(hit: float, roi: float, edge: float | None, score: float, decision: str = "RESEARCH", ev_cons: float | None = None) -> str:
    if decision == "TRADE":
        head = f"TRADE. Conservative net edge {ev_cons:+.1%} per unit risked after commission." if ev_cons is not None else "TRADE."
    elif decision == "NO TRADE":
        head = "NO TRADE: no proven advantage at the current exchange price" + (f" (conservative edge {ev_cons:+.1%})." if ev_cons is not None else ".")
    else:
        head = "RESEARCH ONLY: no exchange price, so no advantage can be claimed."
    tail = f" The plan pays off about {hit:.0%} of the time; modelled return {roi:+.1%} per unit risked."
    return head + tail
