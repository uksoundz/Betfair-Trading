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
           "BETFAIR_ACCOUNTS_URL": url + "/exchange/account/json-rpc/v1", "BETFAIR_SCORES_URL": url + "/inplayservice/v1"}
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


def test_track_keeps_the_advised_stake_but_the_slip_floors_it(srv):
    server, c, ex = srv
    m, i = _ideas(c, "NO TRADE")[3]
    body = {"date": DAY, "match_id": m["id"], "strategy": i["strategy"], "sport": "football"}
    r = c.post("/api/journal", json=body).json()
    assert r["entry"]["stake_money"] == 2.0  # advised unit for a NO TRADE: nothing inflated for the record
    p = c.post("/api/betslip/preview", json=body).json()
    assert p["stake_money"] >= p["stake_floor"] >= 2.0


def test_override_is_logged_even_when_the_idea_was_tracked_first(srv):
    server, c, ex = srv
    m, i = _ideas(c, "NO TRADE")[1]
    body = {"date": DAY, "match_id": m["id"], "strategy": i["strategy"], "sport": "football"}
    c.post("/api/journal", json=body)
    r = c.post("/api/betslip/place", json={**body, "confirm": True, "override": True}).json()
    assert r["ok"] and r["override"]
    e = next(e for e in server.journal.entries if e.home == m["home"] and e.strategy == i["strategy"])
    assert "override" in e.note and e.placed == "live" and e.placed_total > 0
    # placing the same plan again adds to the money committed rather than replacing it, and needs the override
    p = c.post("/api/betslip/preview", json=body).json()
    assert p["already_placed"] and any("Already placed" in x for x in p["place_block_reasons"]) and not p["can_place"]
    ex.recent_refs.clear()
    before = e.placed_total
    r2 = c.post("/api/betslip/place", json={**body, "confirm": True, "override": True}).json()
    assert r2["ok"] and e.placed_total > before and len(e.bet_refs) >= 2


def test_per_trade_cap_and_risk_engine_blocks(srv, monkeypatch):
    server, c, ex = srv
    m, i = _ideas(c, "TRADE")[0]
    body = {"date": DAY, "match_id": m["id"], "strategy": i["strategy"], "sport": "football", "stake_money": 60.0}
    p = c.post("/api/betslip/preview", json=body).json()
    assert not p["can_place"] and p["override_allowed"] and any("per-trade cap" in x for x in p["place_block_reasons"])
    assert c.post("/api/betslip/place", json={**body, "confirm": True}).status_code == 409
    # the risk engine saying no (loss limit reached) is a hard block, override or not
    from tradescout.risk import Exposure
    monkeypatch.setattr(server, "exposure_from_journal", lambda entries, bank: Exposure(current_bank=500.0, peak_bank=500.0, realised_today=-50.0))
    server.rt.cache.clear()
    p2 = c.post("/api/betslip/preview", json={k: v for k, v in body.items() if k != "stake_money"}).json()
    assert not p2["can_place"] and not p2["override_allowed"] and any("risk engine" in x for x in p2["place_block_reasons"])
    monkeypatch.undo()
    server.rt.cache.clear()


def test_research_idea_is_never_placeable(srv):
    server, c, ex = srv
    m, i = _ideas(c, "TRADE")[0]
    body = {"date": DAY, "match_id": m["id"], "strategy": i["strategy"], "sport": "football"}
    ex.control({"fail_login": True, "expire_sessions": True})
    server.rt.betfair.invalidate()
    try:
        p = c.post("/api/betslip/preview", json=body).json()
        assert p["decision"] == "RESEARCH" and not p["can_place"] and not p["override_allowed"]
        assert c.post("/api/betslip/place", json={**body, "confirm": True, "override": True}).status_code in (400, 409)
    finally:
        ex.control({"fail_login": False})
        server.rt.betfair.login(force=True)
        server.rt.betfair.invalidate()
        server.rt.cache.clear()


def test_autotrade_arm_flow_through_the_routes(srv, tmp_path):
    server, c, ex = srv
    server.autotrader.path = tmp_path / "autotrade.json"
    server.autotrader.jobs = {}
    server.autotrader.auto_start = False
    ex.recent_refs.clear()
    m, i = _ideas(c, "TRADE")[0]
    body = {"date": DAY, "match_id": m["id"], "strategy": i["strategy"], "sport": "football"}
    r = c.post("/api/betslip/place", json={**body, "confirm": True, "override": True}).json()  # placed earlier in this module: needs the override
    assert r["ok"] and r["entry_id"], r
    entry = r["entry_id"]
    server.settings.autotrade = False
    try:
        assert c.post("/api/autotrade/preview", json={"entry_id": entry}).status_code == 403
        server.settings.autotrade = True
        if not r["can_autotrade"]:
            assert c.post("/api/autotrade/preview", json={"entry_id": entry}).status_code == 400
            return
        p = c.post("/api/autotrade/preview", json={"entry_id": entry}).json()
        assert p["job"]["rules_text"] and all(t.startswith("When ") for t in p["job"]["rules_text"]) and not p["job"]["simulate"]
        assert c.post("/api/autotrade/arm", json={"entry_id": entry}).status_code == 400  # needs confirm
        a = c.post("/api/autotrade/arm", json={"entry_id": entry, "confirm": True}).json()
        jid = a["job"]["id"]
        assert a["job"]["state"] == "armed" and a["job"]["bet_ids"]
        assert c.get("/api/status").json()["autotrade"]["active"] == 1
        assert c.get("/api/autotrade").json()["jobs"][0]["id"] == jid
        assert c.post("/api/autotrade/disarm", json={"job_id": jid}).json()["job"]["state"] == "stopped"
        assert c.post("/api/autotrade/stop_all").json()["stopped"] == 0
    finally:
        server.settings.autotrade = False


def test_autotrade_refuses_plans_without_automated_rules(srv, tmp_path):
    server, c, ex = srv
    server.settings.autotrade = True
    try:
        e = server.journal.entries[0]
        saved = (e.rules, e.strategy)
        e.rules, e.strategy = [], "cs_basket"
        r = c.post("/api/autotrade/preview", json={"entry_id": e.id})
        assert r.status_code == 400 and "not automated" in r.json()["detail"]
        e.rules, e.strategy = saved
    finally:
        server.settings.autotrade = False



def test_adopt_a_bet_placed_on_the_betfair_website(srv, tmp_path):
    from tradescout.autotrade import position as P
    server, c, ex = srv
    server.autotrader.path = tmp_path / "adopt.json"
    server.autotrader.jobs = {}
    server.autotrader.auto_start = False
    ex.control({"delayed": False})
    bf = server.rt.betfair
    r = c.get(f"/api/scan?date={DAY}&sport=football").json()
    m = r["matches"][-1]
    from tradescout.models import Fixture
    fx = next(f for f in server.rt.fixture_cache[f"football:{DAY}"] if f.home == m["home"])
    full = bf.resolve_full(fx, "MATCH_ODDS", "draw")
    # placed "on the website": no TradeScout tag on the order
    rep = bf._rpc("placeOrders", {"marketId": full["market_id"], "instructions": [{"selectionId": full["selection_id"], "handicap": 0, "side": "LAY", "orderType": "LIMIT",
                                  "limitOrder": {"size": 4.0, "price": full["best_lay"], "persistenceType": "LAPSE"}}], "customerRef": "website-bet"})
    bet = rep["instructionReports"][0]["betId"]
    rows = c.get("/api/autotrade/betfair_bets").json()["bets"]
    row = next(x for x in rows if bet in x["bet_ids"])
    assert row["side"] == "lay" and row["runner_name"] == "The Draw" and row["sport"] == "football" and [p["key"] for p in row["plans"]] == ["ltd"]
    server.settings.autotrade = True
    try:
        p = c.post("/api/autotrade/preview_bet", json={"key": row["key"], "strategy": "ltd"}).json()
        assert p["job"]["rules_text"] and abs(p["job"]["unit"] - row["liability"]) < 0.01 and any("Adopted" in w for w in p["warnings"])
        assert c.post("/api/autotrade/preview_bet", json={"key": row["key"], "strategy": "b2l_fav"}).status_code == 400  # wrong plan for a draw lay
        a = c.post("/api/autotrade/arm_bet", json={"key": row["key"], "strategy": "ltd", "confirm": True}).json()
        job = server.autotrader.jobs[a["job"]["id"]]
        assert c.get("/api/autotrade/betfair_bets").json()["bets"][[x["key"] for x in rows].index(row["key"])]["armed_job"] == job.id
        name = m["exchange_event"]
        ex.control({"price": {"event": name, "market_type": "MATCH_ODDS", "runner": "The Draw", "back": 6.0, "lay": 6.2}})
        ex.control({"play": {"event": name, "minute": 25, "home": 1, "away": 0, "first_goal": "home"}})
        server.autotrader.tick()
        assert job.state == "done", job.log
        mine = [o for o in ex.orders if o["betId"] in job.bet_ids]
        exp = P.exposure(mine, full["selection_id"])
        assert abs(exp.win - exp.lose) < 0.5 and exp.worst > 0
    finally:
        server.settings.autotrade = False
        ex.control({"inplay": []})


def test_plans_offered_for_outside_bets():
    from tradescout.autotrade.adopt import plans_for
    assert plans_for("MATCH_ODDS", "The Draw", "lay", "football") == ["ltd"]
    assert plans_for("MATCH_ODDS", "Arsenal", "back", "football") == ["b2l_fav"]
    assert plans_for("OVER_UNDER_25", "Over 2.5 Goals", "back", "football") == ["over25_ins"]
    assert plans_for("OVER_UNDER_25", "Under 2.5 Goals", "lay", "football") == ["lay_under25_staged"]
    assert plans_for("OVER_UNDER_25", "Under 2.5 Goals", "back", "football") == ["under25_tradeout"]
    assert plans_for("CORRECT_SCORE", "0 - 0", "lay", "football") == ["lay_00"]
    assert plans_for("CORRECT_SCORE", "1 - 1", "back", "football") == []
    assert plans_for("MATCH_ODDS", "C Alcaraz", "back", "tennis") == ["tn_b2l_fav"]
    assert plans_for("COMBINED_TOTAL", "Over 22.5", "back", "tennis") == ["tn_over_games"]
    assert plans_for("MATCH_ODDS", "C Alcaraz", "lay", "tennis") == []
