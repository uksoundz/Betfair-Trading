from __future__ import annotations

from ..models import MarketPrices, MatchForecast, MatchResult
from .base import Scenario, Strategy, StrategyResult

BASKET_SIZE = 5
CS_OVERROUND = 1.10  # correct-score books are far less efficient than match odds


class CorrectScoreBasket(Strategy):
    key = "cs_basket"
    label = "Correct Score target basket"
    description = ("Dutch the five most likely scorelines so every covered score returns the same profit. "
                   "Gives a target-score formula with the model's full scoreline distribution behind it.")
    needs_prices = ("correct_scores",)

    def evaluate(self, fc: MatchForecast, prices: MarketPrices) -> StrategyResult | None:
        ranked = sorted(fc.p_cs.items(), key=lambda kv: -kv[1])[:BASKET_SIZE]
        covered = sum(p for _, p in ranked)
        if covered < 0.45:
            return None
        legs = []
        any_market = False
        for score, p in ranked:
            mkt = prices.correct_scores.get(score)
            if mkt and mkt > 1:
                price, any_market = float(mkt), True
            else:
                price = max(1.01, (1 / max(p, 1e-6)) / CS_OVERROUND)
            legs.append((score, p, price))
        book = sum(1 / price for _, _, price in legs)
        profit_if_hit = 1.0 / book - 1.0  # equal profit per unit staked across the basket
        scenarios = [Scenario(f"finishes {s}", p, profit_if_hit) for s, p, _ in legs]
        scenarios.append(Scenario("another score", 1 - covered, -1.0))
        edge = (covered - book) if any_market else None
        plan = [f"Pre-match: dutch {', '.join(s for s, _, _ in legs)} - stakes proportional to 1/price so each returns ~{profit_if_hit:+.0%}."]
        plan += [f"  {s}: {p:.0%} model, price ~{price:.1f}, stake {(1/price)/book:.0%} of total" for s, p, price in legs]
        plan += [
            "On the first goal: re-centre - lay off scores now impossible, add the new neighbour score with the freed green.",
            "Equalisers and late goals are the enemy: reduce liability after 75' if the covered score is live.",
        ]
        rationale = [
            f"Top {BASKET_SIZE} scorelines cover {covered:.0%} of the model distribution",
            f"Expected goals {fc.home_xg:.2f} - {fc.away_xg:.2f}; most likely score {legs[0][0]}",
        ]
        return StrategyResult("Correct Score", "back", legs[0][0], covered, 1 / legs[0][1],
                              prices.correct_scores.get(legs[0][0]) if any_market else None, edge, scenarios, plan, rationale)

    def settle(self, fc: MatchForecast, result: MatchResult) -> tuple[float, float]:
        ranked = sorted(fc.p_cs.items(), key=lambda kv: -kv[1])[:BASKET_SIZE]
        book = sum(1 / max(1.01, (1 / max(p, 1e-6)) / CS_OVERROUND) for _, p in ranked)
        key = f"{result.home_goals}-{result.away_goals}"
        if key in {s for s, _ in ranked}:
            return 1.0, self.net(1.0 / book - 1.0)
        return 0.0, -1.0
