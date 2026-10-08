"""Command line entry point.

  tradescout app                        open the web app (buttons, date picker, plans)
  tradescout scan                       rank every fixture today (live feeds if keys set, else sample data)
  tradescout scan --date 2025-11-08     replay a past day from the bundled data and show what happened
  tradescout match "Arsenal FC" "Chelsea FC" --date 2025-11-30
  tradescout backtest --from 2024-08-01 --to 2025-05-31 [--write-calibration]
  tradescout ratings --date 2025-11-08
  tradescout refresh-data                 download this season's latest results (ratings stay fresh)
  tradescout betfair-check --date ...     test the Betfair login and see which fixtures the exchange prices (and why not)
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
        prices = BetfairPrices(settings.betfair_app_key, settings.betfair_session_token, settings.betfair_username, settings.betfair_password,
                               jurisdiction=settings.betfair_jurisdiction, cert_file=settings.betfair_cert_file, key_file=settings.betfair_key_file)
    return Scout(results, fixtures, prices, xi=settings.time_decay_xi, history_days=settings.history_days), sample


def _explain_no_fixtures(on: date, scout: Scout, sample: OpenFootballProvider, leagues) -> None:
    from datetime import timedelta
    live = scout.fixtures is not sample
    print(f"No matches in the covered leagues on {on:%A %d %B %Y}.")
    upcoming: dict[date, int] = {}
    if live:
        try:
            upcoming = scout.fixtures.upcoming(on + timedelta(days=1), 10, leagues)  # type: ignore[attr-defined]
        except Exception as exc:
            print(f"(could not query the fixture feed for the days ahead: {exc})")
    if not upcoming:
        for d in sample.match_days(on + timedelta(days=1), on + timedelta(days=21), leagues):
            upcoming[d] = len(sample.fixtures(d, leagues))
    if upcoming:
        print("Next match days:")
        for d, n in list(upcoming.items())[:7]:
            print(f"  {d:%a %d %b}  {n} fixtures   ->  tradescout scan --date {d.isoformat()}")
    else:
        print("Nothing found in the next few weeks either. Replay a past day with, for example:  tradescout scan --date 2025-11-08 --show-results")
    if not live:
        print("Tip: run  tradescout setup  and enter a football-data.org key to scan live fixtures.")


def cmd_holdout(args) -> int:
    from .eval.holdout import run
    run(verbose=True)
    return 0


def cmd_scan(args) -> int:
    if getattr(args, "sport", "football") == "tennis":
        from .tennis.data import TennisProvider
        from .tennis.scout import TennisScout
        tp = TennisProvider()
        scan = TennisScout(tp, tp).scan(_date(args.date))
        print(f"Tennis {scan.date}: {len(scan.fixtures)} matches, {len(scan.ideas)} ideas (research only without Betfair)")
        for i in scan.top(args.top):
            print(f"  {i.decision:9s} {i.score:5.1f} {i.fixture.label:42s} {i.strategy_label:40s} pays off {i.calibrated_hit_prob:.0%}  plan ROI {i.calibrated_roi:+.1%}")
        return 0
    scout, sample = build_scout(args)
    on = _date(args.date)
    if not args.offline and not args.date:
        if sample.refresh_current_if_stale():
            print("Downloaded this season's latest results for the ratings.")
    scan = scout.scan(on, args.leagues)
    if not scan.fixtures:
        _explain_no_fixtures(on, scout, sample, args.leagues)
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
    seasons = args.seasons or [sample.current_season()]
    written = sample.refresh(seasons, args.leagues)
    print(f"Downloaded {len(written)} files into {sample.data_dir}")
    return 0


def cmd_betfair_check(args) -> int:
    """Show exactly what the exchange returns for a day: login, events, which fixtures matched, prices."""
    from .data.betfair import BetfairError, BetfairPrices
    if not settings.has_betfair:
        print("Betfair is not set up: run  tradescout setup  or use Settings in the app (application key, username, password).")
        return 1
    bf = BetfairPrices(settings.betfair_app_key, settings.betfair_session_token, settings.betfair_username, settings.betfair_password,
                       jurisdiction=settings.betfair_jurisdiction, cert_file=settings.betfair_cert_file, key_file=settings.betfair_key_file)
    print(f"Login host: {bf.login_url}")
    try:
        bf.ensure_session()
        print("Login: OK")
        print(f"Betting API: OK ({bf.probe()['event_types']} event types visible)")
        d = bf.app_key_delayed()
        print("Application key:", "DELAYED (prices up to 3 minutes old)" if d else "live" if d is False else "unknown type")
    except BetfairError as exc:
        print(f"FAILED: {exc}")
        return 1
    on = _date(args.date)
    sport = getattr(args, "sport", "football")
    if sport == "tennis":
        from .data.betfair_tennis import BetfairTennis
        from .tennis.data import TennisProvider
        bt = BetfairTennis(bf, TennisProvider())
        fixtures = bt.fixtures(on)
        diag = bt.diagnose(on, fixtures)
    else:
        scout, sample = build_scout(args)
        fixtures = scout.fixtures.fixtures(on)
        diag = bf.diagnose(on, fixtures)
    rep = diag.get("report") or {}
    print(f"\n{sport.title()} on {on}: {len(fixtures)} fixtures from the fixture feed, {rep.get('events_on_day', 0)} exchange events, "
          f"{rep.get('matched', 0)} matched, {rep.get('priced', 0)} priced, {len(rep.get('inplay', []))} in play, {len(rep.get('suspended', []))} suspended; "
          f"{rep.get('calls', 0)} API calls in {rep.get('elapsed_ms', 0)} ms")
    if rep.get("error"):
        print("Feed error:", rep["error"])
    for row in diag.get("fixtures", []):
        tag = row["status"].upper().ljust(10)
        extra = f"-> {row['event_name']}" if row.get("event_name") else row.get("note", "")
        print(f"  {tag} {row['fixture']:48s} {extra}")
        for name, score in (row.get("candidates") or [])[:2]:
            print(f"             nearest exchange event: {name} ({score:.2f})")
    if rep.get("event_names") and args.verbose:
        print("\nExchange events that day:")
        for n in rep["event_names"]:
            print("  ", n)
    return 0


def cmd_app(args) -> int:
    from .web.server import run
    run(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_setup(args) -> int:
    """Ask for the API keys and save them to .env next to the code."""
    from .config import ENV_FILE
    print("TradeScout setup. Press Enter to skip any question.\n")
    print("1) Fixtures: free key from https://www.football-data.org/client/register (arrives by email)")
    fd = input("   football-data.org API key: ").strip()
    print("\n2) Betfair (optional, for real prices). Needs a Betfair account with an Application Key.")
    print("   Guide: https://developer.betfair.com/get-started/  -> create a Delayed App Key (free).")
    bf_key = input("   Betfair application key: ").strip()
    bf_user = input("   Betfair username (leave blank to skip prices): ").strip()
    bf_pass = input("   Betfair password: ").strip() if bf_user else ""
    print("\n3) Bank size used for stake suggestions.")
    bank = input("   Bank in pounds [1000]: ").strip() or "1000"
    lines = ["# TradeScout settings - keep this file private"]
    if fd:
        lines.append(f"FOOTBALL_DATA_API_KEY={fd}")
    if bf_key:
        lines.append(f"BETFAIR_APP_KEY={bf_key}")
    if bf_user:
        lines.append(f"BETFAIR_USERNAME={bf_user}")
        lines.append(f"BETFAIR_PASSWORD={bf_pass}")
    lines.append(f"TRADESCOUT_BANK={bank}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    print(f"\nSaved to {ENV_FILE}")
    print("Fixtures feed:", "ON" if fd else "off (bundled sample data only; use --date to replay a past day)")
    print("Betfair prices:", "ON" if (bf_key and bf_user) else "off (model prices, no edge column)")
    print("\nNow run:  tradescout scan")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tradescout", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", default=str(SAMPLE_DATA_DIR), help="openfootball JSON directory")
    p.add_argument("--leagues", nargs="*", default=None, help="restrict to league codes e.g. en.1 de.1")
    p.add_argument("--offline", action="store_true", help="ignore API keys and use bundled data only")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="rank today's fixtures")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")
    s.add_argument("--sport", choices=["football", "tennis"], default="football")
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

    bc = sub.add_parser("betfair-check", help="test the Betfair login and show which of a day's fixtures the exchange prices, and why not")
    bc.add_argument("--date", help="YYYY-MM-DD (default today)")
    bc.add_argument("--sport", choices=["football", "tennis"], default="football")
    bc.add_argument("--verbose", action="store_true", help="also list every exchange event name that day")
    bc.set_defaults(func=cmd_betfair_check)

    ap = sub.add_parser("app", help="start the point-and-click web app in your browser")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.set_defaults(func=cmd_app)

    st = sub.add_parser("setup", help="enter your API keys once; saved to .env")
    st.set_defaults(func=cmd_setup)

    ho = sub.add_parser("holdout", help="out-of-sample protocol: calibrate on tuning seasons, report the holdout for both sports")
    ho.set_defaults(func=cmd_holdout)

    rf = sub.add_parser("refresh-data", help="download season files from openfootball")
    rf.add_argument("--seasons", nargs="*", default=None, help="e.g. 2025-26 2026-27 (default: current season)")
    rf.set_defaults(func=cmd_refresh)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
