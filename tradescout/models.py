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
    meta: dict = field(default_factory=dict)  # sport-specific extras (tennis: surface, best_of, tourney)

    def __hash__(self):
        return hash((self.date, self.league, self.home, self.away))

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
    quotes: dict = field(default_factory=dict)  # "MARKET_TYPE:selection" -> value.Quote (ladders, matched, timestamp)
    as_of: Optional[str] = None  # ISO timestamp of the price snapshot
    # feed diagnostics: why a fixture has, or has not, got usable pre-match prices
    status: str = "none"          # ok | none | no_event | no_markets | inplay | suspended | closed | error
    note: str = ""                # plain-English reason shown to the user when status != ok
    inplay: bool = False          # the match odds market has turned in play (match started)
    delayed: Optional[bool] = None  # exchange says the data is delayed (Delayed application key)
    market_status: dict = field(default_factory=dict)  # market type -> OPEN | SUSPENDED | CLOSED | INPLAY
    event_id: Optional[str] = None
    event_name: Optional[str] = None  # how the exchange names this fixture
    candidates: list = field(default_factory=list)  # nearest exchange events when unmatched: [(name, score)]
    raw_markets: list = field(default_factory=list)  # what the exchange said per market (status, inplay, start, matched) for the diagnosis
    flags: list = field(default_factory=list)        # oddities noticed while building (e.g. in-play flag before the start time)

    @property
    def available(self) -> bool:
        """Usable pre-match prices exist for at least one market."""
        return self.source != "none" and (bool(self.quotes) or self.home is not None)

    def diagnostics(self) -> dict:
        return {"status": self.status, "note": self.note, "inplay": self.inplay, "delayed": self.delayed, "as_of": self.as_of,
                "event_name": self.event_name, "event_id": self.event_id, "markets": dict(self.market_status),
                "candidates": [list(c) for c in self.candidates], "quotes": len(self.quotes), "raw_markets": list(self.raw_markets), "flags": list(self.flags)}

    def quote(self, market: str, selection: str):
        """Quote for an order leg. Selection keys: home/away/draw for MATCH_ODDS, runner names otherwise."""
        return self.quotes.get(f"{market}:{selection}")


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
    scenarios: list = field(default_factory=list)  # [{"label","prob","profit"}] every way the match can go
    orders: list = field(default_factory=list)  # [OrderLeg] the pre-match selections this plan needs
    rules: list = field(default_factory=list)   # in-play steps as machine rules, used only when the user arms auto-trading
    fav: str = ""                               # home | away: which side the model makes favourite (the rules refer to it)
    entry: str = "prematch"                     # prematch | inplay (conditional entry placed by the engine on a trigger)
    entry_info: dict = field(default_factory=dict)  # in-play entry: market, selection, side, limit, window, probabilities
    # --- exchange-aware assessment (value.py) ---
    decision: str = "RESEARCH"            # TRADE | NO TRADE | ARM (conditional in-play entry) | RESEARCH (no exchange price)
    decision_reasons: list = field(default_factory=list)
    evidence: str = "model-synthetic"     # how the plan's return is established: exchange-priced-static | simulated-inplay | model-synthetic
    p_conservative: Optional[float] = None  # market-shrunk probability the entry selection wins
    ev_conservative: Optional[float] = None  # conservative net EV per unit risked on the entry leg(s), after commission
    ev_model: Optional[float] = None      # net EV on the raw model probability (for comparison only)
    p_market: Optional[float] = None      # market-implied probability of the entry selection
    execution: float = 0.0                # 0..1 fill / spread / liquidity quality
    legs: list = field(default_factory=list)  # per-leg value assessments (dicts)
    max_loss_per_unit: float = 1.0        # worst-case loss per unit risked across the plan
    stake_money: float = 0.0              # risk engine's suggested stake in money
    risk_money: float = 0.0               # money at risk for that stake
    risk_notes: list = field(default_factory=list)
    sport: str = "football"

    # Stars grade TRADE ideas only, on the rank score (10 points per 1% of conservative net edge after
    # commission, times execution quality 0..1 and evidence weight: 1.0 when the plan settles at the result,
    # 0.6 when its exits are simulated in play). NO TRADE and RESEARCH ideas get no stars.
    STAR_BANDS = ((50.0, 5), (30.0, 4), (15.0, 3), (5.0, 2))

    @property
    def stars(self) -> int:
        if self.decision != "TRADE":
            return 0
        for floor, n in self.STAR_BANDS:
            if self.score >= floor:
                return n
        return 1
