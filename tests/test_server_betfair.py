"""The web routes with Betfair wired to the local stand-in exchange: status, scan payload with the price
feed report, diagnosis, placement gate (TRADE places, non-TRADE needs an explicit override, mode off blocks)."""
import importlib
import os
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_betfair import serve

DAY = "2026-10-10"


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    srv, url, ex = serve(edge=0.5)
    env = {"BETFAIR_LOGIN_URL": url + "/api/login", "BETFAIR_KEEPALIVE_URL": url + "/api/keepAlive", "BETFAIR_BETTING_URL": url + "/exchange/betting/json-rpc/v1",
           "BETFAIR_ACCOUNTS_URL": url + "/exchange/account/json-rpc/v1"}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    from tradescout.config import settings
    saved = dict(vars(settings))
    settings.betfair_app_key, settings.betfair_username, settings.betfair_password, settings.betfair_session_token = "testkey", "u", "secret", None
    settings.betting_mode, settings.bank, settings.daily_cap = "live", 500.0, 100.0
    settings.cache_dir = tmp_path_factory.mktemp("cache")
    import tradescout.web.server as server
    importlib.reload(server)
    from tradescout.journal import Journal
    server.journal = Journal(tmp_path_factory.mktemp("j") / "journal.json")
    server.signals.record = lambda *a, **k: 0
    server.rt.betfair.ensure_session()
    yield server, TestClient(server.app), ex
    srv.shutdown()
    settings.__dict__.update(saved)
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_status_reports_connection_and_key_type(srv):
    server, c, ex = srv
    s = c.get("/api/status").json()
    assert s["betfair_configured"] and s["betfair"] and s["betfair_error"] is None and s["auto_refresh_seconds"] == server.AUTO_REFRESH_SECONDS
    assert s["betfair_state"]["health"]["logins"] >= 1


def test_scan_payload_carries_feed_report_and_per_match_price_status(srv):
    server, c, ex = srv
    r = c.get(f"/api/scan?date={DAY}&sport=football").json()
    assert r["price_source"] == "betfair" and r["priced"] == r["fixtures"] == r["feed"]["priced"] and not r["feed"]["unmatched"]
    m = r["matches"][0]
    assert m["price_status"] == "ok" and m["exchange_event"] and m["price_as_of"] and m["markets"]["MATCH_ODDS"] == "OPEN"
    assert r["decisions"]["RESEARCH"] == 0 and r["decisions"]["TRADE"] >= 1
    assert r["betfair"]["connected"] and r["auto_refresh_seconds"]


def test_diagnose_lists_every_fixture_with_its_exchange_event(srv):
    server, c, ex = srv
    d = c.get(f"/api/betfair/diagnose?date={DAY}&sport=football").json()
    assert d["ok"] and d["configured"] and d["report"]["matched"] == len(d["fixtures"]) and d["report"]["event_names"]
    assert all(row["status"] == "ok" and row["event_name"] for row in d["fixtures"])


def _ideas(c, decision):
    r = c.get(f"/api/scan?date={DAY}&sport=football").json()
    return [(m, i) for m in r["matches"] for i in m["ideas"] if i["decision"] == decision and i["orders"]]


def test_trade_idea_can_be_placed_after_confirmation(srv):
    server, c, ex = srv
    m, i = _ideas(c, "TRADE")[0]
    body = {"date": DAY, "match_id": m["id"], "strategy": i["strategy"], "sport": "football"}
    p = c.post("/api/betslip/preview", json=body).json()
    assert p["decision"] == "TRADE" and p["can_place"] and not p["override_needed"] and p["sendable_lines"] >= 1 and p["price_source"] == "betfair"
    assert c.post("/api/betslip/place", json=body).status_code == 400  # needs confirm
    before = len(ex.orders)
    r = c.post("/api/betslip/place", json={**body, "confirm": True}).json()
    assert r["ok"] and r["result"]["bet_ids"] and len(ex.orders) > before and not r["override"]
    assert any(e.placed == "live" and "override" not in e.note for e in server.journal.entries)
    # the same slip again within the window is a duplicate on the exchange side: same deterministic customerRef
    refs = {o["customerRef"] for o in ex.orders[before:]}
    assert all(ref.startswith("ts-") for ref in refs)


def test_non_trade_needs_explicit_override_and_is_logged_as_such(srv):
    server, c, ex = srv
    m, i = _ideas(c, "NO TRADE")[0]
    body = {"date": DAY, "match_id": m["id"], "strategy": i["strategy"], "sport": "football"}
    p = c.post("/api/betslip/preview", json=body).json()
    assert not p["can_place"] and p["override_allowed"] and p["override_needed"] and any("NO TRADE" in x for x in p["place_block_reasons"])
    blocked = c.post("/api/betslip/place", json={**body, "confirm": True})
    assert blocked.status_code == 409 and "override" in blocked.json()["detail"]
    before = len(ex.orders)
    r = c.post("/api/betslip/place", json={**body, "confirm": True, "override": True}).json()
    assert r["ok"] and r["override"] and len(ex.orders) > before
    assert any("override" in e.note and e.decision == "NO TRADE" for e in server.journal.entries)


def test_mode_off_blocks_placement_and_explains(srv):
    server, c, ex = srv
    m, i = _ideas(c, "TRADE")[0]
    body = {"date": DAY, "match_id": m["id"], "strategy": i["strategy"], "sport": "football"}
    server.settings.betting_mode = "off"
    try:
        p = c.post("/api/betslip/preview", json=body).json()
        assert not p["can_place"] and not p["override_allowed"] and any("Settings > Betting" in x for x in p["place_block_reasons"])
        assert c.post("/api/betslip/place", json={**body, "confirm": True, "override": True}).status_code == 403
    finally:
        server.settings.betting_mode = "live"


def test_inplay_fixture_is_no_trade_with_reason(srv):
    server, c, ex = srv
    r = c.get(f"/api/scan?date={DAY}&sport=football").json()
    name = r["matches"][0]["exchange_event"]
    ex.control({"inplay": [name]})
    try:
        r = c.get(f"/api/scan?date={DAY}&sport=football&refresh=full").json()
        m = next(x for x in r["matches"] if x["exchange_event"] == name)
        assert m["price_status"] == "inplay" and m["inplay"] and r["feed"]["inplay"] == [f"{m['home']} v {m['away']}"]
        assert all(i["decision"] == "NO TRADE" and "started" in i["decision_reasons"][0] for i in m["ideas"])
    finally:
        ex.control({"inplay": []})
        c.get(f"/api/scan?date={DAY}&sport=football&refresh=full")


def test_reconnect_after_expiry_and_bad_credentials_message(srv):
    server, c, ex = srv
    ex.control({"expire_sessions": True})
    r = c.post("/api/betfair/reconnect").json()
    assert r["ok"] and r["betfair"]["connected"]
    server.settings.betfair_password = "wrong"
    try:
        t = c.post("/api/test/betfair").json()
        assert not t["ok"] and "username" in t["error"].lower()
    finally:
        server.settings.betfair_password = "secret"


def test_tennis_scan_prices_exchange_fixtures(srv):
    server, c, ex = srv
    r = c.get(f"/api/scan?date={DAY}&sport=tennis").json()
    assert r["fixtures"] >= 5 and r["priced"] == r["fixtures"] and r["price_source"] == "betfair"
    assert all(m["price_status"] == "ok" for m in r["matches"])


def test_slip_blocks_lines_on_inplay_markets_and_reuses_cached_fixtures(srv):
    server, c, ex = srv
    m, i = _ideas(c, "TRADE")[0]
    body = {"date": DAY, "match_id": m["id"], "strategy": i["strategy"], "sport": "football"}
    ex.control({"inplay": [m["exchange_event"]]})
    server.rt.betfair.invalidate()
    try:
        p = c.post("/api/betslip/preview", json=body).json()
        assert all(l["blocked"] and l["market_status"] == "INPLAY" for l in p["lines"]) and not p["can_place"] and not p["override_allowed"]
        assert any("in play" in x for x in p["place_block_reasons"])
        assert c.post("/api/betslip/place", json={**body, "confirm": True, "override": True}).status_code == 409
    finally:
        ex.control({"inplay": []})
        server.rt.betfair.invalidate()
    assert f"football:{DAY}" in server.rt.fixture_cache
    assert c.post("/api/betslip/preview", json={**body, "match_id": "nope"}).status_code == 404
