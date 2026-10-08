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
    slip: list = field(default_factory=list)  # the reviewed selections: market, selection, side, price, size
    placed: str = ""  # "" | paper | live
    bet_refs: list = field(default_factory=list)  # exchange bet ids when placed live
    placed_total: float = 0.0  # money committed on the exchange (back stakes + lay liabilities)
    sport: str = "football"
    decision: str = ""  # TRADE | NO TRADE | RESEARCH at the time it was tracked
    ev_conservative: Optional[float] = None


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
                         round(idea.market_price or idea.model_price, 2), round(stake_money, 2), idea.plan[0].text if idea.plan else "", note=note,
                         sport=getattr(idea, "sport", "football"), decision=getattr(idea, "decision", ""),
                         ev_conservative=getattr(idea, "ev_conservative", None))
        self.entries.insert(0, e)
        self.save()
        return e

    def attach_slip(self, entry_id: str, legs: list, placed: str = "paper", refs: list | None = None, total: float = 0.0) -> None:
        for e in self.entries:
            if e.id == entry_id:
                e.slip = list(legs)
                e.placed = placed
                e.bet_refs = list(refs or [])
                e.placed_total = round(total, 2)
                break
        self.save()

    def committed_today(self, mode: str = "live") -> float:
        """Money sent to the exchange today in the given mode; used for the daily cap."""
        today = date.today().isoformat()
        return round(sum(e.placed_total for e in self.entries if e.placed == mode and e.created[:10] == today), 2)

    def remove(self, entry_id: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.id != entry_id]
        if len(self.entries) != before:
            self.save()
            return True
        return False

    def settle(self, scout, result_lookup) -> int:
        """Settle open football entries (kept for compatibility); see settle_multi."""
        return self.settle_multi({"football": scout}, lambda sport, fx: result_lookup(fx))

    def settle_multi(self, scouts: dict, result_lookup) -> int:
        """Settle open entries of every sport whose match has a result. result_lookup(sport, fixture).
        Returns how many were settled. P/L uses the strategy's own settle(): exact for plans settled
        at the result, modelled exits for in-play plans (flagged in the note)."""
        from .strategies import get_strategy
        settled = 0
        forecasters: dict[tuple, object] = {}
        for e in self.entries:
            if e.status != "open" or date.fromisoformat(e.date) > date.today():
                continue
            sport = e.sport or ("tennis" if e.strategy.startswith("tn_") else "football")
            scout = scouts.get(sport)
            if scout is None:
                continue
            meta = {"sport": sport}
            if sport == "tennis" and e.slip:
                pass
            fx = Fixture(date.fromisoformat(e.date), e.league, e.home, e.away, None, None, meta)
            result = result_lookup(sport, fx)
            if result is None:
                continue
            if sport == "tennis":
                fx = Fixture(fx.date, fx.league, fx.home, fx.away, None, None,
                             {"sport": "tennis", "surface": getattr(result, "surface", "Hard"), "best_of": getattr(result, "best_of", 3)})
            fc_aster = forecasters.get((sport, e.date))
            if fc_aster is None:
                try:
                    fc_aster = scout.forecaster(fx.date)
                except Exception:
                    continue
                forecasters[(sport, e.date)] = fc_aster
            fc = fc_aster.forecast(fx)
            strat = get_strategy(e.strategy)
            hit, pnl = strat.settle(fc, result)
            e.pnl_per_unit = round(pnl, 4)
            e.pnl_money = round(pnl * e.stake_money, 2)
            e.status = "won" if hit >= 0.5 else "lost"
            e.result = getattr(result, "score", None) or f"{result.home_goals}-{result.away_goals}"
            if getattr(strat, "inplay", True) and "modelled exits" not in e.note:
                e.note = (e.note + "; " if e.note else "") + "P/L uses modelled in-play exits"
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
