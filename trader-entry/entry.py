#!/usr/bin/env python3
"""Entry plans for trader-entry. Standard library only.

Turns one ticker into one written entry rule for today: the exact price that
triggers a buy, the most you will pay (the limit), and the time the idea
expires. It writes trader/trade.json, which trader-exit, trader-timemachine
and trader-budget read. Nothing is bought here.

    python3 entry.py AMD --setup orb --minutes 15          # opening range breakout
    python3 entry.py AMD --setup pdh                       # break of yesterday's high
    python3 entry.py AMD --setup level --price 512.50      # your own level
    python3 entry.py --from-scan --setup orb --write       # top pass in trader/scan.json
    python3 entry.py AMD --setup orb --valid-until 11:00 --slip 0.1 --write

Setups (long only: this pack does not short unless your config allows it,
and these setups are written for buys):

  orb    opening range breakout. The range is the high and low of the first
         N minutes (5, 15 or 30; default 15). Trigger: one cent over the
         range high, any time after the range closes until --valid-until.
  pdh    prior day high. Trigger: one cent over yesterday's regular-session
         high, from the open until --valid-until.
  level  your own price. Above the last price it is a breakout (stop-limit
         buy); below it, a pullback (limit buy). It cannot be replayed by
         trader-timemachine, because a fixed price means nothing on other days.

The limit is the trigger plus --slip percent (default 0.1 %, at least one
cent): the order will not pay more than that, so a stock that jumps past it
is missed, not chased. If the stock already went through the trigger today,
the plan says so: the move happened without you, and the honest answer is
usually no trade.

Bars: Yahoo Finance's free 5-minute chart (unofficial, delayed, regular
session only). --offline DIR reads DIR/yahoo-5m-<TICKER>.json.
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


# ---------------------------------------------------------------- the entry rule

SETUPS = ("orb", "pdh", "level")
BAR_MINUTES = 5


def limit_for(trigger, slip_pct):
    return cents(max(trigger * (1 + float(slip_pct) / 100.0), trigger + 0.01))


def plan_day(setup, days, date, now_time=None):
    """The entry for one session. Pure function, used live and by trader-timemachine.

    setup: {"type", "minutes", "valid_until", "slip_pct", "price"}
    days:  {date: [5-minute bars]} (by_day of get_bars)
    now_time: "HH:MM" New York; bars that start at or after it are not seen yet.

    Returns {"status", "trigger", "limit", "order_type", "range_high", "range_low",
    "prior_high", "fill", "fill_index", "fill_time", ...}. status is one of
    not_ready, waiting, filled, missed (jumped past the limit), expired, no_data."""
    typ = setup.get("type")
    if typ not in SETUPS:
        raise ToolError("unknown setup %r: use one of %s" % (typ, ", ".join(SETUPS)))
    bars = list(days.get(date) or [])
    if now_time:
        bars = [b for b in bars if b["time"] < now_time]
    dates = [d for d in days if d < date]
    prev = days[dates[-1]] if dates else None
    valid_until = setup.get("valid_until") or "11:30"
    out = {"date": date, "setup": typ, "valid_until": valid_until, "range_high": None,
           "range_low": None, "prior_high": cents(max(b["h"] for b in prev)) if prev else None,
           "fill": None, "fill_index": None, "fill_time": None}
    start = 0
    if typ == "orb":
        n_min = int(setup.get("minutes") or 15)
        if n_min <= 0 or n_min % BAR_MINUTES:
            raise ToolError("--minutes must be a multiple of %d (5, 15, 30)" % BAR_MINUTES)
        n = n_min // BAR_MINUTES
        out["range_end"] = minus_minutes(OPEN, -n_min).strftime("%H:%M")
        if len(bars) < n:
            out["status"] = "not_ready" if bars or now_time else "no_data"
            out["why"] = "the %d-minute opening range is not finished until %s" % (n_min, out["range_end"])
            return out
        rng = bars[:n]
        if rng[0]["time"] != "09:30":
            out["status"] = "no_data"
            out["why"] = "the first bar is at %s, not 09:30: the opening range is incomplete" % rng[0]["time"]
            return out
        out["range_high"] = cents(max(b["h"] for b in rng))
        out["range_low"] = cents(min(b["l"] for b in rng))
        trigger = cents(out["range_high"] + 0.01)
        start = n
    elif typ == "pdh":
        if not prev:
            out["status"] = "no_data"
            out["why"] = "no prior session in the bars"
            return out
        trigger = cents(out["prior_high"] + 0.01)
    else:
        if setup.get("price") is None:
            raise ToolError("the level setup needs --price")
        trigger = cents(setup["price"])
    slip = setup.get("slip_pct", 0.1)
    last = bars[-1]["c"] if bars else (prev[-1]["c"] if prev else None)
    pullback = typ == "level" and last is not None and trigger < last
    out.update({"trigger": trigger, "order_type": "limit" if pullback else "stop_limit",
                "limit": trigger if pullback else limit_for(trigger, slip),
                "last_price": cents(last) if last is not None else None,
                "last_time": bars[-1]["time"] if bars else None})
    for i in range(start, len(bars)):
        b = bars[i]
        if b["time"] >= valid_until:
            out["status"] = "expired"
            out["why"] = "no trigger before %s" % valid_until
            return out
        if pullback:
            if b["l"] <= trigger:
                out.update({"status": "filled", "fill": cents(min(b["o"], trigger)), "fill_index": i,
                            "fill_time": b["time"]})
                return out
        elif b["h"] >= trigger:
            if b["o"] > out["limit"]:
                out.update({"status": "missed", "fill_time": b["time"],
                            "why": "opened at %s, past the %s limit" % (money(b["o"]), money(out["limit"]))})
                return out
            out.update({"status": "filled", "fill": cents(max(b["o"], trigger)), "fill_index": i,
                        "fill_time": b["time"]})
            return out
    out["status"] = "waiting"
    out["why"] = "not triggered yet" if bars else "the session has not started"
    if not now_time and bars and len(bars) >= 2:
        s = session(datetime.date.fromisoformat(date))
        if s and bars[-1]["time"] >= minus_minutes(s[1], BAR_MINUTES).strftime("%H:%M"):
            out["status"] = "expired"
            out["why"] = "the session ended without a trigger"
    return out


def next_session(d):
    for _ in range(15):
        d += datetime.timedelta(days=1)
        if session(d):
            return d
    raise ToolError("no NYSE session found after %s" % d)


def build_trade(sym, setup, bars, now_utc):
    """trader/trade.json for today's session, or the next one after the close
    and when the NYSE is shut (plan the evening before)."""
    days = by_day(bars)
    local = to_eastern(now_utc)
    d = local.date()
    s = session(d)
    if s and local.time() < s[1] and d.isoformat() in days:
        date, now_time = d.isoformat(), local.strftime("%H:%M")
    elif s and local.time() < s[1]:
        date, now_time = d.isoformat(), local.strftime("%H:%M")   # before the open, or no bars yet
    else:
        date, now_time = next_session(d).isoformat(), "09:30"     # plan for the next session
    if date not in days:
        days = dict(days)
        days[date] = []
    daily = daily_from(bars)
    e = plan_day(setup, days, date, now_time)
    prior = [r for r in daily if r["date"] < date]
    a = atr(prior, 14)
    return {
        "created_by": "trader-entry",
        "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": sym.upper(), "side": "buy", "session_date": date,
        "setup": setup,
        "entry": e,
        "atr14": round(a, 2) if a else None,
        "exit": None, "backtest": None,
        "note": "A plan, not an order. trader-exit adds the stop, the target and the flat-by time.",
    }


# ---------------------------------------------------------------- report

def report(t):
    e, s = t["entry"], t["setup"]
    lines = ["%s, %s, session %s" % (t["symbol"], {"orb": "opening range breakout (%s min)" % s.get("minutes", 15),
                                                   "pdh": "prior day high breakout",
                                                   "level": "your level"}[s["type"]], t["session_date"])]
    if e.get("range_high") is not None:
        lines.append("  opening range %s to %s (09:30 to %s)" % (money(e["range_low"]), money(e["range_high"]),
                                                               e["range_end"]))
    if e.get("prior_high") is not None:
        lines.append("  yesterday's high %s" % money(e["prior_high"]))
    if t.get("atr14"):
        lines.append("  14-day ATR %s" % money(t["atr14"]))
    if e.get("trigger") is not None:
        if e["order_type"] == "limit":
            lines.append("  ENTRY: buy on a pullback to %s (limit order), valid until %s"
                         % (money(e["trigger"]), e["valid_until"]))
        else:
            lines.append("  ENTRY: buy if it trades at %s or higher, paying at most %s (stop-limit), valid until %s"
                         % (money(e["trigger"]), money(e["limit"]), e["valid_until"]))
    if e.get("last_price") is not None:
        lines.append("  last %s%s" % (money(e["last_price"]), (" at " + e["last_time"]) if e.get("last_time") else ""))
    st = e["status"]
    word = {"waiting": "WAITING for the trigger", "not_ready": "NOT READY", "no_data": "NO DATA",
            "expired": "EXPIRED", "missed": "MISSED", "filled": "ALREADY TRIGGERED"}[st]
    lines.append("  status: %s%s" % (word, (": " + e["why"]) if e.get("why") else ""))
    if st == "filled":
        lines.append("  It went through %s at %s. The move happened without you; buying now is chasing, "
                     "not this plan." % (money(e["trigger"]), e["fill_time"]))
    lines.append("")
    lines.append("Next: trader-exit adds the stop, the target and the flat-by time. Not financial advice.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write today's entry rule for one ticker (trader-entry).")
    ap.add_argument("symbol", nargs="?")
    ap.add_argument("--from-scan", nargs="?", const="trader/scan.json", metavar="PATH",
                    help="take the top pass from trader/scan.json")
    ap.add_argument("--setup", choices=SETUPS, default="orb")
    ap.add_argument("--minutes", type=int, default=15, help="opening range length (orb)")
    ap.add_argument("--price", type=float, help="your level (level setup)")
    ap.add_argument("--valid-until", default="11:30", help="New York time the idea expires (default 11:30)")
    ap.add_argument("--slip", type=float, default=0.1, help="limit = trigger + this percent (default 0.1)")
    ap.add_argument("--offline", metavar="DIR", help="read DIR/yahoo-5m-<TICKER>.json, no network")
    ap.add_argument("--now", help="pretend it is this time (ISO; no offset = New York time)")
    ap.add_argument("--write", nargs="?", const="trader/trade.json", metavar="PATH")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        sym = a.symbol
        if a.from_scan and not sym:
            scan = read_json(a.from_scan, "scan (run trader-scanner)")
            if not scan.get("passed"):
                raise ToolError("nothing passed in %s: no candidate, no trade" % a.from_scan)
            sym = scan["passed"][0]
        if not sym:
            raise ToolError("which ticker? entry.py AMD --setup orb, or --from-scan")
        hhmm(a.valid_until)
        setup = {"type": a.setup, "valid_until": a.valid_until, "slip_pct": a.slip}
        if a.setup == "orb":
            setup["minutes"] = a.minutes
        if a.setup == "level":
            setup["price"] = a.price
        now = parse_now(a.now)
        bars = get_bars(sym, "5m", "1mo", a.offline)
        t = build_trade(sym, setup, bars, now)
        if a.write:
            write_json(a.write, t)
    except ToolError as e:
        print("entry.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(t, indent=2) if a.json else report(t))
    if a.write and not a.json:
        print("Wrote %s." % a.write)
    return 0 if t["entry"]["status"] in ("waiting", "not_ready") else 1


if __name__ == "__main__":
    sys.exit(main())
