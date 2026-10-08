"""Command line entry point.

  tradescout scan                       rank every fixture today (live feeds if keys set, else sample data)
  tradescout scan --date 2025-11-08     replay a past day from the bundled data and show what happened
  tradescout match "Arsenal FC" "Chelsea FC" --date 2025-11-30
  tradescout backtest --from 2024-08-01 --to 2025-05-31 [--write-calibration]
  tradescout ratings --date 2025-11-08
  tradescout refresh-data --seasons 2025-26 2026-27
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from .config import SAMPLE_DATA_DIR, settings
from .data.base import NoPrices
from .data.openfootball import OpenFootballProvider
from .report import print_backtest, print_match, print_scan, write_html
from .scout import Scout


def _date(s: str | None) -> date:
    return date.today() if not s else datetime.strptime(s, "%Y-%m-%d").date()


def build_scout(args) -> tuple[Scout, OpenFootballProvider]:
    sample = OpenFootballProvider(args.data_dir)
    fixtures = sample
    results = sample
    prices = NoPrices()
    if not args.offline and settings.football_data_org_key:
        from .data.football_data_org import FootballDataOrgProvider
        fixtures = FootballDataOrgProvider(settings.football_data_org_key)
    if not args.offline and settings.has_betfair:
        from .data.betfair import BetfairPrices
        prices = BetfairPrices(settings.betfair_app_key, settings.betfair_session_token, settings.betfair_username, settings.betfair_password)
    return Scout(results, fixtures, prices, xi=settings.time_decay_xi, history_days=settings.history_days), sample


def cmd_scan(args) -> int:
    scout, sample = build_scout(args)
    on = _date(args.date)
    scan = scout.scan(on, args.leagues)
    if not scan.fixtures:
        print(f"No fixtures found for {on}. Known match days near this date: "
              + ", ".join(d.isoformat() for d in sample.match_days(on.replace(day=1), on.replace(day=28))[:12]))
        return 1
    print_scan(scan, top=args.top, min_score=args.min_score, per_match=args.per_match, sort=args.sort)
    if args.html:
        path = write_html(scan, args.html, top=args.top, min_score=args.min_score)
        print(f"HTML report written to {path}")
    if args.show_results:
        hits = 0
        n = 0
        shown = scan.best_per_match() if args.per_match else scan.ideas
        shown = [i for i in shown if i.score >= args.min_score][:args.top]
        for idea in shown:
            res = sample.result_for(idea.fixture)
            if res is None:
                continue
            from .strategies import get_strategy
            hit, pnl = get_strategy(idea.strategy).settle(scan.forecasts[idea.fixture.label], res)
            hits += hit
            n += 1
            tag = "HIT " if hit >= 0.5 else "miss"
            print(f"  {idea.score:5.1f}  {idea.fixture.label:45s} {idea.strategy_label:34s} -> {res.home_goals}-{res.away_goals}  {tag} {pnl:+.2f}")
        if n:
            print(f"  {hits:.1f}/{n} of the displayed ideas paid off (model expected {sum(i.calibrated_hit_prob for i in shown):.1f})")
    return 0


def cmd_match(args) -> int:
    scout, sample = build_scout(args)
    on = _date(args.date)
    from .data.names import canonical
    from .models import Fixture
    home, away = canonical(args.home), canonical(args.away)
    fixtures = [f for f in sample.fixtures(on, args.leagues) if f.home == home and f.away == away]
    fixture = fixtures[0] if fixtures else Fixture(on, args.league or "?", home, away)
    fcaster = scout.forecaster(on)
    fc = fcaster.forecast(fixture)
    prices = scout.prices.prices(fixture)
    ideas = []
    for strat in scout.strategies:
        r = strat.evaluate(fc, prices)
        if r is not None:
            ideas.append(scout.scorer.score(fixture, fc, strat, r))
    ideas.sort(key=lambda i: -i.score)
    print_match(fc, ideas, sample.result_for(fixture) if fixtures else None)
    return 0


def cmd_backtest(args) -> int:
    from .backtest import Backtester
    sample = OpenFootballProvider(args.data_dir)
    bt = Backtester(sample, refit_every_days=args.refit_days, xi=settings.time_decay_xi, history_days=settings.history_days)
    start, end = _date(args.start), _date(args.end)

    def progress(day, n):
        if args.verbose:
            print(f"  {day} -> {n} trades", file=sys.stderr)

    rep = bt.run(start, end, args.leagues, progress=progress)
    print_backtest(rep)
    if args.write_calibration:
        from .ranking.calibration import DEFAULT_PATH
        rep.calibration.save(args.write_calibration if isinstance(args.write_calibration, str) else DEFAULT_PATH)
        print(f"Calibration written ({len(rep.calibration.entries)} entries)")
    return 0


def cmd_ratings(args) -> int:
    scout, _ = build_scout(args)
    fcaster = scout.forecaster(_date(args.date), args.leagues)
    print(f"{'Team':40s} {'Att':>6s} {'Def':>6s} {'Games':>5s}")
    for team, att, dfn, n in fcaster.model.ratings_table()[: args.top]:
        print(f"{team:40s} {att:+6.2f} {dfn:+6.2f} {n:5d}")
    print(f"home advantage {fcaster.model.home_adv:+.3f}, rho {fcaster.model.rho:+.3f}, fitted on {fcaster.model.n_matches} matches")
    return 0


def cmd_refresh(args) -> int:
    sample = OpenFootballProvider(args.data_dir)
    written = sample.refresh(args.seasons, args.leagues)
    print(f"Downloaded {len(written)} files into {sample.data_dir}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tradescout", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", default=str(SAMPLE_DATA_DIR), help="openfootball JSON directory")
    p.add_argument("--leagues", nargs="*", default=None, help="restrict to league codes e.g. en.1 de.1")
    p.add_argument("--offline", action="store_true", help="ignore API keys and use bundled data only")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="rank today's fixtures")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")
    s.add_argument("--top", type=int, default=25)
    s.add_argument("--min-score", type=float, default=0.0)
    s.add_argument("--per-match", action="store_true", help="show only the best idea per match")
    s.add_argument("--sort", choices=["score", "hit", "roi", "edge"], default="score", help="ranking key for the table")
    s.add_argument("--html", help="write an HTML report to this path")
    s.add_argument("--show-results", action="store_true", help="replay mode: settle the displayed ideas against real results")
    s.set_defaults(func=cmd_scan)

    m = sub.add_parser("match", help="full breakdown of one fixture")
    m.add_argument("home")
    m.add_argument("away")
    m.add_argument("--date")
    m.add_argument("--league")
    m.set_defaults(func=cmd_match)

    b = sub.add_parser("backtest", help="walk-forward backtest over the bundled seasons")
    b.add_argument("--from", dest="start", required=True)
    b.add_argument("--to", dest="end", required=True)
    b.add_argument("--refit-days", type=int, default=7)
    b.add_argument("--write-calibration", nargs="?", const=True, default=False)
    b.add_argument("--verbose", action="store_true")
    b.set_defaults(func=cmd_backtest)

    r = sub.add_parser("ratings", help="print team attack/defence ratings")
    r.add_argument("--date")
    r.add_argument("--top", type=int, default=40)
    r.set_defaults(func=cmd_ratings)

    rf = sub.add_parser("refresh-data", help="download season files from openfootball")
    rf.add_argument("--seasons", nargs="+", required=True)
    rf.set_defaults(func=cmd_refresh)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
