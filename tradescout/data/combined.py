"""Results from the bundled/openfootball files plus the live feed's finished matches.

The live feed (football-data.org) covers leagues the bundled files do not (Eredivisie, Primeira
Liga, Champions League) and is fresher than any download. Its results are fetched in the
background, one competition every 7 seconds to stay inside the free plan's 10 requests a minute,
cached on disk for a day, and merged with the bundled results (deduplicated on date + teams).
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from ..models import MatchResult
from .football_data_org import COMPETITIONS, FootballDataOrgProvider, RateLimited
from .openfootball import OpenFootballProvider


class CombinedResults:
    def __init__(self, sample: OpenFootballProvider, live: FootballDataOrgProvider | None, cache_dir: Path, max_age_hours: int = 24):
        self.sample = sample
        self.live = live
        self.cache_path = Path(cache_dir) / "live_results.json"
        self.max_age = max_age_hours * 3600
        self._live_results: list[MatchResult] = []
        self.status: dict = {"state": "disabled" if live is None else "idle", "leagues": {}, "updated": None}
        self._lock = threading.Lock()
        self._load_cache()

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            payload = json.loads(self.cache_path.read_text())
            self._live_results = [MatchResult(date.fromisoformat(r.pop("date")), **r) for r in payload["results"]]
            self.status.update({"state": "cached", "updated": payload.get("updated"), "leagues": payload.get("leagues", {})})
        except Exception:
            self._live_results = []

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for r in self._live_results:
            d = asdict(r)
            d["date"] = r.date.isoformat()
            rows.append(d)
        self.cache_path.write_text(json.dumps({"updated": self.status["updated"], "leagues": self.status["leagues"], "results": rows}))

    def cache_is_fresh(self) -> bool:
        return self.cache_path.exists() and (time.time() - self.cache_path.stat().st_mtime) < self.max_age

    def refresh_in_background(self, spacing_seconds: float = 7.0) -> None:
        if self.live is None or self.cache_is_fresh() or self.status["state"] == "running":
            return
        threading.Thread(target=self._refresh, args=(spacing_seconds,), daemon=True).start()

    def _refresh(self, spacing: float) -> None:
        assert self.live is not None
        self.status["state"] = "running"
        fresh: dict[str, list[MatchResult]] = {}
        for league in COMPETITIONS:
            for _attempt in range(3):
                try:
                    fresh[league] = self.live.finished_matches(league)
                    self.status["leagues"][league] = len(fresh[league])
                    break
                except RateLimited:
                    time.sleep(61)
                except Exception as exc:
                    self.status["leagues"][league] = f"error: {exc}"
                    break
            time.sleep(spacing)
        with self._lock:
            merged = [r for r in self._live_results if r.league not in fresh]
            for rows in fresh.values():
                merged.extend(rows)
            self._live_results = merged
            self.status.update({"state": "done", "updated": datetime.now().isoformat(timespec="minutes")})
            self._save_cache()

    def results(self, leagues: Iterable[str] | None = None, before: date | None = None) -> list[MatchResult]:
        base = self.sample.results(leagues, before)
        seen = {(r.date, r.home, r.away) for r in base}
        wanted = set(leagues) if leagues else None
        with self._lock:
            extra = [r for r in self._live_results
                     if (wanted is None or r.league in wanted) and (before is None or r.date < before) and (r.date, r.home, r.away) not in seen]
        out = base + extra
        out.sort(key=lambda r: r.date)
        return out

    def result_for(self, fixture) -> MatchResult | None:
        r = self.sample.result_for(fixture)
        if r is not None:
            return r
        with self._lock:
            for x in self._live_results:
                if x.date == fixture.date and x.home == fixture.home and x.away == fixture.away:
                    return x
        return None
