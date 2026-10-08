from __future__ import annotations

from ..models import MarketPrices, MatchForecast, MatchResult
from ..model.inplay import exit_profit_lay, fair_price
from .base import OrderLeg, Scenario, Strategy, StrategyResult, entry, exit_, inplay, note, stop


class BackOversWithInsurance(Strategy):
    key = "over25_ins"
    label = "Back Over 2.5 + 1-1 insurance"
    description = ("80% of stake on Over 2.5 goals, 20% on the 1-1 correct score. The 1-1 cover pays for "
                   "the most common 'two goals and stop' scoreline; held to settlement unless an early goal lets you green.")
    best_for = "Two attacking sides or a big favourite against a leaky defence, 2.8+ goals expected, Over 2.5 priced 1.7 to 2.3."
    avoid_when = "Over 2.5 shorter than 1.6 (the insurance costs more than it covers) or fewer than 2.6 goals expected."
    needs_prices = ("over_25",)
    inplay = False

    def evaluate(self, fc: MatchForecast, prices: MarketPrices) -> StrategyResult | None:
        p_over = fc.p_over[2.5]
        if p_over < 0.50 or fc.total_xg < 2.6:
            return None  # only genuinely goal-friendly fixtures
        o_price, o_market = self.price_or_fair(prices.over_25, p_over)
        if o_price < 1.6:
            return None  # overs too short to carry 20% insurance: the plan would lose even when 3 goals arrive
        p11 = fc.p_cs.get("1-1", 0.0)
        c_price, c_market = self.price_or_fair(prices.correct_scores.get("1-1"), p11)
        w_over, w_ins = 0.8, 0.2
        p_other_under = 1 - p_over - p11
        scenarios = [
            Scenario("3+ goals", p_over, w_over * (o_price - 1) - w_ins),
            Scenario("finishes 1-1", p11, w_ins * (c_price - 1) - w_over),
            Scenario("other under-2.5 score", p_other_under, -1.0),
        ]
        hit = p_over + (p11 if scenarios[1].profit > 0 else 0.0)
        edge = self.edge_back(p_over, o_price) if o_market else None
        plan = [
            entry(f"Before kick-off: back Over 2.5 Goals at {o_price:.2f} or higher with 80% of your stake."),
            entry(f"Before kick-off: back Correct Score 1-1 at around {c_price:.1f} with the other 20%. This is your insurance."),
            inplay("If a goal arrives before 20 minutes: lay Over 2.5 for your original stake to remove all risk and leave a free bet running. Keep the 1-1 running."),
            exit_("At 1-1: both legs are winning. Either lock equal profit across the correct score market or hold for the third goal."),
            note("Otherwise hold to the final whistle. Three or more goals pays the overs leg; 1-1 pays the insurance leg."),
            stop("There is no in-play stop: the maximum loss is the stake, and that happens on 0-0, 1-0, 0-1, 2-0 or 0-2."),
        ]
        rationale = [
            f"Model P(Over 2.5) {p_over:.1%} (fair {1/p_over:.2f}) vs {o_price:.2f}" + (" [exchange]" if o_market else " [model]"),
            f"P(1-1) {p11:.1%}; expected goals {fc.total_xg:.2f}",
            f"BTTS probability {fc.p_btts:.0%}",
        ]
        warnings = []
        if fc.total_xg < 2.6:
            warnings.append("Total xG under 2.6: this is a marginal overs match")
        return StrategyResult("Over/Under 2.5", "back", "Over 2.5", hit, 1 / p_over, prices.over_25 if o_market else None, edge,
                              scenarios, plan, rationale, warnings,
                              orders=[OrderLeg("OVER_UNDER_25", "Over 2.5 Goals", "back", round(o_price, 2), w_over, "stake", "main leg", p_model=p_over),
                                      OrderLeg("CORRECT_SCORE", "1-1", "back", round(c_price, 2), w_ins, "stake", "insurance leg", p_model=p11)])

    def settle(self, fc: MatchForecast, result: MatchResult) -> tuple[float, float]:
        p_over = fc.p_over[2.5]
        o_price, _ = self.price_or_fair(None, p_over)
        c_price, _ = self.price_or_fair(None, fc.p_cs.get("1-1", 0.0))
        if result.total_goals >= 3:
            return 1.0, self.net(0.8 * (o_price - 1) - 0.2)
        if result.home_goals == 1 and result.away_goals == 1:
            p = self.net(0.2 * (c_price - 1) - 0.8)
            return (1.0 if p > 0 else 0.0), p
        return 0.0, -1.0


class LayUndersStaged(Strategy):
    key = "lay_under25_staged"
    label = "Lay Under 2.5 (staged entry)"
    description = ("Lay half the liability on Under 2.5 pre-match and the other half on 15 minutes if still 0-0, "
                   "when the price has shortened. Exit on the second goal or at 70'.")
    best_for = "Matches expecting 3+ goals where Under 2.5 is 1.8 to 3.0, giving room for the price to shorten before you add."
    avoid_when = "Under 2.5 already below 1.7, or defensive sides where a slow start is likely to stay slow."
    needs_prices = ("under_25",)
    settlement = "approximate"

    def evaluate(self, fc: MatchForecast, prices: MarketPrices) -> StrategyResult | None:
        p_under = 1 - fc.p_over[2.5]
        if p_under > 0.58:
            return None
        u0, is_market = self.price_or_fair(prices.under_25, p_under, "lay")
        u15 = fair_price(self.cond(fc, 0, 0, 15)["under_25"])
        # Scenario tree: goal before 15' (second tranche never placed; first tranche greens or settles),
        # 0-0 at 15' then outcome over/under on the full position.
        p_goal_15 = fc.p_goal_before[15]
        after_goal = self.cond(fc, 1, 0, 15)  # symmetric enough for totals
        p_over_given_goal = after_goal["over_25"]
        scenarios = [
            Scenario("early goal, match goes over", p_goal_15 * p_over_given_goal, 0.5 * (1 / (u0 - 1))),
            Scenario("early goal, match stays under", p_goal_15 * (1 - p_over_given_goal), -0.5),
        ]
        p_00_15 = 1 - p_goal_15
        p_over_given_00 = self.cond(fc, 0, 0, 15)["over_25"]
        full_win = 0.5 * (1 / (u0 - 1)) + 0.5 * (1 / (u15 - 1))
        scenarios += [
            Scenario("0-0 on 15', both tranches in, match goes over", p_00_15 * p_over_given_00, full_win),
            Scenario("0-0 on 15', both tranches in, match stays under", p_00_15 * (1 - p_over_given_00), -1.0),
        ]
        hit = sum(s.prob for s in scenarios if s.profit > 0)
        edge = self.edge_lay(p_under, u0) if is_market else None
        plan = [
            entry(f"Before kick-off: lay Under 2.5 Goals at {u0:.2f} with half of your planned liability."),
            inplay(f"On 15 minutes, if still 0-0: lay the second half of your liability. The price should have shortened to around {u15:.2f}, so you get more for the same risk."),
            exit_("After the second goal: back Under 2.5 to lock in a profit, or let it run to the third goal if the match is open and the live stats back it."),
            stop("If it is still 0-0 on 70 minutes: back Under 2.5 and close. The price will have collapsed against you and the third goal is unlikely."),
        ]
        rationale = [
            f"Model P(Under 2.5) {p_under:.1%}; expected goals {fc.total_xg:.2f}",
            f"Under price expected to shorten from {u0:.2f} to ~{u15:.2f} by 15' if no goal",
        ]
        return StrategyResult("Over/Under 2.5", "lay", "Under 2.5", hit, 1 / p_under, prices.under_25 if is_market else None, edge,
                              scenarios, plan, rationale,
                              orders=[OrderLeg("OVER_UNDER_25", "Under 2.5 Goals", "lay", round(u0, 2), 0.5, "liability",
                                               "first half of the liability; the second half goes on in play at 15 minutes if still 0-0", p_model=p_under)])

    def settle(self, fc: MatchForecast, result: MatchResult) -> tuple[float, float]:
        p_under = 1 - fc.p_over[2.5]
        u0, _ = self.price_or_fair(None, p_under, "lay")
        u15 = fair_price(self.cond(fc, 0, 0, 15)["under_25"])
        over = result.total_goals >= 3
        ht_goals = (result.ht_home or 0) + (result.ht_away or 0)
        # P(first goal before 15' | a goal in the first half) from the timing profile
        from ..model.timing import cumulative_share
        p_early = cumulative_share(15) / cumulative_share(45) if ht_goals > 0 else 0.0
        one_tranche = self.net(0.5 / (u0 - 1)) if over else -0.5
        two_tranches = self.net(0.5 / (u0 - 1) + 0.5 / (u15 - 1)) if over else -1.0
        return (1.0 if over else 0.0), p_early * one_tranche + (1 - p_early) * two_tranches
