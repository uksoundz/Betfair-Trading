# Football Trading Genie: review, and how TradeScout is built to beat it

## 1. What the Football Trading Genie is

Football Trading Genie is a subscription web app (plus iOS/Android app) from the Ultimate Football
Trading (UFT) group, marketed as "AI for football trading". Sources for this review: the UFT
walkthrough video ([youtube.com/watch?v=ReE3og9d0Yc](https://www.youtube.com/watch?v=ReE3og9d0Yc),
full transcript read), the vendor's own pages, and third-party write-ups at
[sportstradinglife.com](https://sportstradinglife.com/2025/11/football-trading-genie-review-a-game-changer-for-football-traders/),
[thetradingreview.com](https://thetradingreview.com/football-trading-genie-review-the-ai-revolution-in-football-trading-research/),
[laythedrawmastery.com](https://laythedrawmastery.com/review-how-i-use-football-trading-genie-as-a-lay-the-draw-trader/),
[ultimatefootballtrading.com](https://ultimatefootballtrading.com/football-trading-genie-review-my-experience-as-a-newbie/)
and the [App Store listing](https://apps.apple.com/us/app/football-trading-genie/id6745094142).
Several of those reviewers are UFT members or affiliates, so their praise is not independent.

### What you get, from the video

* **A match list** for the next 24-48 hours, browsable by country, "starting soon" and "tomorrow",
  with a search box.
* **Per-match prompts.** You open a match and press "Best football trading strategy" (or
  "Correct score trading"). A chat-style response comes back with an opening angle, stake split,
  entry conditions, exit/stop guidance and a half-time contingency. Examples shown:
  * Red Star v Boulogne: back Red Star 50% + Over 2.5 50%.
  * Arouca v Sporting: lay Under 2.5, 50% at entry, 50% fifteen minutes later.
  * Mainz v Wolfsburg: 80% Over 2.5 + 20% CS 1-1.
  * Leverkusen v Villarreal: lay the draw + 1-1 insurance, exit at a two-goal lead.
  * Ajax v Olympiacos: back Olympiacos 50% + Over 2.5 at 2.0 or better 50%, half-time contingency
    "back Over 1.5 with half stake if 0-0".
  * Correct score: a "target score formula", e.g. 0-1, 0-2, 1-1, 1-2, 1-3 plus a 2-1 outlier.
* **Strategy shortlists.** Pick a named strategy (Fireball, Snowball, Golden Goals, Genie's
  Gambit, Golden Egg, Lay the Dip, Momentum, Countdown ...) and it returns the day's matches
  that fit, with a plan each. Reviewers describe the strategy library as broad but still being
  organised.
* **Strategy vault**: tutorial and live-trade video library per strategy.
* **Mobile app**, which the presenter says crashed at launch and is now fast.
* **Under the hood**, per the presenter: a "maths-based AI" that "thinks in probabilities", a
  live API data feed with xG and market context, "100% trained on football trading", "not
  ChatGPT with a prompt".

### Pricing, as reported (unverified)

Third-party pages quote £299 per quarter (Basic: 5 matches/day, 5+ leagues) to £399 per quarter
(Pro: unlimited, 25+ leagues) and a Golden tier; the App Store page carries a complaint about a
$1,199/year subscription being required after a $0.99 download. The vendor's own site could not
be reached from this environment, so treat all figures as unconfirmed.

## 2. Assessment

### What it does well

* **Reduces research time to seconds.** This is the real value and the presenter is right to
  lead with it. Finding the day's fixtures, pulling form and settling on an angle is an hour of
  work by hand.
* **The plans are structured like a trader thinks.** Entry, stake split, insurance leg,
  scenario-based exits, half-time contingency. That template is good practice and TradeScout
  keeps it.
* **The strategy shortlist is the right product shape.** "Show me today's matches for strategy X"
  is what a trader actually wants in the morning.
* **It is honest that the trader still does the in-play work.** Several times the presenter says
  "we don't have to follow it", uses xG at half-time to decide, and changes entry prices.

### Where it falls short

1. **No ranking across the day.** You get a plan per match, or a shortlist per strategy, but
   nothing says "of today's 60 matches, these five are the best trades and here is why". The
   lay-the-draw reviewer's workflow is to run prompts match by match and pin the best 5-6
   himself. The buyer's own requirement (pull all matches, rank them) is exactly this gap.
2. **Opaque numbers.** Outputs are prose. No probability of the plan paying off, no expected
   value, no stake sizing, no confidence. "Back Over 2.5 at 2.0 or above" never says what the
   model thinks fair is, so you cannot tell edge from noise.
3. **No track record.** There is no backtest, no published strike rate per strategy, no
   calibration ("when it says 70%, how often does it happen?"). The video shows six winning
   trades chosen for the video.
4. **Not in-play aware in a quantitative sense.** Plans say "green up when the favourite scores"
   but not what price to expect, so you cannot evaluate the plan's payoff before kick-off. The
   product is pre-match only; reviewers confirm live adjustments are on you.
5. **LLM-generated text, with the failure modes that implies.** Reviewers report mismatched teams
   and odd prices occasionally. A system that writes a fresh "custom strategy" per match is also
   impossible to evaluate statistically: the same inputs can give different advice.
6. **Closed data, closed model, high price.** You cannot see the inputs, adjust the time decay,
   add a league, or test a strategy of your own. At several hundred pounds a quarter you are
   renting a black box.
7. **Marketing claims that do not survive inspection.** "It's not going to hallucinate" and "AI
   which thinks exactly like a football trader" are not properties any current system has.
   Treat the "maths-based AI" framing as a chat model with a stats feed in context.

## 3. How TradeScout is built to be better

| Requirement | Trading Genie | TradeScout |
|---|---|---|
| Pull every fixture of the day | yes, browse by country | yes, one command, every league in the feed |
| Rank matches across the whole day | no | yes: one ranked table, `--per-match` for best idea per game |
| Probability the trade pays off | prose only | explicit hit probability per idea, calibrated against history |
| Expected value / stake | none | expected ROI per unit risked, quarter-Kelly stake |
| In-play exit prices known pre-match | no | yes, from conditional scoreline pricing at any minute |
| Strategy library | ~10 named strategies, LLM-described | 7 strategies as code with scenario-tree payoffs; add your own in one file |
| Track record | none published | walk-forward backtest, strike vs predicted, ROI by rank band, re-runnable |
| Determinism / auditability | varies per prompt | same inputs, same output; every number traceable to a model parameter |
| Market edge | not measured | model probability vs exchange price when Betfair is connected |
| Data | closed feed | open: openfootball (bundled), football-data.org, football-data.co.uk, Betfair |
| Cost | hundreds of pounds per quarter | free, MIT |
| Mobile app, video vault, chat UI | yes | no (CLI + HTML report) |

### 3.1 How the ranking works

The Genie answers "how should I trade this match?". TradeScout answers "which trades should I
take today?", which needs three things the Genie does not have: a probability model, a payoff
model, and a feedback loop.

**Probability model.** A time-weighted Dixon-Coles goal model fitted to the last ~900 days of
results. It produces a full scoreline distribution, so every market probability (1X2, O/U at
every line, BTTS, correct scores, half-time scores) is internally consistent. A goal-timing model
adds *when*: P(goal before 15'/30'/45'/70') and who scores first. Both together give in-play
conditional prices: "if it is 1-0 to the favourite on 25 minutes, the draw is fair at 9.7".

**Payoff model.** Each strategy is a scenario tree. Lay the Draw, for example, enumerates
"favourite scores first in 0-15', 15-30', ... , underdog scores first in each band, still 0-0 on
70'". Each leaf has a probability from the timing model and a profit from the entry price and the
conditional exit price, with a 3% friction haircut and 2% commission. Summing gives expected ROI
per unit risked; the positive leaves give the probability the plan pays off. This is also why
TradeScout can say *before kick-off* what each exit should trade at.

**Feedback loop.** The backtester replays every match day with no look-ahead, settles each
strategy against the real result, and stores strike rate vs predicted per strategy and league.
The live scorer shrinks today's hit probability towards that history, so a strategy that has
under-delivered in Serie A is marked down in Serie A no matter how pretty today's numbers look.

**Score.** `50 + 150 * calibrated expected ROI + 100 * market edge`, scaled by data confidence
(effective matches behind both ratings) and league liquidity, clipped to 0-100. 50 is break-even
after friction. The backtest shows the score is monotone in both strike rate and ROI, which is
the minimum a ranking must satisfy and which the Genie cannot demonstrate.

### 3.2 What the daily run looks like

```
tradescout scan                       # live fixtures + Betfair prices if keys are set
tradescout scan --sort hit --top 10   # highest calibrated strike rate first
tradescout scan --html today.html     # sortable report with every plan expanded
tradescout match "Home" "Away"        # one match, every strategy, full numbers
```

Each idea carries the Genie-style plan (entry, insurance, scenario exits, stop) **plus** the
numbers behind it: hit probability, calibrated hit probability, expected ROI, win/loss size,
stake, confidence, warnings (thin data, promoted team, no price feed).

### 3.3 What is deliberately not copied

* **No chat interface and no generated prose.** Plans are templated from the numbers. That loses
  the "AI assistant" feel and gains repeatability. An LLM narration layer could be added on top
  without touching the ranking.
* **No claim of a live xG feed yet.** The model uses results. Understat/FBref xG is a planned
  input to the attack/defence ratings; it is not required for the ranking to work.
* **No mobile app, no video library.** Out of scope for a ranking engine.

### 3.4 Honest limitations of this first version

* **Edge needs a price feed.** Without Betfair connected, prices are model-fair less a typical
  overround and the ranking cannot see market value. The bundled backtest therefore measures
  calibration and ranking quality, not profit against the market. Hook up Betfair or
  football-data.co.uk closing odds before believing any ROI figure.
* **Goal minutes are inferred in the backtest.** The bundled data has half-time and full-time
  scores only, so timing strategies are settled as an expectation over the first-goal minute.
  Minute-level feeds (football-data.org, API-Football) make this exact.
* **Six leagues bundled.** The architecture supports any competition the feeds carry; the Genie
  advertises 25+.
* **In-play exits are model prices.** Real markets overreact to goals and dry up in thin leagues.
  The friction parameter is a crude stand-in for that until live price paths are recorded.
* **Lineups, injuries, motivation, weather are not modelled.** Form via time decay is the only
  proxy. A trader should still sanity-check the top of the list.

## 4. Roadmap

1. Record Betfair pre-match and in-play prices for every scanned match, so exit-price assumptions
   and edge are measured, not modelled.
2. Add xG-weighted ratings (Understat) and a lineup-strength adjustment.
3. Add the remaining Genie-style strategies as scenario trees (half-time LTD, Over 1.5 second
   half, lay the favourite, drip-backing Fireball/Snowball variants) and a config file for
   personal strategy parameters.
4. Nightly job: scan, write HTML, push a notification with the top five.
5. Optional LLM narration of each plan, strictly from the computed numbers.
