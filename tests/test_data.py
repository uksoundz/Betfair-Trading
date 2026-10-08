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


def test_football_data_org_parse_keeps_finished_and_filters_by_uk_date():
    from tradescout.data.football_data_org import FootballDataOrgProvider
    payload = {"matches": [
        {"id": 1, "utcDate": "2026-10-10T14:00:00Z", "status": "TIMED", "competition": {"code": "PL"},
         "homeTeam": {"name": "Arsenal FC"}, "awayTeam": {"name": "Chelsea FC"}},
        {"id": 2, "utcDate": "2026-10-10T19:00:00Z", "status": "FINISHED", "competition": {"code": "BL1"},
         "homeTeam": {"name": "Bayern Munich"}, "awayTeam": {"name": "Dortmund"}},
        {"id": 3, "utcDate": "2026-10-11T11:00:00Z", "status": "TIMED", "competition": {"code": "PL"},
         "homeTeam": {"name": "Everton FC"}, "awayTeam": {"name": "Fulham FC"}},
        {"id": 4, "utcDate": "2026-10-10T15:00:00Z", "status": "POSTPONED", "competition": {"code": "PL"},
         "homeTeam": {"name": "Leeds United FC"}, "awayTeam": {"name": "Burnley FC"}},
        {"id": 5, "utcDate": "2026-10-10T15:00:00Z", "status": "TIMED", "competition": {"code": "XYZ"},
         "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"}},
    ]}
    fx = FootballDataOrgProvider.parse_fixtures(payload, date(2026, 10, 10))
    assert [f.fixture_id for f in fx] == ["1", "2"]
    assert fx[1].home == "FC Bayern München" and fx[1].away == "Borussia Dortmund"
    assert fx[0].kickoff.hour == 15  # 14:00 UTC is 15:00 UK time in October
