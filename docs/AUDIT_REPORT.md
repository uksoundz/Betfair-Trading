# TradeScout: independent audit, improvements and final assessment

Date: 8 October 2026. Scope: the whole repository, both sports. Every number below was produced by
code in this repository on the bundled data and can be reproduced with `tradescout holdout`,
`python -m tradescout.eval.football_eval` style scripts in `tradescout/eval/`, and `pytest`.

**Summary.** The original application worked as software but its profitability figures were not
evidence of anything: they came from a backtest that priced entries at the model's own fair prices,
shaded lays in the trader's favour, settled in-play exits at modelled prices, and fed strike rates
from the same seasons back into the ranking. The rebuilt system is honest about what it knows. Its
forecasts have measurable but modest skill over naive baselines, its probabilities are calibrated
out of sample, and it now refuses to call anything a trade without an exchange price that clears a
conservative net edge after commission. No strategy in either sport has been shown to make money
against the market, because no historical exchange prices were available. The system is built to
collect that evidence (signals log, paper mode) and to stay out of the market until it exists.

---

## A. Original system score

| | Score | Why |
|---|---|---|
| Football (as of the start of this audit) | **34 / 100** | Sound Dixon-Coles core and a working app, but: no exchange prices, so "edge" was never measured; the 0-100 score was an arbitrary function of a synthetic ROI; lay entries were priced *in favour* of the layer (about +3% per lay plan); in-play exits were modelled prices with a flat 3% friction; strike-rate calibration for the demo was built on the same seasons it was shown on; goals forecasts had no skill over league averages and were badly calibrated; Kelly used the raw model probability. |
| Tennis (first integration this session, before audit) | **18 / 100** | A match-id collision in the 2025 file paired fixtures with the wrong results, so the first holdout was meaningless; the point model over-predicted match length and under-predicted straight sets; one strategy's settlement could not be verified from the data; no exchange mapping had been exercised. |
| Overall platform | **30 / 100** | Usable, well presented, and over-stating what it knew. |

## B. Problems discovered

Modelling and statistics

1. **Lay entries shaded the wrong way.** `price_or_fair` divided the fair price by the overround for
   lays as well as backs, so every synthetic lay was placed at a *better* price than fair. Lay the
   Draw, Lay the 0-0 and the staged Under-2.5 lay all benefited by roughly 3% per unit. Fixed; the
   corrected holdout turned Lay the Draw from −1.2% to −2.8% and Lay the 0-0 from 0% to −0.4%.
2. **Football goals forecasts had no skill and were miscalibrated.** On the untouched 2025-26
   season the raw model's Over 2.5 Brier score was *worse* than the league base rate (skill −1.4%),
   and matches it called 36% to go over went over 53% of the time. Cause: team attack/defence
   spread too wide. Ridge 0.3 and totals shrinkage 0.7 (chosen on 2024-25) fix the calibration;
   skill is now +0.2%, i.e. still none. Goals strategies therefore rest on league averages unless the
   exchange misprices them.
3. **Tennis 2025 data was mis-joined.** Many 2025 rows have a blank match number; the fixture id
   `tourney:match_num` collided and `result_for` returned the first row for the tournament. The
   first "holdout" showed a coin-flip model and 29-game best-of-fives. Fixed with player-qualified
   ids and exact-match lookup; a test asserts every fixture maps to its own result.
4. **Tennis point model too even.** The iid serve model predicted 27.9 games and 35% straight-sets
   wins against 25.9 and 41% observed. A strength-uncertainty mixture did not help (tested).
   An empirical logistic calibration for straight sets and a games shift per format, fitted on
   2024, holds on 2025 (41.1% vs 40.4% straight sets; 26.05 vs 26.09 games).
5. **Calibration leakage in the demo.** The shipped `calibration.json` had been built on 2024-26
   and then used to "replay" 2025-26 days. Rebuilt from the tuning windows only.
6. **Timing strategies settled on inferred goal minutes.** Half-time scores only; the first
   version assumed a single minute and was optimistic by up to 12 points of strike rate. Now
   settled as an expectation over the timing distribution; every such strategy is labelled
   "approximate" and its evidence class "simulated in-play".
7. **"Strike rate" rounded expected hits to 1.** Fixed: strike is the mean expected hit.
8. **Kelly on raw model probabilities, with a 5% cap and no exposure control.** Replaced.

Exchange mechanics and execution

9. No account of spread, depth, partial fills, stale snapshots or suspended markets. Added.
10. Commission assumed 2%. Default is now the 5% UK base rate, user-settable.
11. Tennis market type codes on Betfair are not documented in one place; the integration now
    matches markets by name as well as type and skips what it cannot identify.

Software and data

12. Fixture objects were unhashable once sport metadata was added (fixed).
13. The bundled tennis results are a research-licensed mirror and end in January 2026; the
    football-data.co.uk odds endpoint and Betfair itself are unreachable from the build
    environment, so no odds-based or live validation could be run here.

## C. Improvements implemented

| Area | Change | How it was tested |
|---|---|---|
| Football model | xi 0.0045→0.003, ridge 0.01→0.3, totals shrinkage 0.7 | Grid on 2024-25 (tune), confirmed on 2025-26: 1X2 skill +5.4%→+5.9%, Over-2.5 skill −1.4%→+0.2%, calibration buckets now within 2-6 points |
| Tennis model | Elo K scale 0.5, no surface blend; data loader fixed; set/games calibration | Grid on 2024, confirmed 2025: log-loss 0.6253 vs ranking baseline 0.6336, accuracy 63.6%; calibration buckets for straight sets within 3 points |
| Value engine | market-implied probability, model weight by market, uncertainty shrinkage, net EV after commission at the size available, spread/liquidity/stale/partial-fill checks, in-play structural cost, TRADE / NO TRADE / RESEARCH | `tests/test_value_risk.py`, `tests/test_decisions.py` (fair quotes → all NO TRADE; 40% overpricing → TRADEs with capped stakes; no quotes → all RESEARCH) |
| Risk engine | fractional Kelly on the conservative probability of the entry bet; per-trade 2%, open 10%, strategy 5%, sport 8%; daily 3% and weekly 6% loss stops; drawdown halving; exchange minimum; risk-of-ruin Monte Carlo | unit tests for every cap and block |
| Ranking | transparent score = 10 × conservative edge % × execution × evidence; research capped at 39 | decision tests |
| Backtests | lay shading corrected; expected-hit settlement; strike/avg win/avg loss/ROI/profit factor/max drawdown/losing streak/volatility/bootstrap CI; evidence class on every row; tuning→holdout protocol; cost sensitivity | `tradescout holdout`, `data/strategy_stats.json`, `data/cost_sensitivity.json` |
| Strategies | enabled/settlement/in-play metadata; losers and unverifiable plans ship disabled; user toggles | registry tests, settings round-trip |
| Tennis integration | Betfair fixtures (event type 2), name-based markets, player matching, slip resolution, sport-aware server and UI | unit tests on matching/classification; browser end-to-end on replay data |
| Signals log | every idea shown on a live day is appended with its prices and decision, settled later | tests |
| UI | sport switch, decision badges, value check per selection, performance and bankroll tabs, both-sports ranking, stale-price timestamp | Playwright end-to-end, no console errors |

## D. Final system score

| | Score | Reasoning |
|---|---|---|
| Football | **54 / 100** | Calibrated match-odds model with real but modest skill; goals model honest (no skill); exchange-aware decisions; risk controls. Missing: any validation against market prices, xG and lineup inputs, recorded in-play price paths. |
| Tennis | **44 / 100** | Correct data pipeline, calibrated set and games probabilities, exact in-play repricing, exchange mapping written but never exercised against Betfair, ratings only 1.3% better than the ATP ranking and well short of market sharpness, ATP only, data ends January 2026 without a licensed feed. |
| Overall | **50 / 100** | A sound, honest decision-support foundation with no demonstrated profitability. The next 20 points come only from forward evidence at observed prices. |

## E. Evidence of profitability

| Level | Status |
|---|---|
| Theoretical advantage | Present only where the exchange price differs from a calibrated model probability by more than the stated shrinkage and costs. The value engine computes it per selection; it has never been observed on a live market from this environment. |
| Statistical forecasting accuracy | Established out of sample: football 1X2 +5.9% log-loss skill over league base rates (2,159 holdout matches); tennis +1.3% over an ATP-ranking baseline (2,650). Football goals: none. These are below the skill level published for sharp bookmaker and exchange prices, which is why the model gets only 15-30% weight against the market. |
| Historical simulated profitability (model prices) | Mixed and inconsistent. Football: two strategies positive on 2025-26 (+8.2%, +4.3%) and negative on 2024-25; the rest negative. Tennis: all negative. Simulated figures depend on modelled exits and shading; cost sensitivity in `data/cost_sensitivity.json` shows in-play strategies lose 2-3 further points per 3% of friction. |
| Backtested profitability using observed odds | **Not performed.** No odds history was reachable. Code for football-data.co.uk closing odds exists and is untested here. |
| Forward paper trading | **Not started.** The signals log and paper mode are built for it. |
| Real executed profitability | **None.** |

**Profitability is unproven.**

## F. Strategy leaderboard (holdout, model prices; evidence class in brackets)

Football 2025-26, 5% commission, 3% friction:

| Rank | Strategy | n | Strike | ROI/unit | Max DD (units) | Losing run | Status |
|---|---|---|---|---|---|---|---|
| 1 | Lay Under 2.5 staged | 1,933 | 56.9% | +8.2% | 17 | 6 | experimental (negative on tuning season) [simulated in-play] |
| 2 | Over 2.5 + 1-1 insurance | 888 | 69.6% | +4.3% | 14 | 5 | experimental (−7.9% on tuning season) [model-fair static] |
| 3 | Lay the 0-0 | 2,135 | 88.3% | −0.4% | 13 | 4 | calibrated, structurally break-even [simulated in-play] |
| 4 | Lay the Draw | 2,159 | 64.0% | −2.8% | 61 | 6 | calibrated; needs ≥3% draw mispricing [simulated in-play] |
| 5 | Back-to-lay favourite | 1,958 | 54.6% | −3.7% | 74 | 13 | disabled |
| 6 | Correct-score basket | 1,592 | 52.8% | −6.0% | 104 | 10 | disabled |
| 7 | Back Under 2.5 trade-out | 863 | 25.7% | −7.2% | 64 | 18 | disabled |

Tennis 2025 + Jan 2026:

| Rank | Strategy | n | Strike | ROI/unit | Max DD | Losing run | Status |
|---|---|---|---|---|---|---|---|
| 1 | Straight sets (favourite) | 1,095 | 52.1% | −3.6% | 52 | 10 | calibrated; market-check only [model-fair static] |
| 2 | Over total games | 2,327 | 53.6% | −5.9% | 164 | 13 | calibrated; market-check only [model-fair static] |
| 3 | Back-to-lay after set one | 2,300 | 58.8% | −6.0% | 138 | 8 | disabled |
| 4 | Lay favourite, early break | 1,081 | n/a | n/a | n/a | n/a | disabled (unverifiable from results data) |

Static strategies at model-fair prices lose roughly the overround plus commission by
construction; their value is that the probabilities are calibrated, so a genuine exchange mispricing
can be recognised. In-play strategies additionally pay exit friction, which the scorer now charges.

## G. Data-source audit

| Source | Status | Notes |
|---|---|---|
| openfootball results (bundled) | operational, public domain | 2023-24 to 2026-27, six leagues, half-time scores, no goal minutes, no odds |
| football-data.org | operational with free key | fixtures and results for nine competitions, 10 requests/minute; goal minutes not used yet |
| TML-Database ATP results (bundled) | operational, research licence, ends 23 Jan 2026 | Sackmann layout, no WTA, no point-by-point, approximate match dates |
| Betfair Exchange API | operational with account + app key; **not exercised from this environment** | prices, depth, tennis fixtures, placement; tennis market codes matched by name |
| football-data.co.uk odds | code present, host unreachable here | would enable bookmaker and exchange closing-odds backtests |
| xG (Understat/FBref), lineups, injuries | not integrated | scraping is fragile and licence-restricted; a paid API would be needed |
| Tennis point-by-point, serve stats | not integrated | Sackmann's point-by-point repos were unreachable; paid feeds exist |

## H. What you need to do

1. **Install.** Download the ZIP from GitHub, unzip, double-click `install.bat`, then `app.bat`.
2. **Football fixtures (free).** Open football-data.org/client/register, enter your name and
   email, copy the code from their email, open TradeScout, click Settings, paste it under
   *Fixtures feed*, click Save, then Test connection.
3. **Betfair (needed for any TRADE).** Log in to your Betfair account, follow the link in Settings
   to create an Application Key (the free Delayed key is enough to start), then in Settings enter
   the key, your Betfair username and password, click Save, then Test login. This also turns on live
   tennis fixtures.
4. **Set your commission** in Settings to the rate Betfair charges you (5% if unsure).
5. **Run Paper mode for several weeks.** Settings > Betting > Paper. Each day open Opportunities,
   look only at TRADE ideas, press Bet slip, Record as paper bet. Press Update results in My picks
   the next day. Watch Performance: the signals log shows how many TRADE decisions paid off at the
   prices seen.
6. Only if that record is positive over a few hundred decisions, consider Live mode with a small
   daily cap.

Necessary accounts: football-data.org (free) and Betfair (account; app key free or one-off fee).
Optional: none of the paid data feeds are required for the system to run; they would be required to
improve the forecasts materially.

## I. Remaining limitations and next improvements

1. **No market validation.** The single most important missing piece. Record Betfair pre-match
   prices daily (the signals log does this once connected) and, where possible, obtain historical
   closing odds to backtest the static strategies against real prices.
2. **In-play exits are modelled.** Record in-play price paths for scanned matches to replace the 3%
   friction assumption and to validate the timing strategies, or disable them for staking.
3. **Model skill is modest.** Football goals and tennis ratings need better inputs (xG, lineups,
   serve/return statistics, fatigue) to deserve more than a 15-30% weight against the market. The
   weights are settings; raise them only when the signals log shows the model beating the market.
4. **Tennis data.** Licensed, current ATP (and WTA) results with match dates; point-by-point data
   to validate break-based strategies.
5. **Goal minutes** from football-data.org are available on the live feed and would make the
   football timing settlement exact for tracked picks.
6. **Correlated exposure.** Caps are per strategy and sport; positions on the same match or the same
   market across strategies are not yet netted.
7. **Live monitoring.** Tennis in-play repricing is exact in the model but there is no live score
   feed; a streaming price and score source would make the in-play plans actionable rather than
   advisory.

## J. Live price feed and placement round (8 October 2026)

User report after connecting a real Betfair account: no live prices appeared and there was no way to
place trades from the Matches view. Root causes, verified by replaying the code against a local
stand-in for the Betfair API (`tests/fake_betfair.py`, which answers login, keepAlive, listEvents,
listMarketCatalogue, listMarketBook, placeOrders, listCurrentOrders and cancelOrders the way Betfair
does, with Betfair's own club spellings):

| Cause | Effect | Fix |
|---|---|---|
| Fixture-to-event matching needed an exact alias hit on both club names | 19 of 34 fixtures priced on a sample Saturday; no Championship club, PSG, Athletic Club, Napoli or newly promoted side ever matched | scored matcher (accent folding, abbreviation expansion, noise tokens, qualifier penalty, kickoff proximity, ambiguity margin); 34 of 34 priced; 116 must-match and 29 must-not-match spellings under test (reserve, youth and women's sides never match the first team) |
| Session token fetched once at start-up, never kept alive or renewed (and, once fixed, a wrong password would have been retried every minute until Betfair locked the account) | after the Betfair session expired (24 h UK, 12 h elsewhere, 20 min IT/ES) every call failed and every idea silently became RESEARCH | lazy login, keepAlive every 10 minutes, automatic re-login on INVALID_SESSION_INFORMATION, a 30-minute back-off after a rejected login (6 hours after a lock), Reconnect button |
| Price feed errors swallowed into a list the UI never showed; connection pill reflected only the start-up login | the screen said "Exchange prices" while nothing was priced | feed report in every scan (priced / matched / unmatched with nearest exchange names / in play / suspended / error), per-match price chips, diagnosis route and `tradescout betfair-check` |
| Two API calls per fixture, sequential | a 30-fixture day took 40 calls | one listEvents, listMarketCatalogue within the 200-point request budget (30 events per call, 5 untyped), listMarketBook in chunks of 25 (11 calls for 34 fixtures); a response that hits the cap is re-fetched per event so no match is cached as 'no markets' |
| MATCH_ODDS suspended or in play made the whole fixture "unpriced" | pre-kick-off suspensions turned TRADEs into RESEARCH for the cache lifetime | per-market status; in-play or suspended matches are NO TRADE with the reason |
| Place button hidden unless the idea was already a TRADE, with no explanation | with no prices, no idea was a TRADE, so no button anywhere | Place on Betfair on every plan; the slip spells out each blocking reason; non-TRADE ideas can be placed only after an explicit override that is logged as the user's call |
| Default slip stake split across legs fell under the exchange minimum | a £2 plan split 80/20 had nothing sendable | stake floor so every leg clears the minimum, shown on the slip |
| Placement edge cases | a Betfair timeout counted as "not placed" (double stake on retry); a rejected market left half a plan live; the slip could be sent at a stake other than the one confirmed; a tracked idea placed by override lost its override tag | deterministic customerRef (exchange de-duplication) and a per-line customerOrderRef so a timed-out or dropped order is found by reference, polled for up to 15 s; a plan is all or nothing across markets (first rejection stops the rest, accepted legs cancelled, only matched money counted); the previewed stake is what is sent; soft blocks needing the override for stakes above the per-trade cap and for plans already placed today; hard blocks when the risk engine says no or no exchange price was seen; override logged whether or not the idea was tracked first; repeated live placements add to the daily-cap total |
| Tennis runner names | total-games runners ("Over 22.5 Games") and set-betting runners ("Alcaraz 2-0") never matched the strategies' selections; player names with trailing initials or multi-word surnames resolved wrongly | token-based player matcher and runner-key normalisation |

Evidence: 234 unit and route tests pass, including 45 that drive the real client, scan pipeline, web
routes and order placement against the stand-in exchange (login, expiry and renewal, batched prices, in-play and suspended
handling, unmatched diagnostics, placement with confirmation, override logging, mode-off refusal). A
Playwright run in Chromium against the app wired to the stand-in shows prices on every fixture, the
feed panel and diagnosis table, the Place button, the confirmation step, orders arriving at the
exchange stand-in, the override path and the journal entries.

Not established: behaviour against Betfair itself, which this build environment cannot reach. The
stand-in follows the documented API; real event spellings for leagues outside the six bundled ones,
the exact tennis market type codes, and account-specific login responses (two-factor, jurisdiction)
remain to be confirmed on a live account. The diagnosis panel exists so that whatever differs is
visible on the first day rather than silent.
