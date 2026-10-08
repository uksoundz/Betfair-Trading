import pytest

from tradescout.models import MarketPrices
from tradescout.strategies import ALL_STRATEGIES


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda s: s.key)
def test_scenarios_are_a_distribution(strategy, forecaster, fixtures, provider):
    evaluated = 0
    for fx in fixtures:
        fc = forecaster.forecast(fx)
        r = strategy.evaluate(fc, MarketPrices())
        if r is None:
            continue
        evaluated += 1
        assert abs(sum(s.prob for s in r.scenarios) - 1) < 1e-6
        assert 0 <= r.hit_prob <= 1
        assert r.edge is None  # no market feed
        assert r.plan and r.rationale
        result = provider.result_for(fx)
        if result is not None:
            hit, pnl = strategy.settle(fc, result)
            assert -1.0 - 1e-9 <= pnl <= 10
    assert evaluated > 0, "strategy never fires on a 32-fixture Saturday"


def test_market_prices_produce_edge(forecaster, fixtures):
    fc = forecaster.forecast(fixtures[0])
    generous = MarketPrices(home=10.0, draw=10.0, away=10.0, over_25=10.0, under_25=10.0, source="test",
                            correct_scores={"0-0": 50.0, "1-1": 20.0})
    for s in ALL_STRATEGIES:
        r = s.evaluate(fc, generous)
        if r is None:
            continue
        assert r.edge is not None
        if r.side == "back":
            assert r.edge > 0  # 10.0 is always value when the model says >10%
