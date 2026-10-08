# Getting started, step by step

You do not need to know any programming. There are three stages. Stage 1 works with nothing but
the code. Stages 2 and 3 are optional upgrades you can do later.

---

## The app

Once installed, double-click **`app.bat`** (or type `tradescout app`). Your browser opens on
TradeScout:

* **Pick any date** with the calendar at the top: today, this weekend, next month, or a past
  Saturday. Press **Scan this day**.
* **Matches**: every fixture with its best score. Click one for the model's view in plain English,
  then each strategy that fits with a colour-coded plan: Before kick-off, In play, Get out, Stop loss,
  and a Why section.
* **Top picks**: the day's ranked shortlist. Choose how many, one per match or not, and whether to
  rank by score, chance of paying off, expected return or market edge.
* **By strategy**: pick Lay the Draw (or any other) and see which matches suit it today, plus when to
  use it and when to avoid it.
* **Past dates** show the real result and whether each plan paid off, so you can check the tool
  against reality before risking anything.

Keep the black window open while you use the app; close it to stop.

## Stage 1: run it on the built-in data (10 minutes)

### Windows: the double-click way (recommended)

1. On the GitHub page click the green **Code** button, then **Download ZIP**. Unzip it.
2. Open the unzipped folder. Double-click **`install.bat`**. A black window appears, installs
   Python if it is missing, then installs TradeScout. Wait for "Done", press any key.
   * If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.
     The files are plain text; right-click, Edit to read them.
3. Double-click **`app.bat`**. The app opens in your browser. Pick 8 November 2025 in the date
   box and press Scan this day to see a full Saturday with results.
4. Later: double-click **`setup.bat`** to enter your keys (Stage 2). After that `app.bat` scans
   real fixtures for any date you pick.

If you prefer typing commands, or you are on a Mac, carry on below.

### Step 1. Install Python

Python is the program that runs TradeScout.

* **Windows:** go to https://www.python.org/downloads/ and click the big yellow "Download Python"
  button. Run the installer. **Tick the box that says "Add python.exe to PATH"** before clicking
  Install. This matters.
  * Newer Windows machines come with a "Python install manager" (the `py` command) but no
    Python. In that case type `py install 3` in a command window first.
* **Mac:** go to https://www.python.org/downloads/ and download the macOS installer. Run it.

### Step 2. Download the code

On the GitHub page for this repository click the green **Code** button, then **Download ZIP**.
Unzip it. You now have a folder called something like `Betfair-Trading`. Remember where it is,
for example `Documents\Betfair Trading\Betfair-Trading`.

### Step 3. Open a command window in that folder

* **Windows:** open the folder in File Explorer, click in the address bar at the top, type `cmd`
  and press Enter. A black window opens.
* **Mac:** open Terminal (press Cmd+Space, type Terminal, press Enter). Type `cd ` with a space
  after it, drag the folder from Finder into the Terminal window, press Enter.

Everything below is typed into that window, one line at a time, pressing Enter after each.

### Step 4. Install TradeScout

Windows:
```
py -m pip install -e .
```
Mac:
```
python3 -m pip install -e .
```

**The dot at the end matters.** It means "install the code in this folder". Wait for it to
finish (a minute or so).

If Windows says `'pip' is not recognized`, always use `py -m pip ...` instead of `pip ...`.
If `tradescout` is not recognised afterwards, use `py -m tradescout.cli` in place of
`tradescout` in every command below, or just use the `.bat` files.

### Step 5. Try it

Replay a Saturday from last season and see how the picks did:

```
tradescout scan --date 2025-11-08 --show-results
```

You should see a ranked table of trade ideas, and under it each idea with the real score and
whether it paid off.

Other things to try:

```
tradescout scan --date 2025-11-08 --html today.html
```
makes a web page called `today.html` in the folder. Double-click it to open it in your browser.
Click any row to expand the full trading plan.

```
tradescout match "Chelsea FC" "Wolverhampton Wanderers FC" --date 2025-11-08
```
shows everything about one match.

Team names must match the spelling in the data (`Arsenal FC`, `Manchester United FC`,
`FC Bayern München`). If unsure, run a scan for that date first and copy the name from the table.

That is Stage 1 done. Everything so far uses the match results stored inside the folder, which
run up to the end of the 2025-26 season.

---

## Stage 2: today's real fixtures (5 minutes, free)

To scan today's matches the tool needs a fixture list from the internet. A free account at
football-data.org provides it.

### Step 1. Get the free key

1. Go to https://www.football-data.org/client/register
2. Enter your name and email, click Register.
3. Check your email. The message contains a long code of letters and numbers. That is your
   **API key**. It is just a password that identifies you to their website.

### Step 2. Give the key to TradeScout

In the command window type:

```
tradescout setup
```

It asks for the football-data.org key: paste it and press Enter. Press Enter to skip the Betfair
questions for now. Enter your bank size (how much money you trade with) or press Enter for 1000.

That saves a small text file called `.env` in the folder. You only do this once.

### Step 3. Scan today

```
tradescout scan
```

Now it lists today's real fixtures from the Premier League, Championship, Bundesliga, La Liga,
Serie A, Ligue 1, Eredivisie, Primeira Liga and Champions League, with ranked trade ideas.

Tips:

* `tradescout scan --per-match` shows only the best idea for each match.
* `tradescout scan --sort hit` puts the ideas most likely to pay off at the top instead of the
  best expected value.
* `tradescout scan --html today.html` for the clickable web page.
* The free tier allows 10 requests a minute. One scan uses one or two, so this is plenty.

### Step 4. Keeping the ratings fresh

The team ratings are learned from past results. `tradescout scan` downloads this season's latest
results automatically once a day. To force it: `tradescout refresh-data`

---

## Stage 3: real Betfair prices (optional)

Without this the tool shows the prices it *thinks* are fair. With it, the tool compares its own
numbers against the actual exchange prices and the Edge column fills in. Edge is where the money
is, so this is worth doing once you are comfortable.

### Step 1. Get a Betfair Application Key

1. You need a normal Betfair account, logged in and funded.
2. Go to https://developer.betfair.com/get-started/ and follow "Get an Application Key".
   In short: visit the Accounts API demo page linked there, log in, and press "createDeveloperAppKeys".
3. You get two keys. The **Delayed** one is free and fine for TradeScout. (The "Live" key costs a
   one-off activation fee and gives prices with no delay. Not needed to start.)

### Step 2. Tell TradeScout

```
tradescout setup
```

Enter the football-data.org key again (or press Enter to keep skipping), then the Betfair
application key, your Betfair username and password. These are saved only in the `.env` file on
your own computer. Never send that file to anyone.

### Step 3. Scan

```
tradescout scan
```

The table now shows real prices, the Edge column, and a note of how much money is matched in
each market. Ideas with positive edge in liquid markets rise to the top.

---

## Reading the table

| Column | Meaning |
|---|---|
| Score | 0 to 100. 50 is break-even. Higher is better. Green at 60+. |
| Strategy | The trade. Each one has a step-by-step plan underneath (use `--html` or `match` to read it). |
| Hit% | How often the model thinks this plan ends in profit. |
| Cal% | Same, after adjusting for how this strategy has actually performed in this league historically. |
| Price | The entry price to look for. |
| Edge | Model probability minus what the market price implies. Positive is good. Blank without Betfair. |
| ROI | Expected profit per £1 risked. |
| Stake% | Suggested stake as a percentage of your bank. Deliberately small. |
| Conf | How much data sits behind both teams. Below 0.5 means be careful. |

## Rules of thumb

* Start by paper trading: write down the top three each day and check the results. The
  `--show-results` replay lets you do this on past dates in seconds.
* Prefer high Score **and** high Conf in a big league. A great number on a promoted team with
  six games of data is not a great number.
* The plan tells you what to do when a goal goes in and when to stop out. Decide that before kick-off.
* Nothing here is a tip. It is a ranked research sheet. The in-play decisions are still yours.

## If something goes wrong

* `'pip' is not recognized`: type `py -m pip install -e .` (Windows) or
  `python3 -m pip install -e .` (Mac). Do not forget the dot.
* `py install -e` shows a long help page: that is the Python *install manager*, not pip. You
  typed `py install` instead of `py -m pip install`. Run `py install 3` once to get Python, then
  `py -m pip install -e .`
* `tradescout is not recognized`: close the command window and open a new one in the folder, or
  run `py -m tradescout.cli scan` instead of `tradescout scan`, or use the `.bat` files.
* `No matches in the covered leagues on ...`: there are simply no games today in the leagues
  covered (international breaks are the usual reason: early September, early October, mid
  November, late March). The tool lists the next match days; copy the suggested command.
* `401` or `403` from football-data.org: the key was pasted wrongly. Run `tradescout setup` again.
* Betfair login failed: check username/password, and that your account is not set to require
  two-factor on API login.
