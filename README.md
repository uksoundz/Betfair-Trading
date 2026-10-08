# TradeScout

Pulls every football fixture of the day, models each match from the numbers, runs a library of
Betfair trading strategies over it and ranks the ideas by how likely they are to pay off.
Think of it as the research half of a football trader's morning, done in two seconds and
fully auditable: every probability, price and plan step comes from a model you can inspect
and a backtest you can re-run.

Built as an open alternative to the Football Trading Genie. The full review of that product and
the design rationale is in [`docs/TRADING_GENIE_REVIEW.md`](docs/TRADING_GENIE_REVIEW.md).

## What it does

```
$ tradescout scan --date 2025-11-08 --top 10 --show-results

 #  Score  Match                                        Lg    KO     Strategy                        Hit%  Cal%  Price  Edge   ROI  Stake%  Conf
 1     54  West Ham United FC v Burnley FC              en.1  15:00  Back Over 2.5 + 1-1 insurance    57%   60%   2.21     -  -2.9%   0.9   1.00
 2     52  West Ham United FC v Burnley FC              en.1  15:00  Lay Under 2.5 (staged entry)     44%   44%   1.82     -  +0.7%   0.3   1.00
 3     51  Sevilla FC v CA Osasuna                      es.1  16:15  Back Over 2.5 + 1-1 insurance    59%   62%   2.09     -  -2.9%   0.7   1.00
 ...
   53.7  West Ham United FC v Burnley FC     Back Over 2.5 + 1-1 insurance  -> 3-2  HIT  +0.70
   51.9  West Ham United FC v Burnley FC     Lay Under 2.5 (staged entry)   -> 3-2  HIT  +1.24
  6.0/10 of the displayed ideas paid off (model expected 6.0)
```

For every fixture it produces:

* a full scoreline distribution (expected goals, 1X2, over/unders, BTTS, correct scores, HT 0-0)
* goal-timing probabilities (goal before 15'/30'/45'/70', who scores first)
* one trade plan per strategy with entry price, the in-play exit prices to expect in each
  scenario, the stop, and the probability each scenario happens
* a 0-100 rank score, a calibrated hit probability, expected ROI per unit risked, and a
  quarter-Kelly stake

`tradescout match "Chelsea FC" "Wolverhampton Wanderers FC" --date 2025-11-08` prints the full
breakdown for a single game.

## The app

`tradescout app` (or double-click `app.bat` on Windows) opens a local web app: date picker for any
day past or future, every match with its best score, the model's view in plain English, and for
each strategy a plan split into Before kick-off / In play / Get out / Stop loss with the reasoning
and a scenario table. Top picks ranks the whole day; By strategy answers "which matches suit lay
the draw today?". Past dates show the real result and whether each plan paid off.

## Quick start

New to the command line? Follow [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md), it walks through every click.

```bash
pip install -e ".[dev]"
tradescout app                                          # web app at http://127.0.0.1:8765
tradescout scan --date 2025-11-08 --show-results        # replay a Saturday from the bundled data
tradescout scan --date 2025-11-08 --html reports/sat.html
tradescout match "Arsenal FC" "Tottenham Hotspur FC" --date 2025-11-23
tradescout backtest --from 2024-08-01 --to 2026-05-31 --write-calibration
tradescout ratings --date 2025-11-08
pytest
```

Everything above runs offline. The repo ships three seasons (2023-24 to 2025-26) of results for the
Premier League, Championship, Bundesliga, La Liga, Serie A and Ligue 1 from the public-domain
[openfootball](https://github.com/openfootball/football.json) project.

### Going live

| What | How | Needed for |
|---|---|---|
| Today's fixtures | `export FOOTBALL_DATA_API_KEY=...` ([football-data.org](https://www.football-data.org/), free tier) | the daily `scan` without `--date` |
| Exchange prices | `export BETFAIR_APP_KEY=... BETFAIR_SESSION_TOKEN=...` (or username/password) | real edge, real entry prices, liquidity |
| More history | `tradescout refresh-data --seasons 2025-26 2026-27` | keeping ratings current |
| Closing-odds backtest | `tradescout.data.football_data_couk.FootballDataCoUk` | measuring ROI against real prices |

Run `tradescout setup` to enter the keys once; they are saved to a `.env` file in the folder.

Without a price feed the tool still ranks, but prices are the model's fair prices shaded by a
typical exchange overround and the Edge column is blank. The ranking then reflects the payoff
structure and calibrated hit probability, not market value. Connect Betfair to rank on edge.

## How it works

```
fixtures ──> Dixon-Coles goal model ──> scoreline matrix ──> market probabilities
                 (time-weighted,              │                    │
                  attack/defence,             ▼                    ▼
                  home adv, rho)       goal-timing model     7 strategies, each a
                                       (when, who first)     scenario tree with payoffs
                                              │                    │
                                              ▼                    ▼
                                       in-play conditional   calibration from the
                                       prices for exits      walk-forward backtest
                                                                   │
                                                                   ▼
                                                          scorer: 0-100, Kelly stake
```

1. **Goal model** (`tradescout/model/dixon_coles.py`). Each team gets attack and defence
   ratings; there is a home-advantage term and the Dixon-Coles correction for low scores. Matches
   are weighted by `exp(-xi * days_ago)` so form matters more than history. Maximum likelihood
   with an analytic gradient: fitting on 5,000 matches takes under 0.1 s.
2. **Timing model** (`model/timing.py`). Goals arrive as a Poisson process whose intensity rises
   through the match (about 45% of goals in the first half, the last 15 minutes busiest). Gives
   P(goal before minute m), P(first scorer), half-time scorelines.
3. **In-play conditional pricing** (`model/inplay.py`). Given a scoreline at minute t, the goals
   still to come are Poisson with the remaining share of each side's expected goals. That prices
   any market at any minute, which is how a plan can say "if Chelsea lead 1-0 on 20', the draw
   trades around 9.7, green up there".
4. **Strategies** (`tradescout/strategies/`). Each strategy is a scenario tree: for every way the
   match can unfold (favourite scores first in 15-30', underdog scores first, still 0-0 on 70'
   ...) it knows the probability and the profit per unit risked from the entry and exit prices.
   Hit probability is the mass of positive-profit scenarios. Each strategy also knows how to
   settle itself against a real result, which is what the backtester uses.
5. **Backtest and calibration** (`tradescout/backtest/`). Walk-forward over every match day: the
   model is refit using only earlier matches, strategies are evaluated exactly as the daily scan
   does, then settled. It reports strike rate vs predicted, ROI per strategy and league, and ROI
   by rank-score band. The per-strategy/league strike rates are saved to
   `data/calibration.json` and fed back into the live ranking.
6. **Scorer** (`tradescout/ranking/scorer.py`).
   `score = clip(50 + 150 * calibrated_roi + 100 * edge) * (0.5 + 0.5 * confidence) * (0.7 + 0.3 * liquidity)`.
   50 means break-even after friction. Confidence is how many effective matches sit behind both
   ratings; liquidity is a per-league proxy for exchange depth. Stake is quarter-Kelly capped at 5%.

### Strategies shipped

| Key | Plan | Pays off when |
|---|---|---|
| `ltd` | Lay the Draw, green on the favourite's goal, stop at 0-0 on 70' | favourite scores first before 70' |
| `over25_ins` | 80% Over 2.5 + 20% CS 1-1 | 3+ goals, or 1-1 when the cover pays |
| `lay_under25_staged` | Lay Under 2.5 half pre-match, half at 15' if 0-0 | 3+ goals |
| `b2l_fav` | Back the favourite, lay off when they lead, stop at 0-0 on 60' | favourite scores first before 60' |
| `lay_00` | Lay 0-0 correct score, stop at 0-0 on 70' | any goal before 70' |
| `under25_tradeout` | Back Under 2.5, lay it back on 60' | no goal before 60' |
| `cs_basket` | Dutch the five most likely scorelines | the match finishes on a covered score |

Adding a strategy is one class implementing `evaluate()` and `settle()` in
`tradescout/strategies/` plus a line in `registry.py`.

## Backtest results (bundled data, model prices)

Walk-forward over 2024-25 and 2025-26, six leagues, 415 match days, 22,432 strategy-trades:

| Strategy | n | Strike | Model predicted | ROI / unit |
|---|---|---|---|---|
| Lay the 0-0 | 3920 | 86.4% | 87.7% | -0.8% |
| Lay the Draw | 4427 | 69.5% | 70.9% | -0.7% |
| Back Over 2.5 + 1-1 insurance | 1903 | 66.0% | 63.5% | +1.4% |
| Lay Under 2.5 (staged) | 3441 | 55.8% | 56.1% | +3.7% |
| Back-to-Lay favourite | 3710 | 52.8% | 51.7% | -4.2% |
| Correct Score basket | 3067 | 51.6% | 53.7% | -12.7% |
| Back Under 2.5 trade-out | 1964 | 30.1% | 35.3% | -5.5% |

| Rank score band | n | Strike | ROI / unit |
|---|---|---|---|
| 0-40 | 7259 | 54.7% | -7.1% |
| 40-50 | 13721 | 63.8% | -0.7% |
| 50-60 | 1452 | 71.5% | +3.1% |

Taking only the single highest-scored idea each day: 414 trades, 66.8% strike, +10.7% ROI.

Read these carefully. Prices in this backtest are the model's own fair prices less a 3%
overround, because the bundled data has no odds. So the strike-rate columns test whether the
model is **calibrated** (it is: predicted and actual agree within a couple of points for every
strategy) and whether the **ranking orders trades correctly** (it does: strike and ROI rise
monotonically with score). They do not measure edge against the real market. For that, pull
closing odds with `FootballDataCoUk` or run the scan live against Betfair for a season and keep
the results. Goal minutes are not in the bundled data either, so timing strategies are settled as
an expectation over the first-goal minute inferred from the half-time score (see
`Strategy.first_goal_scenarios`); with a minute-level feed this becomes exact.

## Layout

```
tradescout/
  config.py            env-driven settings, league liquidity table
  models.py            Fixture, MatchResult, MarketPrices, MatchForecast, TradeIdea
  data/                openfootball (bundled), football-data.org, football-data.co.uk, Betfair, name aliases
  model/               dixon_coles.py, timing.py, inplay.py, forecast.py
  strategies/          one file per strategy + registry
  ranking/             scorer.py, calibration.py
  backtest/engine.py   walk-forward backtester
  report/              rich console tables, Jinja HTML report
  scout.py             the daily pipeline
  cli.py               tradescout command
data/sample/           3 seasons x 6 leagues of results (openfootball)
data/calibration.json  strike rates from the last backtest, consumed by the scorer
tests/                 21 tests, run with pytest
```

## Disclaimer

This is research tooling, not financial advice. Exchange trading involves loss of capital; the
in-play exit prices are model estimates and real markets will differ, especially in thin
leagues. Backtests on model prices overstate what is achievable against a sharp market.
