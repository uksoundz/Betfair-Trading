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
    # one listEvents, one catalogue call, and books in chunks of 25 markets: far fewer than two calls per fixture
    assert rep.calls <= 3 + (6 * len(day_fixtures)) // 25 + 1
    mp = per["Sheffield United FC v Lincoln City FC"]
    assert mp.available and mp.status == "ok" and mp.event_name == "Sheff Utd v Lincoln" and mp.home and mp.draw and mp.over_25
    assert mp.quote("MATCH_ODDS", "home").best_back and mp.quote("OVER_UNDER_25", "Over 2.5 Goals").best_lay
    assert mp.quote("CORRECT_SCORE", "1-1") is not None
    assert mp.delayed is True and mp.market_status["MATCH_ODDS"] == "OPEN"


def test_second_fetch_reuses_event_and_catalogue_caches(client, day_fixtures):
    client.prices_for_day(day_fixtures)
    _, rep = client.prices_for_day(day_fixtures)
    assert rep.calls <= 1 + (6 * len(day_fixtures)) // 25 + 1  # books only


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
    assert rep.priced == len(fx) and rep.calls <= 4
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
