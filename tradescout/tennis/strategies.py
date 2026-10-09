"""Tennis trading strategies. Same shape as the football ones: a scenario tree with the probability
and profit of every way the match can go, a phased plan, the pre-match selections for the slip,
and a settle() against a real result for the backtest and the journal.

Prices after an event (a set won, an early break) come from the Markov model's in-play state, so the
exits in each plan are the model's own view of where the market will be, not round numbers.
"""
from __future__ import annotations

from ..autotrade import rules as R

from ..model.inplay import exit_profit_back, exit_profit_lay, fair_price
from ..models import MarketPrices
from ..strategies.base import OrderLeg, Scenario, Strategy, StrategyResult, entry, exit_, inplay, note, stop
from .data import TennisResult
from .forecast import TennisForecast


def _fav_prices(fc: TennisForecast, prices: MarketPrices) -> tuple:
    """(favourite's exchange price or None, favourite is home?)"""
    fav_home = fc.p_a >= 0.5
    mkt = prices.home if fav_home else prices.away
    return mkt, fav_home


def _settle_sets(fc: TennisForecast, result: TennisResult) -> tuple[bool, bool, int]:
    """(favourite won, favourite won set 1, total games)"""
    fav = fc.fav_name()
    fav_won = result.winner == fav
    fav_set1 = result.first_set_winner == fav
    return fav_won, fav_set1, result.total_games


class BackToLayFavouriteSet(Strategy):
    key = "tn_b2l_fav"
    label = "Back favourite, lay off after set one"
    description = ("Back the favourite before the match and lay off for a profit once they take the first set. "
                   "If they lose the first set, close for a controlled loss rather than hoping.")
    best_for = "A clear favourite priced 1.25 to 1.9 who wins the first set often; big servers on fast courts."
    avoid_when = "Odds-on under 1.2 (no room to shorten), evenly matched players, or a favourite returning from a layoff."
    settlement = "approximate"
    enabled_default = False  # -6% per unit at model prices in both 2024 and 2025 (profit factor 0.69): exits cost more than the set-one signal is worth

    def trade_rules(self) -> list[dict]:
        """The plan's in-play steps as rules for armed auto-trading (independent of today's prices)."""
        return [R.rule("set1_won", R.set_won(1, "fav"), R.green(0), "Favourite wins set 1: lay the favourite to lock an equal profit.", final=True),
                                     R.rule("set1_lost", R.set_won(1, "dog"), R.green(0), "Favourite loses set 1: lay the favourite and take the loss.", final=True)]

    def evaluate(self, fc: TennisForecast, prices: MarketPrices) -> StrategyResult | None:
        mkt, fav_home = _fav_prices(fc, prices)
        p_fav = fc.p_fav
        price, is_market = self.price_or_fair(mkt, p_fav)
        if price < 1.22 or price > 2.0:
            return None
        p_set1 = fc.p_set1_a if fav_home else 1 - fc.p_set1_a
        p_won = fc.p_fav_from(1, 0, 0, 0, "fav")
        p_lost = fc.p_fav_from(0, 1, 0, 0, "fav")
        f_up, f_down = fair_price(p_won), fair_price(p_lost)
        scenarios = [
            Scenario("favourite wins set one, lay off", p_set1, exit_profit_back(price, f_up)),
            Scenario("favourite loses set one, close", 1 - p_set1, exit_profit_back(price, f_down)),
        ]
        edge = self.edge_back(p_fav, price) if is_market else None
        fav, dog = fc.fav_name(), fc.dog_name()
        plan = [
            entry(f"Before the match: back {fav} at {price:.2f} or higher. Your stake is the unit you are risking."),
            inplay("Do nothing during the first set. Breaks come and go; the set is the signal."),
            exit_(f"When {fav} win the first set: lay {fav} at around {f_up:.2f} to lock an equal profit whatever happens next."),
            stop(f"If {fav} lose the first set: lay at around {f_down:.2f} and take the loss. Do not wait for a comeback."),
            note("Retirements: if the favourite retires you lose the full stake, so prefer players with no injury news."),
        ]
        rationale = [
            f"Model: {fav} {p_fav:.0%} to win (fair {1/p_fav:.2f}) vs entry {price:.2f}" + (" [exchange]" if is_market else " [model]"),
            f"{fav} win set one {p_set1:.0%} of the time; after it their price should be ~{f_up:.2f}, after losing it ~{f_down:.2f}",
            f"Elo {fc.elo_a:.0f} v {fc.elo_b:.0f} on {fc.surface.lower()}; serve points {fc.pa_serve:.0%} v {fc.pb_serve:.0%}",
        ]
        return StrategyResult("Match Odds", "back", fav, 0, 1 / p_fav, mkt if is_market else None, edge, scenarios, plan, rationale,
                              orders=[OrderLeg("MATCH_ODDS", "home" if fav_home else "away", "back", round(price, 2), 1.0, "stake", "back the favourite pre-match", p_model=p_fav)],
                              rules=self.trade_rules())

    def settle(self, fc: TennisForecast, result: TennisResult) -> tuple[float, float]:
        mkt, fav_home = _fav_prices(fc, MarketPrices())
        price, _ = self.price_or_fair(None, fc.p_fav)
        _, fav_set1, _ = _settle_sets(fc, result)
        if result.retired and result.winner != fc.fav_name():
            return 0.0, -1.0
        p_after = fc.p_fav_from(1, 0, 0, 0, "fav") if fav_set1 else fc.p_fav_from(0, 1, 0, 0, "fav")
        profit = self.net(exit_profit_back(price, fair_price(p_after)))
        return (1.0 if profit > 0 else 0.0), profit


class LayFavouriteEarlyBreak(Strategy):
    key = "tn_lay_fav_break"
    label = "Lay favourite, green on an early break"
    description = ("Lay a short favourite before the match. If the underdog breaks first in set one the favourite's "
                   "price jumps and you back it to green; if the favourite breaks first, close for a small loss.")
    best_for = "Odds-on favourites priced 1.15 to 1.5 against an underdog who holds serve well (big server, fast court)."
    avoid_when = "Underdogs who get broken early and often, or clay where the favourite's returning edge shows quickly."
    settlement = "unverifiable"   # who broke first is not in the results data; the backtest cannot settle this plan
    enabled_default = False

    def trade_rules(self) -> list[dict]:
        """The plan's in-play steps as rules for armed auto-trading (independent of today's prices)."""
        return []

    def evaluate(self, fc: TennisForecast, prices: MarketPrices) -> StrategyResult | None:
        mkt, fav_home = _fav_prices(fc, prices)
        p_fav = fc.p_fav
        price, is_market = self.price_or_fair(mkt, p_fav, "lay")
        if price < 1.12 or price > 1.55:
            return None
        pb_a, pb_b = fc.p_first_break_a, fc.p_first_break_b
        p_dog_first = pb_b if fav_home else pb_a
        p_fav_first = pb_a if fav_home else pb_b
        p_neither = max(0.0, 1 - p_dog_first - p_fav_first)
        f_dog_up = fair_price(fc.p_fav_from(0, 0, 1, 2, "dog"))   # favourite broken, trails 1-2
        f_fav_up = fair_price(fc.p_fav_from(0, 0, 2, 1, "fav"))   # favourite breaks, leads 2-1
        f_tb = fair_price(fc.p_fav_from(0, 0, 6, 6, "fav"))
        scenarios = [
            Scenario("underdog breaks first, back the favourite to green", p_dog_first, exit_profit_lay(price, f_dog_up)),
            Scenario("favourite breaks first, close", p_fav_first, exit_profit_lay(price, f_fav_up)),
            Scenario("no break before 6-6, close at the tiebreak", p_neither, exit_profit_lay(price, f_tb)),
        ]
        edge = self.edge_lay(p_fav, price) if is_market else None
        fav, dog = fc.fav_name(), fc.dog_name()
        plan = [
            entry(f"Before the match: lay {fav} at {price:.2f} or lower. Your liability is the unit you are risking."),
            inplay("Watch the first set for the first break of serve. Holds mean nothing yet."),
            exit_(f"If {dog} break first: back {fav} at around {f_dog_up:.2f} to lock in the profit. Do not hold for a second break."),
            stop(f"If {fav} break first: back {fav} at around {f_fav_up:.2f} and close. Small loss, move on."),
            stop(f"If the set reaches 6-6 with no break: close at around {f_tb:.2f}."),
        ]
        rationale = [
            f"Model: {fav} {p_fav:.0%} (fair {1/p_fav:.2f}) vs lay at {price:.2f}" + (" [exchange]" if is_market else " [model]"),
            f"{dog} break first in {p_dog_first:.0%} of first sets; {fav} break first in {p_fav_first:.0%}",
            f"{dog} hold {fc.pb_serve if fav_home else fc.pa_serve:.0%} of serve points, so an early break against the favourite is live",
        ]
        return StrategyResult("Match Odds", "lay", fav, 0, 1 / p_fav, mkt if is_market else None, edge, scenarios, plan, rationale,
                              orders=[OrderLeg("MATCH_ODDS", "home" if fav_home else "away", "lay", round(price, 2), 1.0, "liability", "lay the favourite pre-match", p_model=p_fav)],
                              rules=self.trade_rules())  # break-of-serve triggers need a point-by-point feed: not automated

    def settle(self, fc: TennisForecast, result: TennisResult) -> tuple[float, float]:
        """Who broke first is not in the results, so settle as an expectation: the first-set winner
        is far more likely to have broken first. P(dog broke first | dog won set 1) is taken from the
        model's first-break and set probabilities."""
        price, _ = self.price_or_fair(None, fc.p_fav, "lay")
        fav_home = fc.p_a >= 0.5
        _, fav_set1, _ = _settle_sets(fc, result)
        p_dog_first = fc.p_first_break_b if fav_home else fc.p_first_break_a
        p_fav_set1 = fc.p_set1_a if fav_home else 1 - fc.p_set1_a
        # crude Bayes: dog breaking first roughly doubles the dog's set chance
        p_dog_first_given = min(0.95, p_dog_first * 2.0) if not fav_set1 else max(0.05, p_dog_first * 0.5 / max(p_fav_set1, 1e-6))
        win = self.net(exit_profit_lay(price, fair_price(fc.p_fav_from(0, 0, 1, 2, "dog"))))
        loss = self.net(exit_profit_lay(price, fair_price(fc.p_fav_from(0, 0, 2, 1, "fav"))))
        pnl = p_dog_first_given * win + (1 - p_dog_first_given) * loss
        return p_dog_first_given, pnl


class OverGames(Strategy):
    key = "tn_over_games"
    label = "Back Over total games"
    description = ("Back Over the games line nearest the model's median when two strong servers meet. "
                   "Held to settlement; green up after a tight first set if you prefer.")
    best_for = "Evenly matched players who both hold serve (serve points 64%+), fast courts, best-of-five."
    avoid_when = "A heavy favourite (short matches), clay returners, or a player with injury doubts."
    inplay = False

    def trade_rules(self) -> list[dict]:
        """The plan's in-play steps as rules for armed auto-trading (independent of today's prices)."""
        return [R.rule("tiebreak", R.set_tiebreak(1), R.green(0), "Set 1 reaches a tiebreak: lay Over to lock the profit.", final=True),
                                     R.rule("rout", R.set_won_easily(1, "fav", 2), R.green(0), "Favourite takes set 1 6-0, 6-1 or 6-2: lay Over to cut the loss.", final=True)]

    def evaluate(self, fc: TennisForecast, prices: MarketPrices) -> StrategyResult | None:
        # choose the line with P(over) closest to 55%, among the lines the exchange actually quotes when known
        quoted = []
        for key in getattr(prices, "quotes", {}) or {}:
            if key.startswith("TOTAL_GAMES:Over "):
                try:
                    quoted.append(float(key.split("Over ", 1)[1]))
                except ValueError:
                    pass
        candidates = {ln: p for ln, p in fc.p_over.items() if not quoted or any(abs(ln - q) < 1e-6 for q in quoted)} or dict(fc.p_over)
        line, p_over = min(candidates.items(), key=lambda kv: abs(kv[1] - 0.55))
        if p_over < 0.5 or fc.p_fav > 0.8:
            return None
        q = prices.quote("TOTAL_GAMES", f"Over {line:g}") if hasattr(prices, "quote") else None
        price, is_market = self.price_or_fair(q.best_back if q and q.best_back else None, p_over)
        scenarios = [Scenario(f"more than {line:g} games", p_over, price - 1), Scenario(f"{line:g} games or fewer", 1 - p_over, -1.0)]
        plan = [
            entry(f"Before the match: back Over {line:g} games at {price:.2f} or higher."),
            inplay("If the first set goes to a tiebreak you are well ahead: lay Over to lock profit if the market offers it."),
            stop("If the favourite races through set one 6-1 or 6-2, consider cutting the loss; the maximum loss is the stake."),
        ]
        rationale = [f"Model expects {fc.expected_games:.1f} games; P(over {line:g}) {p_over:.0%}",
                     f"Both hold serve well: {fc.pa_serve:.0%} and {fc.pb_serve:.0%} of serve points", f"Match {fc.p_a:.0%} / {1-fc.p_a:.0%}"]
        return StrategyResult("Total Games", "back", f"Over {line:g}", 0, 1 / p_over, None, None, scenarios, plan, rationale,
                              orders=[OrderLeg("TOTAL_GAMES", f"Over {line:g}", "back", round(price, 2), 1.0, "stake", "total games line", p_model=p_over)],
                              rules=self.trade_rules())

    def settle(self, fc: TennisForecast, result: TennisResult) -> tuple[float, float]:
        line, p_over = min(fc.p_over.items(), key=lambda kv: abs(kv[1] - 0.55))
        price, _ = self.price_or_fair(None, p_over)
        if result.retired:
            return 0.0, 0.0  # Betfair voids totals on retirement
        over = result.total_games > line
        return (1.0 if over else 0.0), (self.net(price - 1) if over else -1.0)


class FavouriteStraightSets(Strategy):
    key = "tn_straight_sets"
    label = "Back favourite in straight sets"
    description = "Back the favourite to win without dropping a set in the Set Betting market. Settlement bet with a clear model edge check."
    best_for = "Dominant favourites (70%+) on their best surface against opponents who do not hold serve well."
    avoid_when = "Best-of-five against a big server, or when the favourite is priced near evens."
    inplay = False

    def trade_rules(self) -> list[dict]:
        """The plan's in-play steps as rules for armed auto-trading (independent of today's prices)."""
        return [R.rule("halved", R.all_of(R.set_won(1, "fav"), R.price_ratio_at_most(0, 0.5)), R.green(0),
                                            "Favourite wins set 1 and the price has halved: lay the same selection to green up.", final=True)]

    def evaluate(self, fc: TennisForecast, prices: MarketPrices) -> StrategyResult | None:
        fav_home = fc.p_a >= 0.5
        key = ("2-0" if fav_home else "0-2") if fc.best_of == 3 else ("3-0" if fav_home else "0-3")
        p = fc.p_sets.get(key, 0.0)
        if p < 0.42:
            return None
        mkt = prices.correct_scores.get(key)  # set-betting prices ride in the correct_scores slot
        price, is_market = self.price_or_fair(mkt, p)
        scenarios = [Scenario("favourite wins in straight sets", p, price - 1), Scenario("drops a set or loses", 1 - p, -1.0)]
        edge = self.edge_back(p, price) if is_market else None
        fav = fc.fav_name()
        label = key.replace("-", " - ")
        plan = [entry(f"Before the match: back {fav} {label} in Set Betting at {price:.2f} or higher."),
                exit_(f"If {fav} take the first set comfortably: lay the same selection to green if the price has halved."),
                stop("Hold otherwise; the maximum loss is the stake.")]
        rationale = [f"Model: straight sets {p:.0%} (fair {1/p:.2f}) vs {price:.2f}" + (" [exchange]" if is_market else " [model]"),
                     f"{fav} win each set about {(fc.p_set1_a if fav_home else 1-fc.p_set1_a):.0%} of the time"]
        return StrategyResult("Set Betting", "back", f"{fav} {label}", 0, 1 / p, mkt if is_market else None, edge, scenarios, plan, rationale,
                              orders=[OrderLeg("SET_BETTING", key, "back", round(price, 2), 1.0, "stake", "straight sets", p_model=p)],
                              rules=self.trade_rules())

    def settle(self, fc: TennisForecast, result: TennisResult) -> tuple[float, float]:
        fav_home = fc.p_a >= 0.5
        key = ("2-0" if fav_home else "0-2") if fc.best_of == 3 else ("3-0" if fav_home else "0-3")
        price, _ = self.price_or_fair(None, fc.p_sets.get(key, 0.0))
        w, l = result.set_score
        hit = (result.winner == fc.fav_name()) and l == 0 and not result.retired
        return (1.0 if hit else 0.0), (self.net(price - 1) if hit else -1.0)


TENNIS_STRATEGIES: list[Strategy] = [BackToLayFavouriteSet(), LayFavouriteEarlyBreak(), OverGames(), FavouriteStraightSets()]


def get_tennis_strategy(key: str) -> Strategy:
    for s in TENNIS_STRATEGIES:
        if s.key == key:
            return s
    raise KeyError(key)
