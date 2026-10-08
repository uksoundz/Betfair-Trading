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
3. In the app, **Settings** > *Betfair*: enter the application key, your Betfair username and
   password. Press **Save**, then **Test login**. These are stored only in a file called `.env`
   inside the app folder on your computer. Never share that file.

With Betfair connected:

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
* **Live**: the bet slip gains a red **Place bets on Betfair** button for ideas marked TRADE. You
  see every order and the total at risk, then confirm. Orders are limit orders at the plan price
  and lapse at the start if unmatched. A daily cap, per-trade and exposure caps, daily and weekly
  loss limits all apply. Only the pre-match legs are placed; the in-play exits and stops are yours.

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
* `Betfair login failed`: check the key, username and password, and that the account is not
  locked. The Test login button shows the exact message.
* Every idea is RESEARCH: Betfair is not connected, or its prices could not be fetched for that
  fixture. Check the pill at the top right.
