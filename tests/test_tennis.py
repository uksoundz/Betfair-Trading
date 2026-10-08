from datetime import date

import pytest

from tradescout.data.base import NoPrices
from tradescout.data.betfair_tennis import PlayerMatcher, classify_competition, surname_initial
from tradescout.models import Fixture, MarketPrices
from tradescout.ranking import Calibration
from tradescout.tennis.calibration import TennisCalibration
from tradescout.tennis.data import TennisProvider, TennisResult
from tradescout.tennis.forecast import TennisForecaster
from tradescout.tennis.markov import match_distribution, p_first_break, p_game, p_match_from, p_tiebreak, set_distribution, solve_serve_probs
from tradescout.tennis.scout import TennisScout
from tradescout.tennis.strategies import TENNIS_STRATEGIES

AS_OF = date(2025, 6, 2)


@pytest.fixture(scope="module")
def tprov():
    return TennisProvider()


@pytest.fixture(scope="module")
def tfc(tprov):
    return TennisForecaster.fit(tprov.results(before=AS_OF), AS_OF)


def test_markov_consistency():
    assert p_game(0.5) == pytest.approx(0.5)
    assert p_tiebreak(0.64, 0.64) == pytest.approx(0.5)
    assert abs(sum(set_distribution(0.66, 0.60, "A").values()) - 1) < 1e-9
    d = match_distribution(0.66, 0.60, 3, "A")
    assert abs(sum(d["sets"].values()) - 1) < 1e-9 and abs(sum(d["games"].values()) - 1) < 1e-9
    assert d["p_match"] == pytest.approx(p_match_from(0.66, 0.60, 3, 0, 0, 0, 0, "A"))
    assert p_match_from(0.66, 0.60, 3, 1, 0, 0, 0, "A") > d["p_match"] > p_match_from(0.66, 0.60, 3, 0, 1, 0, 0, "A")
    assert p_match_from(0.66, 0.60, 3, 2, 0, 0, 0, "A") == 1.0 and p_match_from(0.66, 0.60, 3, 0, 2, 0, 0, "A") == 0.0
    a, b = p_first_break(0.66, 0.60, "A")
    assert a > b and a + b < 1
    pa, pb = solve_serve_probs(0.75, 3, "Hard")
    assert pa > pb and match_distribution(pa, pb, 3, "A")["p_match"] == pytest.approx(0.75, abs=0.01)


def test_result_parsing_and_matching(tprov):
    r = TennisResult(date(2025, 1, 1), "atp.250", "T", "Hard", 3, "A", "B", "7-6(5) 3-6 6-4", 10, 20, date(2025, 1, 1), "x")
    assert r.sets() == [(7, 6), (3, 6), (6, 4)] and r.set_score == (2, 1) and r.total_games == 32 and r.first_set_winner == "A"
    ret = TennisResult(date(2025, 1, 1), "atp.250", "T", "Hard", 3, "A", "B", "6-4 2-1 RET", 10, 20, date(2025, 1, 1), "y")
    assert ret.retired
    # every fixture maps back to its own result (the 2025 file has blank match numbers)
    for d in (date(2025, 8, 26), date(2025, 1, 13)):
        for fx in tprov.fixtures(d):
            res = tprov.result_for(fx)
            assert res is not None and {res.winner, res.loser} == {fx.home, fx.away}
    assert len({r.match_id for r in tprov.results()}) == len(tprov.results())


def test_forecast_and_strategies(tprov, tfc):
    fx = tprov.fixtures(AS_OF)[0]
    fc = tfc.forecast(fx)
    assert 0 < fc.p_a < 1 and abs(sum(fc.p_sets.values()) - 1) < 1e-6 and abs(sum(fc.p_games.values()) - 1) < 1e-6
    assert fc.cond["set1_won"] > fc.p_a > fc.cond["set1_lost"]
    res = tprov.result_for(fx)
    for s in TENNIS_STRATEGIES:
        r = s.evaluate(fc, MarketPrices())
        if r is None:
            continue
        assert abs(sum(sc.prob for sc in r.scenarios) - 1) < 1e-6 and r.orders and r.orders[0].p_model > 0
        hit, pnl = s.settle(fc, res)
        assert 0 <= hit <= 1 and -1.0 - 1e-9 <= pnl <= 10


def test_retirement_settlement(tprov, tfc):
    fx = tprov.fixtures(AS_OF)[0]
    fc = tfc.forecast(fx)
    fav = fc.fav_name()
    dog = fc.dog_name()
    ret_fav_lost = TennisResult(AS_OF, fx.league, "T", "Clay", 5, dog, fav, "6-4 2-1 RET", 1, 2, AS_OF, "z")
    from tradescout.tennis.strategies import BackToLayFavouriteSet, OverGames
    hit, pnl = BackToLayFavouriteSet().settle(fc, ret_fav_lost)
    assert hit == 0.0 and pnl == -1.0  # favourite retired: full stake lost
    hit, pnl = OverGames().settle(fc, ret_fav_lost)
    assert pnl == 0.0  # totals void on retirement


def test_tennis_calibration_apply():
    cal = TennisCalibration(straight_a=0.3, straight_b=1.0, games_shift={"3": -1.5, "5": -3.0}, fitted_on="test")
    sets = {"2-0": 0.35, "2-1": 0.25, "1-2": 0.2, "0-2": 0.2}
    out = cal.apply_sets(sets, 0.7, 3)
    assert abs(sum(out.values()) - 1) < 1e-9 and out["2-0"] > 0.35 and out["2-1"] < 0.25
    games = {20: 0.5, 24: 0.5}
    shifted = cal.shift_games(games, 3)
    assert abs(sum(shifted.values()) - 1) < 1e-9 and sum(n * p for n, p in shifted.items()) == pytest.approx(22 - 1.5)


def test_scan_and_names(tprov):
    scout = TennisScout(tprov, tprov, NoPrices(), calibration=Calibration())
    scan = scout.scan(AS_OF)
    assert scan.fixtures and all(i.sport == "tennis" and i.decision == "RESEARCH" for i in scan.ideas)
    assert surname_initial("N Djokovic") == ("djokovic", "n") and surname_initial("Novak Djokovic") == ("djokovic", "n")
    m = PlayerMatcher(["Novak Djokovic", "Carlos Alcaraz", "Alex de Minaur"])
    assert m.resolve("N Djokovic") == "Novak Djokovic" and m.resolve("Alcaraz") == "Carlos Alcaraz" and m.same("C Alcaraz", "Carlos Alcaraz")
    assert classify_competition("ATP Paris Masters 2026") == ("atp.1000", 3)
    assert classify_competition("Wimbledon 2026 Men's Singles") == ("atp.gs", 5)
    assert classify_competition("WTA Rome") is None and classify_competition("Challenger Lyon") is None
