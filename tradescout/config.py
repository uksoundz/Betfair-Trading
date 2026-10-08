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
    # tennis (ATP singles)
    "atp.gs": 1.00,
    "atp.1000": 0.85,
    "atp.finals": 0.90,
    "atp.500": 0.65,
    "atp.250": 0.50,
    "atp.olympics": 0.70,
    "atp.tour": 0.50,
}
DEFAULT_LIQUIDITY = 0.40

LEAGUE_NAMES: dict[str, str] = {
    "en.1": "Premier League",
    "en.2": "Championship",
    "de.1": "Bundesliga",
    "es.1": "La Liga",
    "it.1": "Serie A",
    "fr.1": "Ligue 1",
    "nl.1": "Eredivisie",
    "pt.1": "Primeira Liga",
    "uefa.cl": "Champions League",
    "atp.gs": "Grand Slam",
    "atp.1000": "ATP Masters 1000",
    "atp.500": "ATP 500",
    "atp.250": "ATP 250",
    "atp.finals": "ATP Finals",
    "atp.olympics": "Olympics",
    "atp.tour": "ATP Tour",
}
SPORTS = {"football": "Football", "tennis": "Tennis (ATP)"}
FOOTBALL_LEAGUES = [k for k in LEAGUE_NAMES if not k.startswith("atp.")]
TENNIS_LEAGUES = [k for k in LEAGUE_NAMES if k.startswith("atp.")]
JOURNAL_PATH = REPO_ROOT / "data" / "journal.json"


@dataclass
class Settings:
    football_data_org_key: str | None = field(default_factory=lambda: os.getenv("FOOTBALL_DATA_API_KEY"))
    api_football_key: str | None = field(default_factory=lambda: os.getenv("API_FOOTBALL_KEY"))
    betfair_app_key: str | None = field(default_factory=lambda: os.getenv("BETFAIR_APP_KEY"))
    betfair_session_token: str | None = field(default_factory=lambda: os.getenv("BETFAIR_SESSION_TOKEN"))
    betfair_username: str | None = field(default_factory=lambda: os.getenv("BETFAIR_USERNAME"))
    betfair_password: str | None = field(default_factory=lambda: os.getenv("BETFAIR_PASSWORD"))
    # com (UK and international), it, es, ro, se, com.au: picks the identity (login) host
    betfair_jurisdiction: str = field(default_factory=lambda: os.getenv("BETFAIR_JURISDICTION", "com"))
    # certificate login for accounts with two-factor authentication (Betfair "non-interactive" login)
    betfair_cert_file: str | None = field(default_factory=lambda: os.getenv("BETFAIR_CERT_FILE"))
    betfair_key_file: str | None = field(default_factory=lambda: os.getenv("BETFAIR_KEY_FILE"))
    cache_dir: Path = field(default_factory=lambda: Path(os.getenv("TRADESCOUT_CACHE", REPO_ROOT / ".cache")))
    # Model hyper-parameters
    time_decay_xi: float = field(default_factory=lambda: float(os.getenv("TRADESCOUT_XI", "0.003")))  # per day; tuned out of sample
    commission: float = field(default_factory=lambda: float(os.getenv("TRADESCOUT_COMMISSION", "0.05")))  # Betfair UK base rate on net market winnings
    min_edge: float = field(default_factory=lambda: float(os.getenv("TRADESCOUT_MIN_EDGE", "0.02")))  # conservative net EV per unit risked needed to call a trade
    max_spread: float = field(default_factory=lambda: float(os.getenv("TRADESCOUT_MAX_SPREAD", "0.04")))  # (lay - back) / back
    # multiplier on the model's weight against the market (1.0 = the audited default; higher trusts the model more; unproven)
    model_weight_scale: float = field(default_factory=lambda: float(os.getenv("TRADESCOUT_MODEL_WEIGHT_SCALE", "1.0")))
    history_days: int = field(default_factory=lambda: int(os.getenv("TRADESCOUT_HISTORY_DAYS", "900")))
    max_goals: int = 8
    bank: float = field(default_factory=lambda: float(os.getenv("TRADESCOUT_BANK", "1000")))
    kelly_fraction: float = field(default_factory=lambda: float(os.getenv("TRADESCOUT_KELLY", "0.25")))
    # off = review only, paper = record slips in the journal, live = send orders to Betfair after the user confirms on screen
    betting_mode: str = field(default_factory=lambda: os.getenv("TRADESCOUT_BETTING_MODE", "off"))
    daily_cap: float = field(default_factory=lambda: float(os.getenv("TRADESCOUT_DAILY_CAP", "50")))

    @property
    def has_live_fixtures(self) -> bool:
        return bool(self.football_data_org_key or self.api_football_key)

    @property
    def has_betfair(self) -> bool:
        return bool(self.betfair_app_key and (self.betfair_session_token or (self.betfair_username and self.betfair_password)))

    @property
    def betfair_can_login(self) -> bool:
        """Credentials that can open a fresh session (a pasted session token alone cannot)."""
        return bool(self.betfair_app_key and self.betfair_username and self.betfair_password)


settings = Settings()


def write_env(values: dict[str, str | None], path: Path = ENV_FILE) -> None:
    """Merge values into .env (None removes a key) and reload `settings` in place so every module
    that imported it sees the change."""
    current: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                current[k.strip()] = v.strip()
    for k, v in values.items():
        if v is None or v == "":
            current.pop(k, None)
            os.environ.pop(k, None)
        else:
            current[k] = str(v)
            os.environ[k] = str(v)
    lines = ["# TradeScout settings - keep this file private"] + [f"{k}={v}" for k, v in current.items()]
    path.write_text("\n".join(lines) + "\n")
    settings.__dict__.update(vars(Settings()))
