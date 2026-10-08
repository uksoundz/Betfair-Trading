"""Picks journal: the user's paper-trading (or real) record, settled automatically against results.

Stored as JSON in data/journal.json. Each entry remembers enough to re-settle itself: the fixture,
the strategy and the stake. Settlement re-fits the model as of the fixture date (no look-ahead)
and uses the strategy's own settle() so the journal and the backtest agree.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .config import JOURNAL_PATH
from .models import Fixture


@dataclass
class JournalEntry:
    id: str
    created: str
    date: str
    league: str
    home: str
    away: str
    strategy: str
    strategy_label: str
    score: float
    hit_prob: float
    entry_price: float
    stake_money: float
    first_step: str
    status: str = "open"  # open | won | lost
    pnl_money: Optional[float] = None
    pnl_per_unit: Optional[float] = None
    result: Optional[str] = None
    note: str = ""
    slip: list = field(default_factory=list)  # the reviewed selections (paper mode): market, selection, side, price, size


@dataclass
class Journal:
    path: Path = JOURNAL_PATH
    entries: list[JournalEntry] = field(default_factory=list)

    def __post_init__(self):
        self.path = Path(self.path)
        if self.path.exists():
            try:
                self.entries = [JournalEntry(**e) for e in json.loads(self.path.read_text())]
            except Exception:
                self.entries = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(e) for e in self.entries], indent=1))

    def add(self, idea, stake_money: float, note: str = "") -> JournalEntry:
        fx = idea.fixture
        for e in self.entries:
            if e.date == fx.date.isoformat() and e.home == fx.home and e.away == fx.away and e.strategy == idea.strategy:
                return e
        e = JournalEntry(uuid.uuid4().hex[:10], datetime.now().isoformat(timespec="minutes"), fx.date.isoformat(), fx.league, fx.home, fx.away,
                         idea.strategy, idea.strategy_label, round(idea.score, 1), round(idea.calibrated_hit_prob, 3),
                         round(idea.market_price or idea.model_price, 2), round(stake_money, 2), idea.plan[0].text if idea.plan else "", note=note)
        self.entries.insert(0, e)
        self.save()
        return e

    def attach_slip(self, entry_id: str, legs: list) -> None:
        for e in self.entries:
            if e.id == entry_id:
                e.slip = list(legs)
                break
        self.save()

    def remove(self, entry_id: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.id != entry_id]
        if len(self.entries) != before:
            self.save()
            return True
        return False

    def settle(self, scout, result_lookup) -> int:
        """Settle open entries whose match has a result. Returns how many were settled."""
        from .strategies import get_strategy
        settled = 0
        forecasters: dict[str, object] = {}
        for e in self.entries:
            if e.status != "open" or date.fromisoformat(e.date) > date.today():
                continue
            fx = Fixture(date.fromisoformat(e.date), e.league, e.home, e.away)
            result = result_lookup(fx)
            if result is None:
                continue
            fc_aster = forecasters.get(e.date)
            if fc_aster is None:
                try:
                    fc_aster = scout.forecaster(fx.date)
                except Exception:
                    continue
                forecasters[e.date] = fc_aster
            fc = fc_aster.forecast(fx)
            hit, pnl = get_strategy(e.strategy).settle(fc, result)
            e.pnl_per_unit = round(pnl, 4)
            e.pnl_money = round(pnl * e.stake_money, 2)
            e.status = "won" if hit >= 0.5 else "lost"
            e.result = f"{result.home_goals}-{result.away_goals}"
            settled += 1
        if settled:
            self.save()
        return settled

    def summary(self) -> dict:
        done = [e for e in self.entries if e.status in ("won", "lost")]
        staked = sum(e.stake_money for e in done)
        pnl = sum(e.pnl_money or 0 for e in done)
        won = sum(1 for e in done if e.status == "won")
        return {"picks": len(self.entries), "open": sum(1 for e in self.entries if e.status == "open"), "settled": len(done),
                "won": won, "strike": (won / len(done)) if done else None, "staked": round(staked, 2), "pnl": round(pnl, 2),
                "roi": (pnl / staked) if staked else None}
