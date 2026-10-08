from __future__ import annotations

from ..models import MarketPrices, MatchForecast, MatchResult
from ..model.inplay import exit_profit_lay, fair_price
from .base import Scenario, Strategy, StrategyResult

EXIT_MINUTE = 70.0


class LayZeroZero(Strategy):
    key = "lay_00"
    label = "Lay the 0-0"
    description = ("Lay 0-0 in the Correct Score market. Any goal settles the lay as a full win; "
                   "if it is still 0-0 on 70' close for a loss before the price collapses.")
    needs_prices = ("correct_scores",)

    def evaluate(self, fc: MatchForecast, prices: MarketPrices) -> StrategyResult | None:
        p00 = fc.p_cs.get("0-0", 0.0)
        if p00 > 0.12:
            return None  # low-scoring game: the lay is too expensive
        price, is_market = self.price_or_fair(prices.correct_scores.get("0-0"), p00)
        p_goal = fc.p_goal_before[70]
        z70 = fair_price(self.cond(fc, 0, 0, EXIT_MINUTE)["cs"][0, 0])
        scenarios = [
            Scenario("goal before 70'", p_goal, 1.0 / (price - 1)),
            Scenario("0-0 on 70' - stop out", 1 - p_goal, exit_profit_lay(price, z70)),
        ]
        hit = p_goal
        edge = self.edge_lay(p00, price) if is_market else None
        plan = [
            f"Pre-match: lay Correct Score 0-0 at ~{price:.1f} (liability = 1 unit).",
            "First goal: the position is a full win, nothing to do.",
            f"0-0 on 70': back 0-0 at ~{z70:.1f} to cap the loss.",
        ]
        rationale = [
            f"Model P(0-0) {p00:.1%} (fair {1/p00:.1f}) vs {price:.1f}" + (" [exchange]" if is_market else " [model]"),
            f"P(goal before 70') {p_goal:.0%}; expected goals {fc.total_xg:.2f}",
        ]
        return StrategyResult("Correct Score", "lay", "0-0", hit, 1 / p00, prices.correct_scores.get("0-0") if is_market else None,
                              edge, scenarios, plan, rationale)

    def settle(self, fc: MatchForecast, result: MatchResult) -> tuple[float, float]:
        p00 = fc.p_cs.get("0-0", 0.0)
        price, _ = self.price_or_fair(None, p00)
        stop = self.net(exit_profit_lay(price, fair_price(self.cond(fc, 0, 0, EXIT_MINUTE)["cs"][0, 0])))
        win = self.net(1.0 / (price - 1))
        return self.expect(self.first_goal_scenarios(fc, result),
                           lambda minute, side: stop if (minute is None or minute > EXIT_MINUTE) else win)
