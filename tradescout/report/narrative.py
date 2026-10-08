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


def idea_verdict(hit: float, roi: float, edge: float | None, score: float) -> str:
    if score >= 54:
        head = "Strong candidate."
    elif score >= 50:
        head = "Good trade at the right price."
    elif score >= 46:
        head = "Fair. Only enter at the price shown or better."
    else:
        head = "Leave it."
    tail = f" The plan pays off about {hit:.0%} of the time for an expected {roi:+.1%} return per unit risked"
    if edge is not None:
        tail += f", and the exchange price is {'better' if edge > 0 else 'worse'} than the model's fair price by {abs(edge):.1%}"
    return head + tail + "."
