from __future__ import annotations

from ..models import MarketPrices, MatchForecast, MatchResult
from ..model.inplay import exit_profit_back, fair_price
from .base import Scenario, Strategy, StrategyResult

EXIT_MINUTE = 60.0


class BackUndersTradeOut(Strategy):
    key = "under25_tradeout"
    label = "Back Under 2.5, trade out on 60'"
    description = ("Back Under 2.5 in a low-xG match and lay it back on 60 minutes (or on the first goal). "
                   "Profits from time decay rather than the final result; the stop is the first goal.")
    needs_prices = ("under_25",)

    def evaluate(self, fc: MatchForecast, prices: MarketPrices) -> StrategyResult | None:
        p_under = 1 - fc.p_over[2.5]
        if p_under < 0.5:
            return None
        price, is_market = self.price_or_fair(prices.under_25, p_under)
        scenarios: list[Scenario] = []
        for start, end, p_band in self.first_goal_bands(fc, EXIT_MINUTE):
            mid = (start + end) / 2 + 2
            u_after = fair_price(self.cond(fc, 1, 0, mid)["under_25"])
            scenarios.append(Scenario(f"first goal {int(start)}-{int(end)}' - exit", p_band, exit_profit_back(price, u_after)))
        p_00 = 1 - sum(s.prob for s in scenarios)
        u60 = fair_price(self.cond(fc, 0, 0, EXIT_MINUTE)["under_25"])
        scenarios.append(Scenario("0-0 on 60' - green up", p_00, exit_profit_back(price, u60)))
        hit = sum(s.prob for s in scenarios if s.profit > 0)
        edge = self.edge_back(p_under, price) if is_market else None
        plan = [
            f"Pre-match: back Under 2.5 at ~{price:.2f} (stake = 1 unit).",
            f"If 0-0 on 60': lay Under 2.5 at ~{u60:.2f} and bank the time decay.",
            "On the first goal: lay immediately to cut the loss (the earlier the goal, the bigger the hit).",
        ]
        rationale = [
            f"Model P(Under 2.5) {p_under:.1%} (fair {1/p_under:.2f}) vs {price:.2f}" + (" [exchange]" if is_market else " [model]"),
            f"P(0-0 at 60') {p_00:.0%}; expected goals {fc.total_xg:.2f}",
        ]
        return StrategyResult("Over/Under 2.5", "back", "Under 2.5", hit, 1 / p_under, prices.under_25 if is_market else None, edge,
                              scenarios, plan, rationale)

    def settle(self, fc: MatchForecast, result: MatchResult) -> tuple[float, float]:
        p_under = 1 - fc.p_over[2.5]
        price, _ = self.price_or_fair(None, p_under)
        green = self.net(exit_profit_back(price, fair_price(self.cond(fc, 0, 0, EXIT_MINUTE)["under_25"])))

        def profit(minute, side):
            if minute is None or minute > EXIT_MINUTE:
                return green
            return self.net(exit_profit_back(price, fair_price(self.cond(fc, 1, 0, minute + 2)["under_25"])))

        return self.expect(self.first_goal_scenarios(fc, result), profit)
