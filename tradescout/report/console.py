from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..backtest import BacktestReport
from ..config import LEAGUE_NAMES
from ..models import MatchForecast, TradeIdea
from ..scout import ScanResult

import os
import sys

console = Console(width=None if sys.stdout.isatty() else int(os.getenv("COLUMNS", "170")))


def _fmt_price(p):
    return "-" if p is None else f"{p:.2f}"


SORT_KEYS = {
    "score": lambda i: -i.score,
    "hit": lambda i: -i.calibrated_hit_prob,
    "roi": lambda i: -i.expected_roi,
    "edge": lambda i: -(i.edge if i.edge is not None else -9),
}


def print_scan(scan: ScanResult, top: int = 20, min_score: float = 0.0, per_match: bool = False, sort: str = "score") -> None:
    ideas = scan.best_per_match() if per_match else scan.ideas
    ideas = sorted((i for i in ideas if i.score >= min_score), key=SORT_KEYS[sort])[:top]
    title = f"TradeScout - {scan.date:%A %d %B %Y} - {len(scan.fixtures)} fixtures, {len(scan.ideas)} ideas, model fitted on {scan.model_matches} matches, prices: {scan.price_source}"
    t = Table(title=title, show_lines=False, expand=True)
    for col, just in [("#", "right"), ("Score", "right"), ("Match", "left"), ("Lg", "left"), ("KO", "left"), ("Strategy", "left"),
                      ("Hit%", "right"), ("Cal%", "right"), ("Price", "right"), ("Edge", "right"), ("ROI", "right"), ("Stake%", "right"), ("Conf", "right")]:
        t.add_column(col, justify=just)
    for n, i in enumerate(ideas, 1):
        colour = "green" if i.score >= 60 else "yellow" if i.score >= 50 else "white"
        t.add_row(str(n), f"[{colour}]{i.score:.0f}[/]", i.fixture.label, i.fixture.league,
                  i.fixture.kickoff.strftime("%H:%M") if i.fixture.kickoff else "-", i.strategy_label,
                  f"{i.hit_prob:.0%}", f"{i.calibrated_hit_prob:.0%}", _fmt_price(i.market_price or i.model_price),
                  "-" if i.edge is None else f"{i.edge:+.1%}", f"{i.expected_roi:+.1%}", f"{i.stake_pct:.1f}", f"{i.confidence:.2f}")
    console.print(t)
    if scan.skipped:
        console.print(f"[yellow]{len(scan.skipped)} price lookups failed[/]")
    if scan.price_source == "none":
        console.print("[dim]No exchange feed connected: prices shown are model-fair less a typical overround. "
                      "Connect Betfair (BETFAIR_APP_KEY) to rank on real edge.[/]")


def print_match(fc: MatchForecast, ideas: list[TradeIdea], actual=None) -> None:
    f = fc.fixture
    hdr = f"[bold]{f.home} v {f.away}[/]  {LEAGUE_NAMES.get(f.league, f.league)}  {f.date:%d %b %Y}"
    if actual:
        hdr += f"   [dim]actual {actual.home_goals}-{actual.away_goals}" + (f" (HT {actual.ht_home}-{actual.ht_away})" if actual.ht_home is not None else "") + "[/]"
    lines = [
        f"Expected goals  {fc.home_xg:.2f} - {fc.away_xg:.2f}   (total {fc.total_xg:.2f})",
        f"Match odds      H {fc.p_home:.1%} ({1/fc.p_home:.2f})   D {fc.p_draw:.1%} ({1/fc.p_draw:.2f})   A {fc.p_away:.1%} ({1/fc.p_away:.2f})",
        f"Goals           O1.5 {fc.p_over[1.5]:.0%}  O2.5 {fc.p_over[2.5]:.0%}  O3.5 {fc.p_over[3.5]:.0%}  BTTS {fc.p_btts:.0%}  0-0 {fc.p_cs['0-0']:.1%}  HT 0-0 {fc.p_ht_00:.0%}",
        f"Timing          goal before 15' {fc.p_goal_before[15]:.0%}  30' {fc.p_goal_before[30]:.0%}  45' {fc.p_goal_before[45]:.0%}  70' {fc.p_goal_before[70]:.0%}   favourite scores first {fc.p_fav_scores_first:.0%}",
        f"Ratings         {f.home}: att {fc.home_strength.attack:+.2f} def {fc.home_strength.defence:+.2f} ({fc.home_strength.matches_in_window} games)   "
        f"{f.away}: att {fc.away_strength.attack:+.2f} def {fc.away_strength.defence:+.2f} ({fc.away_strength.matches_in_window} games)   confidence {fc.confidence:.2f}",
    ]
    top_cs = sorted(fc.p_cs.items(), key=lambda kv: -kv[1])[:6]
    lines.append("Likely scores   " + "   ".join(f"{s} {p:.1%}" for s, p in top_cs))
    for n in fc.notes:
        lines.append(f"[yellow]! {n}[/]")
    console.print(Panel("\n".join(lines), title=hdr, expand=True))
    for i in ideas:
        colour = "green" if i.score >= 60 else "yellow" if i.score >= 50 else "white"
        body = [f"[bold]{i.strategy_label}[/]  score [{colour}]{i.score:.0f}[/]  hit {i.hit_prob:.0%} (calibrated {i.calibrated_hit_prob:.0%})  "
                f"expected ROI {i.expected_roi:+.1%} per unit risked  win {i.win_return:+.0%} / loss {i.loss_return:+.0%}  stake {i.stake_pct:.1f}% of bank"]
        if i.historical_strike_rate is not None:
            body.append(f"History: {i.historical_strike_rate:.0%} strike over {i.historical_sample} similar trades")
        body += [f"  {r}" for r in i.rationale]
        body.append("Plan:")
        body += [f"  {n}. {p}" for n, p in enumerate(i.plan, 1)]
        for w in i.warnings:
            body.append(f"  [yellow]! {w}[/]")
        console.print(Panel("\n".join(body), expand=True))


def print_backtest(rep: BacktestReport) -> None:
    console.print(f"[bold]Backtest[/]  {rep.days} match days, {len(rep.trades)} strategy-trades, prices: {rep.calibration.price_source}")
    t = Table(title="By strategy")
    for c in ("Strategy", "n", "Strike", "Predicted", "ROI/unit"):
        t.add_column(c, justify="right" if c != "Strategy" else "left")
    for r in rep.by_strategy():
        t.add_row(r["strategy"], str(r["n"]), f"{r['strike']:.1%}", f"{r['predicted']:.1%}", f"{r['roi']:+.1%}")
    console.print(t)
    t = Table(title="By league")
    for c in ("League", "n", "Strike", "ROI/unit"):
        t.add_column(c, justify="right" if c != "League" else "left")
    for r in rep.by_league():
        t.add_row(LEAGUE_NAMES.get(r["league"], r["league"]), str(r["n"]), f"{r['strike']:.1%}", f"{r['roi']:+.1%}")
    console.print(t)
    t = Table(title="By rank score band (does the score mean anything?)")
    for c in ("Score", "n", "Strike", "ROI/unit"):
        t.add_column(c, justify="right" if c != "Score" else "left")
    for r in rep.by_score_band():
        t.add_row(r["band"], str(r["n"]), f"{r['strike']:.1%}", f"{r['roi']:+.1%}")
    console.print(t)
    for n in (1, 3, 5):
        d = rep.top_n_daily(n)
        console.print(f"Top {n} ideas per day: n={d['n']}  strike {d['strike']:.1%}  ROI {d['roi']:+.1%}")
