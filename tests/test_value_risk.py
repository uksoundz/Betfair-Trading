import pytest

from tradescout.risk import Exposure, RiskLimits, advise_stake, kelly_fraction, risk_of_ruin
from tradescout.value import Quote, assess, net_ev, simulate_fill


def test_net_ev_back_and_lay_with_commission():
    # fair bet at 2.0 with p=0.5: EV = -commission/2
    assert net_ev(0.5, 2.0, "back", 0.05) == pytest.approx(0.5 * 1 * 0.95 - 0.5)
    # lay at 3.0 with p=1/3 is fair before commission
    ev = net_ev(1 / 3, 3.0, "lay", 0.0)
    assert ev == pytest.approx(0.0, abs=1e-9)
    assert net_ev(1 / 3, 3.0, "lay", 0.05) < 0


def test_fill_simulation_walks_the_ladder():
    ladder = [(2.10, 50.0), (2.08, 100.0), (2.06, 500.0)]
    f = simulate_fill(ladder, 2.08, 120.0, "back")
    assert f.fillable == 120.0 and f.fraction == 1.0 and 2.08 < f.avg_price < 2.10
    f2 = simulate_fill(ladder, 2.12, 50.0, "back")
    assert f2.fraction == 0.0 and f2.avg_price is None
    lay = [(1.90, 30.0), (1.92, 30.0)]
    f3 = simulate_fill(lay, 1.91, 50.0, "lay")
    assert f3.fillable == 30.0 and f3.fraction == pytest.approx(0.6)


def test_assess_decisions():
    q = Quote(back=[(2.40, 500.0)], lay=[(2.44, 500.0)], total_matched=50000)
    # model 55% vs market ~41%: a large gap is needed because the model only gets 30% weight
    a = assess(0.55, "back", q, 2.40, 20.0, "MATCH_ODDS", 1.0, 0.05, 0.02, 0.04)
    assert a.decision == "TRADE" and a.ev_conservative > 0.02 and a.p_market == pytest.approx(2 / 4.84)
    # a 7-point gap is not enough after shrinkage and commission
    b = assess(0.48, "back", q, 2.40, 20.0, "MATCH_ODDS", 1.0, 0.05, 0.02, 0.04)
    assert b.decision == "NO TRADE" and 0 < b.ev_conservative < 0.02
    # low confidence pulls the probability back to the market
    c = assess(0.55, "back", q, 2.40, 20.0, "MATCH_ODDS", 0.2, 0.05, 0.02, 0.04)
    assert c.p_conservative < a.p_conservative and c.decision == "NO TRADE"
    # a wide spread shades execution and is noted; only a spread so wide the mid means nothing blocks
    wide = Quote(back=[(2.40, 500.0)], lay=[(2.70, 500.0)], total_matched=50000)
    d = assess(0.55, "back", wide, 2.40, 20.0, "MATCH_ODDS", 1.0, 0.05, 0.02, 0.04)
    assert d.decision == "NO TRADE" and not d.price_reliable and any("No reliable exchange price" in r for r in d.reasons)
    moderately = Quote(back=[(2.40, 500.0)], lay=[(2.56, 500.0)], total_matched=50000)  # 6.7%: noted, shaded, not blocked
    d2 = assess(0.55, "back", moderately, 2.40, 20.0, "MATCH_ODDS", 1.0, 0.05, 0.02, 0.04)
    assert d2.decision == "TRADE" and d2.execution < a.execution and any("wide right now" in r for r in d2.reasons)
    # thin money now is a note, not a blocker: the order rests until kick-off
    quiet = Quote(back=[(2.40, 500.0)], lay=[(2.44, 500.0)], total_matched=300)
    d3 = assess(0.55, "back", quiet, 2.40, 20.0, "MATCH_ODDS", 1.0, 0.05, 0.02, 0.04)
    assert d3.decision == "TRADE" and any("thin now" in r for r in d3.reasons) and d3.execution < a.execution
    # an offer on one side only has no midpoint: no edge can be claimed
    lonely = Quote(back=[(1.01, 50.0)], lay=[], total_matched=0)
    d5 = assess(0.6, "back", lonely, 1.01, 20.0, "SET_BETTING", 1.0, 0.05, 0.001, 0.04)
    assert d5.decision == "NO TRADE" and not d5.price_reliable and any("one side only" in r for r in d5.reasons)
    # trusting the model more raises the conservative edge
    d4 = assess(0.48, "back", q, 2.40, 20.0, "MATCH_ODDS", 1.0, 0.05, 0.02, 0.04, model_weight_scale=2.0)
    assert d4.ev_conservative > b.ev_conservative
    # no quote: research only
    e = assess(0.48, "back", None, 2.40, 20.0, "MATCH_ODDS", 1.0, 0.05, 0.02, 0.04)
    assert e.decision == "RESEARCH" and e.execution == 0.0
    # stale snapshot: flagged, then blocked
    from datetime import datetime, timedelta, timezone
    old = Quote(back=[(2.40, 500.0)], lay=[(2.44, 500.0)], total_matched=50000,
                as_of=(datetime.now(timezone.utc) - timedelta(minutes=7)).isoformat())
    f = assess(0.55, "back", old, 2.40, 20.0, "MATCH_ODDS", 1.0, 0.05, 0.02, 0.04)
    assert f.decision == "TRADE" and any("minutes old" in r for r in f.reasons) and f.execution < a.execution
    older = Quote(back=[(2.40, 500.0)], lay=[(2.44, 500.0)], total_matched=50000,
                  as_of=(datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat())
    g = assess(0.55, "back", older, 2.40, 20.0, "MATCH_ODDS", 1.0, 0.05, 0.02, 0.04)
    assert g.decision == "NO TRADE"
    # partial fill: only part of the size is available at the plan price
    thin = Quote(back=[(2.40, 8.0)], lay=[(2.44, 500.0)], total_matched=50000)
    h = assess(0.55, "back", thin, 2.40, 20.0, "MATCH_ODDS", 1.0, 0.05, 0.02, 0.04)
    assert h.fill_fraction == pytest.approx(0.4) and any("partial" in r for r in h.reasons)


def test_kelly_and_stake_caps():
    assert kelly_fraction(0.5, 1.0, -1.0) == 0.0
    assert kelly_fraction(0.6, 1.0, -1.0) == pytest.approx(0.2)
    limits = RiskLimits()
    ex = Exposure(current_bank=1000, peak_bank=1000)
    adv = advise_stake(0.6, 1.0, -1.0, 1.0, 1000, limits, ex, "ltd", "football")
    assert not adv.blocked and adv.risk_money <= 20.0 and any("per-trade cap" in c for c in adv.caps_hit)  # 0.25*0.2 = 5% -> capped at 2%
    # exposure cap: already 9.5% open -> only 0.5% room
    ex2 = Exposure(open_total=95.0, current_bank=1000, peak_bank=1000)
    adv2 = advise_stake(0.6, 1.0, -1.0, 1.0, 1000, limits, ex2, "ltd", "football")
    assert adv2.risk_money <= 5.0 and "open exposure cap" in adv2.caps_hit
    # daily loss limit blocks
    ex3 = Exposure(realised_today=-31.0, current_bank=969, peak_bank=1000)
    assert advise_stake(0.6, 1.0, -1.0, 1.0, 1000, limits, ex3, "ltd", "football").blocked
    # drawdown halves
    ex4 = Exposure(current_bank=850, peak_bank=1000)
    adv4 = advise_stake(0.55, 1.0, -1.0, 1.0, 850, limits, ex4, "ltd", "football")
    assert any("halved" in c for c in adv4.caps_hit)
    # negative EV -> below minimum -> blocked
    assert advise_stake(0.4, 1.0, -1.0, 1.0, 1000, limits, ex, "ltd", "football").blocked


def test_risk_of_ruin_shape():
    r = risk_of_ruin(0.55, 1.0, -1.0, 0.02, n_trades=200, sims=500)
    assert 0 <= r["p_ruin"] <= 1 and r["median_final"] > 0.5
    aggressive = risk_of_ruin(0.55, 1.0, -1.0, 0.25, n_trades=200, sims=500)
    assert aggressive["p_ruin"] > r["p_ruin"]
