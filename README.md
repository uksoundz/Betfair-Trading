# TradeScout

Exchange trading research for football and ATP tennis. It pulls the day's fixtures, forecasts every
market from the numbers, checks each strategy against the Betfair price and depth actually on offer,
and ranks what is left. Most ideas end as **NO TRADE**. That is the point.

* Two sports, selectable: **Football** (6 bundled leagues, 9 live) and **Tennis** (ATP singles).
* Every idea carries a decision: **TRADE** (conservative net edge after commission at an available
  price and size), **NO TRADE** (priced, no proven advantage) or **RESEARCH** (no exchange price).
* Every number is out-of-sample tested: the model settings were chosen on one season and confirmed
  on a later one that was never used for tuning. The results, including the bad ones, are in
  [`docs/AUDIT_REPORT.md`](docs/AUDIT_REPORT.md).
* Decision support first. Order placement exists behind an off-by-default switch, a confirmation
  step, a daily cap and exposure limits. TRADE ideas place directly; anything else needs an
  explicit, logged override on the slip. Nothing is ever sent without a confirmation click.
* Optional auto-trading of plans you have placed and explicitly armed: the engine follows the plan's
  in-play rules (green up, close at the stop, scale in, free bet) from Betfair's live score and prices,
  with hedges that can only reduce the worst case, a simulate mode, and disarm / stop-all controls. Works with
  the free Delayed application key: hedges find the real price through Betfair's order replies, or an
  alert mode beeps and links you to Cash Out.
* The price feed explains itself: how many fixtures the exchange priced, which could not be matched
  to an exchange event and the nearest names, which are in play or suspended, session and key
  state. Sessions renew themselves; prices refresh every minute while the app is open.

New to all this? Start with [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md).

## Quick start

```bash
pip install -e ".[dev]"
tradescout app                      # web app: sport switch, date picker, opportunities, plans, journal, settings
tradescout scan --date 2025-11-08   # football replay in the terminal
tradescout scan --sport tennis --date 2025-06-02
tradescout betfair-check            # Betfair login test and, per fixture, the exchange event matched or why not
tradescout holdout                  # out-of-sample protocol for both sports (writes data/strategy_stats.json)
pytest                              # 258 tests (incl. the Betfair client, routes, placement and auto-trading against a local stand-in exchange)
```

Windows: `install.bat` then `app.bat`.

## How a trade decision is made

```
fixtures ─► forecast ─► strategies ─► value engine ─► risk engine ─► rank
             (model)    (scenario       (exchange       (stake, caps)   (score)
                         trees)          prices)
```

1. **Forecast.** Football: time-weighted Dixon-Coles with ridge shrinkage and league-average
   totals shrinkage (all three tuned on 2024-25, confirmed on 2025-26), plus a goal-timing model and
   conditional in-play pricing. Tennis: Elo (K tuned on 2024, confirmed on 2025) feeding a
   point-to-match Markov model that prices any score state, with an empirical calibration layer for
   straight-sets and total-games probabilities (fit 2024, validated 2025).
2. **Strategies.** Each is a scenario tree: every way the match can go, its probability, and the
   profit per unit risked using the model's own in-play exit prices less friction and commission.
   Each declares its pre-match selections (market, side, limit price, stake share), whether it needs
   in-play action, and how well the backtest can settle it. Weak or unverifiable ones ship disabled.
3. **Value engine** (`tradescout/value.py`). For each selection: market-implied probability from
   the back/lay midpoint, the model's probability blended in with a small stated weight, pulled
   further towards the market by data uncertainty, net EV after commission at the size-weighted
   price available, spread and depth checks, partial-fill and stale-price handling. In-play plans
   are charged their structural cost (exits, friction) on top. No quote means RESEARCH, never TRADE.
4. **Risk engine** (`tradescout/risk.py`). Fractional Kelly on the conservative probability of the
   entry bet, then per-trade, open-exposure, per-strategy and per-sport caps, daily and weekly loss
   limits, stake halving in drawdown, exchange minimum, risk-of-ruin Monte Carlo.
5. **Rank score.** 10 points per 1% of conservative net edge, times execution quality (0..1), times
   an evidence weight (1.0 when the plan settles at the result on exchange prices, 0.6 when exits
   are model-simulated). NO TRADE ideas never score above 39; RESEARCH ideas carry a model-only
   research score for ordering.

## What the out-of-sample tests show

All figures below are **model-synthetic**: entry at the model's own fair price shaded against the
trader by a 3% overround, in-play exits at modelled prices less 3% friction, 5% commission, settled
on real results. They test calibration and plan structure. They are not exchange performance, and
no historical exchange prices were available to produce any.

Forecast skill, holdout seasons (log-loss or Brier vs a league-base-rate baseline):

| Sport / market | Holdout | Skill vs baseline |
|---|---|---|
| Football 1X2 | 2025-26, 2,159 matches | +5.9% log-loss improvement |
| Football Over 2.5 | 2025-26 | +0.2% Brier (none; calibration fixed, no skill) |
| Tennis match winner | 2025 + Jan 2026, 2,650 matches | +1.3% log-loss vs ATP-ranking baseline |

Strategy results on the holdout (strike = mean realised hit, predicted = model's hit probability):

| Football strategy | n | Strike | Predicted | ROI/unit | 95% CI | Default |
|---|---|---|---|---|---|---|
| Lay Under 2.5 staged | 1,933 | 56.9% | 53.3% | +8.2% | +4.2%..+12.0% | on |
| Over 2.5 + 1-1 insurance | 888 | 69.6% | 65.0% | +4.3% | −0.1%..+9.1% | on |
| Lay the 0-0 | 2,135 | 88.3% | 87.0% | −0.4% | −0.8%..+0.1% | on |
| Lay the Draw | 2,159 | 64.0% | 64.0% | −2.8% | −3.2%..−2.3% | on (market check only) |
| Back-to-lay favourite | 1,958 | 54.6% | 51.3% | −3.7% | −5.5%..−1.8% | off |
| Correct-score basket | 1,592 | 52.8% | 50.8% | −6.0% | −10.0%..−1.6% | off |
| Back Under 2.5 trade-out | 863 | 25.7% | 32.8% | −7.2% | −8.9%..−5.2% | off |

Both "positive" football strategies were **negative on the 2024-25 tuning season** (−0.9% and
−7.9%). No football strategy is consistently profitable at model prices across both seasons.

| Tennis strategy | n | Strike | Predicted | ROI/unit | 95% CI | Default |
|---|---|---|---|---|---|---|
| Straight sets (favourite) | 1,095 | 52.1% | 52.1% | −3.6% | −9.2%..+2.0% | off (replaced by set betting value) |
| Over total games | 2,327 | 53.6% | 54.9% | −5.9% | −9.5%..−2.5% | on |
| Back-to-lay after set one | 2,300 | 58.8% | 58.4% | −6.0% | −7.4%..−4.6% | off |
| Lay favourite, early break | 1,081 | unverifiable | | | | off |

The tennis probabilities are well calibrated; every tennis plan loses at model prices once the
overround, friction and commission are charged. Edge, if any, has to come from exchange mispricing,
which is what the value engine measures and the signals log will record.

### Market-anchored tennis (October 2026)

Our Elo does not beat the exchange's match odds, so the two newer tennis plans do not try to. They take
the probability the exchange's own match-odds market implies and correct only what the
independent-points model gets wrong about the *shape* of a match, using ATP closing prices with set
scores (fitted 2021-23, kept only where it beat the plain model on 2024-25;
`python -m tradescout.eval.tennis_market_fit`, results in `data/tennis_market_corrections.json`):

| Quantity (best of 3, 2024-25 holdout) | Independent points | Actual | Corrected log-loss |
|---|---|---|---|
| Favourite wins in straight sets | 38.2% | 44.9% | 0.6667 → 0.6562 |
| Underdog wins in straight sets | 15.7% | 18.7% | 0.4662 → 0.4627 |
| Favourite comes back after losing set 1 | 35.5% | 31.4% | 0.6134 → 0.6109 |
| ... after losing set 1 6-3 or wider | 34.9% | 27.4% | 0.5980 → 0.5900 |
| ... after losing set 1 7-5, 6-4 or in a tiebreak | 35.9% | 33.7% | 0.6226 → 0.6220 |

* **Set betting value against match odds** (`tn_sets_value`, on): prices every set score from the
  match odds with those corrections and backs or lays the set score the Set Betting market has out of
  line, through the usual value engine. Settles at the result.
* **Lay favourite after a clear first-set loss** (`tn_lay_fav_lost_set1`, on): no bet before the
  start. Decision **ARM**: arm it and the auto-trader lays the favourite in play only if they lose set
  1 6-3 or wider (best of 3), with a limit price that keeps a 3% margin per unit of liability over the
  validated comeback rate. The order rests for two minutes, then the rest is cancelled; what matched
  runs to the result. Works on the free Delayed key (the trigger is the live score, the limit is the
  value test).

Neither is proven on Betfair: no historical exchange set-betting or in-play prices were available. If
the exchange priced like the plain independent-points model, the in-play lay would have returned
+9.4% per unit of liability on the 566 holdout triggers (95% CI +3.7%..+15.1%), and +2.2% if the
exchange already shades the comeback 10% lower. Expect about one trigger a day across the ATP tour.
Your own armed trades and the signals log are the measurement.

## Data sources

| Source | Status | Used for |
|---|---|---|
| openfootball (bundled, public domain) | operational | football results 2023-24 to 2026-27, replay fixtures |
| football-data.org (free key) | operational with key | live football fixtures, fresh results, 9 competitions |
| TML-Database ATP mirror (bundled, research use) | operational, ends Jan 2026 | tennis results, Elo, replay. A commercial release needs a licensed feed |
| Betfair Exchange API (account + app key) | operational with key; exercised end to end against a local stand-in exchange (`tests/fake_betfair.py`), not against Betfair itself from this build environment | prices, depth, tennis fixtures, placement |
| football-data.co.uk closing odds | code present, not reachable from this build environment | bookmaker/exchange closing-odds backtests |
| xG, lineups, injuries, point-by-point tennis | not integrated | would need licensed providers |

## Layout

```
tradescout/
  model/        Dixon-Coles, goal timing, in-play conditional pricing, forecast
  tennis/       results loader, Elo, Markov point model, calibration, strategies, scout, backtest
  strategies/   football strategies (scenario trees), registry with enable flags
  value.py      exchange-aware value engine (TRADE / NO TRADE / RESEARCH)
  risk.py       staking and exposure limits, risk of ruin
  ranking/      scorer and strike-rate calibration store
  eval/         walk-forward evaluation, holdout protocol, trade statistics
  backtest/     football walk-forward backtester
  betting.py    bet slip, tick ladder, placement (live mode only)
  autotrade/    in-play rules, position and green-up maths, live score feed, the auto-trading engine
  signals.py    append-only log of every idea shown with the prices seen
  journal.py    tracked picks, settled against results
  data/         openfootball, football-data.org, Betfair (football and tennis), scored team-name matching
  web/          FastAPI server and single-page UI
data/sample/    bundled results; data/calibration.json, strategy_stats.json, tennis_calibration.json
docs/           GETTING_STARTED.md, AUDIT_REPORT.md, TRADING_GENIE_REVIEW.md
tests/          unit tests; fake_betfair.py is a local stand-in for the Betfair API used by the client, route and browser tests
```

## Disclaimer

Research tooling, not advice. Exchange trading risks capital. Nothing here has been shown to make
money against the market; see the audit report for exactly what has and has not been established.
