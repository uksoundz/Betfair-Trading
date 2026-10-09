"""Market-implied tennis probabilities, corrected for what the point model gets wrong.

Pre-match match odds on the big exchange markets are sharp: our own Elo does not beat them. What the
independent-points model does get wrong is the *structure* of a match given that price: favourites win in
straight sets more often, and come back from a lost first set less often (above all after losing it 6-3 or
wider), than independent points imply. eval/tennis_market_fit.py fits a logistic correction
    P = sigmoid(a + b * logit(P_markov))
for each of those quantities on 2021-23 ATP closing prices and keeps it only when it beats the plain model
on 2024-25. This module applies those corrections to the probability the exchange's match-odds market
implies, so every set-level number is anchored to the market, not to our Elo.

The corrected set-score probabilities are made consistent with the match price: P(fav 2-0) + P(fav 2-1)
always equals the match-odds probability.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from ..config import REPO_ROOT
from .markov import match_distribution, p_match_from, solve_serve_probs

DEFAULT_PATH = REPO_ROOT / "data" / "tennis_market_corrections.json"


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("targets", {})
    except Exception:
        return {}


def correction(name: str, best_of: int, path: Path | str = DEFAULT_PATH) -> Optional[dict]:
    """The fitted correction for a quantity and format, or None when it is missing or did not validate."""
    c = _load(str(path)).get(name, {}).get(str(best_of))
    return c if c and c.get("validated") else None


def corrected(name: str, best_of: int, p_model: float, path: Path | str = DEFAULT_PATH) -> tuple[float, bool]:
    """(probability, corrected?) - the model value is returned unchanged when there is no validated correction."""
    c = correction(name, best_of, path)
    if c is None:
        return p_model, False
    return _sigmoid(c["a"] + c["b"] * _logit(p_model)), True


def p_fav_from_quotes(home_q, away_q) -> Optional[tuple[float, str]]:
    """(favourite's de-vigged match probability, 'home'|'away') from the two match-odds quotes, using each
    side's back/lay midpoint. None unless both sides have a two-sided book."""
    if home_q is None or away_q is None or not (home_q.back and home_q.lay and away_q.back and away_q.lay):
        return None
    ih, ia = home_q.implied, away_q.implied
    if not ih or not ia:
        return None
    ph = ih / (ih + ia)
    return (ph, "home") if ph >= 0.5 else (1 - ph, "away")


@dataclass
class MarketView:
    p_fav: float          # from the exchange's match odds (de-vigged mid)
    best_of: int
    sets: dict            # fav-first set scores: {"2-0": p, "2-1": p, "1-2": p, "0-2": p} (best of 5: 3-0 ... 0-3)
    p_set1: float         # favourite wins set 1
    after_won_set1: float
    after_lost_set1: float
    after_lost_set1_clear: float   # lost set 1 conceding 6-3 or wider
    corrected: dict       # quantity -> whether a validated correction was applied
    markov: dict          # the uncorrected values, for display

    def set_score_home_away(self, fav: str) -> dict:
        """Set scores keyed home-away (how the exchange quotes are stored)."""
        if fav == "home":
            return dict(self.sets)
        return {f"{k.split('-')[1]}-{k.split('-')[0]}": v for k, v in self.sets.items()}


@lru_cache(maxsize=2048)
def _markov(p_fav: float, best_of: int, surface: str) -> dict:
    pa, pb = solve_serve_probs(p_fav, best_of, surface)
    md = match_distribution(pa, pb, best_of)
    lost = 0.5 * (p_match_from(pa, pb, best_of, 0, 1, 0, 0, "A") + p_match_from(pa, pb, best_of, 0, 1, 0, 0, "B"))
    won = 0.5 * (p_match_from(pa, pb, best_of, 1, 0, 0, 0, "A") + p_match_from(pa, pb, best_of, 1, 0, 0, 0, "B"))
    return {"sets": {f"{a}-{b}": v for (a, b), v in md["sets"].items()}, "p_set1": md["p_set1"], "won": won, "lost": lost}


def market_view(p_fav: float, best_of: int = 3, surface: str = "Hard", path: Path | str = DEFAULT_PATH) -> MarketView:
    p_fav = min(max(float(p_fav), 0.5), 0.98)
    best_of = 5 if int(best_of) == 5 else 3
    m = _markov(round(p_fav, 3), best_of, surface if surface in ("Hard", "Clay", "Grass") else "Hard")
    need = best_of // 2 + 1
    s_fav_key, s_dog_key = f"{need}-0", f"0-{need}"
    s_fav, c1 = corrected("straight_fav", best_of, m["sets"][s_fav_key], path)
    s_dog, c2 = corrected("straight_dog", best_of, m["sets"][s_dog_key], path)
    # keep the split consistent with the match price: the favourite's other wins get what is left of p_fav
    s_fav = min(s_fav, p_fav - 0.01)
    s_dog = min(s_dog, 1 - p_fav - 0.005)
    sets: dict = {}
    fav_rest = {k: v for k, v in m["sets"].items() if int(k.split("-")[0]) == need and k != s_fav_key}
    dog_rest = {k: v for k, v in m["sets"].items() if int(k.split("-")[1]) == need and k != s_dog_key}
    sets[s_fav_key] = s_fav
    for rest, total in ((fav_rest, p_fav - s_fav), (dog_rest, 1 - p_fav - s_dog)):
        z = sum(rest.values()) or 1.0
        for k, v in rest.items():
            sets[k] = total * v / z
    sets[s_dog_key] = s_dog
    p_set1, c3 = corrected("set1_fav", best_of, m["p_set1"], path)
    won, c4 = corrected("after_won_set1", best_of, m["won"], path)
    lost, c5 = corrected("after_lost_set1", best_of, m["lost"], path)
    lost_clear, c6 = corrected("after_lost_set1_clear", best_of, m["lost"], path)
    order = sorted(sets, key=lambda k: (-int(k.split("-")[0]), int(k.split("-")[1])))
    return MarketView(p_fav, best_of, {k: sets[k] for k in order}, p_set1, won, lost, lost_clear,
                      {"straight_fav": c1, "straight_dog": c2, "set1_fav": c3, "after_won_set1": c4, "after_lost_set1": c5,
                       "after_lost_set1_clear": c6},
                      {"sets": m["sets"], "p_set1": m["p_set1"], "after_won_set1": m["won"], "after_lost_set1": m["lost"]})


def lay_limit(q: float, commission: float, margin: float) -> float:
    """Highest lay price at which laying a selection that wins with probability q still returns at least
    `margin` per unit of liability after commission: (1-q)(1-c)/(L-1) - q >= margin."""
    return 1 + (1 - q) * (1 - commission) / (q + margin)
