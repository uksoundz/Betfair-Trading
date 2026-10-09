"""Auto-trading armed plans: rules, hedge arithmetic, and whole matches played on the stand-in exchange."""
import os
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.fake_betfair import serve
from tradescout.autotrade import position as P
from tradescout.autotrade import rules as R
from tradescout.autotrade.engine import AutoTrader, new_job
from tradescout.data.betfair import BetfairPrices
from tradescout.data.openfootball import OpenFootballProvider

DAY = date(2026, 10, 10)


# ------------------------------------------------------------------ rules and maths
def test_rule_conditions():
    st = {"minute": 34, "home": 1, "away": 0, "first_goal": "home", "fav": "home"}
    assert R.evaluate(R.goals_at_least(1), st) and not R.evaluate(R.goals_at_most(0), st)
    assert R.evaluate(R.first_goal("fav"), st) and not R.evaluate(R.first_goal("dog"), st)
    assert R.evaluate(R.first_goal("dog"), {**st, "fav": "away"})
    assert R.evaluate(R.all_of(R.minute_at_least(30), R.goals_at_least(1)), st)
    assert R.evaluate(R.goals_at_least(1), {"minute": 10}) is None  # unknown score: no decision
    stop = R.rule("s", R.all_of(R.minute_at_least(70), R.goals_at_most(0)), R.green(0), "x", final=True)
    goal = R.rule("g", R.goals_at_least(1), R.green(0), "x", final=True)
    scale = R.rule("c", R.all_of(R.minute_at_least(15), R.goals_at_most(0)), R.scale_in(0, 0.5), "x")
    assert stop["fire_if_score_unknown"] and not goal["fire_if_score_unknown"] and not scale["fire_if_score_unknown"]
    assert R.evaluate(stop["when"], {"minute": 71}, assume_score=True) and not R.evaluate(stop["when"], {"minute": 50}, assume_score=True)
    tn = {"fav": "home", "set_winners": ["home"], "sets": [[6, 2], [1, 0]], "sets_done": 1}
    assert R.evaluate(R.set_won(1, "fav"), tn) and not R.evaluate(R.set_won(1, "dog"), tn) and not R.evaluate(R.set_won(2, "fav"), tn)
    assert R.evaluate(R.set_won_easily(1, "fav", 2), tn) and not R.evaluate(R.set_tiebreak(1), tn)
    assert R.evaluate(R.set_tiebreak(1), {"sets": [[6, 6]]})
    assert R.evaluate(R.price_ratio_at_most(0, 0.5), {"prices": {0: {"back": 1.4, "lay": 1.42, "entry": 3.0, "side": "back"}}})


def test_every_automated_strategy_describes_itself():
    from tradescout.strategies.registry import ALL_STRATEGIES
    from tradescout.tennis.strategies import TENNIS_STRATEGIES
    names = {"fav": "Arsenal", "dog": "Leeds"}
    legs = [{"runner_name": "The Draw", "market_label": "Match Odds", "side": "lay"}, {"runner_name": "1-1", "market_label": "Correct Score", "side": "back"}]
    for r in [R.rule("a", R.goals_at_least(1), R.green(0), "t", True), R.rule("b", R.goals_at_least(1), R.free_bet(0), "t"),
              R.rule("c", R.minute_at_least(15), R.scale_in(0, 0.5), "t"), R.rule("d", R.set_won(1, "fav"), R.hold(), "t", True)]:
        assert R.describe(r, legs, names).startswith("When ")
    assert ALL_STRATEGIES and TENNIS_STRATEGIES


def test_green_up_equalises_and_never_lowers_the_worst_case():
    for side, entry, now_b, now_l in [("lay", 3.6, 6.0, 6.2), ("lay", 3.6, 2.3, 2.34), ("back", 2.0, 1.5, 1.52), ("back", 2.0, 3.0, 3.1)]:
        e = P.add_bet(P.Exposure(), side, entry, 10.0)
        h = P.green_up(e, now_b, now_l)
        assert abs(h.after.win - h.after.lose) < 0.1 and h.after.worst >= e.worst - 1e-9
    e = P.add_bet(P.Exposure(), "back", 2.5, 8.0)
    fb = P.free_bet(e, 1.6)
    assert fb.size == 8.0 and abs(fb.after.lose) < 1e-9 and fb.after.win > 0


# ------------------------------------------------------------------ matches on the stand-in exchange
@pytest.fixture(scope="module")
def fake():
    srv, url, ex = serve(edge=0.0)
    env = {"BETFAIR_LOGIN_URL": url + "/api/login", "BETFAIR_KEEPALIVE_URL": url + "/api/keepAlive", "BETFAIR_BETTING_URL": url + "/exchange/betting/json-rpc/v1",
           "BETFAIR_ACCOUNTS_URL": url + "/exchange/account/json-rpc/v1", "BETFAIR_SCORES_URL": url + "/inplayservice/v1"}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    yield url, ex
    srv.shutdown()
    for k, v in old.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@pytest.fixture(scope="module")
def bf(fake):
    return BetfairPrices("testkey", None, "user", "secret")


@pytest.fixture(scope="module")
def fixtures():
    return OpenFootballProvider().fixtures(DAY)


def _trader(bf, tmp_path):
    t = AutoTrader(lambda: bf, tmp_path / "autotrade.json")
    t.auto_start = False
    from tradescout.autotrade.scores import ScoreFeed
    t.scores = ScoreFeed()
    return t


def _enter(bf, fx, market, selection, side, liability_or_stake, rules, fav="home", simulate=False):
    """Place the pre-match entry on the fake (as the bet slip would) and build the job for it."""
    full = bf.resolve_full(fx, market, selection)
    price = full["best_lay"] if side == "lay" else full["best_back"]
    size = round(liability_or_stake / (price - 1), 2) if side == "lay" else liability_or_stake
    bet_ids = []
    if not simulate:
        rep = bf.place_orders(full["market_id"], [{"selectionId": full["selection_id"], "handicap": full.get("handicap", 0.0), "side": side, "price": price, "size": size}],
                              f"entry-{fx.home[:6]}-{market[:6]}-{time.time()}")
        bet_ids = [rep["instructionReports"][0]["betId"]]
    m = bf.match_fixture(fx)
    leg = {"market": market, "market_label": market, "market_id": full["market_id"], "selection": selection, "selection_id": full["selection_id"],
           "handicap": full.get("handicap", 0.0), "side": side, "entry_price": price, "size": size, "runner_name": full["runner_name"]}
    return new_job(entry_id=f"e-{fx.home}-{market}", sport="football", date=DAY.isoformat(), home=fx.home, away=fx.away, fav=fav, strategy="x",
                   strategy_label="x", event_id=m.event_id, legs=[leg], rules=rules, unit=float(liability_or_stake), max_liability=float(liability_or_stake) + 0.01,
                   simulate=simulate, bet_ids=bet_ids), m.event_name


def _ltd_rules():
    return [R.rule("first_goal", R.goals_at_least(1), R.green(0), "First goal: green up.", final=True),
            R.rule("stop70", R.all_of(R.minute_at_least(70), R.goals_at_most(0)), R.green(0), "0-0 on 70': close.", final=True)]


def test_lay_the_draw_greens_up_after_the_first_goal(bf, fake, fixtures, tmp_path):
    url, ex = fake
    t = _trader(bf, tmp_path)
    job, name = _enter(bf, fixtures[0], "MATCH_ODDS", "draw", "lay", 10.0, _ltd_rules())
    t.arm(job)
    t.tick()
    assert job.state == "armed" and "Waiting for the start" in job.status
    ex.control({"play": {"event": name, "minute": 12, "home": 0, "away": 0}})
    n_orders = len(ex.orders)
    t.tick()
    assert job.state == "live" and len(ex.orders) == n_orders and job.score["minute"] == 12  # nothing to do at 0-0
    ex.control({"price": {"event": name, "market_type": "MATCH_ODDS", "runner": "The Draw", "back": 6.0, "lay": 6.2}})
    ex.control({"play": {"event": name, "minute": 34, "home": 1, "away": 0, "first_goal": "home"}})
    t.tick()
    assert job.state == "done" and "first_goal" in job.fired, job.log
    hedge = ex.orders[-1]
    assert hedge["side"] == "BACK" and hedge["priceSize"]["price"] == 6.0 and hedge["customerStrategyRef"] == "tradescout"
    e = job.exposure["0"]
    exp = P.exposure([o for o in ex.orders if o["betId"] in job.bet_ids], job.legs[0]["selection_id"])
    assert abs(exp.win - exp.lose) < 0.1 and exp.worst > 0  # green: profit whatever happens
    t.tick()
    assert len(ex.orders) == n_orders + 1  # a finished job sends nothing more


def test_stop_on_the_clock_when_the_score_feed_is_down(bf, fake, fixtures, tmp_path):
    url, ex = fake
    t = _trader(bf, tmp_path)
    job, name = _enter(bf, fixtures[1], "MATCH_ODDS", "draw", "lay", 10.0, _ltd_rules())
    t.arm(job)
    ex.control({"play": {"event": name, "minute": 72, "home": 0, "away": 0, "score_feed": False}})
    job.market_start = (datetime.now(timezone.utc) - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%S.000Z")  # 75' on the market clock
    ex.control({"price": {"event": name, "market_type": "MATCH_ODDS", "runner": "The Draw", "back": 2.1, "lay": 2.14}})
    t.tick()
    assert job.state == "done" and "stop70" in job.fired and any("Score unavailable" in l["text"] for l in job.log)
    exp = P.exposure([o for o in ex.orders if o["betId"] in job.bet_ids], job.legs[0]["selection_id"])
    assert abs(exp.win - exp.lose) < 0.1 and exp.worst > -10.0  # closed for a loss smaller than the full liability


def test_goal_rule_waits_while_the_score_is_unknown(bf, fake, fixtures, tmp_path):
    url, ex = fake
    t = _trader(bf, tmp_path)
    job, name = _enter(bf, fixtures[2], "MATCH_ODDS", "draw", "lay", 10.0, _ltd_rules())
    t.arm(job)
    job.market_start = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    ex.control({"play": {"event": name, "minute": 30, "home": 1, "away": 0, "score_feed": False}})
    n = len(ex.orders)
    t.tick()
    assert job.state == "live" and len(ex.orders) == n and not job.fired


def test_suspended_market_is_waited_out(bf, fake, fixtures, tmp_path):
    url, ex = fake
    t = _trader(bf, tmp_path)
    job, name = _enter(bf, fixtures[3], "MATCH_ODDS", "draw", "lay", 10.0, _ltd_rules())
    t.arm(job)
    ex.control({"play": {"event": name, "minute": 40, "home": 0, "away": 1, "first_goal": "away"}, "suspend": [name]})
    n = len(ex.orders)
    t.tick()
    assert job.state == "live" and len(ex.orders) == n and "suspended" in job.status
    ex.control({"suspend": []})
    t.tick()
    assert job.state == "done" and len(ex.orders) == n + 1  # underdog scored: closed for the smaller loss


def test_staged_under_lay_scales_in_within_the_plan_stake(bf, fake, fixtures, tmp_path):
    url, ex = fake
    t = _trader(bf, tmp_path)
    rules = [R.rule("scale15", R.all_of(R.minute_at_least(15), R.minute_before(25), R.goals_at_most(0), R.price_not_worse_than_entry(0)), R.scale_in(0, 0.5), "scale"),
             R.rule("second_goal", R.goals_at_least(2), R.green(0), "second goal", final=True)]
    job, name = _enter(bf, fixtures[4], "OVER_UNDER_25", "Under 2.5 Goals", "lay", 5.0, rules)
    job.unit, job.max_liability = 10.0, 10.01
    t.arm(job)
    entry = job.legs[0]["entry_price"]
    ex.control({"play": {"event": name, "minute": 16, "home": 0, "away": 0}})
    ex.control({"price": {"event": name, "market_type": "OVER_UNDER_25", "runner": "Under 2.5 Goals", "back": round(entry - 0.12, 2), "lay": round(entry - 0.1, 2)}})
    t.tick()
    assert "scale15" in job.fired
    exp = P.exposure([o for o in ex.orders if o["betId"] in job.bet_ids], job.legs[0]["selection_id"])
    assert 9.5 <= -exp.worst <= 10.01  # the second half of the liability, never more than the plan stake
    t.tick()
    assert len([o for o in ex.orders if o["betId"] in job.bet_ids]) == 2  # fires once


def test_scale_in_refused_when_the_price_moved_against_and_never_on_unknown_score(bf, fake, fixtures, tmp_path):
    url, ex = fake
    t = _trader(bf, tmp_path)
    rules = [R.rule("scale15", R.all_of(R.minute_at_least(15), R.goals_at_most(0), R.price_not_worse_than_entry(0)), R.scale_in(0, 0.5), "scale")]
    job, name = _enter(bf, fixtures[5], "OVER_UNDER_25", "Under 2.5 Goals", "lay", 5.0, rules)
    t.arm(job)
    entry = job.legs[0]["entry_price"]
    ex.control({"play": {"event": name, "minute": 16, "home": 0, "away": 0}})
    ex.control({"price": {"event": name, "market_type": "OVER_UNDER_25", "runner": "Under 2.5 Goals", "back": round(entry + 0.3, 2), "lay": round(entry + 0.32, 2)}})
    n = len(ex.orders)
    t.tick()
    assert not job.fired and len(ex.orders) == n
    ex.control({"play": {"event": name, "minute": 17, "home": 0, "away": 0, "score_feed": False}})
    ex.control({"price": {"event": name, "market_type": "OVER_UNDER_25", "runner": "Under 2.5 Goals", "back": round(entry - 0.2, 2), "lay": round(entry - 0.18, 2)}})
    job.market_start = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    t.tick()
    assert not job.fired and len(ex.orders) == n


def test_overs_free_bet_after_an_early_goal(bf, fake, fixtures, tmp_path):
    url, ex = fake
    t = _trader(bf, tmp_path)
    rules = [R.rule("early_goal", R.all_of(R.goals_at_least(1), R.minute_before(20)), R.free_bet(0), "free bet")]
    job, name = _enter(bf, fixtures[6], "OVER_UNDER_25", "Over 2.5 Goals", "back", 8.0, rules)
    t.arm(job)
    ex.control({"play": {"event": name, "minute": 9, "home": 1, "away": 0, "first_goal": "home"}})
    ex.control({"price": {"event": name, "market_type": "OVER_UNDER_25", "runner": "Over 2.5 Goals", "back": 1.5, "lay": 1.52}})
    t.tick()
    assert "early_goal" in job.fired and job.state == "live"  # not final: the rest of the plan runs on
    exp = P.exposure([o for o in ex.orders if o["betId"] in job.bet_ids], job.legs[0]["selection_id"])
    assert abs(exp.lose) < 0.01 and exp.win > 0


def test_simulate_mode_sends_nothing(bf, fake, fixtures, tmp_path):
    url, ex = fake
    t = _trader(bf, tmp_path)
    job, name = _enter(bf, fixtures[7], "MATCH_ODDS", "draw", "lay", 10.0, _ltd_rules(), simulate=True)
    t.arm(job)
    n = len(ex.orders)
    ex.control({"play": {"event": name, "minute": 50, "home": 0, "away": 1, "first_goal": "away"}})
    t.tick()
    assert job.state == "done" and len(ex.orders) == n and any("SIMULATED" in l["text"] for l in job.log)
    assert len(job.sim_bets) == 2


def test_unmatched_entry_expires_and_disarm_and_persistence(bf, fake, fixtures, tmp_path):
    url, ex = fake
    t = _trader(bf, tmp_path)
    fx = fixtures[8]
    full = bf.resolve_full(fx, "MATCH_ODDS", "draw")
    rep = bf.place_orders(full["market_id"], [{"selectionId": full["selection_id"], "side": "lay", "price": 1.01, "size": 5.0}], "unmatched-entry")
    m = bf.match_fixture(fx)
    job = new_job(entry_id="never", sport="football", date=DAY.isoformat(), home=fx.home, away=fx.away, fav="home", strategy="ltd", strategy_label="LTD",
                  event_id=m.event_id, legs=[{"market": "MATCH_ODDS", "market_label": "Match Odds", "market_id": full["market_id"], "selection": "draw",
                                               "selection_id": full["selection_id"], "handicap": 0.0, "side": "lay", "entry_price": 1.01, "size": 5.0, "runner_name": "The Draw"}],
                  rules=_ltd_rules(), unit=0.05, max_liability=0.06, bet_ids=[rep["instructionReports"][0]["betId"]])
    t.arm(job)
    ex.control({"play": {"event": m.event_name, "minute": 2, "home": 0, "away": 0}})
    t.tick()
    assert job.state == "expired" and "never matched" in job.status
    # persistence and disarm
    job2, name2 = _enter(bf, fixtures[9], "MATCH_ODDS", "draw", "lay", 10.0, _ltd_rules())
    t.arm(job2)
    t2 = _trader(bf, tmp_path)
    assert t2.jobs[job2.id].state == "armed" and t2.jobs[job.id].state == "expired"
    t2.disarm(job2.id)
    assert t2.jobs[job2.id].state == "stopped" and _trader(bf, tmp_path).jobs[job2.id].state == "stopped"
    assert t2.stop_all() == 0


def test_tennis_back_to_lay_greens_after_set_one(bf, fake, tmp_path):
    from tradescout.data.betfair_tennis import BetfairTennis
    from tradescout.tennis.data import TennisProvider
    url, ex = fake
    bt = BetfairTennis(bf, TennisProvider())
    fx = bt.fixtures(DAY)[0]
    full = bt.resolve_full(fx, "MATCH_ODDS", "home")
    rep = bf.place_orders(full["market_id"], [{"selectionId": full["selection_id"], "side": "back", "price": full["best_back"], "size": 10.0}], "tn-entry")
    t = _trader(bf, tmp_path)
    rules = [R.rule("set1_won", R.set_won(1, "fav"), R.green(0), "set 1 won", final=True), R.rule("set1_lost", R.set_won(1, "dog"), R.green(0), "set 1 lost", final=True)]
    job = new_job(entry_id="tn", sport="tennis", date=DAY.isoformat(), home=fx.home, away=fx.away, fav="home", strategy="tn_b2l_fav", strategy_label="B2L",
                  event_id=str(fx.fixture_id)[3:], legs=[{"market": "MATCH_ODDS", "market_label": "Match Odds", "market_id": full["market_id"], "selection": "home",
                                                           "selection_id": full["selection_id"], "handicap": 0.0, "side": "back", "entry_price": full["best_back"],
                                                           "size": 10.0, "runner_name": full["runner_name"]}],
                  rules=rules, unit=10.0, max_liability=10.01, bet_ids=[rep["instructionReports"][0]["betId"]])
    t.arm(job)
    name = fx.meta["betfair_names"][0] + " v " + fx.meta["betfair_names"][1]
    ex.control({"play_tennis": {"event": name, "sets": [[4, 3]], "set_counts": [0, 0]}})
    n = len(ex.orders)
    t.tick()
    assert job.state == "live" and len(ex.orders) == n
    ex.control({"play_tennis": {"event": name, "sets": [[6, 3], [0, 0]], "set_counts": [1, 0]}})
    ex.control({"price": {"event": name, "market_type": "MATCH_ODDS", "runner": fx.meta["betfair_names"][0], "back": round(full["best_back"] * 0.7, 2),
                          "lay": round(full["best_back"] * 0.7 + 0.02, 2)}})
    t.tick()
    assert job.state == "done" and "set1_won" in job.fired
    exp = P.exposure([o for o in ex.orders if o["betId"] in job.bet_ids], full["selection_id"])
    assert abs(exp.win - exp.lose) < 0.1 and exp.worst > 0
