"""The auto-trader: follows the in-play rules of plans the user has placed and explicitly armed.

Nothing here opens a position on its own. A job exists only after the user places a plan and presses
Auto-trade on it. From then on, once the match is in play, the engine checks every few seconds:

  1. the exchange: market status, best prices, and the user's matched bets on the plan's selections
     (only the bets of that plan: the journal's bet ids plus the engine's own);
  2. the match state: minute and score (football) or sets and games (tennis);
  3. the plan's rules, in order; the first whose condition holds is carried out.

Safety, in this order of importance:
  * a hedge (green up, free bet) can only raise the worst case, never lower it; the engine checks that
    before sending;
  * a scale-in is the only action that adds risk, is bounded by the plan's own stake, counts against the
    daily cap, and never fires on an unknown score;
  * nothing is sent while a market is suspended; a wide spread is waited out for up to a minute;
  * unmatched engine orders are cancelled after 15 s and the hedge is recomputed from the position;
  * simulate mode runs the same logic on the real prices and records what it would have done;
  * Disarm and Stop all stop the engine for a job or for everything at once.

The engine runs inside the TradeScout app: if the app is closed nothing happens (pre-match orders still
lapse at the start if unmatched, so an unwatched plan simply runs to the result).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..betting import min_bet_ok
from . import position as P
from . import rules as R
from .scores import ScoreFeed, market_clock

TICK_LIVE = 4.0          # seconds between checks while any armed match is in play
TICK_IDLE = 15.0         # otherwise (how quickly kick-off is noticed)
STALE_ORDER_SECONDS = 15.0
SPREAD_WAIT_LIMIT = 0.15  # (lay - back) / back above this is waited out ...
SPREAD_WAIT_SECONDS = 60.0  # ... for at most this long, then the action goes ahead at the best price
MAX_ORDERS_PER_JOB = 12
ACTIVE = ("armed", "live")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    entry_id: str
    sport: str
    date: str
    home: str
    away: str
    fav: str                      # home | away
    strategy: str
    strategy_label: str
    event_id: Optional[str]
    legs: list                    # [{market, market_label, market_id, selection, selection_id, handicap, side, entry_price, size, runner_name}]
    rules: list
    unit: float                   # the plan stake (unit risked)
    max_liability: float          # the most this job may ever have at risk
    simulate: bool = False
    market_start: Optional[str] = None
    bet_ids: list = field(default_factory=list)
    fired: list = field(default_factory=list)
    state: str = "armed"          # armed | live | done | stopped | expired | error
    status: str = "Waiting for the start."
    log: list = field(default_factory=list)
    score: dict = field(default_factory=dict)
    exposure: dict = field(default_factory=dict)  # leg index -> exposure dict
    engine_orders: list = field(default_factory=list)  # [{bet_id, market_id, placed_at, leg}]
    sim_bets: list = field(default_factory=list)       # [{leg, side, price, size}] in simulate mode
    waits: dict = field(default_factory=dict)
    orders_sent: int = 0
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)

    def note(self, text: str) -> None:
        self.log.append({"ts": _now(), "text": text})
        self.log = self.log[-200:]
        self.updated = _now()


class AutoTrader:
    def __init__(self, client: Callable[[], object], path: Path, jurisdiction: Callable[[], str] = lambda: "com",
                 can_commit: Callable[[float], Optional[str]] = lambda amount: None, on_done: Callable[[Job], None] = lambda job: None,
                 scores: Optional[ScoreFeed] = None):
        self.client = client
        self.path = Path(path)
        self.jurisdiction = jurisdiction
        self.can_commit = can_commit
        self.on_done = on_done
        self.scores = scores or ScoreFeed()
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.auto_start = True
        self.enabled = lambda: True
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.last_tick: Optional[str] = None
        self.last_error: Optional[str] = None
        self.load()

    # ------------------------------------------------------------------ persistence
    def load(self) -> None:
        if self.path.exists():
            try:
                self.jobs = {d["id"]: Job(**d) for d in json.loads(self.path.read_text())}
            except Exception:
                self.jobs = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(j) for j in self.jobs.values()], indent=1))
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ control
    def active(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.state in ACTIVE]

    def arm(self, job: Job) -> Job:
        with self.lock:
            for j in self.jobs.values():
                if j.entry_id == job.entry_id and j.state in ACTIVE:
                    return j
            if job.simulate and not job.sim_bets:
                job.sim_bets = [{"leg": i, "side": l["side"], "price": l["entry_price"], "size": l["size"]} for i, l in enumerate(job.legs)]
            job.note(("Armed in SIMULATE mode: rules run on live prices, nothing is sent." if job.simulate else "Armed: TradeScout will follow the plan's in-play rules on your Betfair account.")
                     + " Rules: " + " | ".join(r.get("text", "") for r in job.rules))
            self.jobs[job.id] = job
            self.save()
        if self.auto_start:
            self.start()
        return job

    def disarm(self, job_id: str, reason: str = "Disarmed by you. Your position stays as it is on Betfair.") -> Optional[Job]:
        with self.lock:
            j = self.jobs.get(job_id)
            if j and j.state in ACTIVE:
                self._cancel_engine_unmatched(j, older_than=0)
                j.state, j.status = "stopped", reason
                j.note(reason)
                self.save()
            return j

    def stop_all(self) -> int:
        with self.lock:
            n = 0
            for j in self.active():
                self.disarm(j.id, "Stopped by Stop all auto-trading. Your positions stay as they are on Betfair.")
                n += 1
            return n

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="autotrader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            live = False
            try:
                live = self.tick()
            except Exception as exc:  # pragma: no cover - never let the loop die
                self.last_error = str(exc)
            self._stop.wait(TICK_LIVE if live else TICK_IDLE)

    # ------------------------------------------------------------------ the tick
    def tick(self) -> bool:
        """One pass over every active job. Returns True when any match is in play (tick faster)."""
        self.last_tick = _now()
        bf = self.client()
        with self.lock:
            jobs = self.active()
        if not jobs or bf is None or not self.enabled():
            return False
        any_live = False
        events = {(j.event_id, j.sport) for j in jobs if j.event_id}
        try:
            states = self.scores.states(events) if events else {}
        except Exception:
            states = {}
        for j in jobs:
            with self.lock:
                if j.state not in ACTIVE:
                    continue
                try:
                    any_live |= self._tick_job(bf, j, states.get(str(j.event_id)) if j.event_id else None)
                except Exception as exc:
                    j.status = f"Error, will retry: {exc}"
                    j.note(j.status)
                    self.last_error = str(exc)
                j.updated = _now()
        with self.lock:
            self.save()
        return any_live

    def _tick_job(self, bf, j: Job, score: Optional[dict]) -> bool:
        mids = sorted({l["market_id"] for l in j.legs if l.get("market_id")})
        books = bf.books(mids)
        if not books:
            j.status = "No price book from the exchange yet."
            return False
        statuses = {b.get("status") for b in books.values()}
        inplay = any(b.get("inplay") for b in books.values())
        if statuses <= {"CLOSED"}:
            self._finish(j, "done", "The market has closed (match over); nothing more to do.")
            return False
        orders = [] if j.simulate else [o for o in bf.current_orders(mids) if str(o.get("betId")) in set(map(str, j.bet_ids))]
        exps = [self._exposure(j, i, orders) for i in range(len(j.legs))]
        j.exposure = {str(i): e.to_dict() for i, e in enumerate(exps)}
        if not inplay:
            if j.state != "armed":
                j.state = "armed"
            j.status = "Waiting for the start. Matched so far: " + ", ".join(f"£{e.backed + e.laid:.2f}" for e in exps) + "."
            return False
        if j.state == "armed":
            j.state = "live"
            j.note("Match in play: following the plan.")
        if not j.simulate and all(e.backed + e.laid < 0.01 and e.unmatched < 0.01 for e in exps):
            self._finish(j, "expired", "The entry was never matched before the start (unmatched orders lapse at kick-off). Nothing to trade.")
            return True
        state = self._match_state(j, score, books)
        j.score = {k: state.get(k) for k in ("minute", "home", "away", "first_goal", "sets", "set_winners", "source")}
        self._cancel_engine_unmatched(j)
        sent_before = (j.orders_sent, len(j.sim_bets))
        try:
            return self._run_rules(bf, j, state, books, exps)
        finally:
            if (j.orders_sent, len(j.sim_bets)) != sent_before:  # show the position after what was just done
                after = [] if j.simulate else [o for o in bf.current_orders(mids) if str(o.get("betId")) in set(map(str, j.bet_ids))]
                j.exposure = {str(i): self._exposure(j, i, after).to_dict() for i in range(len(j.legs))}

    def _run_rules(self, bf, j: Job, state: dict, books: dict, exps: list) -> bool:
        for r in j.rules:
            if r["id"] in j.fired:
                continue
            v = R.evaluate(r["when"], state)
            if v is None and r.get("fire_if_score_unknown") and state.get("home") is None and state.get("minute") is not None:
                v = R.evaluate(r["when"], state, assume_score=True)
                if v:
                    j.note(f"Score unavailable; protective rule '{r['text']}' fired on the clock alone.")
            if not v:
                continue
            outcome, msg = self._act(bf, j, r["do"], books, exps, r["id"])
            if outcome == "done":
                j.fired.append(r["id"])
                j.note(f"{r['text']} {msg}".strip())
                if r.get("final"):
                    self._finish(j, "done", f"Plan completed: {r['text']}")
                    return True
                continue
            j.status = msg
            if r["do"].get("a") in ("green", "free_bet"):
                break  # an exit in progress takes priority over anything later in the plan
        else:
            if j.state == "live":
                j.status = self._describe_state(j)
        return True

    # ------------------------------------------------------------------ helpers
    def _exposure(self, j: Job, i: int, orders: list[dict]) -> P.Exposure:
        leg = j.legs[i]
        if j.simulate:
            e = P.Exposure()
            for b in j.sim_bets:
                if b["leg"] == i:
                    e = P.add_bet(e, b["side"], b["price"], b["size"])
            return e
        return P.exposure([o for o in orders if o.get("marketId") == leg["market_id"]], leg["selection_id"], leg.get("handicap", 0.0))

    def _match_state(self, j: Job, score: Optional[dict], books: dict) -> dict:
        s = dict(score or {})
        if s.get("minute") is None and j.sport == "football":
            mo = next((b for b in books.values() if b.get("inplay")), None)
            s["minute"] = market_clock(j.market_start, bool(mo))
            s["source"] = (s.get("source") or "no score feed") + ", clock from the exchange"
        s["fav"] = j.fav
        prices = {}
        for i, leg in enumerate(j.legs):
            rb = self._runner_book(books.get(leg["market_id"]), leg)
            prices[i] = {"back": rb[0], "lay": rb[1], "entry": leg["entry_price"], "side": leg["side"]}
        s["prices"] = prices
        return s

    @staticmethod
    def _runner_book(book: Optional[dict], leg: dict) -> tuple[Optional[float], Optional[float]]:
        if not book:
            return None, None
        for r in book.get("runners", []):
            if r.get("selectionId") == leg["selection_id"] and abs(float(r.get("handicap") or 0.0) - float(leg.get("handicap") or 0.0)) < 1e-9:
                ex = r.get("ex") or {}
                b = (ex.get("availableToBack") or [{}])[0].get("price")
                l = (ex.get("availableToLay") or [{}])[0].get("price")
                return b, l
        return None, None

    def _describe_state(self, j: Job) -> str:
        sc = j.score or {}
        if j.sport == "football":
            where = f"{sc.get('minute'):.0f}'" if sc.get("minute") is not None else "minute unknown"
            score = f"{sc.get('home')}-{sc.get('away')}" if sc.get("home") is not None else "score unknown"
        else:
            where = "sets " + (f"{sc.get('home')}-{sc.get('away')}" if sc.get("home") is not None else "unknown")
            score = " ".join(f"{a}-{b}" for a, b in (sc.get("sets") or []))
        return f"In play, {where}, {score}. Watching for the next rule."

    def _cancel_engine_unmatched(self, j: Job, older_than: float = STALE_ORDER_SECONDS) -> None:
        if j.simulate or not j.engine_orders:
            return
        bf = self.client()
        keep = []
        for o in j.engine_orders:
            if time.time() - o["placed_at"] >= older_than:
                try:
                    bf.cancel_orders(o["market_id"], [o["bet_id"]])
                except Exception:
                    keep.append(o)
                    continue
            else:
                keep.append(o)
        j.engine_orders = keep

    def _finish(self, j: Job, state: str, text: str) -> None:
        self._cancel_engine_unmatched(j, older_than=0)
        j.state, j.status = state, text
        j.note(text)
        try:
            self.on_done(j)
        except Exception:
            pass

    def _place(self, bf, j: Job, leg_i: int, side: str, price: float, size: float, tag: str) -> tuple[str, str]:
        leg = j.legs[leg_i]
        if j.simulate:
            j.sim_bets.append({"leg": leg_i, "side": side, "price": price, "size": size})
            return "done", f"SIMULATED: {side} {leg.get('runner_name')} £{size:.2f} at {price:.2f}."
        if j.orders_sent >= MAX_ORDERS_PER_JOB:
            return "wait", "Order limit for this plan reached; auto-trading paused. Manage it on Betfair."
        ref = f"at-{j.id[:8]}-{tag}-{j.orders_sent}"[:32]
        rep = bf.place_orders(leg["market_id"], [{"selectionId": leg["selection_id"], "handicap": leg.get("handicap", 0.0), "side": side,
                                                  "price": price, "size": size, "customerOrderRef": ref}], ref)
        j.orders_sent += 1
        r = (rep.get("instructionReports") or [{}])[0]
        if r.get("status") != "SUCCESS":
            return "wait", f"Exchange refused the order ({r.get('errorCode') or rep.get('errorCode') or 'unknown'}); will retry."
        bet = str(r.get("betId"))
        j.bet_ids.append(bet)
        matched = float(r.get("sizeMatched") or 0.0)
        if matched + 0.01 < size:
            j.engine_orders.append({"bet_id": bet, "market_id": leg["market_id"], "placed_at": time.time(), "leg": leg_i})
            return "partial", f"{side} {leg.get('runner_name')} £{size:.2f} at {price:.2f}: £{matched:.2f} matched so far."
        return "done", f"{side} {leg.get('runner_name')} £{size:.2f} at {price:.2f}, matched."

    def _act(self, bf, j: Job, action: dict, books: dict, exps: list, tag: str) -> tuple[str, str]:
        kind = action.get("a")
        if kind == "hold":
            return "done", ""
        i = action.get("leg", 0)
        leg = j.legs[i]
        book = books.get(leg["market_id"]) or {}
        if book.get("status") != "OPEN":
            return "wait", f"{leg.get('market_label')} is {str(book.get('status', 'unavailable')).lower()}: waiting to act."
        back, lay = self._runner_book(book, leg)
        e = exps[i]
        if not j.simulate and e.unmatched > 0.01 and kind in ("green", "free_bet"):
            # unmatched money on the selection would change the position if it matched: cancel first
            open_ids = [o["bet_id"] for o in j.engine_orders if o["leg"] == i]
            try:
                bf.cancel_orders(leg["market_id"], open_ids or None) if open_ids else None
            except Exception:
                pass
            if not open_ids:
                return "wait", "Unmatched entry money is still waiting on this selection; it lapses or matches first."
            j.engine_orders = [o for o in j.engine_orders if o["leg"] != i]
            return "wait", "Cancelled unmatched orders before hedging."
        if kind in ("green", "free_bet"):
            h = P.green_up(e, back, lay) if kind == "green" else P.free_bet(e, lay)
            if h is None:
                if (kind == "green" and abs(e.win - e.lose) < 0.01) or (kind == "free_bet" and e.backed - e.laid < 0.01):
                    return "done", "Position already level."
                return "wait", "No price on offer to hedge against yet."
            if back and lay and (lay - back) / back > SPREAD_WAIT_LIMIT:
                first = j.waits.setdefault(tag, time.time())
                if time.time() - first < SPREAD_WAIT_SECONDS:
                    return "wait", f"Spread {back:.2f}/{lay:.2f} is wide; waiting up to a minute for it to settle."
            if h.after.worst + 0.01 < e.worst:
                return "wait", "Refused: that hedge would make the worst case worse."
            if not min_bet_ok(h.size, h.price, jurisdiction=self.jurisdiction()):
                return "done", f"Remaining hedge £{h.size:.2f} is below the exchange minimum; position left as it is (worst case £{e.worst:.2f})."
            out, msg = self._place(bf, j, i, h.side, h.price, h.size, tag)
            if out == "done":
                msg += f" Now: £{h.after.win:+.2f} if it wins, £{h.after.lose:+.2f} if it loses."
            return ("done" if out == "done" else "wait"), msg
        if kind == "scale_in":
            extra = round(float(action.get("fraction", 0)) * j.unit, 2)
            if leg["side"] == "lay":
                if not lay or lay <= 1.0:
                    return "wait", "No lay price on offer to scale in."
                price, size = lay, round(extra / (lay - 1), 2)
            else:
                if not back:
                    return "wait", "No back price on offer to scale in."
                price, size = back, extra
            if -e.worst + extra > j.max_liability + 0.01:
                return "done", f"Scale-in skipped: it would take the risk above the plan's £{j.max_liability:.2f}."
            if not j.simulate:
                blocked = self.can_commit(extra)
                if blocked:
                    return "done", f"Scale-in skipped: {blocked}"
            if not min_bet_ok(size, price, jurisdiction=self.jurisdiction()):
                return "done", f"Scale-in of £{size:.2f} is below the exchange minimum; skipped."
            out, msg = self._place(bf, j, i, leg["side"], price, size, tag)
            return ("done" if out in ("done", "partial") else "wait"), msg
        return "done", f"Unknown action {kind}; ignored."

    # ------------------------------------------------------------------ manual
    def green_now(self, job_id: str, leg: Optional[int] = None) -> Optional[Job]:
        """Green up every leg (or one) right now, outside the rules. Same safety checks."""
        bf = self.client()
        with self.lock:
            j = self.jobs.get(job_id)
            if j is None or bf is None:
                return j
            mids = sorted({l["market_id"] for l in j.legs})
            books = bf.books(mids)
            orders = [] if j.simulate else [o for o in bf.current_orders(mids) if str(o.get("betId")) in set(map(str, j.bet_ids))]
            exps = [self._exposure(j, i, orders) for i in range(len(j.legs))]
            for i in ([leg] if leg is not None else range(len(j.legs))):
                outcome, msg = self._act(bf, j, {"a": "green", "leg": i}, books, exps, f"manual{i}")
                j.note(f"Green up now (you): {msg}")
                j.status = msg
            self.save()
            return j


def new_job(**kw) -> Job:
    return Job(id=uuid.uuid4().hex[:12], **kw)
