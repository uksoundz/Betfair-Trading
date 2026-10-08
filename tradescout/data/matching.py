"""Fuzzy matching of team names across feeds (openfootball, football-data.org, Betfair).

Betfair names clubs the way punters say them ("Man Utd", "Nottm Forest", "Sheff Wed", "Paris St-G",
"Athletic Bilbao", "Mgladbach"); the model keys ratings on the openfootball spelling ("Manchester
United FC", "Paris Saint-Germain FC", "Athletic Club"). An exact alias table cannot keep up with
every club in every league, so matching is scored:

1. normalise (accents folded, punctuation dropped, lower case), expand abbreviations token by
   token (utd -> united, nottm -> nottingham, st -> saint ...) and apply a few whole-name aliases
   that cannot be guessed (inter, mgladbach, athletic bilbao, wolves, spurs ...);
2. drop noise tokens that carry no identity (fc, afc, cf, sc, calcio, 1909 ...) and give common
   suffix words low weight (united, city, town, real, borussia ...);
3. score = half weighted Jaccard overlap + half weighted containment of the shorter name, with
   tokens matching on equality or a shared prefix of at least five letters (milan ~ milano,
   lyon ~ lyonnais).

`best_event` then picks, for one fixture, the Betfair event whose two sides both match, insists
on a margin over the runner-up, and returns the candidates it considered so a miss can be shown
to the user instead of silently producing "no exchange price".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from .names import canonical

NOISE = {"fc", "afc", "cf", "sc", "ac", "as", "ss", "us", "ssc", "rc", "rcd", "ud", "cd", "sd", "ca", "club", "de", "do", "da", "la", "le", "les", "el",
         "and", "calcio", "balompie", "futbol", "hsc", "osc", "sco", "cfc", "bc", "acf", "sv", "tsg", "vfb", "vfl", "fsv", "bsc", "fk", "nk", "sk", "ks",
         "bk", "ik", "aj", "es", "ogc", "the", "of", "1", "04", "05", "07", "29", "1846", "1848", "1899", "1901", "1907", "1909", "1910", "1913", "i", "ii",
         "football", "soccer", "team"}
LOW_WEIGHT = {"united", "city", "town", "rovers", "wanderers", "county", "albion", "athletic", "atletico", "hotspur", "north", "end", "real", "borussia",
              "racing", "sporting", "olympique", "stade", "deportivo", "dynamo", "dinamo", "spartak", "lokomotiv", "eintracht", "hellas", "alsace",
              "milano", "madrid", "barcelona", "vigo", "bremen", "frankfurt", "san", "sport", "association", "club", "fc"}
LOW = 0.3
TOKEN_ALIASES = {
    "utd": "united", "man": "manchester", "nottm": "nottingham", "sheff": "sheffield", "wed": "wednesday", "brom": "bromwich", "st": "saint",
    "ath": "athletic", "atl": "atletico", "boro": "middlesbrough", "dep": "deportivo", "munich": "munchen", "muenchen": "munchen", "koeln": "koln",
    "cologne": "koln", "lyonnais": "lyon", "brestois": "brest", "rennais": "rennes", "marseilles": "marseille", "gladbach": "monchengladbach",
    "moenchengladbach": "monchengladbach", "internazionale": "inter", "bilbao": "athletic", "psg": "paris saint germain", "qpr": "queens park rangers",
    "wolves": "wolverhampton wanderers", "spurs": "tottenham hotspur", "sociedad": "sociedad", "napoli": "napoli", "naples": "napoli", "turin": "torino",
    "rome": "roma", "florence": "fiorentina", "seville": "sevilla", "genoa": "genoa", "sevilla": "sevilla",
}
# whole normalised names that no token rule can guess, mapped to the openfootball spelling
NAME_ALIASES = {
    "inter": "FC Internazionale Milano", "inter milan": "FC Internazionale Milano", "internazionale": "FC Internazionale Milano",
    "ac milan": "AC Milan", "milan": "AC Milan", "mgladbach": "Borussia Mönchengladbach", "m gladbach": "Borussia Mönchengladbach",
    "athletic bilbao": "Athletic Club", "ath bilbao": "Athletic Club", "athletic club": "Athletic Club", "paris st g": "Paris Saint-Germain FC",
    "paris stg": "Paris Saint-Germain FC", "psg": "Paris Saint-Germain FC", "dep la coruna": "RC Deportivo La Coruña", "deportivo": "RC Deportivo La Coruña",
    "wolves": "Wolverhampton Wanderers FC", "spurs": "Tottenham Hotspur FC", "boro": "Middlesbrough FC", "st pauli": "FC St. Pauli",
    "fc koln": "1. FC Köln", "koln": "1. FC Köln", "cologne": "1. FC Köln", "bayern munich": "FC Bayern München", "bayern": "FC Bayern München",
    "hamburg": "Hamburger SV", "hamburg sv": "Hamburger SV", "ein frankfurt": "Eintracht Frankfurt", "schalke": "FC Schalke 04",
    "racing santander": "Real Racing Club de Santander", "celta": "RC Celta de Vigo", "sociedad": "Real Sociedad de Fútbol", "betis": "Real Betis Balompié",
    "oviedo": "Real Oviedo", "lyon": "Olympique Lyonnais", "marseille": "Olympique de Marseille", "brest": "Stade Brestois 29", "rennes": "Stade Rennais FC 1901",
    "lens": "RC Lens", "st etienne": "AS Saint-Étienne", "saint etienne": "AS Saint-Étienne", "le havre": "Le Havre AC", "le mans": "Le Mans FC",
    "nice": "OGC Nice", "reims": "Stade de Reims", "verona": "Hellas Verona FC", "hellas verona": "Hellas Verona FC", "sheff wed": "Sheffield Wednesday FC",
    "sheffield wed": "Sheffield Wednesday FC", "sheff utd": "Sheffield United FC", "west brom": "West Bromwich Albion FC", "qpr": "Queens Park Rangers FC",
    "nottm forest": "Nottingham Forest FC", "man utd": "Manchester United FC", "man city": "Manchester City FC", "newcastle": "Newcastle United FC",
    "brighton": "Brighton & Hove Albion FC", "bournemouth": "AFC Bournemouth", "oxford utd": "Oxford United FC", "mk dons": "Milton Keynes Dons FC",
}
_strip_re = re.compile(r"[^a-z0-9 ]+")


def fold(s: str) -> str:
    """'Borussia Mönchengladbach' -> 'borussia monchengladbach'; '&' -> 'and'; punctuation -> space."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ").replace("-", " ").replace("'", "").replace(".", " ").replace("/", " ")
    return re.sub(r"\s+", " ", _strip_re.sub(" ", s)).strip()


def tokens(name: str) -> list[str]:
    """Identity tokens of a club name after aliasing and noise removal."""
    folded = fold(name)
    if folded in NAME_ALIASES:
        folded = fold(NAME_ALIASES[folded])
    out: list[str] = []
    for t in folded.split():
        t = TOKEN_ALIASES.get(t, t)
        for part in t.split():
            if part not in NOISE:
                out.append(part)
    if "inter" in out and "milano" in out:  # Internazionale is "Inter", never "Milan"
        out.remove("milano")
    if not out:  # the whole name was noise (e.g. "FC"): keep what there was
        out = folded.split()
    return out


def _tok_match(a: str, b: str) -> bool:
    if a == b:
        return True
    n = min(len(a), len(b))
    return n >= 5 and a[:n] == b[:n]


def _weight(t: str) -> float:
    return LOW if t in LOW_WEIGHT else 1.0


def score(a: str, b: str) -> float:
    """0..1 similarity of two club names. 1.0 for the same club under any known spelling."""
    if not a or not b:
        return 0.0
    if canonical(a) == canonical(b):
        return 1.0
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    matched_a = {t for t in ta if any(_tok_match(t, u) for u in tb)}
    matched_b = {t for t in tb if any(_tok_match(t, u) for u in ta)}
    wa, wb = sum(_weight(t) for t in ta), sum(_weight(t) for t in tb)
    inter = sum(_weight(t) for t in matched_a)
    union = wa + wb - inter
    jaccard = inter / union if union else 0.0
    # containment of the shorter name ("Espanyol" in "Espanyol de Barcelona"), discounted by whatever
    # the longer name says that the shorter one does not ("Paris FC" is not "Paris Saint-Germain")
    short, short_m, long_, long_m = (ta, matched_a, tb, matched_b) if wa <= wb else (tb, matched_b, ta, matched_a)
    matched_w = sum(_weight(t) for t in short_m)
    extra_long = sum(_weight(t) for t in long_ if t not in long_m)
    containment = (matched_w / sum(_weight(t) for t in short)) * (matched_w / (matched_w + 0.5 * extra_long) if matched_w else 0.0)
    s = 0.5 * jaccard + 0.5 * containment
    if len(matched_a) < len(ta) and len(matched_b) < len(tb):
        s -= 0.25  # each side carries a qualifier the other lacks: Man Utd / Man City, Bristol City / Bristol Rovers
    return round(max(0.0, s), 3)


def same_team(a: str, b: str, threshold: float = 0.6) -> bool:
    return score(a, b) >= threshold


@dataclass
class EventMatch:
    event_id: Optional[str]
    event_name: Optional[str]
    score: float
    home_score: float = 0.0
    away_score: float = 0.0
    candidates: list = field(default_factory=list)  # [(event name, pair score)] best first
    reason: str = ""


def split_event(name: str) -> Optional[tuple[str, str]]:
    for sep in (" v ", " vs ", " - ", " @ "):
        if sep in name:
            h, a = name.split(sep, 1)
            return h.strip(), a.strip()
    return None


def best_event(home: str, away: str, events: Iterable[dict], kickoff: Optional[datetime] = None, threshold: float = 0.6, margin: float = 0.12) -> EventMatch:
    """Pick the Betfair event for a fixture. `events` are listEvents rows ({"event": {"id","name","openDate"}}).
    Both sides must match; a near tie between two events is reported as ambiguous rather than guessed."""
    scored: list[tuple[float, float, float, dict]] = []
    for ev in events:
        e = ev.get("event", ev)
        parts = split_event(e.get("name", ""))
        if not parts:
            continue
        h, a = parts
        sh, sa = score(h, home), score(a, away)
        pair = min(sh, sa) * 0.7 + 0.3 * (sh + sa) / 2
        if kickoff is not None and e.get("openDate"):
            try:
                od = datetime.fromisoformat(e["openDate"].replace("Z", "+00:00"))
                ko = kickoff if kickoff.tzinfo else kickoff.replace(tzinfo=timezone.utc)
                hours = abs((od - ko).total_seconds()) / 3600
                if hours > 6:
                    pair -= 0.2  # same clubs, different day/leg: probably not this fixture
            except ValueError:
                pass
        scored.append((pair, sh, sa, e))
    scored.sort(key=lambda x: -x[0])
    cands = [(e.get("name", ""), round(p, 3)) for p, _, _, e in scored[:3]]
    if not scored:
        return EventMatch(None, None, 0.0, candidates=cands, reason="No events on the exchange for that day.")
    pair, sh, sa, e = scored[0]
    if pair < threshold:
        return EventMatch(None, None, round(pair, 3), round(sh, 3), round(sa, 3), cands,
                          f"No exchange event matched '{home} v {away}' (closest: '{cands[0][0]}' at {cands[0][1]:.2f}).")
    if len(scored) > 1 and scored[1][0] >= pair - margin and scored[1][0] >= threshold:
        return EventMatch(None, None, round(pair, 3), round(sh, 3), round(sa, 3), cands,
                          f"Ambiguous: '{cands[0][0]}' ({cands[0][1]:.2f}) and '{cands[1][0]}' ({cands[1][1]:.2f}) both fit '{home} v {away}'.")
    return EventMatch(e.get("id"), e.get("name"), round(pair, 3), round(sh, 3), round(sa, 3), cands, "matched")


def assign_match_odds(runners: Iterable[dict], home: str, away: str) -> dict:
    """selectionId -> 'home' | 'away' | 'draw' for a Match Odds market, by name with sortPriority as the tie-break
    (Betfair convention: 1 home, 2 away, 3 draw)."""
    rows = list(runners)
    out: dict = {}
    sides = []
    for r in rows:
        name = r.get("runnerName", "")
        if fold(name) in ("the draw", "draw"):
            out[r["selectionId"]] = "draw"
        else:
            sides.append(r)
    if len(sides) == 2:
        s = [(score(r.get("runnerName", ""), home), score(r.get("runnerName", ""), away)) for r in sides]
        first_home = (s[0][0] - s[0][1]) >= (s[1][0] - s[1][1])
        if s[0] == s[1]:  # names tell nothing: fall back to sort priority
            first_home = (sides[0].get("sortPriority", 1) or 1) <= (sides[1].get("sortPriority", 2) or 2)
        out[sides[0]["selectionId"]] = "home" if first_home else "away"
        out[sides[1]["selectionId"]] = "away" if first_home else "home"
    else:
        for r in sides:
            out[r["selectionId"]] = "home" if (r.get("sortPriority") == 1) else "away"
    return out
