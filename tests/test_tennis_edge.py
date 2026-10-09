"""Market-anchored tennis: the corrected set model, set betting against match odds, and the conditional in-play lay
after a clear first-set loss (strategy, scorer decision, journal settlement)."""
from datetime import date

import pytest

from tradescout.config import settings
from tradescout.journal import Journal
from tradescout.models import Fixture, MarketPrices
from tradescout.ranking import Calibration, Scorer
from tradescout.tennis.data import TennisResult
from tradescout.tennis.market_model import correction, lay_limit, market_view, p_fav_from_quotes
from tradescout.tennis.strategies import LayFavLostSet1, SetsValue
from tradescout.value import Quote, net_ev

DAY = date(2026, 10, 10)


def _fc(best_of=3):
    from tradescout.tennis.elo import TennisElo
    from tradescout.tennis.forecast import TennisForecaster
    fx = Fixture(DAY, "atp.500", "Home Player", "Away Player", None, "t1", {"sport": "tennis", "surface": "Hard", "best_of": best_of})
    return TennisForecaster(TennisElo()).forecast(fx)


def _prices(home_back, home_lay, away_back, away_lay, sets=None):
    mp = MarketPrices(source="betfair", status="ok", as_of=None)
    mp.quotes["MATCH_ODDS:home"] = Quote([(home_back, 500.0)], [(home_lay, 500.0)], 50000.0)
    mp.quotes["MATCH_ODDS:away"] = Quote([(away_back, 500.0)], [(away_lay, 500.0)], 50000.0)
    mp.home, mp.away = home_back, away_back
    for k, (b, l) in (sets or {}).items():
        mp.quotes[f"SET_BETTING:{k}"] = Quote([(b, 200.0)], [(l, 200.0)], 8000.0)
    return mp


def test_market_view_is_consistent_with_the_match_price_and_uses_validated_corrections():
    for p in (0.55, 0.65, 0.8):
        v = market_view(p, 3)
        assert sum(v.sets.values()) == pytest.approx(1.0, abs=1e-9)
        assert v.sets["2-0"] + v.sets["2-1"] == pytest.approx(p, abs=1e-9)
        assert v.corrected["straight_fav"] and v.sets["2-0"] > v.markov["sets"]["2-0"]  # favourites win in straight sets more often
        assert v.corrected["after_lost_set1_clear"] and v.after_lost_set1_clear < v.after_lost_set1 < v.markov["after_lost_set1"]
    v5 = market_view(0.7, 5)
    assert sum(v5.sets.values()) == pytest.approx(1.0) and set(v5.sets) == {"3-0", "3-1", "3-2", "2-3", "1-3", "0-3"}
    assert not v5.corrected["after_lost_set1_clear"] and v5.after_lost_set1_clear == v5.markov["after_lost_set1"]  # did not validate: plain model
    assert correction("after_lost_set1", 5) is None and correction("after_lost_set1_clear", 3)["n_holdout"] > 300
    # orientation: an away favourite's 2-0 is the home-away key 0-2
    assert market_view(0.7, 3).set_score_home_away("away")["0-2"] == pytest.approx(market_view(0.7, 3).sets["2-0"])


def test_lay_limit_keeps_the_stated_margin():
    q, c, m = 0.27, 0.05, 0.03
    L = lay_limit(q, c, m)
    assert net_ev(q, L, "lay", c) == pytest.approx(m, abs=1e-9)
    assert net_ev(q, L - 0.1, "lay", c) > m


def test_p_fav_needs_two_sided_books_on_both_players():
    assert p_fav_from_quotes(Quote([(1.5, 10)], [(1.52, 10)]), Quote([(2.9, 10)], [(2.96, 10)]))[1] == "home"
    assert p_fav_from_quotes(Quote([(1.5, 10)], []), Quote([(2.9, 10)], [(2.96, 10)])) is None
    assert p_fav_from_quotes(None, None) is None


def test_sets_value_needs_match_odds_and_trades_only_a_mispriced_set_score():
    s, fc = SetsValue(), _fc()
    assert s.evaluate(fc, MarketPrices()) is None
    p_fav = p_fav_from_quotes(Quote([(1.5, 1)], [(1.52, 1)]), Quote([(2.9, 1)], [(2.96, 1)]))[0]
    fair = market_view(p_fav, 3, "Hard").set_score_home_away("home")
    at_fair = {k: (round(1 / p * 0.99, 2), round(1 / p * 1.01, 2)) for k, p in fair.items()}
    r = s.evaluate(fc, _prices(1.5, 1.52, 2.9, 2.96, at_fair))
    sc = Scorer(Calibration())
    idea = sc.score(fc.fixture, fc, s, r, prices=_prices(1.5, 1.52, 2.9, 2.96, at_fair), sport="tennis")
    assert idea.decision == "NO TRADE"  # priced at fair: no edge after commission
    rich = dict(at_fair)
    rich["2-0"] = (round(1 / fair["2-0"] * 1.25, 2), round(1 / fair["2-0"] * 1.28, 2))  # the favourite's straight sets 25% too long
    prices = _prices(1.5, 1.52, 2.9, 2.96, rich)
    r = s.evaluate(fc, prices)
    assert r.orders[0].selection == "2-0" and r.orders[0].side == "back" and r.fav == "home" and r.orders[0].model_weight == 0.6
    idea = sc.score(fc.fixture, fc, s, r, prices=prices, sport="tennis")
    assert idea.decision == "TRADE" and idea.ev_conservative >= settings.min_edge and idea.stake_money > 0
    assert idea.evidence == "exchange-priced-static"


def test_sets_value_journal_settles_from_the_slip():
    s = SetsValue()

    class E:
        home, away, slip = "Home Player", "Away Player", [{"market": "SET_BETTING", "selection": "2-0", "side": "back", "plan_price": 2.5, "size": 4.0}]
    won = TennisResult(DAY, "atp.500", "x", "Hard", 3, "Home Player", "Away Player", "6-3 6-4", None, None, DAY, "m1")
    lost = TennisResult(DAY, "atp.500", "x", "Hard", 3, "Home Player", "Away Player", "6-3 4-6 6-4", None, None, DAY, "m2")
    st, per, money = s.settle_entry(E, won)
    assert st == "won" and money == pytest.approx(4.0 * 1.5 * (1 - settings.commission))
    assert s.settle_entry(E, lost) == ("lost", -1.0, -4.0)


def test_lay_after_clear_set_one_loss_is_an_arm_idea_with_a_value_limit():
    s, fc = LayFavLostSet1(), _fc()
    assert s.evaluate(fc, MarketPrices()) is None  # anchored to the exchange: nothing without match odds
    assert s.evaluate(_fc(5), _prices(1.5, 1.52, 2.9, 2.96)) is None  # best of five did not validate
    assert s.evaluate(fc, _prices(1.1, 1.11, 11.0, 11.5)) is None  # too short a favourite
    prices = _prices(2.9, 2.96, 1.5, 1.52)  # away favourite
    r = s.evaluate(fc, prices)
    info = r.entry_info
    assert r.entry == "inplay" and not r.orders and r.fav == "away" and info["selection"] == "away" and info["side"] == "lay"
    assert net_ev(info["p_selection"], info["limit"], "lay", settings.commission) >= 0.03 - 1e-9
    enter = next(x for x in r.rules if x["do"]["a"] == "enter")
    assert enter["when"] == {"t": "set_won_easily", "set": 1, "by": "dog", "max": 3} and enter["do"]["limit"] == info["limit"] and enter["final"]
    assert [x["id"] for x in r.rules] == ["lost_set1_clear", "fav_won_set1", "lost_set1_close"]
    idea = Scorer(Calibration()).score(fc.fixture, fc, s, r, prices=prices, sport="tennis")
    assert idea.decision == "ARM" and idea.fav == "away" and idea.entry == "inplay" and idea.stake_money > 0 and idea.score < 40 and idea.stars == 0
    assert idea.ev_conservative is None  # no edge is claimed before the exchange has offered the price


def test_lay_after_set_one_journal_settlement(tmp_path):
    s = LayFavLostSet1()

    class E:
        home, away, fav, placed = "Home Player", "Away Player", "home", "live"
        slip = [{"market": "MATCH_ODDS", "selection": "home", "side": "lay", "plan_price": 3.0, "size": 5.0}]
    comeback = TennisResult(DAY, "atp.500", "x", "Hard", 3, "Home Player", "Away Player", "3-6 6-4 6-4", None, None, DAY, "m1")
    held = TennisResult(DAY, "atp.500", "x", "Hard", 3, "Away Player", "Home Player", "6-3 6-4", None, None, DAY, "m2")
    assert s.settle_entry(E, comeback) == ("lost", -1.0, -10.0)
    st, per, money = s.settle_entry(E, held)
    assert st == "won" and money == pytest.approx(5.0 * (1 - settings.commission))
    E.placed = ""
    assert s.settle_entry(E, held) == ("void", 0.0, 0.0)  # armed but never entered: nothing to settle
    j = Journal(path=tmp_path / "j.json")
    assert j.summary()["settled"] == 0
