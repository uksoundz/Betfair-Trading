"""Fixture-to-exchange name matching: Betfair's short names against openfootball spellings."""
import pytest

from tradescout.data.matching import assign_match_odds, best_event, score, tokens

MUST_MATCH = [
    ("Man Utd", "Manchester United FC"), ("Man City", "Manchester City FC"), ("Nottm Forest", "Nottingham Forest FC"), ("Sheff Wed", "Sheffield Wednesday FC"),
    ("Sheff Utd", "Sheffield United FC"), ("West Brom", "West Bromwich Albion FC"), ("QPR", "Queens Park Rangers FC"), ("Wolves", "Wolverhampton Wanderers FC"),
    ("Tottenham", "Tottenham Hotspur FC"), ("Spurs", "Tottenham Hotspur FC"), ("Brighton", "Brighton & Hove Albion FC"), ("Bournemouth", "AFC Bournemouth"),
    ("Hull", "Hull City AFC"), ("Coventry", "Coventry City FC"), ("Preston", "Preston North End FC"), ("Bristol City", "Bristol City FC"),
    ("Charlton", "Charlton Athletic FC"), ("Paris St-G", "Paris Saint-Germain FC"), ("PSG", "Paris Saint-Germain FC"), ("Paris FC", "Paris FC"),
    ("Athletic Bilbao", "Athletic Club"), ("Atletico Madrid", "Club Atlético de Madrid"), ("Real Madrid", "Real Madrid CF"), ("Espanyol", "RCD Espanyol de Barcelona"),
    ("Barcelona", "FC Barcelona"), ("Dep La Coruna", "RC Deportivo La Coruña"), ("Racing Santander", "Real Racing Club de Santander"),
    ("Rayo Vallecano", "Rayo Vallecano de Madrid"), ("Celta Vigo", "RC Celta de Vigo"), ("Alaves", "Deportivo Alavés"), ("Bayern Munich", "FC Bayern München"),
    ("Mgladbach", "Borussia Mönchengladbach"), ("Dortmund", "Borussia Dortmund"), ("FC Koln", "1. FC Köln"), ("Hamburg", "Hamburger SV"), ("Schalke 04", "FC Schalke 04"),
    ("Mainz", "1. FSV Mainz 05"), ("Union Berlin", "1. FC Union Berlin"), ("Paderborn", "SC Paderborn 07"), ("Elversberg", "SV 07 Elversberg"),
    ("Inter", "FC Internazionale Milano"), ("Inter Milan", "FC Internazionale Milano"), ("AC Milan", "AC Milan"), ("Milan", "AC Milan"), ("Napoli", "SSC Napoli"),
    ("Frosinone", "Frosinone Calcio"), ("Lyon", "Olympique Lyonnais"), ("Marseille", "Olympique de Marseille"), ("Brest", "Stade Brestois 29"),
    ("Rennes", "Stade Rennais FC 1901"), ("Lens", "Racing Club de Lens"), ("Le Mans", "Le Mans FC"), ("Le Havre", "Le Havre AC"), ("Troyes", "ES Troyes AC"),
    ("Nice", "OGC Nice"), ("Strasbourg", "RC Strasbourg Alsace"), ("Verona", "Hellas Verona FC"), ("Lazio", "SS Lazio"), ("Roma", "AS Roma"),
    ("Leeds", "Leeds United FC"), ("Newcastle", "Newcastle United FC"), ("Ipswich", "Ipswich Town FC"), ("Werder Bremen", "SV Werder Bremen"),
    ("Hoffenheim", "TSG 1899 Hoffenheim"), ("Stuttgart", "VfB Stuttgart"), ("Osasuna", "CA Osasuna"), ("Malaga", "Málaga CF"), ("Real Betis", "Real Betis Balompié"),
    ("Real Sociedad", "Real Sociedad de Fútbol"), ("Monza", "AC Monza"), ("Fiorentina", "ACF Fiorentina"), ("Bologna", "Bologna FC 1909"), ("Como", "Como 1907"),
    ("Parma", "Parma Calcio 1913"), ("Blackburn", "Blackburn Rovers FC"), ("Bolton", "Bolton Wanderers FC"), ("Derby", "Derby County FC"), ("Swansea", "Swansea City AFC"),
    ("West Ham", "West Ham United FC"), ("Wrexham", "Wrexham AFC"), ("Stoke", "Stoke City FC"), ("Norwich", "Norwich City FC"), ("Middlesbrough", "Middlesbrough FC"),
    ("Boro", "Middlesbrough FC"), ("Man United", "Manchester United FC"), ("Manchester United", "Manchester United FC"),
    ("Braga", "Sporting Clube de Braga"), ("Sporting Lisbon", "Sporting Clube de Portugal"), ("Benfica", "Sport Lisboa e Benfica"), ("Porto", "FC Porto"),
    ("Guimaraes", "Vitória SC"), ("Famalicao", "FC Famalicão"), ("Rio Ave", "Rio Ave FC"), ("Gil Vicente", "Gil Vicente FC"), ("Casa Pia", "Casa Pia AC"),
    ("Santa Clara", "CD Santa Clara"), ("Nacional", "CD Nacional"), ("Estoril", "GD Estoril Praia"), ("Arouca", "FC Arouca"), ("Moreirense", "Moreirense FC"),
    ("PSV", "PSV"), ("Heerenveen", "SC Heerenveen"), ("Ajax", "AFC Ajax"), ("Feyenoord", "Feyenoord Rotterdam"), ("Az Alkmaar", "AZ"), ("FC Twente", "FC Twente '65"),
    ("FC Utrecht", "FC Utrecht"), ("FC Groningen", "FC Groningen"), ("NEC Nijmegen", "NEC"), ("Go Ahead Eagles", "Go Ahead Eagles"), ("Sparta Rotterdam", "Sparta Rotterdam"),
    ("Fortuna Sittard", "Fortuna Sittard"), ("PEC Zwolle", "PEC Zwolle"), ("Heracles", "Heracles Almelo"), ("NAC Breda", "NAC Breda"), ("Volendam", "FC Volendam"),
]
MUST_NOT = [
    ("Man Utd", "Manchester City FC"), ("Sheff Wed", "Sheffield United FC"), ("Inter", "AC Milan"), ("AC Milan", "FC Internazionale Milano"),
    ("Bristol City", "Bristol Rovers FC"), ("Paris FC", "Paris Saint-Germain FC"), ("Real Madrid", "Club Atlético de Madrid"), ("Barcelona", "RCD Espanyol de Barcelona"),
    ("West Brom", "West Ham United FC"), ("Dortmund", "Borussia Mönchengladbach"), ("Athletic Bilbao", "Club Atlético de Madrid"), ("Union Berlin", "Hertha BSC"),
    ("Nottingham Forest", "Notts County FC"), ("Valencia", "Valenciennes FC"), ("Monza", "AS Monaco FC"), ("Parma", "Paris FC"), ("Leeds", "Leicester City FC"),
    ("Burnley", "Burton Albion FC"), ("Stoke", "Stockport County FC"), ("Man Utd", "Manchester City FC"),
    ("Braga", "Sporting Clube de Portugal"), ("Sporting Lisbon", "Sporting Clube de Braga"), ("Sparta Rotterdam", "Feyenoord Rotterdam"),
    ("Borussia Dortmund II", "Borussia Dortmund"), ("Arsenal Women", "Arsenal FC"), ("Real Madrid Castilla", "Real Madrid CF"), ("Chelsea U21", "Chelsea FC"),
    ("Man Utd (W)", "Manchester United FC"), ("Barcelona B", "FC Barcelona"),
]


@pytest.mark.parametrize("bf,of", MUST_MATCH)
def test_betfair_spelling_matches_openfootball(bf, of):
    assert score(bf, of) >= 0.6, (bf, of, score(bf, of), tokens(bf), tokens(of))


@pytest.mark.parametrize("bf,of", MUST_NOT)
def test_rivals_and_lookalikes_do_not_match(bf, of):
    assert score(bf, of) < 0.6, (bf, of, score(bf, of))


def test_best_event_picks_the_right_fixture_and_explains_misses():
    evs = [{"event": {"id": "1", "name": "Man Utd v Nottm Forest", "openDate": "2026-10-10T14:00:00.000Z"}},
           {"event": {"id": "2", "name": "Man City v Chelsea", "openDate": "2026-10-10T14:00:00.000Z"}},
           {"event": {"id": "3", "name": "English Premier League 2026/27"}}]
    m = best_event("Manchester United FC", "Nottingham Forest FC", evs)
    assert m.event_id == "1" and m.score >= 0.95 and m.reason == "matched"
    miss = best_event("Manchester City FC", "Arsenal FC", evs)
    assert miss.event_id is None and "closest" in miss.reason and miss.candidates[0][0] == "Man City v Chelsea"
    # the same two clubs on another date (a cup replay listed for tomorrow) is not this fixture
    from datetime import datetime
    far = best_event("Manchester United FC", "Nottingham Forest FC", evs, kickoff=datetime(2026, 10, 12, 20, 0))
    assert far.score < m.score


def test_best_event_refuses_ambiguity():
    evs = [{"event": {"id": "1", "name": "Sheff Utd v Leeds"}}, {"event": {"id": "2", "name": "Sheffield United v Leeds Utd"}}]
    m = best_event("Sheffield United FC", "Leeds United FC", evs)
    assert m.event_id is None and m.reason.startswith("Ambiguous")


def test_match_odds_runner_assignment_by_name_then_sort_priority():
    runners = [{"selectionId": 1, "runnerName": "Nottm Forest", "sortPriority": 2}, {"selectionId": 2, "runnerName": "Man Utd", "sortPriority": 1},
               {"selectionId": 3, "runnerName": "The Draw", "sortPriority": 3}]
    assert assign_match_odds(runners, "Manchester United FC", "Nottingham Forest FC") == {3: "draw", 1: "away", 2: "home"}
    anon = [{"selectionId": 7, "runnerName": "Team A", "sortPriority": 1}, {"selectionId": 8, "runnerName": "Team B", "sortPriority": 2}, {"selectionId": 9, "runnerName": "The Draw", "sortPriority": 3}]
    assert assign_match_odds(anon, "X", "Y") == {9: "draw", 7: "home", 8: "away"}
