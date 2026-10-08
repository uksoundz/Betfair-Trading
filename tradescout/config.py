"""Runtime configuration. Everything is overridable by environment variable so the tool
runs with zero setup on the bundled sample data and upgrades to live feeds when keys exist."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
SAMPLE_DATA_DIR = REPO_ROOT / "data" / "sample"
ENV_FILE = REPO_ROOT / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Read KEY=value lines from .env so users never have to set environment variables by hand.
    Real environment variables win over the file."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

# Betfair liquidity proxy per competition, 1.0 = deepest markets. Used in ranking so that a
# marginal edge in a thin market never outranks a similar edge in the Premier League.
LEAGUE_LIQUIDITY: dict[str, float] = {
    "en.1": 1.00,  # Premier League
    "es.1": 0.90,  # La Liga
    "de.1": 0.85,  # Bundesliga
    "it.1": 0.85,  # Serie A
    "fr.1": 0.75,  # Ligue 1
    "en.2": 0.70,  # Championship
    "uefa.cl": 0.95,
    "uefa.el": 0.80,
    "nl.1": 0.60,
    "pt.1": 0.60,
    "be.1": 0.50,
    "tr.1": 0.50,
    "sco.1": 0.55,
}
DEFAULT_LIQUIDITY = 0.40

LEAGUE_NAMES: dict[str, str] = {
    "en.1": "Premier League",
    "en.2": "Championship",
    "de.1": "Bundesliga",
    "es.1": "La Liga",
    "it.1": "Serie A",
    "fr.1": "Ligue 1",
}


@dataclass
class Settings:
    football_data_org_key: str | None = field(default_factory=lambda: os.getenv("FOOTBALL_DATA_API_KEY"))
    api_football_key: str | None = field(default_factory=lambda: os.getenv("API_FOOTBALL_KEY"))
    betfair_app_key: str | None = field(default_factory=lambda: os.getenv("BETFAIR_APP_KEY"))
    betfair_session_token: str | None = field(default_factory=lambda: os.getenv("BETFAIR_SESSION_TOKEN"))
    betfair_username: str | None = field(default_factory=lambda: os.getenv("BETFAIR_USERNAME"))
    betfair_password: str | None = field(default_factory=lambda: os.getenv("BETFAIR_PASSWORD"))
    cache_dir: Path = field(default_factory=lambda: Path(os.getenv("TRADESCOUT_CACHE", REPO_ROOT / ".cache")))
    # Model hyper-parameters
    time_decay_xi: float = float(os.getenv("TRADESCOUT_XI", "0.0045"))  # per day; ~half-life 154 days
    history_days: int = int(os.getenv("TRADESCOUT_HISTORY_DAYS", "900"))
    max_goals: int = 8
    bank: float = float(os.getenv("TRADESCOUT_BANK", "1000"))
    kelly_fraction: float = float(os.getenv("TRADESCOUT_KELLY", "0.25"))

    @property
    def has_live_fixtures(self) -> bool:
        return bool(self.football_data_org_key or self.api_football_key)

    @property
    def has_betfair(self) -> bool:
        return bool(self.betfair_app_key and (self.betfair_session_token or (self.betfair_username and self.betfair_password)))


settings = Settings()
