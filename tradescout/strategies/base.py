"""Strategy interface.

A strategy turns a MatchForecast (+ optional exchange prices) into a *plan* with an explicit payoff
model: the probability it pays off, what it returns when it does, what it loses when it does not.
Everything is expressed per 1 unit risked (stake for backs, liability for lays) so strategies are
comparable in the ranking.

Every strategy also knows how to *settle* itself against a real result, which is what the
backtester uses to measure true strike rate and ROI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..models import MarketPrices, MatchForecast, MatchResult
from ..model.inplay import conditional_matrix, market_probs
from ..model.timing import cumulative_share

COMMISSION = 0.02  # Betfair basic commission on net market winnings
DEFAULT_OVERROUND = 1.03  # applied to fair prices when no exchange feed is connected


@dataclass
class PlanStep:
    """One instruction in a trading plan. phase is one of entry | inplay | exit | stop | note."""

    phase: str
    text: str


def entry(text: str) -> PlanStep:
    return PlanStep("entry", text)


def inplay(text: str) -> PlanStep:
    return PlanStep("inplay", text)


def exit_(text: str) -> PlanStep:
    return PlanStep("exit", text)


def stop(text: str) -> PlanStep:
    return PlanStep("stop", text)


def note(text: str) -> PlanStep:
    return PlanStep("note", text)


@dataclass
class OrderLeg:
    """One pre-match selection the plan needs. Prices are the plan's limits: the minimum
    acceptable for a back, the maximum for a lay. fraction is the share of the unit risked;
    sizing says whether that share is a stake (backs) or a liability (lays). Used to build the
    bet slip the user reviews."""

    market: str      # MATCH_ODDS | OVER_UNDER_25 | OVER_UNDER_15 | CORRECT_SCORE | BOTH_TEAMS_TO_SCORE
    selection: str   # home | away | draw | Over 2.5 Goals | Under 2.5 Goals | 1-1 | 0-0 ...
    side: str        # back | lay
    price: float
    fraction: float = 1.0
    sizing: str = "stake"  # stake | liability
    note: str = ""
    p_model: float = 0.0  # model probability that this selection wins (used by the value engine)


@dataclass
class Scenario:
    label: str
    prob: float
    profit: float  # per unit risked


@dataclass
class StrategyResult:
    market: str
    side: str
    selection: str
    hit_prob: float
    model_price: float
    market_price: Optional[float]
    edge: Optional[float]
    scenarios: list[Scenario]
    plan: list[PlanStep]
    rationale: list[str]
    warnings: list[str] = field(default_factory=list)
    orders: list[OrderLeg] = field(default_factory=list)

    def __post_init__(self):
        # The plan "pays off" exactly when it ends in a scenario with positive profit. Deriving this
        # from the scenario tree keeps every strategy's hit probability on the same definition.
        self.hit_prob = float(sum(s.prob for s in self.scenarios if s.profit > 0))

    @property
    def plan_text(self) -> list[str]:
        return [p.text for p in self.plan]

    @property
    def expected_roi(self) -> float:
        return float(sum(s.prob * s.profit for s in self.scenarios))

    @property
    def win_return(self) -> float:
        wins = [s for s in self.scenarios if s.profit > 0]
        p = sum(s.prob for s in wins)
        return float(sum(s.prob * s.profit for s in wins) / p) if p > 0 else 0.0

    @property
    def loss_return(self) -> float:
        losses = [s for s in self.scenarios if s.profit <= 0]
        p = sum(s.prob for s in losses)
        return float(sum(s.prob * s.profit for s in losses) / p) if p > 0 else 0.0


class Strategy:
    key: str = "base"
    label: str = "Base"
    description: str = ""
    best_for: str = ""      # plain-English: the kind of match this suits
    avoid_when: str = ""    # plain-English: when to leave it alone
    needs_prices: tuple[str, ...] = ()
    enabled_default: bool = True   # weak or unverifiable strategies ship disabled; users can enable in Settings
    settlement: str = "exact"      # exact (settles on the final result) | approximate (exits modelled, event timing inferred) | unverifiable
    inplay: bool = True            # needs in-play action after entry

    def evaluate(self, fc: MatchForecast, prices: MarketPrices) -> StrategyResult | None:
        raise NotImplementedError

    def settle(self, fc: MatchForecast, result: MatchResult) -> tuple[float, float]:
        """(hit probability, realised profit per unit risked) for a real result. `hit` is 1/0 when the
        outcome is observed exactly and a probability when it depends on an unobserved goal minute."""
        raise NotImplementedError

    # ----- helpers shared by concrete strategies -------------------------------------------
    @staticmethod
    def price_or_fair(market_price: Optional[float], p: float) -> tuple[float, bool]:
        """Use the exchange price when we have it, else the model's fair price shaded by a
        typical exchange overround. Returns (price, is_market)."""
        if market_price and market_price > 1.0:
            return float(market_price), True
        fair = 1.0 / max(p, 1e-6)
        return float(max(1.01, fair / DEFAULT_OVERROUND)), False

    @staticmethod
    def edge_back(p_model: float, price: float) -> float:
        return p_model - 1.0 / price

    @staticmethod
    def edge_lay(p_model: float, price: float) -> float:
        return 1.0 / price - p_model

    @staticmethod
    def cond(fc: MatchForecast, hg: int, ag: int, minute: float) -> dict:
        return market_probs(conditional_matrix(fc.home_xg, fc.away_xg, hg, ag, minute, fc.score_matrix.shape[0] - 1, fc.rho))

    @staticmethod
    def first_goal_bands(fc: MatchForecast, until: float, step: float = 15.0) -> list[tuple[float, float, float]]:
        """[(band_start, band_end, P(first goal falls in band))] up to `until` minutes."""
        xg = fc.total_xg
        bands = []
        start = 0.0
        while start < until:
            end = min(start + step, until)
            p = float(np.exp(-xg * cumulative_share(start)) - np.exp(-xg * cumulative_share(end)))
            bands.append((start, end, p))
            start = end
        return bands

    @staticmethod
    def fav_goals(fc: MatchForecast, result: MatchResult) -> tuple[int, int, int | None, int | None]:
        """(fav FT goals, dog FT goals, fav HT goals, dog HT goals)."""
        if fc.favourite == "home":
            return result.home_goals, result.away_goals, result.ht_home, result.ht_away
        return result.away_goals, result.home_goals, result.ht_away, result.ht_home

    @staticmethod
    def first_goal_scenarios(fc: MatchForecast, result: MatchResult) -> list[tuple[float, Optional[float], Optional[str]]]:
        """Possible (probability, minute, scorer 'fav'|'dog') for the first goal, inferred from HT/FT scores.

        Only half-time and full-time scores are available in the bundled data, so the minute is
        not observed. Rather than guess a single minute (which biases every timing strategy in its
        own favour) we return a small distribution derived from the goal-timing profile:
          * first-half goal      -> minute 25 (one scenario; the HT score tells us which side if the half was 1-0/0-1)
          * second-half goal     -> 62% at minute 57, 38% after 70' (stop-out territory)
        The scorer side is split by the model's first-scorer probability whenever the score cannot
        tell us (1-1 at HT, two second-half goals apiece, ...). Settlement takes the expectation.
        With minute-level feeds (football-data.org, API-Football) this collapses to one exact scenario."""
        fg, dg, fh, dh = Strategy.fav_goals(fc, result)
        if fg + dg == 0:
            return [(1.0, None, None)]
        p_fav_first = fc.p_fav_scores_first / max(fc.p_goal_before[85], 1e-9)

        def side_split(f_goals: int, d_goals: int) -> list[tuple[float, str]]:
            if f_goals > 0 and d_goals == 0:
                return [(1.0, "fav")]
            if d_goals > 0 and f_goals == 0:
                return [(1.0, "dog")]
            return [(p_fav_first, "fav"), (1 - p_fav_first, "dog")]

        if fh is not None and dh is not None and fh + dh > 0:
            return [(p, 25.0, side) for p, side in side_split(fh, dh)]
        f2, d2 = fg - (fh or 0), dg - (dh or 0)
        split = side_split(f2, d2)
        late_share = (1 - cumulative_share(70)) / (1 - cumulative_share(45))  # P(after 70' | second half)
        out: list[tuple[float, Optional[float], Optional[str]]] = []
        for p, side in split:
            out.append((p * (1 - late_share), 57.0, side))
            out.append((p * late_share, 78.0, side))
        return out

    @staticmethod
    def infer_first_goal(fc: MatchForecast, result: MatchResult) -> tuple[Optional[float], Optional[str]]:
        """Most likely single (minute, side) - kept for display; settlement uses first_goal_scenarios."""
        best = max(Strategy.first_goal_scenarios(fc, result), key=lambda t: t[0])
        return best[1], best[2]

    @staticmethod
    def expect(scenarios: list[tuple[float, Optional[float], Optional[str]]], fn) -> tuple[float, float]:
        """Expected (hit probability, profit) over first-goal scenarios; fn(minute, side) -> profit."""
        hit = 0.0
        pnl = 0.0
        for p, minute, side in scenarios:
            profit = fn(minute, side)
            pnl += p * profit
            hit += p * (1.0 if profit > 0 else 0.0)
        return hit, pnl

    @staticmethod
    def net(profit: float) -> float:
        """Apply commission to a positive settled profit."""
        return profit * (1 - COMMISSION) if profit > 0 else profit
