"""Historical strike rates per (strategy, league) produced by the backtester. The scorer shrinks
the model's hit probability towards these so a strategy that has under-delivered in a league is
ranked down even when today's numbers look pretty."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import REPO_ROOT

DEFAULT_PATH = REPO_ROOT / "data" / "calibration.json"


@dataclass
class CalibrationEntry:
    n: int
    hits: float
    pnl: float
    predicted_hit_sum: float  # sum of model hit_prob -> lets us compute calibration ratio

    @property
    def strike_rate(self) -> float:
        return self.hits / self.n if self.n else 0.0

    @property
    def roi(self) -> float:
        return self.pnl / self.n if self.n else 0.0

    @property
    def predicted_rate(self) -> float:
        return self.predicted_hit_sum / self.n if self.n else 0.0


@dataclass
class Calibration:
    entries: dict[str, CalibrationEntry] = field(default_factory=dict)
    generated: str = ""
    price_source: str = "model"

    @staticmethod
    def key(strategy: str, league: str) -> str:
        return f"{strategy}|{league}"

    def get(self, strategy: str, league: str) -> CalibrationEntry | None:
        return self.entries.get(self.key(strategy, league)) or self.entries.get(self.key(strategy, "*"))

    def add(self, strategy: str, league: str, hit: float, pnl: float, predicted: float) -> None:
        for lg in (league, "*"):
            e = self.entries.setdefault(self.key(strategy, lg), CalibrationEntry(0, 0, 0.0, 0.0))
            e.n += 1
            e.hits += float(hit)
            e.pnl += pnl
            e.predicted_hit_sum += predicted

    def save(self, path: Path | str = DEFAULT_PATH) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        payload = {"generated": self.generated, "price_source": self.price_source,
                   "entries": {k: vars(v) for k, v in self.entries.items()}}
        Path(path).write_text(json.dumps(payload, indent=1, sort_keys=True))

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> "Calibration":
        p = Path(path)
        if not p.exists():
            return cls()
        payload = json.loads(p.read_text())
        return cls({k: CalibrationEntry(**v) for k, v in payload.get("entries", {}).items()},
                   payload.get("generated", ""), payload.get("price_source", "model"))
