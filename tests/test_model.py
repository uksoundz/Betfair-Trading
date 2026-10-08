import numpy as np
import pytest

from tradescout.model.inplay import conditional_matrix, exit_profit_back, exit_profit_lay, market_probs
from tradescout.model.timing import cumulative_share, p_goal_before


def test_fit_is_sane(forecaster):
    m = forecaster.model
    assert 0.05 < m.home_adv < 0.5
    assert -0.3 < m.rho < 0.3
    assert abs(float(np.mean(m.attack))) < 1e-6
    # the best attacking side should be one of the usual suspects
    best = m.ratings_table()[0][0]
    assert best in {"Arsenal FC", "FC Bayern München", "Manchester City FC", "Liverpool FC", "Paris Saint-Germain FC", "FC Barcelona", "Real Madrid CF"}


def test_forecast_probabilities(forecaster, fixtures):
    fc = forecaster.forecast(fixtures[0])
    assert abs(fc.score_matrix.sum() - 1) < 1e-9
    assert abs(fc.p_home + fc.p_draw + fc.p_away - 1) < 1e-9
    assert fc.p_over[0.5] > fc.p_over[1.5] > fc.p_over[2.5] > fc.p_over[3.5]
    assert 0 < fc.p_btts < 1
    assert fc.p_goal_before[15] < fc.p_goal_before[45] < fc.p_goal_before[85]
    assert 0 <= fc.confidence <= 1


def test_unknown_team_lowers_confidence(forecaster, fixtures):
    from dataclasses import replace
    fx = replace(fixtures[0], home="Imaginary Town FC")
    fc = forecaster.forecast(fx)
    assert fc.confidence < 0.5
    assert any("No rating" in n for n in fc.notes)


def test_timing_monotone():
    assert cumulative_share(0) == 0
    assert cumulative_share(90) == 1
    assert cumulative_share(45) == pytest.approx(0.455)
    assert p_goal_before(2.7, 70) > p_goal_before(2.7, 30)


def test_conditional_matrix_shifts_score():
    m = conditional_matrix(1.5, 1.2, 1, 0, 30)
    assert abs(m.sum() - 1) < 1e-9
    assert m[0, :].sum() == 0  # home cannot finish with fewer than 1 goal
    probs = market_probs(m)
    assert probs["home"] > 0.5
    late = market_probs(conditional_matrix(1.5, 1.2, 0, 0, 80))
    assert late["draw"] > 0.5 and late["over_25"] < 0.05


def test_exit_profit_formulas():
    # back at 2.0, lay at 1.5 -> profit 2/1.5-1 = 0.33 before friction
    assert exit_profit_back(2.0, 1.5, friction=0) == pytest.approx(1 / 3)
    # lay at 4.0 then back at 8.0: stake per liability 1/3, profit 1/3 * (1 - 0.5)
    assert exit_profit_lay(4.0, 8.0, friction=0) == pytest.approx(1 / 6)
    assert exit_profit_lay(4.0, 2.0, friction=0) < 0
