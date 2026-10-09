# Getting started, step by step

You do not need to know any programming. There are three stages. Stage 1 works with nothing but
the code. Stage 2 adds live football fixtures (free). Stage 3 connects Betfair, which is the only
way the app can ever call something a **TRADE**: without exchange prices every idea is marked
**RESEARCH** and no stake is suggested.

---

## Stage 1: run it on the built-in data (10 minutes)

### Windows: the double-click way (recommended)

1. On the GitHub page click the green **Code** button, then **Download ZIP**. Unzip it.
2. Open the unzipped folder. Double-click **`install.bat`**. A black window appears, installs
   Python if it is missing, then installs TradeScout. Wait for "Done", press any key.
   * If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.
3. Double-click **`app.bat`**. The app opens in your browser. Keep the black window open while you use it.
4. In the app, pick **Football** or **Tennis** at the top, then a date. Past dates show what
   really happened; the model is always fitted using only matches before that day.

### Mac, or if you prefer typing

Install Python from python.org, open Terminal in the folder, then:

```
python3 -m pip install -e .
tradescout app
```

---

## The app, screen by screen

* **Football | Tennis** switch at the top. Each sport has its own fixtures, model, strategies,
  Betfair markets and statistics.
* **Day strip and calendar.** Click any day. If today is blank the app jumps to the next day with
  matches.
* **Opportunities.** The ranked list. Every idea carries a decision:
  * **TRADE**: the conservative net edge after commission clears your threshold at a price and
    size the exchange is actually offering. A stake is suggested.
  * **NO TRADE**: the exchange prices it, but there is no proven advantage. No stake.
  * **RESEARCH**: no exchange price. The model's view is shown, nothing can be called value.
  Most days have few or no TRADEs. That is correct behaviour, not a fault. Use the filter to
  show TRADE only, priced ideas, or everything. Tick **both sports** to rank football and
  tennis together.
* **Matches.** Every fixture. Click one for the model's view in plain English and every strategy
  that fits, each with: the market, back or lay, the entry price and the maximum or minimum
  acceptable price, stake and maximum loss, why the model sees value, what must happen, when to
  get out, what to do if it goes wrong, how reliable it is (confidence, evidence, history), and
  when the prices were last seen.
* **By strategy.** Which matches suit one strategy today, when to use it and when to avoid it,
  and its out-of-sample record.
* **My picks.** Track any plan, or record a bet slip. Press **Update results** and picks settle
  against the real scores. Plans with in-play exits are settled at *modelled* exit prices and say so.
* **Performance.** Out-of-sample statistics for every strategy (trades, strike rate vs predicted,
  return per unit, confidence interval, profit factor, drawdown, losing run) with the evidence
  class stated, plus what the signals log has recorded so far.
* **Bankroll.** Open risk, realised profit and loss today and this week, drawdown, the risk
  limits in force, and a risk-of-ruin check.
* **Settings.** Keys, bank, staking style, commission rate, value threshold, betting mode, and
  which strategies are switched on.

---

## Stage 2: live football fixtures (5 minutes, free)

1. Go to https://www.football-data.org/client/register and enter your name and email.
2. They email you a long code. That code is an "API key": a password that proves to their
   website it is you.
3. In the app, open **Settings**, paste the code under *Fixtures feed*, press **Save**, then
   **Test connection**.

From then on, with Football selected, **Today** shows real fixtures from nine competitions and
results download automatically for the ratings.

---

## Stage 3: Betfair (needed for TRADE decisions, both sports)

1. You need a funded Betfair account.
2. Follow https://developer.betfair.com/get-started/ to create an **Application Key**. The free
   **Delayed** key is enough to start (prices a minute or so old). The Live key costs a one-off fee
   and is better once you trust the system.
3. In the app, **Settings** > *Betfair*: enter the application key, your Betfair **username** (not
   your e-mail address) and password. Press **Save**, then **Test login**. These are stored only in
   a file called `.env` inside the app folder on your computer. Never share that file.
4. Account with two-factor authentication? Betfair then refuses password logins from programs.
   Open *Certificate login* under the Betfair settings and follow the link to create a client
   certificate (two files); enter their paths and Save.
5. Italian, Spanish, Romanian, Swedish or Australian account? Pick the jurisdiction in the same
   panel, otherwise the login goes to the wrong Betfair site.

With Betfair connected:

* The pill at the top right reads **Exchange prices · 23/34 priced**: how many of the day's fixtures
  the exchange could be matched to and priced. Click it for the details: which fixtures could not be
  matched (with the nearest exchange event names), which are in play or suspended, the session
  state, and a **Run full diagnosis** button that lists every event the exchange has that day.
* Prices refresh themselves every minute while the app is open on today or a future day; the
  countdown is next to the date. **Refresh** pulls everything again straight away.
* Tennis fixtures come from Betfair's own ATP event list; football prices attach to each fixture.
* Each idea's **Value check** shows the model probability, the market-implied probability, the
  probability actually used (pulled towards the market), the live price, spread, how much of your
  size is available, and the net expected value after commission.
* Set **Commission** to the rate you actually pay (5% is the Betfair UK base rate; many accounts
  pay less).

---

## Betting modes (Settings > Betting)

* **Off**: review and copy only.
* **Paper**: the bet slip records what you would have placed, with the prices seen, in My picks.
  Run this for several weeks first.
* **Live**: every plan in the Matches view gets a **Place on Betfair** button. It opens the slip:
  every line with the live price, the size and the money at risk. Press **Place**, then confirm.
  Orders are limit orders at the plan price and lapse at the start if unmatched. A daily cap,
  per-trade and exposure caps, daily and weekly loss limits all apply. Only the pre-match legs are
  placed; the in-play exits and stops are yours.
  * An idea the app marks **TRADE** places directly after your confirmation.
  * A **NO TRADE** or **RESEARCH** idea can still be placed, but the slip asks you to tick an
    override first ("the app finds no edge at this price; this is my call") and My picks shows it
    with an *override* tag. That keeps the record honest: your overrides and the app's trades are
    counted separately.
  * When the button is off, the slip says exactly why: betting mode not Live, Betfair not connected,
    or no line could be found on the exchange.
  * The slip's default stake is the risk engine's advice, raised if needed to the smallest stake at
    which every leg clears Betfair's minimum (£2 a bet, or £1 when the payout reaches £10). It says
    when it has done that.

---

## Auto-trading a plan you have placed (optional)

Every plan says what to do in play: green up after the first goal, close if it is still 0-0 on 70
minutes, add the second half of a staged lay on 15 minutes, and so on. TradeScout can carry those
steps out for you, but only for a plan you have placed yourself and then armed.

1. Settings > Betting: tick **Allow auto-trading of plans I arm** and press that card's **Save**. Off
   by default.
2. Either place a plan through TradeScout's bet slip and press **Auto-trade…** (in the placement
   confirmation, on the plan in Matches, or next to it in My picks), or, for a bet you placed on the
   Betfair website or app, go to My picks and press **Auto-trade a bet I placed on Betfair…**. That
   lists the matched bets on your account with the plans each can follow (a lay of the draw can follow
   Lay the Draw, a back of Over 2.5 the Over 2.5 free-bet rule, and so on); pick one and press
   **Set up…**. Unmatched bets appear once they are matched.
3. The window lists exactly what will happen, in words, for that plan. Tick **Simulate only** to
   watch it run on live prices without sending anything (a good first step). Tick the confirmation
   and press **Arm**.
4. From kick-off, TradeScout checks the match every few seconds: the minute and score from Betfair's
   live scoreboard, the prices, and your matched bets on that plan. When a step's condition is met it
   places the order. The Auto-trading panel in My picks shows the state, your position (what you win
   or lose either way), which steps have run, and a log.
5. **Green up now**, **Disarm** and **Stop all auto-trading** are always available. Disarming leaves
   your position on Betfair exactly as it is.

Safety rules it always follows:

* A green up or free bet can only raise your worst case, never lower it.
* The only step that adds risk is a scale-in, and it never takes the plan above its own stake, counts
  against your daily cap, and never fires when the score is unknown.
* Nothing is sent while a market is suspended (after a goal). A wide spread is waited out for up to a
  minute. Unmatched in-play orders are cancelled after 15 seconds and the hedge is recomputed.
* If the live score is unavailable, steps triggered by goals wait, but the protective stops on the
  clock (for example "still 0-0 on 70 minutes: close") still happen.
* It only acts while TradeScout is open and the computer is awake. If you close it, your position
  simply runs to the result on Betfair.

Plans whose in-play steps need judgement or data the app does not have (the correct-score basket,
laying the favourite on a break of serve) cannot be armed; trade those by hand.

### With the free Delayed application key

The Delayed key shows prices up to three minutes old. That matters for greening up in play, but the
£499 Live key is not needed, because three things are not delayed: Betfair's live scoreboard (goals,
minute, sets), your order results (how much matched and at what price), and the matching itself
(Betfair always gives you the best price available, even if it is better than your limit).

When you arm a plan you choose how it acts:

* **Act for me.** Goals and the clock come from the live scoreboard as usual. To hedge, TradeScout
  waits about 20 seconds after a goal for the market to reopen, sends part of the hedge with a
  protective limit (about 10% beyond the old price), reads the real matched price from Betfair's
  reply, and sizes the rest from that real price. If nothing matches within 15 seconds it cancels and
  widens the limit, up to 25%. Every step is in the log so you can check it against Betfair.
* **Alert me.** TradeScout beeps, shows a desktop notification and a red banner on whatever tab you
  are on, with the step to take, a suggested stake, and a link that opens the market on Betfair. You
  press **Cash Out** in the Betfair app or website, which uses the live price. Keep the TradeScout tab
  open with the sound on. This is the most precise option on a Delayed key.

Small stakes: Betfair's minimum is £2 a bet (or £1 when the payout reaches £10). With a plan stake of
a few pounds the last part of a hedge can fall below that, so the position ends close to level rather
than exactly level (for example £4.50 if it wins and £0 if not, instead of £2.25 either way). The log
says when that happens.

### Tennis: arming an in-play entry (no bet before the match)

Some tennis plans have nothing to bet before the start. **Lay favourite after a clear first-set loss**
shows the gold **ARM** badge instead of TRADE: the edge, if it exists, only appears in play.

1. Open the match (Tennis, Matches) and press **Arm in-play entry…** on that plan.
2. The window says what will happen: if the favourite loses set 1 6-3 or wider, TradeScout lays them
   in Match Odds at the limit shown or lower (for example 3.30). Above that price there is no value,
   so nothing is placed. Set the liability (the most it can lose) and choose **Act for me**, **Alert
   me** (you place it on Betfair yourself) or **Simulate only**.
3. Tick the confirmation and press **Arm**. Nothing is bet now. The plan appears in My picks >
   Auto-trading as ARMED and goes LIVE when the match starts.
4. If the favourite wins set 1, or loses it 7-5, 6-4 or in a tiebreak, the plan ends with nothing
   placed. On a clear loss the order goes on at once and stays up for two minutes; whatever matched is
   held to the result, the rest is cancelled.

This works on the Delayed key: the trigger is the live scoreboard and the limit price is the value test,
so Betfair matches at the real price if it is within the limit. Keep TradeScout open through the first
set. Start with simulate or small stakes: how often Betfair offers the limit is not yet known.

Women's matches (WTA) are included for this plan and the set-betting plan. For women the plan also lays the
favourite after a narrow first-set loss (7-5, 6-4 or a tiebreak), at a lower limit, because that held up in the
women's data too.

**Break-point scalp (practice only).** Turn on "Break-point scalp" under Settings > Strategies to see it on
tennis cards with a **Practise in play…** button. At 15-40 or 0-40 it backs the receiver and greens up when the
game is broken, held or reaches deuce. The historical data shows no edge after costs, so it can only be armed in
**Simulate** or **Alert me** mode. It needs the live point score from Betfair's scoreboard; on a Delayed key use
Alert me and Cash Out yourself.

**Set betting value against match odds** is an ordinary pre-match plan (TRADE or NO TRADE): place it
from the slip like any other and it settles at the result.

---

## Which accounts you need

| Account | Cost | Needed for |
|---|---|---|
| football-data.org | free | live football fixtures and results |
| Betfair account + application key | free (Delayed) / one-off fee (Live) | exchange prices, TRADE decisions, live tennis fixtures, placing bets |
| Nothing else | | the bundled results, models, backtests and replay all work offline |

Optional, not integrated: a licensed tennis data feed for commercial use (the bundled ATP results
are a research mirror), and expected-goals data for football (not available free in a form that can
be bundled).

---

## If something goes wrong

* `'pip' is not recognized`: use `py -m pip install -e .` (Windows). Do not forget the dot.
* `No matches in the covered competitions on ...`: a blank day (international break, or tennis
  replay data ending in January 2026 without Betfair). Pick another day from the strip.
* `football-data.org limit reached`: the free plan allows 10 requests a minute. Wait a minute.
* **Betfair not connected** (amber pill): Settings > Betfair shows the exact message. The usual
  ones: wrong password or an e-mail address typed as the username; two-factor authentication on
  the account (use the certificate login); the application key not yet activated for the betting
  API; the account locked after failed attempts. Fix it, Save, then **Reconnect**.
* **Prices do not appear, or stop updating**: click the pill at the top right. It tells you whether
  the session is live, how many fixtures were priced, and lists the ones that could not be matched
  to an exchange event (with the nearest names on the exchange) or that are in play. Sessions that
  expire are renewed automatically; if Betfair rejects the renewal, press **Reconnect**. From a
  terminal, `tradescout betfair-check --date 2026-10-10` prints the same diagnosis.
* **No Place button on a plan**: open the slip anyway; it lists the reasons (betting mode not Live,
  Betfair not connected, the idea is NO TRADE and needs the override, no line found on the
  exchange, stake below the minimum).
* Every idea is RESEARCH: no exchange prices are attached for that day. For a past day that is
  normal (pre-match markets are gone). For today, see the two points above.
* **Delayed application key**: the free key's prices are up to three minutes old; the app says so
  on every priced card and in the slip. Orders still go through. The Live key removes the delay.
