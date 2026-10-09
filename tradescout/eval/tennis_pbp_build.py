"""Grand Slam point-by-point (Sackmann, 2011-2024) joined to Pinnacle closing odds: one row per point (before it is played)
with the match state seen from the favourite, the pre-match price and the independent-points serve probabilities.

    python -m tradescout.eval.tennis_pbp_build SRC OUT.pkl

SRC holds sack/slam_pointbypoint/*.csv (Sackmann's slam point-by-point, e.g. the tennis-sackmann-archive mirror),
mlt/df_atp.csv and mlt/df_wta.csv (tennis-data.co.uk odds to 2019) and optionally odds/*.xlsx (ATP 2020-25).
Research only: used by tennis_pbp_trades.py to test break-point, slow-starter and lay-the-leader trades."""
import glob
import os
import re
import sys
import unicodedata
from functools import lru_cache

import numpy as np
sys.setrecursionlimit(20000)
import pandas as pd

from ..tennis.markov import p_game, p_tiebreak, solve_serve_probs  # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else "."
OUT = sys.argv[2] if len(sys.argv) > 2 else "pbp_states.pkl"
SLAM = {"ausopen": ("Australian Open", "Hard"), "frenchopen": ("French Open", "Clay"), "wimbledon": ("Wimbledon", "Grass"), "usopen": ("US Open", "Hard")}


def fold(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", " ", s).split()


def odds_surname(name):
    toks = str(name).replace(".", " . ").split()
    out = [t for t in toks if not (len(t.replace(".", "")) <= 2 and t[0].isupper() and (t.endswith(".") or len(t) == 1)) and t != "."]
    return fold(" ".join(out))


def load_odds():
    frames = []
    for f, tour in (("mlt/df_atp.csv", "atp"), ("mlt/df_wta.csv", "wta")):
        x = pd.read_csv(os.path.join(SRC, f), low_memory=False)
        x["tour"] = tour
        frames.append(x)
    for f in sorted(glob.glob(os.path.join(SRC, "odds", "*.xlsx"))):
        x = pd.read_excel(f)
        x["tour"] = "atp"
        frames.append(x)
    d = pd.concat(frames, ignore_index=True)
    d["year"] = pd.to_datetime(d.Date, errors="coerce").dt.year
    d = d[d.Tournament.isin([v[0] for v in SLAM.values()]) & d.PSW.notna() & d.PSL.notna()]
    d = d.drop_duplicates(subset=["year", "Tournament", "Winner", "Loser", "tour"])
    return d


def match_odds(odds, year, slam, tour, p1, p2):
    g = odds[(odds.year == year) & (odds.Tournament == SLAM[slam][0]) & (odds.tour == tour)]
    f1, f2 = fold(p1), fold(p2)

    def hit(sur, full):
        return bool(sur) and full[-len(sur):] == sur

    for _, r in g.iterrows():
        sw, sl = odds_surname(r.Winner), odds_surname(r.Loser)
        if hit(sw, f1) and hit(sl, f2):
            return r, 1
        if hit(sw, f2) and hit(sl, f1):
            return r, 2
    return None, None


@lru_cache(maxsize=None)
def hold_from(p, s, r):
    """Server (point prob p) wins the game from points (s, r)."""
    if s >= 4 and s - r >= 2:
        return 1.0
    if r >= 4 and r - s >= 2:
        return 0.0
    if s >= 3 and r >= 3:
        if s == r:
            return p * p / (p * p + (1 - p) * (1 - p))
        return p + (1 - p) * hold_from(p, 3, 3) if s > r else p * hold_from(p, 3, 3)
    return p * hold_from(p, s + 1, r) + (1 - p) * hold_from(p, s, r + 1)


@lru_cache(maxsize=256)
def tables(pa, pb, bo):
    """V(sa, sb, ga, gb, srv): P(A wins) at the start of a game with srv ('A'|'B') serving."""
    need = bo // 2 + 1
    ha, hb = p_game(pa), p_game(pb)
    tb_a, tb_b = p_tiebreak(pa, pb), 1 - p_tiebreak(pb, pa)
    memo = {}

    def V(sa, sb, ga, gb, srv):
        if sa >= need and sa > sb:
            return 1.0
        if sb >= need:
            return 0.0
        if ga >= 6 and gb >= 6 and (ga, gb) != (6, 6):  # long final set without a tiebreak: approximate from 5-5 / 6-5
            m = min(ga, gb) - 5
            ga, gb = ga - m, gb - m
        k = (sa, sb, ga, gb, srv)
        if k in memo:
            return memo[k]
        oth = "B" if srv == "A" else "A"
        if ga == 6 and gb == 6:
            pt = tb_a if srv == "A" else tb_b
            v = pt * V(sa + 1, sb, 0, 0, oth) + (1 - pt) * V(sa, sb + 1, 0, 0, oth)
        else:
            h = ha if srv == "A" else hb
            win_a = h if srv == "A" else 1 - h
            v = win_a * after(sa, sb, ga + 1, gb, srv) + (1 - win_a) * after(sa, sb, ga, gb + 1, srv)
        memo[k] = v
        return v

    def after(sa, sb, ga, gb, served):
        oth = "B" if served == "A" else "A"
        if (ga >= 6 and ga - gb >= 2) or ga == 7:
            return V(sa + 1, sb, 0, 0, oth)
        if (gb >= 6 and gb - ga >= 2) or gb == 7:
            return V(sa, sb + 1, 0, 0, oth)
        return V(sa, sb, ga, gb, oth)

    return V, after


def p_point_state(pa, pb, bo, sa, sb, ga, gb, srv, s_pts, r_pts):
    """P(A wins the match) before a point: sets, games, server, points (server's, receiver's) in a normal game."""
    V, after = tables(pa, pb, bo)
    p = pa if srv == "A" else pb
    h = hold_from(round(p, 4), s_pts, r_pts)
    if srv == "A":
        return h * after(sa, sb, ga + 1, gb, "A") + (1 - h) * after(sa, sb, ga, gb + 1, "A")
    return h * after(sa, sb, ga, gb + 1, "B") + (1 - h) * after(sa, sb, ga + 1, gb, "B")


def main():
    odds = load_odds()
    rows = []
    files = sorted(glob.glob(os.path.join(SRC, "sack/slam_pointbypoint/*-matches.csv")))
    for fm in files:
        year, slam = os.path.basename(fm).split("-")[:2]
        year = int(year)
        if slam not in SLAM:
            continue
        ms = pd.read_csv(fm)
        pts = pd.read_csv(fm.replace("-matches", "-points"), low_memory=False)
        pts = pts[pd.to_numeric(pts.PointWinner, errors="coerce").isin([1, 2])]
        by = dict(tuple(pts.groupby("match_id")))
        for _, m in ms.iterrows():
            mn = str(m.match_num).strip().upper()
            if mn.isdigit():
                tour = "atp" if int(mn) < 2000 else "wta"
            elif mn.startswith("MS"):
                tour = "atp"
            elif mn.startswith("WS"):
                tour = "wta"
            else:
                continue  # doubles, juniors, wheelchair
            if tour == "wta" and year > 2019:
                continue  # no women's odds after 2019
            if m.match_id not in by:
                continue
            r, order = match_odds(odds, year, slam, tour, m.player1, m.player2)
            if r is None:
                continue
            iw, il = 1 / r.PSW, 1 / r.PSL
            pw = iw / (iw + il)
            p1 = pw if order == 1 else 1 - pw   # P(player1 wins)
            bo = 5 if tour == "atp" else 3
            fav = 1 if p1 >= 0.5 else 2
            p_fav = max(p1, 1 - p1)
            pf, pd_ = solve_serve_probs(round(p_fav, 3), bo, SLAM[slam][1], tour)  # A = favourite
            winner = 1 if order == 1 else 2  # odds row: Winner is player1 when order == 1
            g = by[m.match_id].copy()
            g["pn"] = pd.to_numeric(g.PointNumber, errors="coerce")
            g = g.sort_values("pn")
            sets = [0, 0]
            games = [0, 0]
            pts_ = [0, 0]  # points in the current game, by player
            set1_winner = None
            tb = False
            for _, p in g.iterrows():
                srv = int(p.PointServer)
                if srv not in (1, 2):
                    continue
                rcv = 3 - srv
                tb = games[0] == 6 and games[1] == 6
                if not tb:
                    # state before this point, from the favourite's side (A = favourite)
                    fa = fav
                    sa, sb = sets[fa - 1], sets[2 - fa]
                    ga, gb = games[fa - 1], games[2 - fa]
                    srvAB = "A" if srv == fa else "B"
                    s_pts, r_pts = pts_[srv - 1], pts_[rcv - 1]
                    rows.append((m.match_id, tour, year, slam, bo, p_fav, pf, pd_, int(winner == fav), sa, sb, ga, gb, srvAB, s_pts, r_pts,
                                 len(g), set1_winner))
                w = int(p.PointWinner)
                pts_[w - 1] += 1
                gw = int(p.GameWinner) if str(p.GameWinner).isdigit() else 0
                sw = int(p.SetWinner) if str(p.SetWinner).isdigit() else 0
                if gw in (1, 2):
                    games[gw - 1] += 1
                    pts_ = [0, 0]
                if sw in (1, 2):
                    sets[sw - 1] += 1
                    if set1_winner is None:
                        set1_winner = "A" if sw == fav else "B"
                    games = [0, 0]
                    pts_ = [0, 0]
    cols = ["match_id", "tour", "year", "slam", "bo", "p_fav", "pa", "pb", "fav_won", "sa", "sb", "ga", "gb", "srv", "s_pts", "r_pts", "n_points", "set1_winner"]
    d = pd.DataFrame(rows, columns=cols)
    d.to_pickle(OUT)
    print(d.groupby("tour").match_id.nunique(), len(d))


if __name__ == "__main__":
    main()
