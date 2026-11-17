---
name: trader-entry
description: >-
  Entry Planner. Writes one exact entry rule for one stock today, before
  anything is bought: an opening range breakout (first 5, 15 or 30 minutes),
  a break of yesterday's high, or the user's own level. Gives the trigger
  price, the most the order may pay (the limit), and the time the idea
  expires, says whether the stock already went through without them, and
  writes trader/trade.json for trader-exit, trader-timemachine and
  trader-budget. Use when the user says "plan the entry", "where do I get
  in", "opening range breakout", "buy the break of yesterday's high", "set a
  buy stop", "entry for AMD", or after /trader-scanner and /trader-news.
argument-hint: "[ticker] [orb|pdh|level]"
---

# trader-entry

The entry is written down before the trade, as a rule a tool can check and
replay, not a feeling at 9:52. It is step three of the one-trade flow:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker
```

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" AMD --setup orb --minutes 15 --write   # -> trader/trade.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" --from-scan --setup orb --write         # top pass in trader/scan.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" TSLA --setup pdh --valid-until 11:00 --write
python3 "${CLAUDE_SKILL_DIR}/entry.py" AMD --setup level --price 512.50 --write
```

## The setups

| setup | trigger | when |
| --- | --- | --- |
| `orb` | one cent over the high of the first N minutes (5, 15 or 30; default 15) | after the range closes, until `--valid-until` |
| `pdh` | one cent over yesterday's regular-session high | from the open until `--valid-until` |
| `level` | the user's price: a breakout (stop-limit) above the last price, a pullback (limit) below it | until `--valid-until` |

All three are buys. The limit is the trigger plus `--slip` percent (default
0.1 %, at least a cent): a stock that jumps past it is missed, not chased.
`--valid-until` defaults to 11:30 New York: a breakout that has not
happened by late morning is a different trade.

## Status

| status | means |
| --- | --- |
| WAITING | the trigger is set and has not traded yet: the plan is live |
| NOT READY | the opening range has not finished (an `orb` before 9:45 for 15 minutes) |
| ALREADY TRIGGERED | it went through the trigger earlier today. The move happened without the user; buying now is chasing, not this plan |
| MISSED | it opened past the limit |
| EXPIRED | no trigger before `--valid-until`, or the session ended |

Only WAITING (or NOT READY, once it is ready) goes on to trader-exit and the
budget. trader-budget refuses to size any other status.

## Steps

1. Take the ticker from `/trader-scanner` (`--from-scan`) or from the user.
   Make sure `/trader-news` was checked for it today.
2. Ask which setup, in plain words ("buy if it breaks the first 15 minutes'
   high", "buy if it takes out yesterday's high"). Run it with `--write`.
3. Read the plan back: the range or yesterday's high, the trigger, the limit,
   the expiry time and the status.
4. If the status is not WAITING, say so and stop: no plan, no trade.
5. Hand over to `/trader-exit`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/scan.json` | with `--from-scan`, the first ticker that passed |
| `trader/trade.json` | the plan: symbol, setup, entry (trigger, limit, status), 14-day ATR |

Bars: Yahoo Finance's free 5-minute chart (unofficial, delayed, regular
session only). `--offline DIR` reads `DIR/yahoo-5m-<TICKER>.json`. Free bars
run late, so check the trigger against the broker's live quote before
saying yes to an order.

## Example: AMD on 2026-11-17

At 10:00 New York, `entry.py AMD --setup orb --minutes 15`: the opening range
from 9:30 to 9:45 was $500.50 to $511.85. ENTRY: buy if it trades at $511.86
or higher, paying at most $512.37 (stop-limit), valid until 11:30. Status
WAITING. AMD did not trade there before 11:30 that day: run at 11:30 or later,
the plan says EXPIRED. After the close it plans the next session instead,
so a `pdh` plan can be written the evening before.

## What it never does

It never places or proposes an order, never moves a trigger to catch a move
that already happened, never plans a short, and never says a setup will work.

## Safety

- Education and tooling, not financial advice. An entry rule is a plan, not a signal that it will work.
- Day trading is risky. Most day traders lose money, and costs and slippage add up fast.
- In the US, a margin account with under $25,000 that makes four or more day trades in five
  business days is flagged as a pattern day trader (FINRA). A cash account must use settled cash.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
