#!/usr/bin/env python3
"""Exit plans for trader-exit. Standard library only, no network.

Adds the three ways out to the entry in trader/trade.json, before anything
is bought: the stop (where the idea is wrong), the target (where you take
the money) and the flat-by time (a day trade is closed before the bell, win
or lose). It also works out the risk per share, which trader-budget uses to
size the trade so a stop-out costs what you decided, not more.

    python3 exit.py                                # stop at the range low, target 2R, flat 5 min before the close
    python3 exit.py --stop atr --atr-mult 0.5 --r 2 --write
    python3 exit.py --stop pct --pct 1 --target-price 520
    python3 exit.py --stop price --stop-price 498.50 --flat-minutes 10

Stops (for a buy, always under the entry):
  range   one cent under the opening range low (orb setups only)
  atr     the trigger minus N x the 14-day ATR (--atr-mult, default 0.5)
  pct     the trigger minus N percent (--pct, default 1)
  price   your own price (--stop-price)

Target: the trigger plus R x the planned risk (--r, default 2), or your own
--target-price. R is the planned risk per share, trigger minus stop.

Flat by: --flat-minutes before the close (default 5): 15:55 on a normal day,
12:55 on a 1:00 p.m. early close. trader-alerts rings at that time.

The worst-case risk uses the limit price (the most the entry pays), so the
size from trader-budget holds even on a bad fill. A gap through the stop can
still lose more: a stop is a plan, not a guarantee.
"""

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request


class ToolError(Exception):
    pass

# ---------------------------------------------------------------- the NYSE clock
# Copied from trader-alerts/alerts.py so this skill folder works on its own.
# tests/test_daytrade.py checks that every copy matches.

NYSE_HOLIDAYS = {
    "2026-01-01": "New Year's Day",
    "2026-01-19": "Martin Luther King, Jr. Day",
    "2026-02-16": "Washington's Birthday",
    "2026-04-03": "Good Friday",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth National Independence Day",
    "2026-07-03": "Independence Day (observed)",
    "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving Day",
    "2026-12-25": "Christmas Day",
    "2027-01-01": "New Year's Day",
    "2027-01-18": "Martin Luther King, Jr. Day",
    "2027-02-15": "Washington's Birthday",
    "2027-03-26": "Good Friday",
    "2027-05-31": "Memorial Day",
    "2027-06-18": "Juneteenth National Independence Day (observed)",
    "2027-07-05": "Independence Day (observed)",
    "2027-09-06": "Labor Day",
    "2027-11-25": "Thanksgiving Day",
    "2027-12-24": "Christmas Day (observed)",
}
NYSE_EARLY_CLOSE = {  # 1:00 p.m. ET
    "2026-11-27": "day after Thanksgiving",
    "2026-12-24": "Christmas Eve",
    "2027-11-26": "day after Thanksgiving",
}
OPEN = datetime.time(9, 30)
CLOSE = datetime.time(16, 0)
EARLY = datetime.time(13, 0)


def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def eastern_offset(utc):
    """-4 in New York daylight time, -5 otherwise (US rule since 2007)."""
    y = utc.year
    start = datetime.datetime.combine(_nth_sunday(y, 3, 2), datetime.time(7, 0))
    end = datetime.datetime.combine(_nth_sunday(y, 11, 1), datetime.time(6, 0))
    naive = utc.replace(tzinfo=None)
    return -4 if start <= naive < end else -5


def to_eastern(utc):
    return utc.replace(tzinfo=None) + datetime.timedelta(hours=eastern_offset(utc))


def eastern_to_utc(local):
    for off in (-4, -5):
        utc = local - datetime.timedelta(hours=off)
        if eastern_offset(utc) == off:
            return utc
    return local + datetime.timedelta(hours=5)


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is New York time."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ToolError("--now wants an ISO time like 2026-11-17T09:45 (New York) or ...Z (UTC)")
    if dt.tzinfo is None:
        return eastern_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def session(d):
    """(open, close) New York time for a date, or None when the NYSE is shut."""
    if d.weekday() >= 5 or d.isoformat() in NYSE_HOLIDAYS:
        return None
    return OPEN, (EARLY if d.isoformat() in NYSE_EARLY_CLOSE else CLOSE)


def hhmm(text):
    try:
        h, m = str(text).split(":")
        return datetime.time(int(h), int(m))
    except (ValueError, TypeError):
        raise ToolError("a time must look like 11:30, got %r" % (text,))


def minus_minutes(t, minutes):
    dt = datetime.datetime.combine(datetime.date(2000, 1, 3), t) - datetime.timedelta(minutes=minutes)
    return dt.time()


# ---------------------------------------------------------------- bars

UA = "Mozilla/5.0 (compatible; trader-skill/1.0; +https://github.com/Jakeschincariol/trader-skill)"
PAUSE = 0.5  # seconds between requests: be polite to free endpoints


def yahoo_chart_url(sym, interval, rng):
    return "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s" % (
        sym.upper().replace(".", "-"), rng, interval)


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise ToolError("%s answered HTTP %d" % (url.split("?")[0], e.code))
    except urllib.error.URLError as e:
        raise ToolError("could not reach %s: %s" % (url.split("/")[2], e.reason))


def parse_bars(text, sym, intraday):
    """Yahoo chart JSON -> [{"t", "date", "time", "o", "h", "l", "c", "v"}], oldest first.
    Rows with a missing price are dropped. Intraday bars outside the regular
    session (and the zero-volume closing tick Yahoo adds at 16:00) are dropped."""
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise ToolError("Yahoo returned something unexpected for %s" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise ToolError("Yahoo has no data for %s: %s" % (sym, err.get("description", "unknown ticker")))
    r = res[0]
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    out = []
    for i, t in enumerate(ts):
        try:
            o, h, l, c = (q[k][i] for k in ("open", "high", "low", "close"))
            v = (q.get("volume") or [None] * len(ts))[i] or 0
        except (KeyError, IndexError, TypeError):
            continue
        if None in (o, h, l, c):
            continue
        utc = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=t)
        ny = to_eastern(utc)
        if intraday:
            s = session(ny.date())
            if not s or not (s[0] <= ny.time() < s[1]):
                continue
        out.append({"t": utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "date": ny.date().isoformat(),
                    "time": ny.strftime("%H:%M"), "o": float(o), "h": float(h), "l": float(l),
                    "c": float(c), "v": int(v)})
    if not out:
        raise ToolError("Yahoo returned no usable bars for %s" % sym)
    return out


def get_bars(sym, interval="5m", rng="1mo", offline=None):
    """Bars for one ticker, from Yahoo or from DIR/yahoo-<interval>-<SYM>.json."""
    sym = sym.upper()
    if offline:
        path = os.path.join(offline, "yahoo-%s-%s.json" % (interval, sym))
        if not os.path.exists(path):
            raise ToolError("no offline bar file %s" % path)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = fetch(yahoo_chart_url(sym, interval, rng))
    return parse_bars(text, sym, intraday=interval not in ("1d", "1wk", "1mo"))


def by_day(bars):
    """{date: [bars]} in date order."""
    days = {}
    for b in bars:
        days.setdefault(b["date"], []).append(b)
    return dict(sorted(days.items()))


def daily_from(bars):
    """Daily OHLCV rows built from intraday bars (one per session date)."""
    rows = []
    for d, bs in by_day(bars).items():
        rows.append({"date": d, "o": bs[0]["o"], "h": max(b["h"] for b in bs),
                     "l": min(b["l"] for b in bs), "c": bs[-1]["c"], "v": sum(b["v"] for b in bs)})
    return rows


def atr(daily, n=14):
    """Average true range over the last n daily rows (needs n + 1 rows)."""
    if len(daily) < n + 1:
        return None
    trs = []
    for i in range(len(daily) - n, len(daily)):
        h, l, pc = daily[i]["h"], daily[i]["l"], daily[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / n


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise ToolError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise ToolError("%s is not valid JSON: %s" % (path, e))


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def cents(x):
    return round(float(x) + 1e-9, 2)


def money(x):
    return "$" + "{:,.2f}".format(float(x))


# ---------------------------------------------------------------- the exit rule

STOPS = ("range", "atr", "pct", "price")
DEFAULT_RULES = {"stop": "range", "atr_mult": 0.5, "pct": 1.0, "stop_price": None,
                 "r": 2.0, "target_price": None, "flat_minutes": 5}


def flat_by(date, minutes):
    s = session(datetime.date.fromisoformat(date))
    if not s:
        raise ToolError("%s is not an NYSE session" % date)
    return minus_minutes(s[1], int(minutes)).strftime("%H:%M"), s[1].strftime("%H:%M")


def plan_exit(rules, entry, atr14, date):
    """Pure function, used live and by trader-timemachine.
    Returns {"stop", "target", "flat_by", "close", "risk_per_share", "worst_risk_per_share", "r"}."""
    r = dict(DEFAULT_RULES)
    r.update({k: v for k, v in (rules or {}).items() if v is not None})
    trig, lim = entry.get("trigger"), entry.get("limit")
    if trig is None:
        raise ToolError("the entry has no trigger yet (%s)" % entry.get("why", entry.get("status")))
    kind = r["stop"]
    if kind not in STOPS:
        raise ToolError("unknown stop %r: use one of %s" % (kind, ", ".join(STOPS)))
    if kind == "range":
        if entry.get("range_low") is None:
            raise ToolError("a range stop needs an opening range (orb setup); use --stop atr or pct")
        stop = cents(entry["range_low"] - 0.01)
    elif kind == "atr":
        if not atr14:
            raise ToolError("no 14-day ATR in the trade (not enough history); use --stop pct or price")
        stop = cents(trig - float(r["atr_mult"]) * atr14)
    elif kind == "pct":
        stop = cents(trig * (1 - float(r["pct"]) / 100.0))
    else:
        if r.get("stop_price") is None:
            raise ToolError("--stop price needs --stop-price")
        stop = cents(r["stop_price"])
    if stop >= trig - 0.005:
        raise ToolError("the stop %s must be under the entry %s for a buy" % (money(stop), money(trig)))
    if stop <= 0:
        raise ToolError("the stop works out at %s; pick a closer stop" % money(stop))
    risk = cents(trig - stop)
    if r.get("target_price") is not None:
        target = cents(r["target_price"])
        if target <= trig + 0.005:
            raise ToolError("the target %s must be over the entry %s" % (money(target), money(trig)))
    else:
        target = cents(trig + float(r["r"]) * risk)
    fb, close = flat_by(date, r["flat_minutes"])
    return {"rules": {k: r[k] for k in DEFAULT_RULES}, "stop": stop, "target": target, "flat_by": fb,
            "close": close, "risk_per_share": risk, "worst_risk_per_share": cents((lim or trig) - stop),
            "reward_per_share": cents(target - trig), "r": round((target - trig) / risk, 2)}


def walk_exit(bars, fill_index, fill, stop, target, flat_time):
    """Follow one filled trade bar by bar. Pure function.

    Conservative where 5-minute bars cannot say what came first:
      - on the fill bar, a low at or under the stop counts as stopped out
        (the target is not credited on the fill bar);
      - a later bar that touches both the stop and the target counts as the stop;
      - a gap through the stop fills at the open, which is worse than the stop.
    Returns {"exit", "reason", "time", "same_bar"}."""
    b = bars[fill_index]
    if b["l"] <= stop:
        return {"exit": stop, "reason": "stop", "time": b["time"], "same_bar": True}
    for b in bars[fill_index + 1:]:
        if b["time"] >= flat_time:
            return {"exit": cents(b["o"]), "reason": "flat", "time": b["time"], "same_bar": False}
        if b["l"] <= stop:
            return {"exit": cents(min(b["o"], stop)), "reason": "stop", "time": b["time"],
                    "same_bar": b["h"] >= target}
        if b["h"] >= target:
            return {"exit": cents(max(b["o"], target)), "reason": "target", "time": b["time"], "same_bar": False}
    last = bars[-1]
    return {"exit": cents(last["c"]), "reason": "end of data", "time": last["time"], "same_bar": False}


# ---------------------------------------------------------------- report

def report(t):
    x, e = t["exit"], t["entry"]
    lines = ["%s exit plan, session %s" % (t["symbol"], t["session_date"]),
             "  entry  %s trigger, %s worst fill (the limit)" % (money(e["trigger"]), money(e["limit"])),
             "  stop   %s  (%s)" % (money(x["stop"]), {
                 "range": "one cent under the opening range low",
                 "atr": "%s x the 14-day ATR of %s under the trigger" % (x["rules"]["atr_mult"], money(t.get("atr14") or 0)),
                 "pct": "%s%% under the trigger" % x["rules"]["pct"],
                 "price": "your price"}[x["rules"]["stop"]]),
             "  target %s  (%sR)" % (money(x["target"]), x["r"]),
             "  flat by %s New York, %d minutes before the %s close, win or lose"
             % (x["flat_by"], x["rules"]["flat_minutes"], x["close"]),
             "  risk   %s a share planned, %s a share at the worst fill; reward %s a share"
             % (money(x["risk_per_share"]), money(x["worst_risk_per_share"]), money(x["reward_per_share"]))]
    if t.get("atr14") and x["risk_per_share"] > t["atr14"]:
        lines.append("  note: the stop is wider than a whole day's average range (ATR %s)." % money(t["atr14"]))
    if t.get("atr14") and x["risk_per_share"] < 0.1 * t["atr14"]:
        lines.append("  note: the stop is under a tenth of the ATR; ordinary noise can hit it.")
    lines.append("")
    lines.append("A stop is a plan, not a guarantee: a gap or a halt can fill worse. Next: trader-timemachine "
                 "replays these exact rules on past sessions. Not financial advice.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Add the stop, target and flat-by time to the trade (trader-exit).")
    ap.add_argument("--trade", default="trader/trade.json")
    ap.add_argument("--stop", choices=STOPS)
    ap.add_argument("--atr-mult", type=float)
    ap.add_argument("--pct", type=float)
    ap.add_argument("--stop-price", type=float)
    ap.add_argument("--r", type=float, help="target = trigger + R x risk (default 2)")
    ap.add_argument("--target-price", type=float)
    ap.add_argument("--flat-minutes", type=int, help="minutes before the close to be flat (default 5)")
    ap.add_argument("--write", nargs="?", const=True, help="save into the trade file (or PATH)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        t = read_json(a.trade, "trade (run trader-entry first)")
        rules = dict((t.get("exit") or {}).get("rules") or {})
        for k in DEFAULT_RULES:
            if getattr(a, k, None) is not None:
                rules[k] = getattr(a, k)
        if a.stop_price is not None and a.stop is None:
            rules["stop"] = "price"
        if a.target_price is not None:
            rules["target_price"] = a.target_price
        elif a.r is not None:
            rules["target_price"] = None
        if rules.get("stop", "range") == "range" and (t.get("entry") or {}).get("range_low") is None:
            rules["stop"] = "atr"
        t["exit"] = plan_exit(rules, t["entry"], t.get("atr14"), t["session_date"])
        t["backtest"] = None   # the rules changed: an old replay no longer applies
        if a.write:
            write_json(a.trade if a.write is True else a.write, t)
    except ToolError as e:
        print("exit.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(t, indent=2) if a.json else report(t))
    if a.write and not a.json:
        print("Saved to %s." % (a.trade if a.write is True else a.write))
    return 0


if __name__ == "__main__":
    sys.exit(main())
