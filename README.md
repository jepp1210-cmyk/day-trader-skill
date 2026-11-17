# The Trader skill

15 Claude skills that copy the investors you trust, watch the news and Congress, plan and replay a day trade, size every trade to your budget, and never place an order without your yes.

Free, MIT, no signup, no API key. The tools are plain Python that read public
data: SEC EDGAR, the House Clerk, news feeds.

One reverse-engineers the investor you want to copy, straight from their 13F
filing. Another monitors breaking news on every stock you hold. One manages your
budget: every dollar sized, a cash buffer kept, no stock over your cap. And the
craziest one is the Pelosi skill: it reads the stock trades members of Congress
disclose, the minute the filing posts.

Four more take one day trade from idea to order: scan your watchlist for
movers, write the entry and the exit down before you buy, then send those
exact rules back in time over the last 60 sessions to see what they did.

And no trade goes through until you say yes. Nothing in this pack can place an
order on its own. Paper trading is the default.

## Install

Paste this into Claude:

```
https://github.com/Jakeschincariol/trader-skill

install skill
```

Or as a plugin, in Claude Code:

```
/plugin marketplace add Jakeschincariol/trader-skill
/plugin install trader-skill@trader-skill
```

Claude Code namespaces plugin skills, so installed as a plugin they show up as
`/trader-skill:trader-copycat` and so on. Copy the folders instead if you want
plain `/trader-copycat`:

```bash
git clone https://github.com/Jakeschincariol/trader-skill.git
cp -r trader-skill/trader-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`. No Claude Code at all? Paste any single `SKILL.md` at the
top of a chat and it runs as a mode. You lose the Python tools, but the method
works.

The tools need Python 3.8 or newer. Nothing to pip install. SEC asks every
script to identify itself: put your name and email in `trader/config.json`
(`"sec_user_agent": "Your Name you@example.com"`) or in the `SEC_USER_AGENT`
environment variable.

## The fifteen

| command | job | what it does |
| --- | --- | --- |
| `/trader-copycat` | Reverse Engineer | Reads an investor's 13F-HR on SEC EDGAR and rebuilds their portfolio as weights you can copy, with what they bought, added, trimmed and sold since last quarter. |
| `/trader-news` | News Monitor | Watches headlines and SEC filings (8-Ks) for every stock you hold and flags what touches them. |
| `/trader-budget` | Budget Manager | Sizes every position to your budget, keeps a cash buffer and caps any one stock. |
| `/trader-pelosi` | Pelosi Tracker | Reads Congress's stock-trade disclosures (House PTRs) as soon as they post, turns each PDF into clean rows and alerts you once per new filing. |
| `/trader-analyst` | Earnings Analyst | Pulls each company's reported numbers from SEC XBRL (revenue, net income, diluted EPS, margins, growth) and its latest earnings release. |
| `/trader-screener` | Stock Screener | Screens companies against your own rules and ranks what passes. |
| `/trader-risk` | Risk Manager | Checks every proposed order against your limits and blocks anything over them. |
| `/trader-broker` | Order Executor | Turns an approved order into the exact ticket your brokerage connector receives, and records your yes. It never places anything itself. |
| `/trader-tax` | Tax Optimizer | Tracks cost-basis lots, gains and losses, tax-loss harvesting candidates and wash sales. |
| `/trader-alerts` | Price Alerts | Rings when a price target, a stop or the opening bell hits, with the real NYSE calendar. |
| `/trader-rebalance` | Rebalancer | Brings the portfolio back to its target weights when it drifts past your band. |
| `/trader-scanner` | Day-Trade Scanner | Ranks today's movers in your watchlist by gap and relative volume, with the ATR and liquidity rules each one passes or fails. |
| `/trader-entry` | Entry Planner | Writes one entry rule for today: an opening range breakout, a break of yesterday's high or your level, with the trigger, the limit and an expiry time. |
| `/trader-exit` | Exit Planner | Adds the stop, the target in R and the flat-by time before the close, and the risk per share the size comes from. |
| `/trader-timemachine` | Time Machine | Replays the exact entry and exit rules on the stock's past sessions, 5-minute bar by bar: win rate, average R, drawdown, losing streak. |

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
copycat -> budget -> risk -> broker                                           (copy an investor)
analyst -> screener -> budget -> risk -> broker                               (pick your own)
rebalance / tax -> risk -> broker                                             (maintain)
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by the close                                       (while it is open)
news / analyst / pelosi                                                       (on a schedule)
```

An example: copying Berkshire Hathaway with $5,000.

1. **`/trader-copycat`** "copy Berkshire's top 5". It finds Berkshire Hathaway
   (CIK 1067983), downloads its latest 13F-HR (Q2 2026, filed 2026-08-14) and
   reads every row: Apple 22.04 % of the reported portfolio, American Express
   17.14 %, Alphabet 12.62 % (both share classes), Coca-Cola 10.86 %, Bank of
   America 9.20 %. Since last quarter: new D.R. Horton, sold out of
   Constellation Brands. It writes `trader/targets.json`, the top 5
   renormalised: 30.67 / 23.85 / 17.56 / 15.11 / 12.80 %.
2. **`/trader-budget`** with a $5,000 budget, a 10 % cash buffer and a 25 %
   cap: Apple $1,250 (capped), American Express $1,119, Alphabet $823,
   Coca-Cola $708, Bank of America $600, $500 cash, $0 idle. It writes
   `trader/orders.json` as proposals.
3. **`/trader-risk`** checks all five against your limits: blocked tickers,
   order size, position size after the fill, the cash buffer, no options or
   margin. PASS or BLOCK, with the reason.
4. **`/trader-broker`** shows you each ticket: "Buy $1,250 of AAPL, market,
   day". You type yes for that exact order. Claude calls your brokerage
   connector (Alpaca's MCP server, paper account by default), and Claude's own
   "Allow once / Deny" card asks you one more time.
5. On a schedule: **`/trader-news`** flags headlines on what you hold,
   **`/trader-pelosi`** alerts you when a new disclosure posts, and
   **`/trader-alerts`** rings on your price levels.
6. Every quarter: **`/trader-copycat`** diffs the new 13F against the last one,
   and **`/trader-rebalance`** proposes the trades to match it again.

Another example: one day trade on AMD, 2026-11-17.

1. **`/trader-scanner`** on your watchlist. With a 0.4 % gap and 0.9x
   relative volume, AMD ranks first: +1.45 % gap, 0.96x volume, ATR 9.5 % of
   the price.
2. **`/trader-news`** on AMD: is the gap on news?
3. **`/trader-entry`** at 10:00, 15-minute opening range breakout: the range
   was $500.50 to $511.85, so buy if it trades at $511.86, paying at most
   $512.37, until 11:30.
4. **`/trader-exit`**: stop $500.49 under the range, target $534.60 (2R),
   flat by 15:55.
5. **`/trader-timemachine`**: the same rules on the 21 sessions before it
   made 13 trades, 6 wins, average -0.08R after costs. Too few trades to mean
   much, and they lost money. You hear that before you size anything.
6. **`/trader-budget --trade`** with $5,000 and 1 % risk: 2 shares (the 25 %
   cap binds), one bracket order, about $24 lost at the stop.
7. **`/trader-risk`**, then **`/trader-broker`**: one ticket, one yes.
8. **`/trader-alerts`** `add --from-trade`: it rings at the stop, at the
   target, and at 15:55 to close the trade, win or lose. AMD never reached
   $511.86 before 11:30 that day, so the plan simply expired: no trade.

## The tools

All standard-library Python. The data tools read public endpoints and are
polite about it (SEC's rule: identify yourself, at most 10 requests a second).

```bash
python3 trader-copycat/edgar13f.py find buffett                        # a filer's CIK
python3 trader-copycat/edgar13f.py filings 1067983                     # its 13F-HR filings
python3 trader-copycat/edgar13f.py --help                              # holdings, diff, copy
python3 trader-news/news.py --tickers AAPL AXP GOOGL KO BAC --hours 24 # headlines + 8-Ks
python3 trader-pelosi/ptr.py index --member pelosi                     # her PTRs this year
python3 trader-pelosi/ptr.py --help                                    # new, fetch, parse
python3 trader-analyst/facts.py AAPL                                   # quarters, growth, EPS
python3 trader-screener/screen.py trader/metrics.json --rules trader/rules.json
python3 trader-budget/size.py --budget 5000 --write                    # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-tax/lots.py harvest --min-loss 100                      # with wash-sale checks
python3 trader-alerts/alerts.py clock                                  # NYSE open or closed
python3 trader-rebalance/rebalance.py --band 5                         # the drift table
python3 trader-scanner/scan.py --tickers AAPL AMD NVDA TSLA --write    # -> trader/scan.json
python3 trader-entry/entry.py --from-scan --setup orb --write          # -> trader/trade.json
python3 trader-exit/exit.py --stop range --r 2 --write                 # stop, target, flat by
python3 trader-timemachine/backtest.py --write                         # replay the rules
python3 trader-budget/size.py --trade --write                          # one bracket order
python3 trader-alerts/alerts.py add --from-trade                       # stop, target, flat by
```

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline, on small fixtures cut from the real filings: Berkshire's
Q1 and Q2 2026 information tables, the House Clerk's 2026 index, real PTR text,
real XBRL facts and feeds.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation, it cannot see the future, and
copying an investor does not copy their results. You can lose money. Decide for
yourself, and talk to a licensed professional if you need advice.

**No trade goes through until you say yes.** Only `/trader-broker` can lead to
an order, and only after `/trader-risk` passes that exact order and you type yes
for it in the chat. Set your brokerage tool to "Needs approval" in Claude
(Customize, Connectors, tool permissions), never "Always allow", so Claude's
own "Allow once / Deny" card is a second check. No options, margin, short
selling or leverage unless you turn them on.

**Paper first.** `"paper": true` is the default in `config.example.json`, and
Alpaca's MCP server defaults to its paper account. Keys live in environment
variables, never in the chat.

**13F data is old and partial.** Funds file 45 days after each quarter ends, and
a 13F lists long positions in US-listed stocks and a few related securities:
no shorts, no cash, no foreign-listed shares. You see what they held, not what they hold today.

**Congress's trades are public, not inside information.** Members must disclose
trades over $1,000 within 45 days (the STOCK Act). The Pelosi skill reads those
public filings the minute they post, with the amounts as the ranges they are
filed in. It has no access to anything that is not public.

**The data comes from free public sources.** SEC EDGAR, the House Clerk, news
RSS and Yahoo Finance's chart endpoint (unofficial, and it can change). Check
anything that matters against your broker before you act on it.

**Day trading is risky.** Most people who day trade lose money, and costs
and slippage add up fast. A stop is a plan, not a guarantee: a gap or a halt
can fill far worse. In the US, a margin account under $25,000 that makes four
or more day trades in five business days is flagged as a pattern day trader;
a cash account can only trade with settled cash. A replay by
`/trader-timemachine` describes what the rules did on past bars, with
conservative fills; it does not predict the next trade.

**Taxes.** `/trader-tax` does the arithmetic on lots and wash-sale windows. It
is not tax advice.

## Files

```
trader-copycat/    SKILL.md, edgar13f.py, filers.json, share_classes.json, targets.example.json
trader-news/       SKILL.md, news.py
trader-budget/     SKILL.md, size.py, config.example.json, targets.example.json
trader-pelosi/     SKILL.md, ptr.py
trader-analyst/    SKILL.md, facts.py, metrics.example.json
trader-screener/   SKILL.md, screen.py, rules.example.json
trader-risk/       SKILL.md, check.py
trader-broker/     SKILL.md, ticket.py, claude_desktop_config.example.json
trader-tax/        SKILL.md, lots.py, portfolio.example.json
trader-alerts/     SKILL.md, alerts.py, alerts.example.json
trader-rebalance/  SKILL.md, rebalance.py
trader-scanner/    SKILL.md, scan.py
trader-entry/      SKILL.md, entry.py
trader-exit/       SKILL.md, exit.py
trader-timemachine/ SKILL.md, backtest.py (runs trader-entry and trader-exit)
tests/             unittest suite + offline fixtures
```

MIT. Made by [Jake Schincariol](https://opusjake.ai).
