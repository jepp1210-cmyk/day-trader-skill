---
name: trader-budget
description: >-
  Budget Manager. Sizes every position to your budget before anything is
  bought: keeps a cash buffer, caps any one stock (the excess is shared out to
  the others), drops slices under your minimum order, rounds so every dollar
  is used, supports fractional or whole shares, and writes the proposed buy
  orders to trader/orders.json in the exact shape of Alpaca's
  place_stock_order (notional as a string). Nothing is placed. Use when the
  user says "I have $5,000", "size my trades", "how much of each should I
  buy", "manage my budget", "split my money", "keep 10% in cash", "no stock
  over 25%", "set my rules", "how many shares for this day trade", "size
  the trade to my risk", or after /trader-copycat writes targets or
  /trader-timemachine replays a day trade.
argument-hint: "[budget in dollars]"
---

# trader-budget

The Budget Manager turns "copy this portfolio" into "buy exactly this much of
each", inside rules the user sets once. It writes proposals only. trader-risk
checks them and trader-broker asks the user yes or no for each one.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                       # trader/targets.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --budget 5000 --write # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --whole-shares --prices trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights AAPL=22.04,AXP=17.14,GOOGL=12.62 --json
python3 "${CLAUDE_SKILL_DIR}/size.py" --trade --write          # ONE day trade from trader/trade.json
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/targets.json` | the weights to copy, written by trader-copycat (`targets.example.json` here) |
| `trader/prices.json` | only for whole shares or share counts (trader-alerts writes it) |
| `trader/orders.json` | the proposed buy orders it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how much money this is for. Only money they can afford to lose.
- **cash_buffer_pct**: how much stays in cash (default 10).
- **max_position_pct**: the most any one stock may be, as a percent of the budget (default 25).
- **max_order_usd** and **min_order_usd**: the biggest and smallest single order (2000 and 5).
- **fractional**: true if the broker buys fractional shares (Alpaca does); false for whole shares.
- **blocked**: tickers they never want to own.
- Keep **paper** true until they have watched it work on a paper account.

## Step 2: size it

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

The maths, in order: the cash buffer comes off the top; the weights are
renormalised to 100 over the stocks that are left; any stock over the cap is
pinned at the cap and its excess is shared over the others by weight, again
until nobody is over; a slice under the minimum order is dropped and its
weight shared out; amounts are rounded to whole dollars and the rounding
dollar goes to the biggest stock that is not capped, so nothing is left
idle by accident. Money that truly cannot be invested (every stock at its
cap, or whole shares that do not divide evenly) is shown as "left idle".

## Step 3: show the user

Show the table: ticker, weight, dollars, which stock hit the cap, the cash
buffer and what is left idle. Say where the weights came from and how old
they are (a 13F is up to 45 days old when it is filed). Then hand over to
`/trader-risk`.

## Example: the reel

Berkshire Hathaway's Q2 2026 13F (filed 2026-08-14), top five: Apple 22.04%,
American Express 17.14%, Alphabet 12.62%, Coca-Cola 10.86%, Bank of America
9.20%. Renormalised over those five: 30.67 / 23.85 / 17.56 / 15.11 / 12.80.
A $5,000 budget, a 10% cash buffer and a 25% cap:

| stock | dollars | |
| --- | --- | --- |
| AAPL | $1,250 | capped at 25% |
| AXP | $1,119 | |
| GOOGL | $823 | |
| KO | $708 | |
| BAC | $600 | |
| cash buffer | $500 | |
| left idle | $0 | |

The first order it writes is
`{"symbol": "AAPL", "side": "buy", "notional": "1250", "type": "market", "time_in_force": "day"}`.
Keep the weights to two decimals or more: weights already rounded to one
decimal (30.7 / 23.9 / ...) can move a slice by a dollar.

## One day trade: `--trade`

For the one-trade flow (scanner, news, entry, exit, timemachine, budget,
risk, broker) the size comes from the risk, not from weights:

- **risk_per_trade_pct** (default 1, at most 5): the most one trade may lose
  at its stop, as a percent of the budget. Optional **max_loss_per_trade_usd**
  caps it in dollars.
- Shares are the smallest of: that risk over the worst-case risk per share
  (the limit minus the stop), the `max_position_pct` cap, the money left after
  the cash buffer, and `max_order_usd`. It says which one binds.
- Whole shares only, and one bracket order: the entry (stop-limit for a
  breakout, limit for a pullback), a take-profit at the target and a
  stop-loss at the stop, `time_in_force` day. The `plan` block carries the
  session date and the flat-by time for trader-risk and trader-alerts.
- It refuses a trade with no exit, or an entry that is not WAITING (already
  triggered, missed or expired). It warns when the trade was not replayed by
  trader-timemachine, when the replay lost money, and when the replay has
  under 30 trades. Say every warning to the user.

AMD on 2026-11-17 with a $5,000 budget and 1 % risk ($50): worst-case risk
$11.88 a share allows 4.21 shares, but the 25 % cap allows 2.44, so the cap
binds: **BUY 2 shares**, trigger $511.86, limit $512.37, take profit $534.60,
stop loss $500.49, about $23.76 lost at the stop, flat by 15:55.

Alpaca's documentation describes a bracket as an entry plus a take-profit
and a stop-loss. Check on the paper account that it accepts a stop-limit
entry for a bracket before relying on it; if it is refused, record the
result as failed and use a limit entry once the trigger has traded instead.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget.
- Every number comes from the config, the targets or the prices. Nothing is guessed.
- No projections: never say what the positions will be worth or earn.
- Whole shares need real prices from `trader/prices.json`; never invent a price.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, and never sizes options, margin or short positions.

## Safety

- Education and tooling, not financial advice. Nothing here predicts prices or promises returns.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
