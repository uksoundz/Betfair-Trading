"""Bankroll and risk engine.

Staking is fractional Kelly on the *conservative* probability (never the raw model number), then
capped by a set of hard limits. Survival first: the caps bind far more often than Kelly does.

Limits (fractions of bank unless stated):
  max_per_trade      largest single risk (liability)                     default 2%
  max_open_exposure  sum of open liabilities                              default 10%
  max_per_strategy   open exposure in one strategy                        default 5%
  max_per_sport      open exposure in one sport                           default 8%
  daily_loss_limit   stop for the day once realised losses reach this      default 3%
  weekly_loss_limit  stop for the week                                     default 6%
  drawdown_halve     halve all stakes while drawdown from peak exceeds this default 10%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np


@dataclass
class RiskLimits:
    kelly_fraction: float = 0.25
    max_per_trade: float = 0.02
    max_open_exposure: float = 0.10
    max_per_strategy: float = 0.05
    max_per_sport: float = 0.08
    daily_loss_limit: float = 0.03
    weekly_loss_limit: float = 0.06
    drawdown_halve: float = 0.10
    min_stake: float = 2.0


@dataclass
class Exposure:
    open_total: float = 0.0
    by_strategy: dict = field(default_factory=dict)
    by_sport: dict = field(default_factory=dict)
    realised_today: float = 0.0
    realised_week: float = 0.0
    peak_bank: float = 0.0
    current_bank: float = 0.0

    @property
    def drawdown(self) -> float:
        return 0.0 if self.peak_bank <= 0 else max(0.0, (self.peak_bank - self.current_bank) / self.peak_bank)


def exposure_from_journal(entries, bank: float, today: Optional[date] = None) -> Exposure:
    """Open liabilities and realised P/L from journal entries (only those placed as paper or live)."""
    today = today or date.today()
    ex = Exposure(current_bank=bank, peak_bank=bank)
    week_start = today - timedelta(days=today.weekday())
    cum = 0.0
    peak = bank
    for e in sorted(entries, key=lambda x: x.created):
        if e.placed not in ("paper", "live"):
            continue
        sport = "tennis" if str(e.league).startswith("atp") else "football"
        if e.status == "open":
            risk = e.placed_total or e.stake_money
            ex.open_total += risk
            ex.by_strategy[e.strategy] = ex.by_strategy.get(e.strategy, 0.0) + risk
            ex.by_sport[sport] = ex.by_sport.get(sport, 0.0) + risk
        elif e.pnl_money is not None:
            cum += e.pnl_money
            peak = max(peak, bank + cum)
            if e.date >= today.isoformat():
                ex.realised_today += e.pnl_money
            if e.date >= week_start.isoformat():
                ex.realised_week += e.pnl_money
    ex.current_bank = bank + cum
    ex.peak_bank = peak
    return ex


def kelly_fraction(p: float, win_return: float, loss_return: float) -> float:
    """Optimal fraction of bank to risk for a bet winning `win_return` per unit risked with
    probability p and losing |loss_return| otherwise. Returns 0 when EV <= 0."""
    if win_return <= 0 or loss_return >= 0:
        return 0.0
    b = win_return / abs(loss_return)
    f = (p * b - (1 - p)) / b
    return max(0.0, f * abs(loss_return))


@dataclass
class StakeAdvice:
    stake_money: float
    risk_money: float
    kelly_full_pct: float
    applied_pct: float
    caps_hit: list
    blocked: bool
    reason: str = ""


def advise_stake(p_cons: float, win_return: float, loss_return: float, liability_per_unit: float, bank: float,
                 limits: RiskLimits, exposure: Exposure, strategy: str, sport: str) -> StakeAdvice:
    """Stake = fractional Kelly on the conservative probability, then every cap applied."""
    caps: list[str] = []
    if bank <= 0:
        return StakeAdvice(0.0, 0.0, 0.0, 0.0, ["no bank"], True, "Bank is zero.")
    if exposure.realised_today <= -limits.daily_loss_limit * bank:
        return StakeAdvice(0.0, 0.0, 0.0, 0.0, ["daily loss limit"], True, f"Daily loss limit reached ({limits.daily_loss_limit:.0%} of bank). No new trades today.")
    if exposure.realised_week <= -limits.weekly_loss_limit * bank:
        return StakeAdvice(0.0, 0.0, 0.0, 0.0, ["weekly loss limit"], True, f"Weekly loss limit reached ({limits.weekly_loss_limit:.0%} of bank). No new trades this week.")
    f_full = kelly_fraction(p_cons, win_return, loss_return)
    f = f_full * limits.kelly_fraction
    if exposure.drawdown > limits.drawdown_halve:
        f *= 0.5
        caps.append(f"drawdown {exposure.drawdown:.0%} > {limits.drawdown_halve:.0%}: stakes halved")
    if f > limits.max_per_trade:
        f = limits.max_per_trade
        caps.append(f"per-trade cap {limits.max_per_trade:.0%}")
    room_total = limits.max_open_exposure * bank - exposure.open_total
    room_strat = limits.max_per_strategy * bank - exposure.by_strategy.get(strategy, 0.0)
    room_sport = limits.max_per_sport * bank - exposure.by_sport.get(sport, 0.0)
    risk = f * bank
    for room, label in ((room_total, "open exposure cap"), (room_strat, "strategy exposure cap"), (room_sport, "sport exposure cap")):
        if risk > room:
            risk = max(0.0, room)
            caps.append(label)
    if risk <= 0:
        return StakeAdvice(0.0, 0.0, round(f_full * 100, 2), 0.0, caps, True, "Exposure limits leave no room for this trade.")
    stake = risk / liability_per_unit if liability_per_unit > 0 else risk
    if stake < limits.min_stake:
        if risk > 0 and limits.min_stake * liability_per_unit <= limits.max_per_trade * bank:
            stake = limits.min_stake
            risk = stake * liability_per_unit
            caps.append("raised to exchange minimum")
        else:
            return StakeAdvice(0.0, 0.0, round(f_full * 100, 2), 0.0, caps + ["below exchange minimum"], True, "Suggested stake is below the £2 exchange minimum; skip.")
    return StakeAdvice(round(stake, 2), round(risk, 2), round(f_full * 100, 2), round(100 * risk / bank, 2), caps, False)


def risk_of_ruin(p: float, win_return: float, loss_return: float, fraction: float, n_trades: int = 500,
                 sims: int = 2000, ruin_level: float = 0.5, seed: int = 7) -> dict:
    """Monte Carlo: probability the bank falls below (1 - ruin_level) of its start within n_trades
    when risking `fraction` of the current bank per trade with the given outcome distribution."""
    rng = np.random.default_rng(seed)
    outcomes = rng.random((sims, n_trades)) < p
    returns = np.where(outcomes, win_return, loss_return) * fraction
    paths = np.cumprod(1 + returns, axis=1)
    troughs = paths.min(axis=1)
    finals = paths[:, -1]
    return {"p_ruin": float(np.mean(troughs < 1 - ruin_level)), "median_final": float(np.median(finals)),
            "p_loss": float(np.mean(finals < 1.0)), "p5_final": float(np.percentile(finals, 5)), "max_drawdown_median": float(np.median(1 - troughs))}
