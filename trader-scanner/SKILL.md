---
name: trader-scanner
description: >-
  Day-Trade Scanner. Finds today's movers in the user's own watchlist: the gap
  from yesterday's close, relative volume (time-adjusted during the session),
  the 14-day ATR as a percent of the price, the price range and the average
  dollar volume. Each ticker passes or fails each rule with the number, and
  the passes are ranked by gap x relative volume. Writes trader/scan.json for
  trader-news and trader-entry. Use when the user says "scan for day trades",
  "what's gapping", "what's moving today", "find me a trade", "premarket
  movers in my list", "which of my stocks are in play", or to start the
  one-trade flow.
argument-hint: "[tickers]"
---

# trader-scanner

The Scanner is step one of a single day trade:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by the close                                       (while it is open)
```

It only looks at a list the user gives it. It never searches the whole
market for "hot stocks" and never says what to buy. A scan is a list of
things to look at.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" --tickers AAPL AMD NVDA TSLA KO PLTR
python3 "${CLAUDE_SKILL_DIR}/scan.py" --watchlist trader/watchlist.json --write   # -> trader/scan.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" --holdings --direction up
python3 "${CLAUDE_SKILL_DIR}/scan.py" --tickers AMD TSLA --min-gap 1 --min-rvol 1.2 --json
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the user's tickers: `["AAPL", "AMD"]` or `{"tickers": [...]}` |
| `trader/config.json` | an optional `"scanner"` block with the rules below |
| `trader/scan.json` | every row, the rules used and the tickers that passed (`--write`) |

## The rules

| rule | default | passes when |
| --- | --- | --- |
| `min_gap_pct` | 2 | today's open is at least this far from yesterday's close, either way |
| `min_rvol` | 1.5 | today's volume over the 20-session average, scaled to how much of the session has passed |
| `min_atr_pct` | 1.5 | the 14-day average true range is at least this percent of the price |
| `min_price` / `max_price` | 5 / 2000 | the price is in range |
| `min_dollar_volume` | 20,000,000 | the stock trades at least this many dollars a day on average |
| `direction` | both | `up` keeps gap-ups only; this pack buys and does not short |

Before 9:30 a.m. New York there is no open yet, so every ticker shows "no gap
yet". Run it after the open (9:35 or later) for real gaps; relative volume
early in the session runs high because volume is heaviest at the open.

## Steps

1. Ask for the watchlist if there is none. Save it to `trader/watchlist.json`.
2. Run the scan with `--write`. Show the table: rank, ticker, gap, relative
   volume, ATR %, and for each fail the rule it failed by how much.
3. If nothing passed, say so plainly. No trade is a good outcome. Change a
   rule only when the user asks, and say what changed.
4. If something passed, hand the top one (or the one the user picks) to
   `/trader-news` first: a gap on news (earnings, a downgrade, an offering, a
   halt) is a different trade from a gap on nothing. Then `/trader-entry`.

## Data

Daily bars from Yahoo Finance's free chart endpoint
(`query1.finance.yahoo.com/v8/finance/chart/<TICKER>?range=3mo&interval=1d`),
unofficial and delayed, one request per ticker half a second apart.
`--offline DIR` reads `DIR/yahoo-1d-<TICKER>.json` instead. Your broker's
quote is the one that matters for an order.

## Example: 2026-11-17

After the close on 2026-11-17, `--tickers AAPL AMD NVDA TSLA KO PLTR` with
the default rules passes nothing: the biggest gap was AMD at +1.45 %, and the
heaviest relative volume was NVDA at 1.57x on a +0.49 % gap. With `--min-gap
0.4 --min-rvol 0.9`, AMD, NVDA and KO pass, AMD ranked first.

## What it never does

It never picks a stock from outside the user's list, never calls a scan
result a recommendation, never places or proposes an order, and never
predicts where a price is going.

## Safety

- Education and tooling, not financial advice. A scan is a list to look at, not a list to buy.
- Day trading is risky. Most day traders lose money, and costs and slippage add up fast.
- In the US, a margin account with under $25,000 that makes four or more day trades in five
  business days is flagged as a pattern day trader (FINRA). A cash account must use settled cash.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
