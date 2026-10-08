from __future__ import annotations

from ..models import MarketPrices, MatchForecast, MatchResult
from .base import OrderLeg, Scenario, Strategy, StrategyResult, entry, exit_, inplay, note, stop

BASKET_SIZE = 5
CS_OVERROUND = 1.10  # correct-score books are far less efficient than match odds


class CorrectScoreBasket(Strategy):
    key = "cs_basket"
    label = "Correct Score target basket"
    description = ("Dutch the five most likely scorelines so every covered score returns the same profit. "
                   "Gives a target-score formula with the model's full scoreline distribution behind it.")
    best_for = "Matches with a clear most-likely outcome where the top five scores cover 50%+; liquid leagues only."
    avoid_when = "Thin correct-score markets, very open games where probability is spread across many scores."
    needs_prices = ("correct_scores",)
    inplay = False
    enabled_default = False  # -13% model-synthetic ROI: a 10% correct-score overround is not beaten by this model

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
        plan = [entry(f"Before kick-off: back these five scores so each returns about {profit_if_hit:+.0%} if it lands: " + ", ".join(s for s, _, _ in legs) + ".")]
        plan += [note(f"{s}: model {p:.0%}, price around {price:.1f}, put {(1/price)/book:.0%} of the total stake on it") for s, p, price in legs]
        plan += [
            inplay("On the first goal: scores that are now impossible have lost; move that money onto the new neighbouring score so the basket stays centred on the live scoreline."),
            exit_("When the current score is one you cover and 75 minutes have passed: lay it to lock profit rather than hoping it stays."),
            stop("The maximum loss is the total stake, which happens if the match finishes on an uncovered score. Never add to a losing basket."),
        ]
        rationale = [
            f"Top {BASKET_SIZE} scorelines cover {covered:.0%} of the model distribution",
            f"Expected goals {fc.home_xg:.2f} - {fc.away_xg:.2f}; most likely score {legs[0][0]}",
        ]
        return StrategyResult("Correct Score", "back", legs[0][0], covered, 1 / legs[0][1],
                              prices.correct_scores.get(legs[0][0]) if any_market else None, edge, scenarios, plan, rationale,
                              orders=[OrderLeg("CORRECT_SCORE", s, "back", round(pr, 1), (1 / pr) / book, "stake", f"{p:.0%} model", p_model=p) for s, p, pr in legs])

    def settle(self, fc: MatchForecast, result: MatchResult) -> tuple[float, float]:
        ranked = sorted(fc.p_cs.items(), key=lambda kv: -kv[1])[:BASKET_SIZE]
        book = sum(1 / max(1.01, (1 / max(p, 1e-6)) / CS_OVERROUND) for _, p in ranked)
        key = f"{result.home_goals}-{result.away_goals}"
        if key in {s for s, _ in ranked}:
            return 1.0, self.net(1.0 / book - 1.0)
        return 0.0, -1.0
