from datetime import date

from tradescout.data.names import canonical, names_match
from tests.conftest import AS_OF


def test_results_are_strictly_before_cutoff(provider):
    res = provider.results(before=AS_OF)
    assert len(res) > 4000
    assert max(r.date for r in res) < AS_OF
    assert all(r.home_goals >= 0 and r.away_goals >= 0 for r in res)


def test_fixtures_for_day(provider, fixtures):
    assert len(fixtures) > 10
    assert all(f.date == AS_OF for f in fixtures)
    assert provider.result_for(fixtures[0]) is not None


def test_match_days(provider):
    days = provider.match_days(date(2025, 11, 1), date(2025, 11, 30))
    assert AS_OF in days


def test_name_canonicalisation():
    assert canonical("Man United") == "Manchester United FC"
    assert canonical("Nott'm Forest") == "Nottingham Forest FC"
    assert canonical("Manchester United FC") == "Manchester United FC"
    assert names_match("Tottenham", "Tottenham Hotspur FC")
    assert canonical("Some Unknown Club") == "Some Unknown Club"


def test_current_season_label(provider):
    assert provider.current_season(date(2026, 10, 8)) == "2026-27"
    assert provider.current_season(date(2026, 3, 1)) == "2025-26"
    assert "2026-27" in provider.available_seasons()
