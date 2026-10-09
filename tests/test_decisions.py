"""End-to-end decision logic: the same fixtures through the scorer with no prices, with fair prices
and with generous prices must produce RESEARCH, NO TRADE and TRADE respectively, with stakes only
on trades and every cap respected. Also: multi-sport journal settlement and the signals log."""
from datetime import date

import pytest

from tradescout.config import settings
from tradescout.data.base import NoPrices
from tradescout.models import MarketPrices
from tradescout.ranking import Calibration
from tradescout.scout import Scout
from tradescout.strategies import ALL_STRATEGIES
from tradescout.value import Quote
from tests.conftest import AS_OF


class FairPrices:
    """Exchange prices exactly at the model's fair price: no edge after commission."""

    def __init__(self, forecaster):
        self.fc = forecaster

    def prices(self, fx):
        fc = self.fc.forecast(fx)
        mp = MarketPrices(source="fake", home=1 / fc.p_home, draw=1 / fc.p_draw, away=1 / fc.p_away,
                          over_25=1 / fc.p_over[2.5], under_25=1 / (1 - fc.p_over[2.5]), as_of="2025-11-08T10:00:00")

        def q(p):
            fair = 1 / p
            return Quote(back=[(round(fair * 0.99, 2), 5000.0)], lay=[(round(fair * 1.01, 2), 5000.0)], total_matched=100000)
        mp.quotes = {"MATCH_ODDS:home": q(fc.p_home), "MATCH_ODDS:draw": q(fc.p_draw), "MATCH_ODDS:away": q(fc.p_away),
                     "OVER_UNDER_25:Over 2.5 Goals": q(fc.p_over[2.5]), "OVER_UNDER_25:Under 2.5 Goals": q(1 - fc.p_over[2.5]),
                     "CORRECT_SCORE:1-1": q(fc.p_cs["1-1"]), "CORRECT_SCORE:0-0": q(fc.p_cs["0-0"])}
        for s, p in fc.p_cs.items():
            mp.quotes.setdefault(f"CORRECT_SCORE:{s}", q(max(p, 1e-4)))
        return mp


class GenerousPrices(FairPrices):
    """Backs 40% longer and lays 25% shorter than fair: clear edge everywhere."""

    def prices(self, fx):
        mp = super().prices(fx)
        for k, qt in mp.quotes.items():
            qt.back = [(round(p * 1.4, 2), s) for p, s in qt.back]
            qt.lay = [(round(max(1.02, p * 0.75), 2), s) for p, s in qt.lay]
        return mp


def test_research_without_prices(provider):
    scout = Scout(provider, provider, NoPrices(), strategies=ALL_STRATEGIES, calibration=Calibration())
    scan = scout.scan(AS_OF)
    assert scan.ideas and all(i.decision == "RESEARCH" and i.stake_money == 0 and i.score <= 39 for i in scan.ideas)


def test_fair_prices_give_no_trade(provider):
    scout = Scout(provider, provider, NoPrices(), strategies=ALL_STRATEGIES, calibration=Calibration())
    fc = scout.forecaster(AS_OF)
    scout.prices = FairPrices(fc)
    scan = scout.scan(AS_OF, forecaster=fc)
    priced = [i for i in scan.ideas if i.decision != "RESEARCH"]
    assert priced, "fair quotes should price the ideas"
    assert all(i.decision == "NO TRADE" for i in priced)
    assert all(i.stake_money == 0 for i in priced)
    assert all(i.ev_conservative is not None and i.ev_conservative < settings.min_edge for i in priced)


def test_generous_prices_give_trades_with_capped_stakes(provider):
    scout = Scout(provider, provider, NoPrices(), strategies=ALL_STRATEGIES, calibration=Calibration())
    fc = scout.forecaster(AS_OF)
    scout.prices = GenerousPrices(fc)
    scan = scout.scan(AS_OF, forecaster=fc)
    trades = [i for i in scan.ideas if i.decision == "TRADE"]
    assert trades, "40% overpricing must produce trades"
    for i in trades:
        assert i.ev_conservative >= settings.min_edge and i.execution > 0
        assert 0 < i.risk_money <= settings.bank * 0.02 + 1e-6, "per-trade cap"
        assert i.score > 0
    # ranking: trades first, then by score
    assert scan.ideas[0].decision == "TRADE"


def test_journal_multi_sport_and_signals(tmp_path, provider):
    from tradescout import signals
    from tradescout.journal import Journal
    from tradescout.tennis.data import TennisProvider
    from tradescout.tennis.scout import TennisScout
    fb = Scout(provider, provider, NoPrices(), calibration=Calibration())
    tp = TennisProvider()
    tn = TennisScout(tp, tp, NoPrices(), calibration=Calibration())
    j = Journal(path=tmp_path / "j.json")
    fscan = fb.scan(AS_OF)
    tscan = tn.scan(date(2025, 6, 2))
    j.add(fscan.ideas[0], 10.0)
    j.add(tscan.ideas[0], 10.0)
    n = j.settle_multi({"football": fb, "tennis": tn}, lambda sport, fx: tp.result_for(fx) if sport == "tennis" else provider.result_for(fx))
    assert n == 2 and {e.sport for e in j.entries} == {"football", "tennis"}
    assert all(e.status in ("won", "lost") and e.result for e in j.entries)
    path = tmp_path / "signals.jsonl"
    assert signals.record(fscan.ideas[:5] + tscan.ideas[:2], "none", path) == 7
    rows = signals.load(path)
    assert len(rows) == 7 and rows[0]["decision"] == "RESEARCH"
    lookup = lambda sport, fx: tp.result_for(fx) if sport == "tennis" else provider.result_for(fx)
    with_result = sum(1 for r in rows if lookup(r["sport"], __import__("tradescout.models", fromlist=["Fixture"]).Fixture(
        date.fromisoformat(r["date"]), r["league"], r["home"], r["away"])) is not None)
    settled = signals.settle_signals(lookup, lambda sport, d: tn.forecaster(d) if sport == "tennis" else fb.forecaster(d), path)
    assert settled == with_result >= 6
    s = signals.summary(signals.load(path))
    assert s["total"] == 7 and s["settled"] == settled


def test_combined_hedge_settles_as_one_position(provider, forecaster, fixtures):
    """Overs + 1-1 insurance: a winning insurance leg is not a winning position."""
    from tradescout.models import MatchResult
    from tradescout.strategies import get_strategy
    s = get_strategy("over25_ins")
    fc = next(forecaster.forecast(f) for f in fixtures if s.evaluate(forecaster.forecast(f), MarketPrices()) is not None)
    r = s.evaluate(fc, MarketPrices())
    o_price, c_price = r.orders[0].price, r.orders[1].price
    res_11 = MatchResult(fc.fixture.date, fc.fixture.league, fc.fixture.home, fc.fixture.away, 1, 1, 0, 0)
    hit, pnl = s.settle(fc, res_11)
    expected = 0.2 * (c_price - 1) - 0.8
    assert pnl == pytest.approx(expected * (0.98 if expected > 0 else 1.0), abs=0.02)
    assert (hit == 1.0) == (expected > 0)
    res_10 = MatchResult(fc.fixture.date, fc.fixture.league, fc.fixture.home, fc.fixture.away, 1, 0, 1, 0)
    assert s.settle(fc, res_10) == (0.0, -1.0)


def test_stars_grade_trades_on_the_current_score_scale():
    from datetime import date
    from tradescout.models import Fixture, TradeIdea
    def idea(score, decision):
        return TradeIdea(Fixture(date(2026, 1, 1), "en.1", "A", "B"), "x", "x", "M", "back", "s", 0.5, 2.0, None, None, 0.0, 1.0, -1.0, 0.5, 0.0,
                         None, 0, 1.0, 1.0, score, 0.0, [], [], decision=decision)
    assert [idea(s, "TRADE").stars for s in (1, 5, 15, 30, 50, 80)] == [1, 2, 3, 4, 5, 5]
    assert idea(39, "NO TRADE").stars == 0 and idea(39, "RESEARCH").stars == 0
