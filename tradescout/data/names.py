"""Team-name normalisation. Every feed spells clubs differently; the model keys ratings on the
openfootball spelling, so other feeds are mapped onto it here. Unknown names pass through
unchanged (and the scout warns when a fixture team has no rating)."""
from __future__ import annotations

import re

ALIASES: dict[str, str] = {
    # football-data.org / football-data.co.uk -> openfootball
    "Man United": "Manchester United FC", "Manchester United": "Manchester United FC", "Man Utd": "Manchester United FC",
    "Man City": "Manchester City FC", "Manchester City": "Manchester City FC",
    "Liverpool": "Liverpool FC", "Arsenal": "Arsenal FC", "Chelsea": "Chelsea FC", "Tottenham": "Tottenham Hotspur FC",
    "Tottenham Hotspur": "Tottenham Hotspur FC", "Spurs": "Tottenham Hotspur FC",
    "Newcastle": "Newcastle United FC", "Newcastle United": "Newcastle United FC",
    "Aston Villa": "Aston Villa FC", "Brighton": "Brighton & Hove Albion FC", "Brighton & Hove Albion": "Brighton & Hove Albion FC",
    "Brighton and Hove Albion": "Brighton & Hove Albion FC",
    "West Ham": "West Ham United FC", "West Ham United": "West Ham United FC", "Everton": "Everton FC",
    "Wolves": "Wolverhampton Wanderers FC", "Wolverhampton Wanderers": "Wolverhampton Wanderers FC",
    "Nott'm Forest": "Nottingham Forest FC", "Nottingham Forest": "Nottingham Forest FC", "Nottingham": "Nottingham Forest FC",
    "Crystal Palace": "Crystal Palace FC", "Fulham": "Fulham FC", "Brentford": "Brentford FC", "Bournemouth": "AFC Bournemouth",
    "Leicester": "Leicester City FC", "Leicester City": "Leicester City FC", "Ipswich": "Ipswich Town FC", "Ipswich Town": "Ipswich Town FC",
    "Southampton": "Southampton FC", "Leeds": "Leeds United FC", "Leeds United": "Leeds United FC", "Burnley": "Burnley FC",
    "Sunderland": "Sunderland AFC", "Sheffield United": "Sheffield United FC", "Sheffield Utd": "Sheffield United FC",
    "Luton": "Luton Town FC", "Luton Town": "Luton Town FC",
    # Germany
    "Bayern Munich": "FC Bayern München", "Bayern München": "FC Bayern München", "FC Bayern Munchen": "FC Bayern München",
    "Dortmund": "Borussia Dortmund", "Leverkusen": "Bayer 04 Leverkusen", "Bayer Leverkusen": "Bayer 04 Leverkusen",
    "RB Leipzig": "RB Leipzig", "Ein Frankfurt": "Eintracht Frankfurt", "Stuttgart": "VfB Stuttgart", "Wolfsburg": "VfL Wolfsburg",
    "M'gladbach": "Borussia Mönchengladbach", "Borussia Monchengladbach": "Borussia Mönchengladbach",
    "Mainz": "1. FSV Mainz 05", "1. FSV Mainz 05": "1. FSV Mainz 05", "Freiburg": "SC Freiburg", "Hoffenheim": "TSG 1899 Hoffenheim",
    "Augsburg": "FC Augsburg", "Werder Bremen": "SV Werder Bremen", "Union Berlin": "1. FC Union Berlin", "Heidenheim": "1. FC Heidenheim 1846",
    "Bochum": "VfL Bochum 1848", "St Pauli": "FC St. Pauli", "FC St. Pauli 1910": "FC St. Pauli", "Holstein Kiel": "Holstein Kiel",
    "FC Koln": "1. FC Köln", "1. FC Köln": "1. FC Köln", "Hamburg": "Hamburger SV", "Hamburger SV": "Hamburger SV",
    # Spain
    "Real Madrid": "Real Madrid CF", "Barcelona": "FC Barcelona", "Ath Madrid": "Atlético de Madrid", "Atletico Madrid": "Atlético de Madrid",
    "Club Atlético de Madrid": "Atlético de Madrid", "Ath Bilbao": "Athletic Club", "Athletic Club": "Athletic Club",
    "Sociedad": "Real Sociedad de Fútbol", "Real Sociedad": "Real Sociedad de Fútbol", "Villarreal": "Villarreal CF", "Betis": "Real Betis Balompié",
    "Real Betis": "Real Betis Balompié", "Sevilla": "Sevilla FC", "Valencia": "Valencia CF", "Girona": "Girona FC", "Osasuna": "CA Osasuna",
    "Celta": "RC Celta de Vigo", "Celta Vigo": "RC Celta de Vigo", "Mallorca": "RCD Mallorca", "Getafe": "Getafe CF", "Alaves": "Deportivo Alavés",
    "Deportivo Alavés": "Deportivo Alavés", "Las Palmas": "UD Las Palmas", "Vallecano": "Rayo Vallecano de Madrid", "Rayo Vallecano": "Rayo Vallecano de Madrid",
    "Espanol": "RCD Espanyol de Barcelona", "Espanyol": "RCD Espanyol de Barcelona", "Leganes": "CD Leganés", "Valladolid": "Real Valladolid CF",
    "Levante": "Levante UD", "Elche": "Elche CF", "Oviedo": "Real Oviedo",
    # Italy
    "Inter": "FC Internazionale Milano", "Inter Milan": "FC Internazionale Milano", "Milan": "AC Milan", "AC Milan": "AC Milan", "Juventus": "Juventus FC",
    "Napoli": "SSC Napoli", "Roma": "AS Roma", "Lazio": "SS Lazio", "Atalanta": "Atalanta BC", "Fiorentina": "ACF Fiorentina", "Bologna": "Bologna FC 1909",
    "Torino": "Torino FC", "Udinese": "Udinese Calcio", "Genoa": "Genoa CFC", "Cagliari": "Cagliari Calcio", "Verona": "Hellas Verona FC",
    "Hellas Verona": "Hellas Verona FC", "Como": "Como 1907", "Lecce": "US Lecce", "Parma": "Parma Calcio 1913", "Empoli": "Empoli FC",
    "Venezia": "Venezia FC", "Monza": "AC Monza", "Sassuolo": "US Sassuolo Calcio", "Pisa": "AC Pisa 1909", "Cremonese": "US Cremonese",
    # France
    "Paris SG": "Paris Saint-Germain FC", "Paris Saint-Germain": "Paris Saint-Germain FC", "PSG": "Paris Saint-Germain FC",
    "Marseille": "Olympique de Marseille", "Lyon": "Olympique Lyonnais", "Monaco": "AS Monaco FC", "Lille": "Lille OSC", "Nice": "OGC Nice",
    "Lens": "RC Lens", "Rennes": "Stade Rennais FC 1901", "Strasbourg": "RC Strasbourg Alsace", "Brest": "Stade Brestois 29", "Toulouse": "Toulouse FC",
    "Nantes": "FC Nantes", "Auxerre": "AJ Auxerre", "Angers": "Angers SCO", "Le Havre": "Le Havre AC", "Reims": "Stade de Reims",
    "St Etienne": "AS Saint-Étienne", "Montpellier": "Montpellier HSC", "Lorient": "FC Lorient", "Metz": "FC Metz", "Paris FC": "Paris FC",
}

_norm_re = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _norm_re.sub("", s.lower())


_NORM_ALIASES = {_norm(k): v for k, v in ALIASES.items()}


def canonical(name: str) -> str:
    """Map any feed's spelling to the openfootball spelling where we know it."""
    if name in ALIASES:
        return ALIASES[name]
    return _NORM_ALIASES.get(_norm(name), name)


def names_match(a: str, b: str) -> bool:
    return _norm(canonical(a)) == _norm(canonical(b))
