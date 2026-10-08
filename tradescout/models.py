"""Core domain objects shared by every layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class MatchResult:
    """A completed match. Half-time score is optional (some feeds omit it)."""

    date: date
    league: str
    home: str
    away: str
    home_goals: int
    away_goals: int
    ht_home: Optional[int] = None
    ht_away: Optional[int] = None

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals

    @property
    def outcome(self) -> str:
        if self.home_goals > self.away_goals:
            return "H"
        if self.home_goals < self.away_goals:
            return "A"
        return "D"


@dataclass(frozen=True)
class Fixture:
    """A match that has not kicked off yet."""

    date: date
    league: str
    home: str
    away: str
    kickoff: Optional[datetime] = None
    fixture_id: Optional[str] = None

    @property
    def label(self) -> str:
        return f"{self.home} v {self.away}"


@dataclass
class MarketPrices:
    """Best available exchange back/lay prices. Any field may be None when no feed is connected.
    Prices are decimal odds. Only the markets the strategies need are modelled."""

    home: Optional[float] = None
    draw: Optional[float] = None
    away: Optional[float] = None
    over_15: Optional[float] = None
    under_15: Optional[float] = None
    over_25: Optional[float] = None
    under_25: Optional[float] = None
    over_35: Optional[float] = None
    under_35: Optional[float] = None
    btts_yes: Optional[float] = None
    btts_no: Optional[float] = None
    correct_scores: dict[str, float] = field(default_factory=dict)  # "1-1" -> odds
    source: str = "none"
    total_matched: Optional[float] = None  # GBP matched on the match odds market (liquidity)

    @property
    def available(self) -> bool:
        return self.source != "none" and self.home is not None


@dataclass
class TeamStrength:
    attack: float
    defence: float
    matches_in_window: int
    effective_matches: float  # time-weighted count


@dataclass
class MatchForecast:
    """Everything the model says about one fixture."""

    fixture: Fixture
    score_matrix: np.ndarray  # P(home=i, away=j), shape (max_goals+1, max_goals+1)
    home_xg: float
    away_xg: float
    home_strength: TeamStrength
    away_strength: TeamStrength
    rho: float
    # Half-time scoreline matrix
    ht_matrix: np.ndarray
    # Derived market probabilities
    p_home: float
    p_draw: float
    p_away: float
    p_over: dict[float, float]  # line -> P(total > line)
    p_btts: float
    p_cs: dict[str, float]  # "2-1" -> prob (home-away)
    p_ht_00: float
    p_goal_before: dict[int, float]  # minute -> P(at least one goal before minute)
    p_fav_scores_first: float
    favourite: str  # "home" | "away"
    confidence: float  # 0..1 data-quality score
    notes: list[str] = field(default_factory=list)

    @property
    def total_xg(self) -> float:
        return self.home_xg + self.away_xg

    def fair_odds(self, p: float) -> float:
        return float("inf") if p <= 0 else 1.0 / p


@dataclass
class TradeIdea:
    """One strategy applied to one fixture."""

    fixture: Fixture
    strategy: str
    strategy_label: str
    market: str
    side: str  # "back" | "lay"
    selection: str
    hit_prob: float  # model probability the trade plan pays off
    model_price: float  # fair price for the primary selection
    market_price: Optional[float]  # exchange price if available
    edge: Optional[float]  # model_prob - implied market prob (positive = value)
    expected_roi: float  # expected profit per 1 unit risked
    win_return: float  # profit per unit risked when the plan pays off
    loss_return: float  # (negative) profit per unit risked when it fails
    calibrated_hit_prob: float  # hit_prob shrunk towards the strategy's historical strike rate
    calibrated_roi: float  # expected ROI re-priced with the calibrated hit probability (what the score uses)
    historical_strike_rate: Optional[float]
    historical_sample: int
    confidence: float
    liquidity: float
    score: float  # 0..100 composite rank score
    stake_pct: float  # fractional Kelly stake as % of bank
    plan: list  # list[PlanStep] - structured trading plan (phase, text)
    rationale: list[str]  # bullet points explaining the numbers
    warnings: list[str] = field(default_factory=list)
