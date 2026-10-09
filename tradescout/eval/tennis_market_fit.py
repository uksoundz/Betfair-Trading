"""Fit the tennis 'market-implied' corrections from historical ATP closing odds with results.

Source: the tennis-data.co.uk yearly files (Pinnacle closing odds PSW/PSL, set-by-set scores). The
independent-points Markov model, started from the de-vigged Pinnacle match probability, misprices set
outcomes in a consistent way (favourites win in straight sets more often, and come back from a lost first
set less often, than independence implies). This script fits a logistic correction
    P_true = sigmoid(a + b * logit(P_model))
per quantity and format on a development window, scores it on a later holdout window, then refits on
everything for use. The fitted numbers and the holdout evidence go to data/tennis_market_corrections.json.

    python -m tradescout.eval.tennis_market_fit /path/to/folder/with/2021.xlsx ... 2025.xlsx
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from ..config import REPO_ROOT
from ..tennis.markov import match_distribution, p_match_from, solve_serve_probs

OUT = REPO_ROOT / "data" / "tennis_market_corrections.json"
DEV, HOLD = (2021, 2023), (2024, 2025)


def _lg(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sg(x):
    return 1 / (1 + np.exp(-x))


def load(folder: Path) -> pd.DataFrame:
    frames = []
    for f in sorted(Path(folder).glob("*.xlsx")):
        x = pd.read_excel(f)
        x["year"] = pd.to_datetime(x["Date"]).dt.year
        frames.append(x)
    d = pd.concat(frames, ignore_index=True)
    d = d[(d["Comment"] == "Completed") & d["PSW"].notna() & d["PSL"].notna() & d["W1"].notna()].copy()
    iw, il = 1 / d.PSW, 1 / d.PSL
    pw = iw / (iw + il)
    d["fav_won"] = d.PSW < d.PSL
    d["p_fav"] = np.where(d.fav_won, pw, 1 - pw)
    d["bo"] = d["Best of"].fillna(3).astype(int)
    f1 = np.where(d.fav_won, d.W1, d.L1)
    u1 = np.where(d.fav_won, d.L1, d.W1)
    d["set1_fav"] = f1 > u1
    d["set1_clear"] = np.minimum(f1, u1) <= 3  # set 1 won 6-3 or wider
    lsets = d.Lsets.fillna(0)
    d["straight_fav"] = d.fav_won & (lsets == 0)
    d["straight_dog"] = (~d.fav_won) & (lsets == 0)
    d["surface"] = d.Surface.where(d.Surface.isin(["Hard", "Clay", "Grass"]), "Hard")
    return d


def model_columns(d: pd.DataFrame) -> pd.DataFrame:
    cache: dict = {}
    rows = []
    for p, bo, s in zip(d.p_fav, d.bo, d.surface):
        k = (round(float(p), 3), int(bo), s)
        if k not in cache:
            pa, pb = solve_serve_probs(k[0], k[1], s)
            need = k[1] // 2 + 1
            md = match_distribution(pa, pb, k[1])
            cache[k] = dict(
                m_lost=0.5 * (p_match_from(pa, pb, k[1], 0, 1, 0, 0, "a") + p_match_from(pa, pb, k[1], 0, 1, 0, 0, "b")),
                m_won=0.5 * (p_match_from(pa, pb, k[1], 1, 0, 0, 0, "a") + p_match_from(pa, pb, k[1], 1, 0, 0, 0, "b")),
                m_straight_fav=md["sets"][(need, 0)], m_straight_dog=md["sets"][(0, need)], m_set1=md["p_set1"])
        rows.append(cache[k])
    return d.join(pd.DataFrame(rows, index=d.index))


TARGETS = {  # name: (subset, outcome column, model column)
    "after_lost_set1": (lambda d: ~d.set1_fav, "fav_won", "m_lost"),
    # the first-set margin carries information the start-of-set-2 model state does not: a favourite beaten
    # 6-3 or wider comes back less often than one who lost 7-5 or in a tiebreak
    "after_lost_set1_clear": (lambda d: ~d.set1_fav & d.set1_clear, "fav_won", "m_lost"),
    "after_lost_set1_close": (lambda d: ~d.set1_fav & ~d.set1_clear, "fav_won", "m_lost"),
    "after_won_set1": (lambda d: d.set1_fav, "fav_won", "m_won"),
    "straight_fav": (lambda d: d.p_fav > 0, "straight_fav", "m_straight_fav"),
    "straight_dog": (lambda d: d.p_fav > 0, "straight_dog", "m_straight_dog"),
    "set1_fav": (lambda d: d.p_fav > 0, "set1_fav", "m_set1"),
}


def _fit(x: pd.DataFrame, y: str, m: str) -> np.ndarray:
    yy, mm = x[y].values.astype(float), _lg(x[m].values)
    def nll(w):
        q = np.clip(_sg(w[0] + w[1] * mm), 1e-9, 1 - 1e-9)
        return -np.mean(yy * np.log(q) + (1 - yy) * np.log(1 - q))
    return minimize(nll, [0.0, 1.0]).x


def _ll(x: pd.DataFrame, y: str, m: str, w) -> float:
    yy = x[y].values.astype(float)
    q = np.clip(_sg(w[0] + w[1] * _lg(x[m].values)), 1e-9, 1 - 1e-9)
    return float(-np.mean(yy * np.log(q) + (1 - yy) * np.log(1 - q)))


def fit(d: pd.DataFrame) -> dict:
    d = model_columns(d)
    out: dict = {"source": "tennis-data.co.uk ATP closing odds (Pinnacle) with set scores", "dev": list(DEV), "holdout": list(HOLD), "targets": {}}
    for name, (sub, y, m) in TARGETS.items():
        out["targets"][name] = {}
        for bo in (3, 5):
            g = d[sub(d) & (d.bo == bo)]
            dv = g[(g.year >= DEV[0]) & (g.year <= DEV[1])]
            hd = g[(g.year >= HOLD[0]) & (g.year <= HOLD[1])]
            w_dev = _fit(dv, y, m)
            ll_model, ll_corr = _ll(hd, y, m, [0.0, 1.0]), _ll(hd, y, m, w_dev)
            validated = ll_corr < ll_model
            w_all = _fit(g[(g.year >= DEV[0])], y, m)
            out["targets"][name][str(bo)] = {
                "a": float(w_all[0]) if validated else 0.0, "b": float(w_all[1]) if validated else 1.0, "validated": bool(validated),
                "n_dev": int(len(dv)), "n_holdout": int(len(hd)), "holdout_logloss_model": round(ll_model, 4), "holdout_logloss_corrected": round(ll_corr, 4),
                "holdout_mean_model": round(float(hd[m].mean()), 4), "holdout_mean_actual": round(float(hd[y].mean()), 4)}
    return out


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    res = fit(load(Path(argv[0])))
    OUT.write_text(json.dumps(res, indent=1))
    for name, v in res["targets"].items():
        for bo, r in v.items():
            print(f"{name:16s} best of {bo}: model {r['holdout_mean_model']:.3f} actual {r['holdout_mean_actual']:.3f} "
                  f"logloss {r['holdout_logloss_model']:.4f} -> {r['holdout_logloss_corrected']:.4f} {'used' if r['validated'] else 'NOT used (did not validate)'}")
    print(f"written {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
