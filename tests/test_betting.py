from datetime import date

import pytest

from tradescout.betting import MIN_STAKE, build_slip, place_slip, round_to_tick
from tradescout.data.base import NoPrices
from tradescout.models import Fixture
from tradescout.ranking import Calibration
from tradescout.scout import Scout
from tests.conftest import AS_OF


@pytest.mark.parametrize("price,side,expected", [(2.345, "back", 2.34), (2.345, "lay", 2.36), (3.07, "back", 3.05), (3.07, "lay", 3.1),
                                                 (7.3, "back", 7.2), (7.3, "lay", 7.4), (1.004, "back", 1.01), (45.0, "back", 44.0), (2.0, "lay", 2.0)])
def test_tick_rounding(price, side, expected):
    assert round_to_tick(price, side) == pytest.approx(expected)


class FakeBetfair:
    def __init__(self):
        self.placed: list[tuple] = []

    def resolve(self, fixture, market, selection):
        if market == "OVER_UNDER_25":
            return "1.100", 47972, 1.95, 1.97
        if market == "CORRECT_SCORE":
            return "1.200", 1, 12.0, 12.5
        if market == "MATCH_ODDS":
            return "1.300", 58805 if selection == "draw" else 7, 4.6, 4.7
        return None

    def place_orders(self, market_id, instructions, customer_ref):
        self.placed.append((market_id, instructions, customer_ref))
        return {"status": "SUCCESS", "instructionReports": [
            {"status": "SUCCESS", "betId": f"bet-{market_id}-{k}", "orderStatus": "EXECUTABLE", "sizeMatched": 0.0} for k, _ in enumerate(instructions)]}


def test_place_slip_sends_limit_orders_and_records_ids(provider):
    scout = Scout(provider, provider, NoPrices(), calibration=Calibration())
    scan = scout.scan(AS_OF)
    overs = next(i for i in scan.ideas if i.strategy == "over25_ins")
    bf = FakeBetfair()
    slip = build_slip(overs, 20.0, bf)
    res = place_slip(slip, bf, "ts123", daily_cap=50.0, committed_today=0.0)
    assert res.ok and len(res.bet_ids) == 2 and res.committed == pytest.approx(20.0)
    assert {m for m, _, _ in bf.placed} == {"1.100", "1.200"}
    market, instr, ref = bf.placed[0]
    assert instr[0]["side"] == "back" and instr[0]["size"] == pytest.approx(16.0) and instr[0]["price"] == slip.lines[0].plan_price
    assert ref.startswith("ts123")
    assert all(l.status == "SUCCESS" for l in res.lines)


def test_place_slip_respects_daily_cap_and_minimums(provider):
    scout = Scout(provider, provider, NoPrices(), calibration=Calibration())
    scan = scout.scan(AS_OF)
    overs = next(i for i in scan.ideas if i.strategy == "over25_ins")
    bf = FakeBetfair()
    slip = build_slip(overs, 20.0, bf)
    blocked = place_slip(slip, bf, "ts1", daily_cap=50.0, committed_today=40.0)
    assert not blocked.ok and "Daily cap" in blocked.message and not bf.placed
    small = build_slip(overs, 5.0, bf)  # insurance leg is £1, under the £2 minimum
    res = place_slip(small, bf, "ts2", daily_cap=50.0, committed_today=0.0)
    assert res.ok and len(bf.placed) == 1  # only the main leg was sent
    assert [l.status for l in res.lines] == ["SKIPPED", "SUCCESS"]


def test_slip_sizes_and_prices(provider):
    scout = Scout(provider, provider, NoPrices(), calibration=Calibration())
    scan = scout.scan(AS_OF)
    overs = next(i for i in scan.ideas if i.strategy == "over25_ins")
    slip = build_slip(overs, 20.0, FakeBetfair())
    assert [l.side for l in slip.lines] == ["back", "back"]
    main, ins = slip.lines
    assert main.size == pytest.approx(16.0) and ins.size == pytest.approx(4.0)
    assert main.market_id == "1.100" and main.selection_id == 47972 and main.live_price == 1.95
    assert main.betfair_url == "https://www.betfair.com/exchange/plus/football/market/1.100"
    assert slip.price_source == "betfair" and slip.total_staked == pytest.approx(20.0)
    ltd = next(i for i in scan.ideas if i.strategy == "ltd")
    slip = build_slip(ltd, 10.0, FakeBetfair())
    (line,) = slip.lines
    assert line.side == "lay" and line.liability == pytest.approx(10.0, abs=0.02)
    assert line.size == pytest.approx(10.0 / (line.plan_price - 1), abs=0.01)
    assert line.price_ok is (4.7 <= line.plan_price)


def test_slip_flags_minimum_and_missing_connection(provider):
    scout = Scout(provider, provider, NoPrices(), calibration=Calibration())
    scan = scout.scan(AS_OF)
    overs = next(i for i in scan.ideas if i.strategy == "over25_ins")
    slip = build_slip(overs, 5.0, None)
    assert slip.lines[1].below_minimum and slip.lines[1].size < MIN_STAKE
    assert any("not connected" in w for w in slip.warnings)
    assert slip.price_source == "model" and slip.lines[0].betfair_url is None
