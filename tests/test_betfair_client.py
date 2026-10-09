"""The Betfair client against the local stand-in exchange (tests/fake_betfair.py): login, session
renewal, batched day prices with diagnostics, in-play and suspended handling, slip resolution."""
import os
from datetime import date

import pytest
import requests

from tests.fake_betfair import serve
from tradescout.data.betfair import BetfairError, BetfairPrices
from tradescout.data.openfootball import OpenFootballProvider
from tradescout.scout import Scout

DAY = date(2026, 10, 10)


@pytest.fixture(scope="module")
def fake():
    srv, url, ex = serve(edge=0.5)
    os.environ["BETFAIR_LOGIN_URL"] = url + "/api/login"
    os.environ["BETFAIR_KEEPALIVE_URL"] = url + "/api/keepAlive"
    os.environ["BETFAIR_BETTING_URL"] = url + "/exchange/betting/json-rpc/v1"
    os.environ["BETFAIR_ACCOUNTS_URL"] = url + "/exchange/account/json-rpc/v1"
    yield url, ex
    srv.shutdown()
    for k in ("BETFAIR_LOGIN_URL", "BETFAIR_KEEPALIVE_URL", "BETFAIR_BETTING_URL", "BETFAIR_ACCOUNTS_URL"):
        os.environ.pop(k, None)


@pytest.fixture
def client(fake):
    return BetfairPrices("testkey", None, "user", "secret")


@pytest.fixture(scope="module")
def day_fixtures():
    return OpenFootballProvider().fixtures(DAY)


def test_lazy_login_and_health(client):
    assert client.token is None and not client.health.connected
    client.ensure_session()
    assert client.token and client.health.connected and client.health.logins == 1
    assert client.probe()["ok"] and client.app_key_delayed() is True


def test_every_fixture_matches_an_exchange_event_in_few_calls(client, day_fixtures, fake):
    url, ex = fake
    ex.control({"reset_calls": True})
    per, rep = client.prices_for_day(day_fixtures)
    assert rep.fixtures == len(day_fixtures) == rep.matched == rep.priced and not rep.unmatched and rep.error is None
    # one listEvents, catalogue calls of 10 events, books in chunks of 10 markets: well under two calls per fixture
    from tradescout.data.betfair import BOOK_CHUNK, CATALOGUE_CHUNK
    n = len(day_fixtures)
    assert rep.calls <= 1 + -(-n // CATALOGUE_CHUNK) + -(-6 * n // BOOK_CHUNK) and rep.calls < 2 * n
    mp = per["Sheffield United FC v Lincoln City FC"]
    assert mp.available and mp.status == "ok" and mp.event_name == "Sheff Utd v Lincoln" and mp.home and mp.draw and mp.over_25
    assert mp.quote("MATCH_ODDS", "home").best_back and mp.quote("OVER_UNDER_25", "Over 2.5 Goals").best_lay
    assert mp.quote("CORRECT_SCORE", "1-1") is not None
    assert mp.delayed is True and mp.market_status["MATCH_ODDS"] == "OPEN"


def test_second_fetch_reuses_event_and_catalogue_caches(client, day_fixtures):
    client.prices_for_day(day_fixtures)
    _, rep = client.prices_for_day(day_fixtures)
    from tradescout.data.betfair import BOOK_CHUNK
    assert rep.calls <= -(-6 * len(day_fixtures) // BOOK_CHUNK)  # books only


def test_session_expiry_is_renewed_transparently(client, day_fixtures, fake):
    url, ex = fake
    client.prices_for_day(day_fixtures[:2])
    requests.post(url + "/__control", json={"expire_sessions": True})
    logins = client.health.logins
    per, rep = client.prices_for_day(day_fixtures[:2])
    assert rep.error is None and rep.priced == 2 and client.health.logins == logins + 1


def test_inplay_and_suspended_are_reported_not_priced(client, day_fixtures, fake):
    url, ex = fake
    per, _ = client.prices_for_day(day_fixtures[:3])
    names = [per[f.label].event_name for f in day_fixtures[:3]]
    requests.post(url + "/__control", json={"inplay": [names[0]], "suspend": [names[1]]})
    client.invalidate()
    per, rep = client.prices_for_day(day_fixtures[:3])
    requests.post(url + "/__control", json={"inplay": [], "suspend": []})
    a, b, c = (per[f.label] for f in day_fixtures[:3])
    assert a.status == "inplay" and a.inplay and not a.available and "started" in a.note
    assert b.status == "suspended" and not b.available
    assert c.status == "ok" and c.available
    assert rep.inplay == [day_fixtures[0].label] and rep.suspended == [day_fixtures[1].label]


def test_unmatched_fixture_gets_a_reason_and_nearest_candidates(client, day_fixtures):
    from tradescout.models import Fixture
    ghost = Fixture(DAY, "en.1", "Arsenal FC", "Manchester City FC")
    per, rep = client.prices_for_day([ghost])
    mp = per[ghost.label]
    assert mp.status == "no_event" and not mp.available and "closest" in mp.note and mp.candidates
    assert rep.unmatched[0]["fixture"] == ghost.label and rep.unmatched[0]["candidates"]


def test_bad_password_and_stale_token_give_plain_english(fake, day_fixtures):
    bad = BetfairPrices("testkey", None, "user", "wrong")
    per, rep = bad.prices_for_day(day_fixtures[:1])
    assert rep.error_code == "INVALID_USERNAME_OR_PASSWORD" and "username" in rep.error.lower()
    assert per[day_fixtures[0].label].status == "error"
    with pytest.raises(BetfairError):
        bad.ensure_session()
    token_only = BetfairPrices("testkey", "stale-token", None, None)
    _, rep = token_only.prices_for_day(day_fixtures[:1])
    assert rep.error_code == "INVALID_SESSION_INFORMATION" and not token_only.can_login


def test_keep_alive_and_resolve_for_the_slip(client, day_fixtures):
    client.ensure_session()
    assert client.keep_alive() is True
    fx = day_fixtures[2]
    mid, sel, back, lay = client.resolve(fx, "OVER_UNDER_25", "Over 2.5 Goals")
    assert mid.startswith("1.") and sel and back and lay and lay >= back
    assert client.resolve(fx, "MATCH_ODDS", "draw")[1] != client.resolve(fx, "MATCH_ODDS", "home")[1]
    assert client.resolve(fx, "CORRECT_SCORE", "1-1") is not None
    full = client.resolve_full(fx, "MATCH_ODDS", "home")
    assert full["status"] == "OPEN" and full["event_name"]


def test_scan_through_the_pipeline_attaches_prices_and_feed(client, day_fixtures):
    prov = OpenFootballProvider()
    scan = Scout(prov, prov, client).scan(DAY)
    assert scan.price_source == "betfair" and scan.feed["priced"] == len(day_fixtures) and not scan.skipped
    assert all(scan.price_status[f.label]["status"] == "ok" for f in day_fixtures)
    decisions = {i.decision for i in scan.ideas}
    assert "RESEARCH" not in decisions and decisions & {"TRADE", "NO TRADE"}
    assert any(i.decision == "TRADE" for i in scan.ideas), "the fake's mispriced markets should yield at least one TRADE"


def test_place_orders_round_trip_and_duplicate_protection(client, day_fixtures, fake):
    url, ex = fake
    fx = day_fixtures[2]
    mid, sel, back, lay = client.resolve(fx, "OVER_UNDER_25", "Over 2.5 Goals")
    rep = client.place_orders(mid, [{"selectionId": sel, "side": "back", "price": back, "size": 2.0}], "ts-abc")
    assert rep["status"] == "SUCCESS" and rep["instructionReports"][0]["betId"]
    orders = client.current_orders([mid])
    assert any(o["betId"] == rep["instructionReports"][0]["betId"] for o in orders)
    assert client.cancel_orders(mid)["status"] == "SUCCESS"


def test_tennis_exchange_fixtures_prices_and_set_betting_orientation(fake):
    from tradescout.data.betfair_tennis import BetfairTennis, PlayerMatcher, player_part
    from tradescout.tennis.data import TennisProvider
    tp = TennisProvider()
    pm = PlayerMatcher(tp.players())
    assert pm.resolve("N Djokovic") == "Novak Djokovic" and pm.resolve("Djokovic, N") == "Novak Djokovic" and pm.resolve("Djokovic N") == "Novak Djokovic"
    assert pm.resolve("J M Cerundolo") == "Juan Manuel Cerundolo" and pm.resolve("F Cerundolo") == "Francisco Cerundolo"
    assert pm.resolve("F Auger-Aliassime") == "Felix Auger-Aliassime" and pm.resolve("R Bautista Agut") == "Roberto Bautista Agut"
    assert pm.resolve("Nobody Known") == "Nobody Known"
    assert player_part("Alcaraz 2-0") == "Alcaraz" and player_part("Alcaraz 2 - 1") == "Alcaraz"
    assert pm.same("Alcaraz", "Carlos Alcaraz") and not pm.same("Alcaraz", "Jannik Sinner")
    bt = BetfairTennis(BetfairPrices("testkey", None, "user", "secret"), tp)
    fx = bt.fixtures(DAY)
    assert len(fx) >= 5 and all(f.fixture_id.startswith("bf:") and f.league == "atp.1000" for f in fx)
    per, rep = bt.prices_for_day(fx)
    assert rep.priced == len(fx) and rep.calls <= 2 + -(-len(fx) // 3) + -(-4 * len(fx) // 10)
    f0 = fx[0]
    mp = per[f0.label]
    assert mp.available and mp.quote("MATCH_ODDS", "home") and mp.quote("SET_1_WINNER", "away")
    # set betting is keyed home-away whichever player Betfair names on the runner
    assert set(k for k in mp.quotes if k.startswith("SET_BETTING:")) == {"SET_BETTING:2-0", "SET_BETTING:2-1", "SET_BETTING:0-2", "SET_BETTING:1-2"}
    assert any(k.startswith("TOTAL_GAMES:Over ") and k.endswith((".5")) for k in mp.quotes)
    assert bt.resolve(f0, "SET_BETTING", "0-2") is not None and bt.resolve(f0, "SET_BETTING", "2-0")[1] != bt.resolve(f0, "SET_BETTING", "0-2")[1]
    line = next(k.split("Over ")[1] for k in mp.quotes if k.startswith("TOTAL_GAMES:Over "))
    assert bt.resolve(f0, "TOTAL_GAMES", f"Over {line}") is not None


def test_inplay_flag_before_the_start_time_is_not_believed(client, day_fixtures):
    from tradescout.data.betfair import _starts_in_future
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    past = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert _starts_in_future({"marketStartTime": future}, {}) is True and _starts_in_future({"marketStartTime": past}, {}) is False
    assert _starts_in_future({}, {}) is None
    # a book that says in play for a market starting in five hours keeps its prices and is flagged in the diagnostics
    fx = day_fixtures[0]
    m = client.match_fixture(fx)
    cats = client.catalogue([m.event_id])[m.event_id]
    books = client.books([c["marketId"] for c in cats])
    for b in books.values():
        b["inplay"] = True
    for c in cats:
        c["marketStartTime"] = future
    mp = client._build_prices(fx, cats, books, m)
    assert mp.status == "ok" and not mp.inplay and mp.available and any("ignored" in f for f in mp.flags)
    assert mp.raw_markets and mp.raw_markets[0]["inplay"] is True and mp.raw_markets[0]["start"] == future


def _trade_slip(client, day_fixtures, strategy="over25_ins"):
    from tradescout.betting import build_slip
    prov = OpenFootballProvider()
    scan = Scout(prov, prov, client).scan(DAY)
    idea = next(i for i in scan.ideas if i.strategy == strategy and i.orders and all(scan.price_status[i.fixture.label]["status"] == "ok" for _ in [0]))
    return idea, build_slip(idea, 20.0, client)


def test_first_market_rejected_stops_the_plan(client, day_fixtures, fake):
    import tradescout.betting as betting
    from tradescout.betting import place_slip
    url, ex = fake
    idea, slip = _trade_slip(client, day_fixtures)
    assert len({l.market_id for l in slip.lines}) == 2
    ex.control({"reset_orders": True, "reject_market_types": ["OVER_UNDER_25"]})
    try:
        res = place_slip(slip, client, "ts-first-fails", 500.0, 0.0)
    finally:
        ex.control({"reject_market_types": []})
    assert not res.ok and [l.status for l in res.lines] == ["FAILURE", "SKIPPED"] and "earlier leg" in res.lines[1].error and not ex.orders


def test_later_market_rejected_unwinds_and_counts_only_what_stands(client, day_fixtures, fake):
    from tradescout.betting import place_slip
    url, ex = fake
    idea, slip = _trade_slip(client, day_fixtures)
    # make the main leg rest unmatched so the unwind cancels it entirely
    for l in slip.lines:
        if l.market == "OVER_UNDER_25":
            l.plan_price = 1000.0
    ex.control({"reset_orders": True, "reject_market_types": ["CORRECT_SCORE"]})
    try:
        res = place_slip(slip, client, "ts-second-fails", 500.0, 0.0)
    finally:
        ex.control({"reject_market_types": []})
    assert res.unwound and not res.ok and res.committed == 0.0 and res.bet_ids == []
    main = next(l for l in res.lines if l.market_label.startswith("Over/Under"))
    assert main.status == "SUCCESS" and main.order_status == "CANCELLED" and "cancelled" in main.error
    assert all(o["sizeRemaining"] == 0.0 for o in ex.orders)


def test_timeout_is_reconciled_by_order_reference_not_by_price(client, day_fixtures, fake, monkeypatch):
    import tradescout.betting as betting
    from tradescout.betting import place_slip
    url, ex = fake
    monkeypatch.setattr(betting, "RECONCILE_POLL_SECONDS", (0.05, 0.05))
    idea, slip = _trade_slip(client, day_fixtures)
    ex.control({"reset_orders": True})
    first = place_slip(slip, client, "ts-earlier", 500.0, 0.0)  # an earlier identical order on the exchange
    assert first.ok
    ex.control({"timeout_market_types": ["OVER_UNDER_25", "CORRECT_SCORE"]})
    try:
        res = place_slip(slip, client, "ts-later", 500.0, 0.0)
    finally:
        ex.control({"timeout_market_types": []})
    assert res.ok and not res.pending and len(res.bet_ids) == 2
    assert set(res.bet_ids).isdisjoint(set(first.bet_ids)), "must adopt its own orders, not the earlier identical ones"
    refs = {o["customerOrderRef"] for o in ex.orders if o["betId"] in res.bet_ids}
    assert all(r.startswith("ts-later") for r in refs)


def test_duplicate_submission_is_refused_by_the_exchange(client, day_fixtures, fake):
    from tradescout.betting import place_slip
    url, ex = fake
    idea, slip = _trade_slip(client, day_fixtures)
    ex.control({"reset_orders": True})
    a = place_slip(slip, client, "ts-dup", 500.0, 0.0)
    b = place_slip(slip, client, "ts-dup", 500.0, 0.0)
    assert a.ok and not b.ok and b.lines[0].status == "DUPLICATE" and b.lines[1].status == "SKIPPED" and len(ex.orders) == 2


def test_catalogue_requests_respect_the_weight_limit_and_truncation(client, day_fixtures, fake):
    url, ex = fake
    client.invalidate()
    ex.catalogue_requests.clear()
    per, rep = client.prices_for_day(day_fixtures)
    assert rep.error is None and rep.priced == len(day_fixtures)
    assert ex.catalogue_requests and all(r["weight"] * r["maxResults"] <= 200 for r in ex.catalogue_requests)
    # untyped (tennis-style) calls use small chunks and re-fetch per event when the response hits the cap
    from tradescout.data.betfair_tennis import BetfairTennis
    from tradescout.tennis.data import TennisProvider
    bt = BetfairTennis(client, TennisProvider())
    fx = bt.fixtures(DAY)
    client.invalidate()
    ex.catalogue_requests.clear()
    per, rep = bt.prices_for_day(fx)
    assert rep.priced == len(fx) and all(r["weight"] * r["maxResults"] <= 200 and r["events"] <= 5 for r in ex.catalogue_requests)


def test_rejected_login_backs_off_instead_of_retrying_every_call(fake, day_fixtures):
    bad = BetfairPrices("testkey", None, "user", "wrong")
    url, ex = fake
    before = ex.calls["login"]
    for _ in range(3):
        bad.prices_for_day(day_fixtures[:1])
    assert ex.calls["login"] == before + 1 and bad.health.login_blocked_until
    with pytest.raises(BetfairError):
        bad.login(force=True)  # the user's own Reconnect does try again
    assert ex.calls["login"] == before + 2


def test_requests_shrink_when_the_exchange_says_too_large(client, day_fixtures, fake):
    url, ex = fake
    ex.control({"weight_limit": 20})  # far stricter than the documented 200
    try:
        client.invalidate()
        per, rep = client.prices_for_day(day_fixtures)
        assert rep.error is None and rep.priced == len(day_fixtures), rep.error
        sizes = [r["weight"] * r["maxResults"] for r in ex.catalogue_requests]
        assert any(x > 20 for x in sizes) and any(x <= 20 for x in sizes)  # rejected first, then shrank until accepted
    finally:
        ex.control({"weight_limit": 200})


def test_a_failing_batch_only_affects_its_own_matches(client, day_fixtures, fake):
    url, ex = fake
    ex.control({"weight_limit": 3})  # even one event's catalogue is "too large": every match errors, nothing raises
    try:
        client.invalidate()
        per, rep = client.prices_for_day(day_fixtures[:4])
        assert rep.matched == 4 and rep.priced == 0 and rep.error_code == "TOO_MUCH_DATA" and "4 of 4" in rep.error
        assert all(per[f.label].status == "error" and "this match" in per[f.label].note for f in day_fixtures[:4])
    finally:
        ex.control({"weight_limit": 200})
        client.invalidate()
    per, rep = client.prices_for_day(day_fixtures[:4])
    assert rep.priced == 4 and rep.error is None
