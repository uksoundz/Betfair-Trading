from __future__ import annotations

from ..models import MarketPrices, MatchForecast, MatchResult
from ..model.inplay import exit_profit_lay, fair_price
from ..autotrade import rules as R
from .base import OrderLeg, Scenario, Strategy, StrategyResult, entry, inplay, stop

EXIT_MINUTE = 70.0


class LayZeroZero(Strategy):
    key = "lay_00"
    label = "Lay the 0-0"
    description = ("Lay 0-0 in the Correct Score market. Any goal settles the lay as a full win; "
                   "if it is still 0-0 on 70' close for a loss before the price collapses.")
    best_for = "High-scoring fixtures where 0-0 is under 8% likely and the 0-0 price is 12 or bigger. High strike rate, small wins."
    avoid_when = "Low-scoring leagues or cagey derbies where 0-0 is 12%+; the occasional loss then wipes many wins."
    needs_prices = ("correct_scores",)
    settlement = "approximate"

    def trade_rules(self) -> list[dict]:
        """The plan's in-play steps as rules for armed auto-trading (independent of today's prices)."""
        return [R.rule("first_goal", R.goals_at_least(1), R.hold(), "First goal: the 0-0 lay has won. Nothing to do.", final=True),
                                     R.rule("stop70", R.all_of(R.minute_at_least(EXIT_MINUTE), R.goals_at_most(0)), R.green(0), "Still 0-0 on 70': back 0-0 to cap the loss.", final=True)]

    def evaluate(self, fc: MatchForecast, prices: MarketPrices) -> StrategyResult | None:
        p00 = fc.p_cs.get("0-0", 0.0)
        if p00 > 0.12:
            return None  # low-scoring game: the lay is too expensive
        price, is_market = self.price_or_fair(prices.correct_scores.get("0-0"), p00, "lay")
        p_goal = fc.p_goal_before[70]
        z70 = fair_price(self.cond(fc, 0, 0, EXIT_MINUTE)["cs"][0, 0])
        scenarios = [
            Scenario("goal before 70'", p_goal, 1.0 / (price - 1)),
            Scenario("0-0 on 70' - stop out", 1 - p_goal, exit_profit_lay(price, z70)),
        ]
        hit = p_goal
        edge = self.edge_lay(p00, price) if is_market else None
        plan = [
            entry(f"Before kick-off: lay Correct Score 0-0 at around {price:.1f}. Your liability is the unit you are risking; the win is the lay stake."),
            inplay("First goal, from either side: the trade is won in full. Nothing more to do."),
            stop(f"If it is still 0-0 on 70 minutes: back 0-0 at around {z70:.1f} to cap the loss before the price collapses further."),
        ]
        rationale = [
            f"Model P(0-0) {p00:.1%} (fair {1/p00:.1f}) vs {price:.1f}" + (" [exchange]" if is_market else " [model]"),
            f"P(goal before 70') {p_goal:.0%}; expected goals {fc.total_xg:.2f}",
        ]
        return StrategyResult("Correct Score", "lay", "0-0", hit, 1 / p00, prices.correct_scores.get("0-0") if is_market else None,
                              edge, scenarios, plan, rationale,
                              orders=[OrderLeg("CORRECT_SCORE", "0-0", "lay", round(price, 1), 1.0, "liability", "lay 0-0 pre-match", p_model=p00)],
                              rules=self.trade_rules())

    def settle(self, fc: MatchForecast, result: MatchResult) -> tuple[float, float]:
        p00 = fc.p_cs.get("0-0", 0.0)
        price, _ = self.price_or_fair(None, p00, "lay")
        stop = self.net(exit_profit_lay(price, fair_price(self.cond(fc, 0, 0, EXIT_MINUTE)["cs"][0, 0])))
        win = self.net(1.0 / (price - 1))
        return self.expect(self.first_goal_scenarios(fc, result),
                           lambda minute, side: stop if (minute is None or minute > EXIT_MINUTE) else win)
