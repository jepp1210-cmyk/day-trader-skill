#!/usr/bin/env python3
"""The time machine for trader-timemachine. Standard library only.

Before a day trade is sized, it replays the EXACT entry and exit rules in
trader/trade.json on the same stock's past sessions, five-minute bar by
five-minute bar, and says what those rules would have done: how often they
triggered, how often they won, the average result in R (1R = the planned
risk), the worst losing streak and the drawdown. It uses the same functions
as trader-entry and trader-exit (loaded from those folders), so the replay
cannot drift from the plan.

    python3 backtest.py                          # trader/trade.json, the last 60 sessions Yahoo has
    python3 backtest.py --days 20 --cost 0.02    # 2 cents a share each way for slippage and fees
    python3 backtest.py --risk-usd 50 --write    # dollars at $50 risked a trade; saves into the trade

How a day is replayed (conservative where 5-minute bars hide the order):
  - the entry fills at the trigger, or at the bar's open if it opened past
    the trigger; a bar that opens past the limit is a miss, not a fill
  - the stop, target and flat-by time are set from that day's own bars with
    the same rules (the range low, the ATR as of the day before, ...)
  - a fill bar whose low reaches the stop counts as a loss
  - a bar that touches both the stop and the target counts as the stop
  - a gap through the stop exits at the open, worse than the stop
  - flat at the open of the first bar at or after the flat-by time
  - --cost dollars a share is charged on the way in and on the way out

The plan's own session is left out (it has not finished, or it is the day
you are deciding on). A replay of 20 or 60 sessions is a small sample:
it shows how the rules behave, it does not predict the next trade.

Bars: Yahoo Finance's free 5-minute chart (unofficial, delayed; it keeps
about 60 days of 5-minute bars). --offline DIR reads DIR/yahoo-5m-<TICKER>.json.
"""

import argparse
import datetime
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


class ToolError(Exception):
    pass


def money(x):
    x = float(x)
    return ("-$" if x < 0 else "$") + "{:,.2f}".format(abs(x))


def _sibling(folder, name):
    """Load trader-entry/entry.py and trader-exit/exit.py from next to this folder."""
    path = os.path.join(os.path.dirname(HERE), folder, name + ".py")
    if not os.path.exists(path):
        raise ToolError("the time machine needs %s next to it (%s). Install the whole pack, "
                        "not this folder alone." % (folder, path))
    spec = importlib.util.spec_from_file_location("trader_tm_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def modules():
    return _sibling("trader-entry", "entry"), _sibling("trader-exit", "exit")


# ---------------------------------------------------------------- the replay

def replay(trade, bars, entry_mod, exit_mod, days=60, cost=0.0):
    """Pure function: the trade's rules on every past session in the bars.
    Returns {"rows": [...], "summary": {...}}."""
    setup = trade["setup"]
    if setup.get("type") == "level":
        raise ToolError("a level setup is one fixed price for one day; it cannot be replayed. "
                        "Use orb or pdh to test a rule.")
    rules = (trade.get("exit") or {}).get("rules")
    if not rules:
        raise ToolError("the trade has no exit yet: run trader-exit first, a replay needs the stop and target")
    if rules.get("stop") == "price" or rules.get("target_price") is not None:
        raise ToolError("a fixed stop or target price is one day's number; it cannot be replayed. "
                        "Use --stop range, atr or pct and --r for the target in trader-exit.")
    by = entry_mod.by_day(bars)
    daily = entry_mod.daily_from(bars)
    dates = [d for d in by if d < trade["session_date"]]
    # keep a day of history in front so pdh has a prior high
    dates = dates[1:] if setup.get("type") == "pdh" else dates
    dates = dates[-days:] if days else dates
    rows = []
    for d in dates:
        try:
            e = entry_mod.plan_day(setup, by, d)
        except entry_mod.ToolError as err:
            rows.append({"date": d, "result": "skip", "why": str(err)})
            continue
        row = {"date": d, "trigger": e.get("trigger"), "status": e["status"]}
        if e["status"] != "filled":
            row["result"] = {"expired": "no trigger", "missed": "missed", "waiting": "no trigger"}.get(
                e["status"], "skip")
            row["why"] = e.get("why")
            rows.append(row)
            continue
        prior = [r for r in daily if r["date"] < d]
        a = entry_mod.atr(prior, 14)
        try:
            x = exit_mod.plan_exit(rules, e, a, d)
        except exit_mod.ToolError as err:
            row.update({"result": "skip", "why": str(err)})
            rows.append(row)
            continue
        w = exit_mod.walk_exit(by[d], e["fill_index"], e["fill"], x["stop"], x["target"], x["flat_by"])
        gross = w["exit"] - e["fill"]
        net = gross - 2 * cost
        row.update({"fill": e["fill"], "fill_time": e["fill_time"], "stop": x["stop"], "target": x["target"],
                    "exit": w["exit"], "exit_time": w["time"], "reason": w["reason"],
                    "same_bar": w["same_bar"], "risk_per_share": x["risk_per_share"],
                    "pnl_per_share": round(net, 4), "r": round(net / x["risk_per_share"], 2),
                    "result": "win" if net > 0 else ("loss" if net < 0 else "flat")})
        rows.append(row)
    return {"rows": rows, "summary": summarise(rows, cost)}


def summarise(rows, cost=0.0):
    trades = [r for r in rows if r["result"] in ("win", "loss", "flat")]
    rs = [r["r"] for r in trades]
    s = {"sessions": len(rows), "trades": len(trades),
         "no_trigger": sum(1 for r in rows if r["result"] == "no trigger"),
         "missed": sum(1 for r in rows if r["result"] == "missed"),
         "skipped": sum(1 for r in rows if r["result"] == "skip"),
         "wins": sum(1 for r in trades if r["result"] == "win"),
         "losses": sum(1 for r in trades if r["result"] == "loss"),
         "by_exit": {}, "cost_per_share": cost,
         "first": rows[0]["date"] if rows else None, "last": rows[-1]["date"] if rows else None}
    for r in trades:
        s["by_exit"][r["reason"]] = s["by_exit"].get(r["reason"], 0) + 1
    s["same_bar_calls"] = sum(1 for r in trades if r.get("same_bar"))
    if not trades:
        s.update({"win_rate_pct": None, "avg_r": None, "total_r": 0.0, "best_r": None, "worst_r": None,
                  "avg_win_r": None, "avg_loss_r": None, "max_drawdown_r": 0.0, "longest_losing_streak": 0})
        return s
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    peak = run = dd = 0.0
    streak = longest = 0
    for x in rs:
        run += x
        peak = max(peak, run)
        dd = max(dd, peak - run)
        streak = streak + 1 if x < 0 else 0
        longest = max(longest, streak)
    s.update({"win_rate_pct": round(100.0 * len(wins) / len(trades), 1),
              "avg_r": round(sum(rs) / len(rs), 2), "total_r": round(sum(rs), 2),
              "best_r": max(rs), "worst_r": min(rs),
              "avg_win_r": round(sum(wins) / len(wins), 2) if wins else None,
              "avg_loss_r": round(sum(losses) / len(losses), 2) if losses else None,
              "max_drawdown_r": round(dd, 2), "longest_losing_streak": longest})
    return s


# ---------------------------------------------------------------- report

def _r(x):
    return "" if x is None else "%+.2fR" % x


def report(trade, res, risk_usd=None):
    s = res["summary"]
    st = trade["setup"]
    name = {"orb": "opening range breakout (%s min)" % st.get("minutes", 15), "pdh": "prior day high breakout"}[st["type"]]
    x = trade["exit"]["rules"]
    lines = ["Time machine: %s, %s, valid until %s; stop %s, target %s, flat %d min before the close."
             % (trade["symbol"], name, st.get("valid_until", "11:30"),
                {"range": "range low", "atr": "%sx ATR" % x["atr_mult"], "pct": "%s%%" % x["pct"],
                 "price": "fixed price"}[x["stop"]],
                ("%sR" % x["r"]) if x.get("target_price") is None else "fixed price", x["flat_minutes"]),
             "Replayed %d sessions, %s to %s, cost %s a share each way." % (
                 s["sessions"], s["first"], s["last"], money(s["cost_per_share"])), ""]
    lines.append("%-10s %-10s %9s %6s %9s %9s %6s  %s" % ("date", "result", "fill", "at", "exit", "R", "at", "how"))
    for r in res["rows"]:
        if r["result"] in ("win", "loss", "flat"):
            lines.append("%-10s %-10s %9s %6s %9s %9s %6s  %s%s" % (
                r["date"], r["result"], money(r["fill"]), r["fill_time"], money(r["exit"]), _r(r["r"]),
                r["exit_time"], r["reason"], " (stop and target in one bar: counted as the stop)"
                if r.get("same_bar") and r["reason"] == "stop" and r["exit_time"] != r["fill_time"] else
                (" (stop touched in the fill bar)" if r.get("same_bar") else "")))
        else:
            lines.append("%-10s %-10s %s" % (r["date"], r["result"], r.get("why") or ""))
    lines.append("")
    if not s["trades"]:
        lines.append("The rules never filled in %d sessions. There is nothing to judge them on." % s["sessions"])
    else:
        lines.append("%d trades in %d sessions: %d wins, %d losses, win rate %.1f%%." % (
            s["trades"], s["sessions"], s["wins"], s["losses"], s["win_rate_pct"]))
        lines.append("Average %s a trade (wins %s, losses %s), total %s, best %s, worst %s." % (
            _r(s["avg_r"]), _r(s["avg_win_r"]), _r(s["avg_loss_r"]), _r(s["total_r"]), _r(s["best_r"]),
            _r(s["worst_r"])))
        lines.append("Worst drawdown %s, longest losing streak %d. Exits: %s." % (
            _r(-s["max_drawdown_r"]) if s["max_drawdown_r"] else "0R", s["longest_losing_streak"],
            ", ".join("%s %d" % kv for kv in sorted(s["by_exit"].items()))))
        if risk_usd:
            lines.append("At %s risked a trade: total %s, worst drawdown %s." % (
                money(risk_usd), money(s["total_r"] * risk_usd), money(s["max_drawdown_r"] * risk_usd)))
        if s["trades"] < 30:
            lines.append("Only %d trades: too few to tell skill from luck. Treat it as a description of "
                         "the rules, not evidence they work." % s["trades"])
        if s["avg_r"] is not None and s["avg_r"] <= 0:
            lines.append("These rules lost money on this stock over this window, after costs.")
    lines.append("Past bars, delayed free data, no news or halts modelled. It does not predict the next trade. "
                 "Not financial advice.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay the trade's rules on past sessions (trader-timemachine).")
    ap.add_argument("--trade", default="trader/trade.json")
    ap.add_argument("--days", type=int, default=60, help="how many past sessions (default 60, Yahoo's limit)")
    ap.add_argument("--cost", type=float, default=0.01, help="slippage and fees, dollars a share each way (0.01)")
    ap.add_argument("--risk-usd", type=float, help="show the dollars at this risk per trade")
    ap.add_argument("--config", default="trader/config.json", help="for the risk per trade (budget x risk_per_trade_pct)")
    ap.add_argument("--offline", metavar="DIR", help="read DIR/yahoo-5m-<TICKER>.json, no network")
    ap.add_argument("--write", action="store_true", help="save the summary into the trade and trader/backtest.json")
    ap.add_argument("--out", default="trader/backtest.json")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        entry_mod, exit_mod = modules()
        trade = entry_mod.read_json(a.trade, "trade (run trader-entry and trader-exit first)")
        bars = entry_mod.get_bars(trade["symbol"], "5m", "60d", a.offline)
        res = replay(trade, bars, entry_mod, exit_mod, a.days, a.cost)
        risk_usd = a.risk_usd
        if risk_usd is None:
            cfg = entry_mod.read_json(a.config, "config", required=False) or {}
            if cfg.get("budget") and cfg.get("risk_per_trade_pct"):
                risk_usd = float(cfg["budget"]) * float(cfg["risk_per_trade_pct"]) / 100.0
        if a.write:
            trade["backtest"] = dict(res["summary"], tested_at=datetime.datetime.now().replace(microsecond=0).isoformat(),
                                     rules={"setup": trade["setup"], "exit": trade["exit"]["rules"]})
            entry_mod.write_json(a.trade, trade)
            entry_mod.write_json(a.out, {"created_by": "trader-timemachine", "symbol": trade["symbol"],
                                         "setup": trade["setup"], "exit_rules": trade["exit"]["rules"],
                                         "summary": res["summary"], "rows": res["rows"]})
    except Exception as e:  # ToolError from this file, entry.py or exit.py
        if type(e).__name__ != "ToolError":
            raise
        print("backtest.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2) if a.json else report(trade, res, risk_usd))
    if a.write and not a.json:
        print("Saved the summary to %s and %s." % (a.trade, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
