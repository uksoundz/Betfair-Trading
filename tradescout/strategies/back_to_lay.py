from __future__ import annotations

from ..models import MarketPrices, MatchForecast, MatchResult
from ..model.inplay import exit_profit_back, fair_price
from .base import Scenario, Strategy, StrategyResult, entry, exit_, inplay, stop

EXIT_MINUTE = 60.0


class BackToLayFavourite(Strategy):
    key = "b2l_fav"
    label = "Back-to-Lay the favourite"
    description = ("Back the favourite pre-match and lay off when they take the lead. Stop out at 0-0 on 60' or "
                   "if the underdog scores first.")
    best_for = "A favourite priced 1.5 to 2.5 that starts fast and scores first often; home sides against poor travellers."
    avoid_when = "Favourite shorter than 1.4 (no room to shorten) or longer than 2.6 (not really a favourite)."
    needs_prices = ("home", "away")

    def evaluate(self, fc: MatchForecast, prices: MarketPrices) -> StrategyResult | None:
        fav_home = fc.favourite == "home"
        p_fav = fc.p_home if fav_home else fc.p_away
        mkt = prices.home if fav_home else prices.away
        price, is_market = self.price_or_fair(mkt, p_fav)
        if price < 1.4 or price > 2.6:
            return None
        key = "home" if fav_home else "away"
        scenarios: list[Scenario] = []
        for start, end, p_band in self.first_goal_bands(fc, EXIT_MINUTE):
            mid = (start + end) / 2 + 2
            p_fav_first = p_band * fc.p_fav_scores_first / max(fc.p_goal_before[75], 1e-9)
            p_dog_first = p_band - p_fav_first
            f_up = fair_price(self.cond(fc, 1 if fav_home else 0, 0 if fav_home else 1, mid)[key])
            f_down = fair_price(self.cond(fc, 0 if fav_home else 1, 1 if fav_home else 0, mid)[key])
            scenarios.append(Scenario(f"favourite leads {int(start)}-{int(end)}'", p_fav_first, exit_profit_back(price, f_up)))
            scenarios.append(Scenario(f"underdog scores first {int(start)}-{int(end)}'", p_dog_first, exit_profit_back(price, f_down)))
        p_00 = 1 - sum(s.prob for s in scenarios)
        f_60 = fair_price(self.cond(fc, 0, 0, EXIT_MINUTE)[key])
        scenarios.append(Scenario("0-0 on 60' - stop out", p_00, exit_profit_back(price, f_60)))
        hit = sum(s.prob for s in scenarios if s.profit > 0)
        edge = self.edge_back(p_fav, price) if is_market else None
        fav_name = fc.fixture.home if fav_home else fc.fixture.away
        plan = [
            entry(f"Before kick-off: back {fav_name} at {price:.2f} or higher. Your stake is the unit you are risking."),
            inplay("Wait for the first goal. Do nothing while it is 0-0 before the hour."),
            exit_(f"When {fav_name} score: lay {fav_name} at around {f_up:.2f} to lock a profit whatever happens next."),
            exit_(f"If the underdog scores first: lay {fav_name} at around {f_down:.2f} for a controlled loss. Do not chase an equaliser."),
            stop(f"If it is still 0-0 on 60 minutes: lay {fav_name} at around {f_60:.2f} and exit with a small loss."),
        ]
        rationale = [
            f"Model P({fav_name} win) {p_fav:.1%} (fair {1/p_fav:.2f}) vs {price:.2f}" + (" [exchange]" if is_market else " [model]"),
            f"{fav_name} score first in {fc.p_fav_scores_first:.0%} of simulations; goal before 60' {fc.p_goal_before[60]:.0%}",
        ]
        return StrategyResult("Match Odds", "back", fav_name, hit, 1 / p_fav, mkt if is_market else None, edge,
                              scenarios, plan, rationale)

    def settle(self, fc: MatchForecast, result: MatchResult) -> tuple[float, float]:
        fav_home = fc.favourite == "home"
        p_fav = fc.p_home if fav_home else fc.p_away
        price, _ = self.price_or_fair(None, p_fav)
        key = "home" if fav_home else "away"
        stop = self.net(exit_profit_back(price, fair_price(self.cond(fc, 0, 0, EXIT_MINUTE)[key])))

        def profit(minute, side):
            if minute is None or minute > EXIT_MINUTE:
                return stop
            if side == "fav":
                f = fair_price(self.cond(fc, 1 if fav_home else 0, 0 if fav_home else 1, minute + 2)[key])
            else:
                f = fair_price(self.cond(fc, 0 if fav_home else 1, 1 if fav_home else 0, minute + 2)[key])
            return self.net(exit_profit_back(price, f))

        return self.expect(self.first_goal_scenarios(fc, result), profit)
