---
name: trader-timemachine
description: >-
  The Time Machine. Before a day trade is sized, replays the exact entry and
  exit rules in trader/trade.json on the same stock's past sessions, five
  minute bar by five-minute bar, using the same code as trader-entry and
  trader-exit: how often the rules triggered, wins and losses, the average
  and total result in R, the worst drawdown, the longest losing streak and
  how each trade ended (stop, target, flat by the close), with slippage and
  costs. Conservative where the bars cannot tell what came first. Use when
  the user says "backtest this", "would this have worked", "replay it",
  "test the setup", "what did this do last month", "time machine", or after
  /trader-exit and before /trader-budget.
argument-hint: "[days]"
---

# trader-timemachine

The Time Machine asks one question before money is sized: what did these
exact rules do on this stock lately? Step five of the one-trade flow:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker
```

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/backtest.py"                         # trader/trade.json, up to 60 past sessions
python3 "${CLAUDE_SKILL_DIR}/backtest.py" --days 20 --cost 0.02
python3 "${CLAUDE_SKILL_DIR}/backtest.py" --risk-usd 50 --write   # saves the summary into the trade
```

It loads `trader-entry/entry.py` and `trader-exit/exit.py` from the folders
next to it, so the replay runs the same rules as the plan. Install the whole
pack.

## How a day is replayed

- The entry rule runs on that day's bars: the opening range or yesterday's
  high, the trigger, the limit, the expiry time.
- It fills at the trigger, or at the bar's open if the bar opened past the
  trigger. A bar that opened past the limit is a miss, not a fill.
- The stop, target and flat-by time come from the same exit rules with that
  day's numbers (that day's range low, the ATR as of the day before, that
  day's close, early closes included).
- Conservative calls, because a 5-minute bar hides what came first:
  a fill bar that reaches the stop is a loss; a bar that touches both the
  stop and the target is the stop; a gap through the stop exits at the open.
- Flat at the open of the first bar at or after the flat-by time.
- `--cost` dollars a share (default 0.01) is charged on the way in and out.
- The plan's own session is left out.

A `level` setup, a fixed stop price or a fixed target price is one day's
number and cannot be replayed: it says so and stops.

## Reading the result

Show the table and the summary lines, then say three things plainly:

1. **The sample is small.** 60 sessions of one stock is a handful of trades.
   Under 30 trades it says "too few to tell skill from luck", and that stands.
2. **If the average is at or under 0R after costs, these rules lost money
   here.** Say it. trader-budget repeats it as a warning.
3. **Past bars do not predict the next trade.** No news, halts or fills at
   the size the user would trade are modelled.

The user decides. Never call a replay a green light.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/trade.json` | the rules in; with `--write`, the summary saved as `backtest` |
| `trader/config.json` | `budget` x `risk_per_trade_pct` for the dollar line |
| `trader/backtest.json` | every replayed day (`--write`) |

Bars: Yahoo Finance's free 5-minute chart, about 60 days deep (unofficial,
delayed). `--offline DIR` reads `DIR/yahoo-5m-<TICKER>.json`.

## Example: AMD, 15-minute opening range breakout

Range-low stop, 2R target, flat 5 minutes before the close, valid until 11:30,
1 cent a share each way, on the 21 sessions from 2026-10-19 to 2026-11-16:
13 trades, 6 wins and 7 losses, win rate 46.2 %, average -0.08R, total
-1.03R, worst drawdown -3.80R, longest losing streak 4. Exits: flat 9,
stop 3, target 1. Too few trades to mean much, and these rules lost money
on AMD over that window.

## What it never does

It never says a strategy works, never fits the rules to the past to make a
better number (changing a rule to improve the replay is curve fitting; say
so if the user asks), and never places or proposes an order.

## Safety

- Education and tooling, not financial advice. A replay describes the past; it does not predict the next trade.
- Day trading is risky. Most day traders lose money, and costs and slippage add up fast.
- In the US, a margin account with under $25,000 that makes four or more day trades in five
  business days is flagged as a pattern day trader (FINRA). A cash account must use settled cash.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
