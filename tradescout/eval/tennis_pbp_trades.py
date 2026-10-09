"""Point-level tennis trades against an exchange that prices like the independent-points model (from the pre-match
Pinnacle price). Dev = 2011-2016, holdout = 2017-2024 (women 2017-2019). Costs: 1% spread each way, 5% commission
on winning trades (SPREAD and COMM environment variables override). P/L per unit stake (backs) or per unit liability (lays).

    python -m tradescout.eval.tennis_pbp_trades pbp_states.pkl
"""
import sys

import numpy as np
import pandas as pd

from .tennis_pbp_build import hold_from, p_point_state, tables  # noqa: E402

import os
C, SPREAD = float(os.getenv("COMM", "0.05")), float(os.getenv("SPREAD", "0.01"))
d = pd.read_pickle(sys.argv[1] if len(sys.argv) > 1 else "pbp_states.pkl")
d["gid"] = list(zip(d.match_id, d.sa, d.sb, d.ga, d.gb))
d["period"] = np.where(d.year <= 2016, "dev", "hold")

# ---- game outcomes: did the server hold? ------------------------------------------------------------------
first = d.groupby("gid", sort=False).head(1).reset_index(drop=True)
first["next_match"] = first.match_id.shift(-1)
nxt = first[["sa", "sb", "ga", "gb"]].shift(-1)
same = first.next_match == first.match_id
a_won_game = np.where(same, (nxt.ga > first.ga) | (nxt.sa > first.sa), first.fav_won == 1)
first["a_won_game"] = a_won_game.astype(bool)
first["held"] = np.where(first.srv == "A", first.a_won_game, ~first.a_won_game)
held = dict(zip(first.gid, first.held))
d["held"] = d.gid.map(held)
deuce_games = set(d[(d.s_pts >= 3) & (d.s_pts == d.r_pts)].gid)
d["deuce"] = d.gid.isin(deuce_games)


def pA(r, sa, sb, ga, gb, srv, s, rr):
    return p_point_state(r.pa, r.pb, r.bo, sa, sb, ga, gb, srv, s, rr)


def start_of_game(r, sa, sb, ga, gb, srv):
    V, after = tables(r.pa, r.pb, r.bo)
    return V(sa, sb, ga, gb, srv)


def green(price_in, price_out, side):
    price_in, price_out = min(max(price_in, 1.002), 500.0), min(max(price_out, 1.002), 500.0)
    """Back at price_in then lay at price_out (or lay then back), equal profit, per unit stake/liability, after costs."""
    if side == "back":
        bi, lo = price_in * (1 - SPREAD), price_out * (1 + SPREAD)
        pnl = bi / lo - 1
    else:
        li, bo = price_in * (1 + SPREAD), price_out * (1 - SPREAD)
        pnl = (1 - li / bo) / (li - 1)  # per unit liability
    return pnl * (1 - C) if pnl > 0 else pnl


def summarise(name, rows):
    if not len(rows):
        print(f"{name}: none")
        return
    x = pd.DataFrame(rows)
    for (tour, per), g in x.groupby(["tour", "period"]):
        se = g.pnl.std() / np.sqrt(len(g))
        extra = f" actual {g.y.mean():.3f} vs iid {g.p.mean():.3f}" if "y" in g else ""
        print(f"{name:42s} {tour} {per:4s} n={len(g):5d} P/L {g.pnl.mean():+.4f} ±{1.96*se:.4f}{extra}")


# ---- 1. break-point scalp: back the receiver at 15-40 / 0-40 / 30-40, green at the break, hedge at deuce --------
for label, (sp, rp) in (("15-40", (1, 3)), ("0-40", (0, 3)), ("30-40", (2, 3))):
    rows = []
    sel = d[(d.s_pts == sp) & (d.r_pts == rp)].groupby("gid", sort=False).head(1)
    for r in sel.itertuples():
        srv, rcv = r.srv, ("B" if r.srv == "A" else "A")
        p_a_now = pA(r, r.sa, r.sb, r.ga, r.gb, srv, sp, rp)
        p_rcv_now = p_a_now if rcv == "A" else 1 - p_a_now
        # iid probability that the receiver breaks before deuce
        p_s = r.pa if srv == "A" else r.pb
        need = 3 - sp
        p_break_iid = 1 - p_s ** need
        broke = (not r.held) and not r.deuce
        if broke:
            ga2, gb2 = (r.ga + 1, r.gb) if rcv == "A" else (r.ga, r.gb + 1)
            V, after = tables(r.pa, r.pb, r.bo)
            p_a_out = after(r.sa, r.sb, ga2, gb2, srv)
        else:  # deuce reached (40-40): hedge
            p_a_out = pA(r, r.sa, r.sb, r.ga, r.gb, srv, 3, 3)
        p_rcv_out = p_a_out if rcv == "A" else 1 - p_a_out
        pnl = green(1 / max(p_rcv_now, 1e-3), 1 / max(p_rcv_out, 1e-3), "back")
        rows.append({"tour": r.tour, "period": r.period, "pnl": pnl, "y": float(broke), "p": p_break_iid, "rcv_is_fav": rcv == "A"})
    summarise(f"back receiver at {label}", rows)
    summarise(f"  ...only when the receiver is the favourite", [x for x in rows if x["rcv_is_fav"]])
    summarise(f"  ...only when the receiver is the underdog", [x for x in rows if not x["rcv_is_fav"]])

# ---- 2. slow starter: favourite broken early in set 1 -> back the favourite --------------------------------------
rows_hold, rows_trade = [], []
g0 = first[(first.sa == 0) & (first.sb == 0)]
for mid, gm in g0.groupby("match_id", sort=False):
    gm = gm.reset_index(drop=True)
    for i, r in gm.iterrows():
        if r.ga + r.gb > 5:
            break
        if r.srv == "A" and not r.held:  # favourite broken
            ga2, gb2 = r.ga, r.gb + 1
            if (gb2 >= 6 and gb2 - ga2 >= 2):
                break
            V, after = tables(r.pa, r.pb, r.bo)
            p_now = after(0, 0, ga2, gb2, "A")
            price = 1 / max(p_now, 1e-3)
            y = r.fav_won
            hold_pnl = (price * (1 - SPREAD) - 1) * (1 - C) if y else -1.0
            rows_hold.append({"tour": r.tour, "period": "dev" if r.year <= 2016 else "hold", "pnl": hold_pnl, "y": y, "p": p_now})
            # trade: green when the favourite is back on serve (breaks back) or at the end of set 1
            rest = gm.iloc[i + 1:]
            p_out = None
            for _, q in rest.iterrows():
                if q.srv == "B" and not q.held:
                    ga3, gb3 = q.ga + 1, q.gb
                    p_out = after(0, 0, ga3, gb3, "B")
                    break
            if p_out is None:  # no break back in set 1: exit at the start of set 2 at the set-1 result
                s2 = d[(d.match_id == mid) & (d.sa + d.sb == 1)].head(1)
                if len(s2):
                    q = s2.iloc[0]
                    p_out = V(int(q.sa), int(q.sb), 0, 0, q.srv)
            if p_out:
                rows_trade.append({"tour": r.tour, "period": "dev" if r.year <= 2016 else "hold", "pnl": green(price, 1 / max(p_out, 1e-3), "back")})
            break
summarise("slow starter: back fav after early set-1 break, hold", rows_hold)
summarise("slow starter: back fav, green on break-back/set end", rows_trade)

# ---- 3. lay the leader: favourite won set 1 and breaks early in set 2 -> lay the favourite -------------------------
rows_hold, rows_trade = [], []
g1 = first[(first.sa == 1) & (first.sb == 0)]
for mid, gm in g1.groupby("match_id", sort=False):
    gm = gm.reset_index(drop=True)
    for i, r in gm.iterrows():
        if r.ga + r.gb > 5:
            break
        if r.srv == "B" and not r.held:  # favourite breaks in set 2 after winning set 1
            ga2, gb2 = r.ga + 1, r.gb
            if ga2 >= 6:
                break
            V, after = tables(r.pa, r.pb, r.bo)
            p_now = after(1, 0, ga2, gb2, "B")
            price = 1 / max(p_now, 1e-3)
            y = r.fav_won
            hold_pnl = -1.0 if y else (1 - C) / (price * (1 + SPREAD) - 1)
            rows_hold.append({"tour": r.tour, "period": "dev" if r.year <= 2016 else "hold", "pnl": hold_pnl, "y": y, "p": p_now})
            rest = gm.iloc[i + 1:]
            p_out = None
            for _, q in rest.iterrows():
                if q.srv == "A" and not q.held:  # dog breaks back
                    p_out = after(1, 0, q.ga, q.gb + 1, "A")
                    break
            if p_out is None:
                s3 = d[(d.match_id == mid) & (d.sa + d.sb == 2)].head(1)
                if len(s3):
                    q = s3.iloc[0]
                    p_out = V(int(q.sa), int(q.sb), 0, 0, q.srv)
                else:
                    p_out = 0.999 if y else 0.001
            # lay at price then back at 1/p_out: per unit liability
            li, bo_ = price * (1 + SPREAD), (1 / max(p_out, 1e-3)) * (1 - SPREAD)
            pnl = (bo_ - li) / bo_ / (li - 1)
            rows_trade.append({"tour": r.tour, "period": "dev" if r.year <= 2016 else "hold", "pnl": pnl * (1 - C) if pnl > 0 else pnl})
            break
summarise("lay the leader: lay fav (won set 1, breaks in set 2), hold", rows_hold)
summarise("lay the leader: lay fav, back back on break-back/set end", rows_trade)
