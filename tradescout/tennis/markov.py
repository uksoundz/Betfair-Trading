"""Point-to-match Markov model (Barnett and Clarke / O'Malley).

Two numbers drive everything: pa and pb, the probability each player wins a point on their own
serve. From them: a service game, a tiebreak, a set (with its game score), a best-of-3 or best-of-5
match, the distribution of total games, and the match-win probability from any in-play state
(sets, games, who serves). That last part is what prices the exits in the trading plans.

Only pa and pb are unknown. `solve_serve_probs` finds them from a match-win probability (from Elo)
and the tour's average serve-point rate, so every market is consistent with one view of the match.
"""
from __future__ import annotations

from functools import lru_cache

TOUR_SERVE_AVG = {"Hard": 0.64, "Clay": 0.62, "Grass": 0.66, "Carpet": 0.65}


# ----------------------------------------------------------------------------- game / tiebreak
@lru_cache(maxsize=4096)
def p_game(p: float) -> float:
    """Server with point probability p wins the game."""
    q = 1 - p
    return p ** 4 * (1 + 4 * q + 10 * q * q) + 20 * p ** 5 * q ** 3 / (1 - 2 * p * q)


@lru_cache(maxsize=4096)
def p_tiebreak(pa: float, pb: float) -> float:
    """A wins a tiebreak where A serves the first point and serve alternates every two points."""
    # states (a, b) points; serving pattern: point index k (0-based): A serves k==0, then pairs
    def server(k: int) -> str:
        if k == 0:
            return "A"
        return "A" if ((k - 1) // 2) % 2 == 1 else "B"

    @lru_cache(maxsize=None)
    def win(a: int, b: int) -> float:
        if a >= 7 and a - b >= 2:
            return 1.0
        if b >= 7 and b - a >= 2:
            return 0.0
        if a >= 6 and b >= 6:
            # from 6-6 the next two points are one on each serve; A needs both to lead by 2
            r = pa * (1 - pb)
            s = (1 - pa) * pb
            return r / (r + s)
        k = a + b
        p = pa if server(k) == "A" else 1 - pb  # probability A wins this point
        return p * win(a + 1, b) + (1 - p) * win(a, b + 1)

    return win(0, 0)


# ----------------------------------------------------------------------------- set
@lru_cache(maxsize=4096)
def set_distribution(pa: float, pb: float, first_server: str) -> dict:
    """Distribution over set outcomes. Returns {(winner, games_winner, games_loser): prob}.
    The next set's first server is whoever did not serve the last game, which the caller can
    derive from the total number of games (parity)."""
    ga, gb = p_game(pa), p_game(pb)

    @lru_cache(maxsize=None)
    def dist(a: int, b: int, server: str) -> dict:
        if a >= 6 and a - b >= 2 or a == 7:
            return {("A", a, b): 1.0}
        if b >= 6 and b - a >= 2 or b == 7:
            return {("B", b, a): 1.0}
        if a == 6 and b == 6:
            pt = p_tiebreak(pa, pb) if server == "A" else 1 - p_tiebreak(pb, pa)
            return {("A", 7, 6): pt, ("B", 7, 6): 1 - pt}
        p_hold = ga if server == "A" else gb
        nxt = "B" if server == "A" else "A"
        out: dict = {}
        if server == "A":
            branches = [(p_hold, a + 1, b), (1 - p_hold, a, b + 1)]
        else:
            branches = [(p_hold, a, b + 1), (1 - p_hold, a + 1, b)]
        for p, na, nb in branches:
            for k, v in dist(na, nb, nxt).items():
                out[k] = out.get(k, 0.0) + p * v
        return out

    return dist(0, 0, first_server)


def p_set(pa: float, pb: float, first_server: str = "A") -> float:
    return sum(v for (w, _, _), v in set_distribution(pa, pb, first_server).items() if w == "A")


# ----------------------------------------------------------------------------- match
@lru_cache(maxsize=4096)
def match_distribution(pa: float, pb: float, best_of: int = 3, first_server: str = "A") -> dict:
    """{"p_match": P(A wins), "sets": {(sa, sb): prob}, "games": {n: prob}, "p_set1": P(A wins set 1)}"""
    need = best_of // 2 + 1

    @lru_cache(maxsize=None)
    def rec(sa: int, sb: int, server: str, games: int) -> dict:
        # returns {(winner, sets_a, sets_b, total_games): prob}
        if sa == need:
            return {("A", sa, sb, games): 1.0}
        if sb == need:
            return {("B", sa, sb, games): 1.0}
        out: dict = {}
        for (w, gw, gl), p in set_distribution(pa, pb, server).items():
            total = gw + gl
            # the player who served the last game does not serve first in the next set: with
            # `total` games alternating from `server`, the last game is served by `server` when
            # total is odd, so the next set starts with the other player.
            nxt_server = ("B" if server == "A" else "A") if total % 2 == 1 else server
            branch = rec(sa + (w == "A"), sb + (w == "B"), nxt_server, games + total)
            for k, v in branch.items():
                out[k] = out.get(k, 0.0) + p * v
        return out

    full = rec(0, 0, first_server, 0)
    sets: dict = {}
    games: dict = {}
    p_match = 0.0
    for (w, sa, sb, g), p in full.items():
        sets[(sa, sb)] = sets.get((sa, sb), 0.0) + p
        games[g] = games.get(g, 0.0) + p
        if w == "A":
            p_match += p
    p_set1 = p_set(pa, pb, first_server)
    return {"p_match": p_match, "sets": sets, "games": dict(sorted(games.items())), "p_set1": p_set1}


def p_match_from(pa: float, pb: float, best_of: int, sets_a: int, sets_b: int, games_a: int, games_b: int, server: str) -> float:
    """P(A wins the match) from an in-play state: sets won, games in the current set, who serves next."""
    need = best_of // 2 + 1
    if sets_a == need:
        return 1.0
    if sets_b == need:
        return 0.0
    ga, gb = p_game(pa), p_game(pb)

    @lru_cache(maxsize=None)
    def set_from(a: int, b: int, srv: str) -> dict:
        if a >= 6 and a - b >= 2 or a == 7:
            return {("A", a + b): 1.0}
        if b >= 6 and b - a >= 2 or b == 7:
            return {("B", a + b): 1.0}
        if a == 6 and b == 6:
            pt = p_tiebreak(pa, pb) if srv == "A" else 1 - p_tiebreak(pb, pa)
            return {("A", 13): pt, ("B", 13): 1 - pt}
        p_hold = ga if srv == "A" else gb
        nxt = "B" if srv == "A" else "A"
        branches = [(p_hold, a + 1, b), (1 - p_hold, a, b + 1)] if srv == "A" else [(p_hold, a, b + 1), (1 - p_hold, a + 1, b)]
        out: dict = {}
        for p, na, nb in branches:
            for k, v in set_from(na, nb, nxt).items():
                out[k] = out.get(k, 0.0) + p * v
        return out

    @lru_cache(maxsize=None)
    def rest(sa: int, sb: int, first: str) -> float:
        if sa == need:
            return 1.0
        if sb == need:
            return 0.0
        total = 0.0
        for (w, gw, gl), p in set_distribution(pa, pb, first).items():
            n = gw + gl
            nxt = ("B" if first == "A" else "A") if n % 2 == 1 else first
            total += p * rest(sa + (w == "A"), sb + (w == "B"), nxt)
        return total

    # finish the current set from the given game state, then play out the remaining sets
    total = 0.0
    games_so_far = games_a + games_b
    # who served the first game of this set: server of game k alternates, so derive from parity
    for (w, n_games_in_set), p in set_from(games_a, games_b, server).items():
        # first server of the next set is the one who did not serve the last game of this set
        last_server_is_current = (n_games_in_set - games_so_far) % 2 == 1  # odd remaining games -> `server` serves last
        last_server = server if last_server_is_current else ("B" if server == "A" else "A")
        nxt_first = "B" if last_server == "A" else "A"
        total += p * rest(sets_a + (w == "A"), sets_b + (w == "B"), nxt_first)
    return total


def p_first_break(pa: float, pb: float, first_server: str = "A") -> tuple[float, float]:
    """(P(A breaks first), P(B breaks first)) within a set. A 'break' is winning a game on the
    opponent's serve. Ignores the (rare) 6-6 tiebreak with no breaks."""
    ga, gb = p_game(pa), p_game(pb)
    pA = pB = 0.0
    srv = first_server
    alive = 1.0
    for _ in range(12):
        if srv == "A":
            pB += alive * (1 - ga)
            alive *= ga
            srv = "B"
        else:
            pA += alive * (1 - gb)
            alive *= gb
            srv = "A"
    return pA, pB


def solve_serve_probs(p_match: float, best_of: int, surface: str = "Hard") -> tuple[float, float]:
    """Find (pa, pb) with pa + pb = 2 * tour average such that the Markov match probability equals p_match."""
    avg = TOUR_SERVE_AVG.get(surface, 0.64)
    lo, hi = -0.25, 0.25
    for _ in range(40):
        mid = (lo + hi) / 2
        pa, pb = avg + mid, avg - mid
        pm = match_distribution(round(pa, 4), round(pb, 4), best_of, "A")["p_match"]
        if pm < p_match:
            lo = mid
        else:
            hi = mid
    d = (lo + hi) / 2
    return round(avg + d, 4), round(avg - d, 4)
