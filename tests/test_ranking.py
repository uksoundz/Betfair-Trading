from tradescout.data.base import NoPrices
from tradescout.ranking import Calibration, Scorer
from tradescout.scout import Scout
from tests.conftest import AS_OF


def test_scan_ranks_ideas(provider):
    scout = Scout(provider, provider, NoPrices(), calibration=Calibration())
    scan = scout.scan(AS_OF)
    assert scan.ideas
    scores = [i.score for i in scan.ideas]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 100 for s in scores)
    assert all(0 <= i.stake_pct <= 5 for i in scan.ideas)
    assert len(scan.best_per_match()) <= len(scan.fixtures)


def test_calibration_shrinks_towards_history():
    cal = Calibration()
    for _ in range(300):
        cal.add("ltd", "en.1", hit=0.0, pnl=-0.3, predicted=0.7)  # a strategy that never hits
    sc = Scorer(cal)
    from tradescout.strategies import get_strategy
    p, hist, n = sc.calibrated_hit(get_strategy("ltd"), "en.1", 0.7)
    assert n == 300 and hist == 0.0
    assert p < 0.7


def test_kelly():
    assert Scorer.kelly(0.5, 1.0, -1.0) == 0.0
    assert Scorer.kelly(0.6, 1.0, -1.0) > 0
    assert Scorer.kelly(0.9, 0.1, -1.0) == 0.0  # negative EV
