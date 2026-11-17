---
name: trader-exit
description: >-
  Exit Planner. Adds the three ways out of a day trade to trader/trade.json
  before anything is bought: the stop (one cent under the opening range low,
  N x the 14-day ATR, a percent, or the user's price), the target (R times
  the planned risk, or a price) and the flat-by time (minutes before the
  close, 12:55 on a 1:00 p.m. early close). Works out the risk per share at
  the trigger and at the worst fill, which trader-budget sizes from. Use when
  the user says "where's my stop", "set a stop and target", "2R target",
  "exit plan", "when do I get out", "flat by the close", or after
  /trader-entry.
argument-hint: "[range|atr|pct|price] [R]"
---

# trader-exit

Every day trade has its exit written before its entry is placed: where it is
wrong (the stop), where it is good enough (the target), and when it is over
no matter what (flat by the close). Step four of the one-trade flow:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker
alerts: stop, target, flat by the close
```

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" --write                          # range-low stop, 2R, flat 5 min before the close
python3 "${CLAUDE_SKILL_DIR}/exit.py" --stop atr --atr-mult 0.5 --r 2 --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" --stop pct --pct 1 --r 1.5 --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" --stop price --stop-price 498.50 --target-price 530 --write
```

## The pieces

| piece | rule | default |
| --- | --- | --- |
| stop `range` | one cent under the opening range low | the default for `orb` setups |
| stop `atr` | the trigger minus `--atr-mult` x the 14-day ATR | 0.5; the default when there is no range (`pdh`, `level`) |
| stop `pct` | the trigger minus `--pct` percent | 1 |
| stop `price` | the user's price | |
| target | the trigger plus `--r` x (trigger minus stop), or `--target-price` | 2R |
| flat by | `--flat-minutes` before the close, from the NYSE calendar | 5: 15:55, or 12:55 on an early close |

R is the planned risk per share: trigger minus stop. The worst-case risk per
share uses the limit (the most the entry can pay); trader-budget sizes from
that one, so a bad fill still keeps the loss at the stop inside the budget.

## Steps

1. Ask how the user wants out, in their words, and run `exit.py --write`.
2. Read it back: stop, target, R, flat-by time, risk per share planned and
   worst case. Say the notes it prints (a stop wider than a whole day's range,
   or tighter than normal noise).
3. Say once, plainly: a stop is a plan, not a guarantee. A gap, a halt or a
   fast market can fill far worse than the stop.
4. Hand over to `/trader-timemachine`. Changing the exit clears an earlier
   replay, because the replay was for different rules.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/trade.json` | reads the entry, writes the `exit` block (rules, stop, target, flat_by, risk) |

No network: it uses the range and the ATR that trader-entry saved.

## Example: AMD on 2026-11-17

The 15-minute range was $500.50 to $511.85; trigger $511.86, limit $512.37.
`exit.py --write`: stop $500.49 (one cent under the range low), target
$534.60 (2R), flat by 15:55. Risk $11.37 a share planned, $11.88 a share at
the worst fill; reward $22.74 a share.

## While the trade is open

`/trader-alerts` turns the exit into rules: `alerts.py add --from-trade`
adds the stop, the target and the flat-by alert for that one session. The
bracket order from trader-budget carries the stop and the target to the
broker; the flat-by time is the user's job, and the alert reminds them.

## What it never does

It never moves a stop further away once a trade is on, never places or
cancels an order, and never promises the target will be reached.

## Safety

- Education and tooling, not financial advice. A stop is a plan, not a guarantee: gaps and halts can fill worse.
- Day trading is risky. Most day traders lose money, and costs and slippage add up fast.
- In the US, a margin account with under $25,000 that makes four or more day trades in five
  business days is flagged as a pattern day trader (FINRA). A cash account must use settled cash.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
