#!/usr/bin/env python3
"""Day-trade scanner for trader-scanner. Standard library only.

Finds today's movers in YOUR watchlist: the gap from yesterday's close, how
much heavier than normal the volume is (relative volume), how far the stock
usually moves in a day (ATR), and whether it trades enough to get in and out.
Each ticker passes or fails each rule, with the number, and the ones that pass
are ranked. It writes trader/scan.json for trader-news and trader-entry.

    python3 scan.py --tickers AAPL AMD NVDA TSLA KO PLTR
    python3 scan.py --watchlist trader/watchlist.json --write
    python3 scan.py --tickers AAPL TSLA --min-gap 1 --min-rvol 1.2 --json
    python3 scan.py --holdings                  # tickers in portfolio.json and targets.json

The rules (flags, or "scanner" in trader/config.json):

  min_price             5        skip penny stocks
  max_price             2000
  min_dollar_volume     20000000 average daily dollar volume, 20 sessions
  min_gap_pct           2        |today's open / yesterday's close - 1|
  min_rvol              1.5      today's volume / average, time-adjusted in the session
  min_atr_pct           1.5      14-day ATR as a percent of the price
  direction             both     "up" keeps gap-ups only (this pack buys, it does not short)

Ranking: score = |gap %| x relative volume. A scan is a list of things to
look at, never a list of things to buy.

Data: Yahoo Finance's free chart endpoint (unofficial, delayed), daily bars,
one request per ticker half a second apart. --offline DIR reads
DIR/yahoo-1d-<TICKER>.json instead.
"""

import argparse
import datetime
import json
import os
import sys
import time
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


# ---------------------------------------------------------------- the scan

RULES = {"min_price": 5.0, "max_price": 2000.0, "min_dollar_volume": 20e6, "min_gap_pct": 2.0,
         "min_rvol": 1.5, "min_atr_pct": 1.5, "direction": "both"}


def session_fraction(now_utc, d):
    """How much of today's regular session has passed, 0..1 (1 after the close)."""
    s = session(d)
    if not s:
        return 1.0
    local = to_eastern(now_utc)
    if local.date() != d:
        return 1.0 if local.date() > d else 0.0
    o = datetime.datetime.combine(d, s[0])
    c = datetime.datetime.combine(d, s[1])
    if local <= o:
        return 0.0
    if local >= c:
        return 1.0
    return (local - o).total_seconds() / (c - o).total_seconds()


def measure(sym, daily, now_utc, lookback=20):
    """The numbers for one ticker from its daily bars. today = the bar dated
    today in New York; before the open there is none, so no gap yet."""
    today = to_eastern(now_utc).date().isoformat()
    bars = [b for b in daily if b["date"] <= today]
    if bars and bars[-1]["date"] == today and session_fraction(now_utc, to_eastern(now_utc).date()) <= 0:
        bars = bars[:-1]   # before the open, a bar dated today is not a real session yet
    has_today = bool(bars) and bars[-1]["date"] == today
    hist = bars[:-1] if has_today else bars
    if len(hist) < 2:
        raise ToolError("%s: not enough daily history" % sym)
    prev = hist[-1]
    past = hist[-lookback:]
    avg_vol = sum(b["v"] for b in past) / len(past)
    a = atr(hist, 14)
    m = {"symbol": sym, "date": today, "prev_close": cents(prev["c"]), "prior_high": cents(prev["h"]),
         "prior_low": cents(prev["l"]), "avg_volume": int(avg_vol),
         "avg_dollar_volume": round(avg_vol * prev["c"]), "atr14": round(a, 2) if a else None,
         "has_today": has_today}
    if has_today:
        t = bars[-1]
        frac = session_fraction(now_utc, to_eastern(now_utc).date())
        m.update({"open": cents(t["o"]), "price": cents(t["c"]), "day_high": cents(t["h"]),
                  "day_low": cents(t["l"]), "volume": t["v"],
                  "gap_pct": round(100.0 * (t["o"] / prev["c"] - 1), 2),
                  "change_pct": round(100.0 * (t["c"] / prev["c"] - 1), 2),
                  "session_done_pct": round(100 * frac),
                  "rvol": round(t["v"] / (avg_vol * frac), 2) if avg_vol and frac >= 0.02 else None})
    else:
        m.update({"price": cents(prev["c"]), "gap_pct": None, "change_pct": None, "rvol": None,
                  "session_done_pct": 0})
    m["atr_pct"] = round(100.0 * a / m["price"], 2) if a and m["price"] else None
    return m


def judge(m, rules):
    """(passes, [why it failed]) for one ticker."""
    r = dict(RULES)
    r.update(rules or {})
    fails = []
    p = m["price"]
    if p < r["min_price"]:
        fails.append("price %s is under %s" % (money(p), money(r["min_price"])))
    if p > r["max_price"]:
        fails.append("price %s is over %s" % (money(p), money(r["max_price"])))
    if m["avg_dollar_volume"] < r["min_dollar_volume"]:
        fails.append("trades %s a day on average, under %s" % (_big(m["avg_dollar_volume"]),
                                                               _big(r["min_dollar_volume"])))
    if m["gap_pct"] is None:
        fails.append("no gap yet: the NYSE has not opened today")
    else:
        if abs(m["gap_pct"]) < r["min_gap_pct"] - 1e-9:
            fails.append("gap %+.2f%% is under %s%%" % (m["gap_pct"], _n(r["min_gap_pct"])))
        if r["direction"] == "up" and m["gap_pct"] < 0:
            fails.append("gapped down %.2f%%; direction is up only" % m["gap_pct"])
        if r["direction"] == "down" and m["gap_pct"] > 0:
            fails.append("gapped up %.2f%%; direction is down only" % m["gap_pct"])
    if m["rvol"] is None:
        if m["gap_pct"] is not None:
            fails.append("too early in the session for relative volume")
    elif m["rvol"] < r["min_rvol"] - 1e-9:
        fails.append("relative volume %.2fx is under %sx" % (m["rvol"], _n(r["min_rvol"])))
    if m["atr_pct"] is None:
        fails.append("not enough history for a 14-day ATR")
    elif m["atr_pct"] < r["min_atr_pct"] - 1e-9:
        fails.append("ATR is %.2f%% of the price, under %s%%" % (m["atr_pct"], _n(r["min_atr_pct"])))
    return not fails, fails


def scan(daily_by_sym, now_utc, rules=None):
    """Pure function: {SYM: daily bars} -> ranked rows."""
    rows, errors = [], []
    for sym, daily in daily_by_sym.items():
        try:
            m = measure(sym, daily, now_utc)
        except ToolError as e:
            errors.append(str(e))
            continue
        ok, fails = judge(m, rules)
        m["passes"], m["fails"] = ok, fails
        m["score"] = round(abs(m["gap_pct"] or 0) * (m["rvol"] or 0), 2)
        rows.append(m)
    rows.sort(key=lambda m: (not m["passes"], -m["score"], m["symbol"]))
    for i, m in enumerate(rows, 1):
        m["rank"] = i
    return rows, errors


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x).rstrip("0")


def _big(x):
    x = float(x)
    if x >= 1e9:
        return "$%.1fB" % (x / 1e9)
    if x >= 1e6:
        return "$%.0fM" % (x / 1e6)
    return money(x)


# ---------------------------------------------------------------- report

def report(rows, errors, rules, now_utc):
    r = dict(RULES)
    r.update(rules or {})
    local = to_eastern(now_utc)
    lines = ["Scan at %s New York. Rules: gap >= %s%%, relative volume >= %sx, ATR >= %s%% of price, "
             "price %s to %s, average dollar volume >= %s%s."
             % (local.strftime("%Y-%m-%d %H:%M"), _n(r["min_gap_pct"]), _n(r["min_rvol"]), _n(r["min_atr_pct"]),
                money(r["min_price"]), money(r["max_price"]), _big(r["min_dollar_volume"]),
                "" if r["direction"] == "both" else ", gaps %s only" % r["direction"]), ""]
    lines.append("%-4s %-6s %10s %8s %8s %7s %7s %9s  %s" % ("rank", "ticker", "price", "gap", "change",
                                                           "rvol", "ATR%", "$vol/day", "result"))
    for m in rows:
        lines.append("%-4d %-6s %10s %8s %8s %7s %7s %9s  %s" % (
            m["rank"], m["symbol"], money(m["price"]),
            "" if m["gap_pct"] is None else "%+.2f%%" % m["gap_pct"],
            "" if m["change_pct"] is None else "%+.2f%%" % m["change_pct"],
            "" if m["rvol"] is None else "%.2fx" % m["rvol"],
            "" if m["atr_pct"] is None else "%.2f%%" % m["atr_pct"],
            _big(m["avg_dollar_volume"]), "PASS" if m["passes"] else "fail"))
        for f in m["fails"]:
            lines.append("            - " + f)
    for e in errors:
        lines.append("error: " + e)
    passed = [m["symbol"] for m in rows if m["passes"]]
    lines.append("")
    if passed:
        lines.append("%d passed: %s. Next: trader-news on %s, then trader-entry." % (
            len(passed), ", ".join(passed), passed[0]))
    else:
        lines.append("Nothing passed. No trade is a fine result; loosen a rule only if you mean it.")
    if rows and any(0 < m.get("session_done_pct", 0) < 100 for m in rows):
        lines.append("Relative volume mid-session is time-adjusted in a straight line; real volume is "
                     "heaviest at the open and the close, so early readings run high.")
    lines.append("Delayed free data. A scan is a list to look at, not a list to buy. Not financial advice.")
    return "\n".join(lines)


def watchlist(a):
    syms = [s.upper() for s in (a.tickers or [])]
    if a.watchlist:
        doc = read_json(a.watchlist, "watchlist")
        items = doc.get("tickers", doc) if isinstance(doc, dict) else doc
        syms += [str(s).upper() for s in items]
    if a.holdings:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        syms += [str(p.get("ticker") or p.get("symbol") or "").upper() for p in pf.get("positions", [])]
        tg = read_json(a.targets, "targets", required=False) or {}
        syms += [str(w.get("ticker") or "").upper() for w in tg.get("weights", [])]
    out = []
    for s in syms:
        if s and s not in out:
            out.append(s)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Scan your watchlist for today's movers (trader-scanner).")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--watchlist", help="JSON list, or {\"tickers\": [...]}")
    ap.add_argument("--holdings", action="store_true", help="add tickers from portfolio.json and targets.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--targets", default="trader/targets.json")
    ap.add_argument("--config", default="trader/config.json", help="reads its \"scanner\" block")
    for k, v in RULES.items():
        if k == "direction":
            ap.add_argument("--direction", choices=["both", "up", "down"])
        else:
            ap.add_argument("--" + k.replace("_pct", "").replace("_", "-"), dest=k, type=float)
    ap.add_argument("--offline", metavar="DIR", help="read DIR/yahoo-1d-<TICKER>.json, no network")
    ap.add_argument("--now", help="pretend it is this time (ISO; no offset = New York time)")
    ap.add_argument("--write", nargs="?", const="trader/scan.json", metavar="PATH")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        syms = watchlist(a)
        if not syms:
            raise ToolError("which tickers? --tickers AAPL TSLA, --watchlist FILE or --holdings. "
                            "The scanner only looks at a list you give it.")
        cfg = read_json(a.config, "config", required=False) or {}
        rules = dict(RULES)
        rules.update(cfg.get("scanner") or {})
        for k in RULES:
            if getattr(a, k, None) is not None:
                rules[k] = getattr(a, k)
        now = parse_now(a.now)
        daily, errors = {}, []
        for i, s in enumerate(syms):
            try:
                if i and not a.offline:
                    time.sleep(PAUSE)
                daily[s] = get_bars(s, "1d", "3mo", a.offline)
            except ToolError as e:
                errors.append(str(e))
        rows, errs = scan(daily, now, rules)
        errors += errs
        if a.write:
            write_json(a.write, {"created_by": "trader-scanner", "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "session_date": to_eastern(now).date().isoformat(), "rules": rules,
                                 "passed": [m["symbol"] for m in rows if m["passes"]],
                                 "rows": rows, "errors": errors,
                                 "note": "A scan is a list to look at, not a list to buy."})
    except ToolError as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps({"rows": rows, "errors": errors, "rules": rules}, indent=2))
    else:
        print(report(rows, errors, rules, now))
        if a.write:
            print("Wrote %s." % a.write)
    return 0 if any(m["passes"] for m in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
