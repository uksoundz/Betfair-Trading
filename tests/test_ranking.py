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


def test_journal_roundtrip(tmp_path, provider):
    from datetime import date as _d
    from tradescout.data.base import NoPrices
    from tradescout.journal import Journal
    scout = Scout(provider, provider, NoPrices(), calibration=Calibration())
    scan = scout.scan(AS_OF)
    j = Journal(path=tmp_path / "j.json")
    e = j.add(scan.ideas[0], stake_money=10.0)
    assert j.add(scan.ideas[0], stake_money=10.0).id == e.id  # no duplicates
    assert len(Journal(path=tmp_path / "j.json").entries) == 1
    n = j.settle(scout, provider.result_for)
    assert n == 1 and j.entries[0].status in ("won", "lost") and j.entries[0].pnl_money is not None
    s = j.summary()
    assert s["settled"] == 1 and s["staked"] == 10.0
    assert j.remove(e.id) and not j.entries


def test_write_env_merges_and_reloads(tmp_path, monkeypatch):
    from tradescout import config
    p = tmp_path / ".env"
    config.write_env({"TRADESCOUT_BANK": "250", "FOOTBALL_DATA_API_KEY": "abc"}, path=p)
    assert config.settings.bank == 250.0 and config.settings.football_data_org_key == "abc"
    config.write_env({"FOOTBALL_DATA_API_KEY": None}, path=p)
    assert config.settings.football_data_org_key is None and "TRADESCOUT_BANK=250" in p.read_text()
    config.write_env({"TRADESCOUT_BANK": "1000"}, path=p)
