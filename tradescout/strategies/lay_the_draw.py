from __future__ import annotations

from ..models import MarketPrices, MatchForecast, MatchResult
from ..model.inplay import exit_profit_lay, fair_price
from .base import Scenario, Strategy, StrategyResult

EXIT_MINUTE = 70.0


class LayTheDraw(Strategy):
    key = "ltd"
    label = "Lay the Draw"
    description = ("Lay the draw before kick-off. Green up when the favourite scores; close for a "
                   "controlled loss if it is still 0-0 on 70 minutes or the underdog scores first.")
    needs_prices = ("draw",)

    def evaluate(self, fc: MatchForecast, prices: MarketPrices) -> StrategyResult | None:
        price, is_market = self.price_or_fair(prices.draw, fc.p_draw)
        if price < 2.6:
            return None  # too short: the collapse on a late 0-0 outweighs the green
        scenarios: list[Scenario] = []
        fav_home = fc.favourite == "home"
        for start, end, p_band in self.first_goal_bands(fc, EXIT_MINUTE):
            mid = (start + end) / 2 + 2  # exit a couple of minutes after the goal
            p_fav = p_band * fc.p_fav_scores_first / max(1e-9, 1 - (1 - fc.p_goal_before[75]) ** 0)  # share of first goals
            p_fav = p_band * fc.p_fav_scores_first / max(fc.p_goal_before[75], 1e-9)
            p_dog = p_band - p_fav
            d_fav = fair_price(self.cond(fc, 1 if fav_home else 0, 0 if fav_home else 1, mid)["draw"])
            d_dog = fair_price(self.cond(fc, 0 if fav_home else 1, 1 if fav_home else 0, mid)["draw"])
            scenarios.append(Scenario(f"favourite scores first {int(start)}-{int(end)}'", p_fav, exit_profit_lay(price, d_fav)))
            scenarios.append(Scenario(f"underdog scores first {int(start)}-{int(end)}'", p_dog, exit_profit_lay(price, d_dog)))
        p_nogoal = 1 - sum(s.prob for s in scenarios)
        d_70 = fair_price(self.cond(fc, 0, 0, EXIT_MINUTE)["draw"])
        scenarios.append(Scenario("still 0-0 on 70' - stop out", p_nogoal, exit_profit_lay(price, d_70)))
        hit = sum(s.prob for s in scenarios if s.profit > 0)
        edge = self.edge_lay(fc.p_draw, price) if is_market else None
        fav_name = fc.fixture.home if fav_home else fc.fixture.away
        plan = [
            f"Pre-match: lay The Draw at ~{price:.2f} (liability = 1 unit).",
            f"If {fav_name} score first: back the draw to green up (draw should trade ~{d_fav:.1f} after an early goal).",
            f"If the underdog scores first: close immediately (draw ~{d_dog:.1f}) and take the small loss.",
            f"If 0-0 on 70': close at ~{d_70:.1f}. Never let a lay run into a late 0-0.",
        ]
        rationale = [
            f"Model draw probability {fc.p_draw:.1%} (fair {1/fc.p_draw:.2f}) vs entry {price:.2f}" + (" [exchange]" if is_market else " [model, no feed]"),
            f"P(goal before 70') {fc.p_goal_before[70]:.0%}; favourite scores first {fc.p_fav_scores_first:.0%} of the time",
            f"Expected goals {fc.home_xg:.2f} - {fc.away_xg:.2f}",
        ]
        warnings = []
        if abs(fc.p_home - fc.p_away) < 0.12:
            warnings.append("Evenly matched sides: equaliser risk is high, consider the 1-1 insurance variant")
        return StrategyResult("Match Odds", "lay", "The Draw", hit, 1 / fc.p_draw, prices.draw if is_market else None, edge,
                              scenarios, plan, rationale, warnings)

    def settle(self, fc: MatchForecast, result: MatchResult) -> tuple[float, float]:
        price, _ = self.price_or_fair(None, fc.p_draw)
        fav_home = fc.favourite == "home"
        stop = self.net(exit_profit_lay(price, fair_price(self.cond(fc, 0, 0, EXIT_MINUTE)["draw"])))

        def profit(minute, side):
            if minute is None or minute > EXIT_MINUTE:
                return stop
            if side == "fav":
                d = fair_price(self.cond(fc, 1 if fav_home else 0, 0 if fav_home else 1, minute + 2)["draw"])
            else:
                d = fair_price(self.cond(fc, 0 if fav_home else 1, 1 if fav_home else 0, minute + 2)["draw"])
            return self.net(exit_profit_lay(price, d))

        return self.expect(self.first_goal_scenarios(fc, result), profit)
