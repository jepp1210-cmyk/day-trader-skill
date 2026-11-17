"""The one-trade flow: scanner -> entry -> exit -> timemachine -> budget -> risk -> broker, and alerts.

The fixtures are Yahoo Finance chart responses saved on 2026-11-17 after the
close: 3 months of daily bars for six tickers, 1 month of 5-minute bars for
AMD and TSLA.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

scan = load("trader-scanner", "scan")
entry = load("trader-entry", "entry")
exit_ = load("trader-exit", "exit")
tm = load("trader-timemachine", "backtest")
size = load("trader-budget", "size")
risk = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")
alerts = load("trader-alerts", "alerts")

D = os.path.join(FIX, "trader-daytrade")
WATCH = ["AAPL", "AMD", "NVDA", "TSLA", "KO", "PLTR"]


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def amd_trade(now="2026-11-17T10:00", **exit_rules):
    bars = entry.get_bars("AMD", "5m", offline=D)
    setup = {"type": "orb", "minutes": 15, "valid_until": "11:30", "slip_pct": 0.1}
    t = entry.build_trade("AMD", setup, bars, entry.parse_now(now))
    rules = {"stop": "range", "r": 2, "flat_minutes": 5}
    rules.update(exit_rules)
    t["exit"] = exit_.plan_exit(rules, t["entry"], t["atr14"], t["session_date"])
    return t


class Calendar(unittest.TestCase):
    def test_every_copy_of_the_nyse_calendar_matches_trader_alerts(self):
        for mod in (scan, entry, exit_):
            self.assertEqual(mod.NYSE_HOLIDAYS, alerts.NYSE_HOLIDAYS, mod.__name__)
            self.assertEqual(mod.NYSE_EARLY_CLOSE, alerts.NYSE_EARLY_CLOSE, mod.__name__)

    def test_bars_keep_the_regular_session_only(self):
        bars = entry.get_bars("AMD", "5m", offline=D)
        self.assertTrue(all("09:30" <= b["time"] < "16:00" for b in bars))
        days = entry.by_day(bars)
        self.assertEqual(len(days), 22)
        self.assertEqual(len(days["2026-11-17"]), 78)
        self.assertEqual(days["2026-11-17"][0]["time"], "09:30")


class Scanner(unittest.TestCase):
    def daily(self):
        return {s: scan.get_bars(s, "1d", offline=D) for s in WATCH}

    def test_default_rules_pass_nothing_on_a_quiet_day(self):
        rows, errors = scan.scan(self.daily(), scan.parse_now("2026-11-17T16:30"))
        self.assertEqual(errors, [])
        self.assertFalse(any(r["passes"] for r in rows))
        amd = [r for r in rows if r["symbol"] == "AMD"][0]
        self.assertEqual(amd["gap_pct"], 1.45)
        self.assertEqual(amd["rvol"], 0.96)
        self.assertIn("gap +1.45% is under 2%", amd["fails"])

    def test_looser_rules_rank_by_gap_times_volume(self):
        rules = {"min_gap_pct": 0.4, "min_rvol": 0.9}
        rows, _ = scan.scan(self.daily(), scan.parse_now("2026-11-17T16:30"), rules)
        self.assertEqual([r["symbol"] for r in rows if r["passes"]], ["AMD", "NVDA", "KO"])
        self.assertEqual(rows[0]["rank"], 1)

    def test_direction_up_drops_gap_downs(self):
        rules = {"min_gap_pct": 0.4, "min_rvol": 0.9, "direction": "up"}
        rows, _ = scan.scan(self.daily(), scan.parse_now("2026-11-17T16:30"), rules)
        self.assertEqual([r["symbol"] for r in rows if r["passes"]], ["AMD", "NVDA"])

    def test_before_the_open_there_is_no_gap(self):
        rows, _ = scan.scan(self.daily(), scan.parse_now("2026-11-17T08:00"))
        self.assertTrue(all(r["gap_pct"] is None for r in rows))
        self.assertIn("no gap yet: the NYSE has not opened today", rows[0]["fails"])

    def test_relative_volume_is_time_adjusted(self):
        frac = scan.session_fraction(scan.parse_now("2026-11-17T12:45"), scan.parse_now("2026-11-17T12:45").date())
        self.assertAlmostEqual(frac, 0.5, places=3)

    def test_cli_needs_a_list(self):
        code, _, err = run(scan, ["--offline", D])
        self.assertEqual(code, 1)
        self.assertIn("only looks at a list you give it", err)

    def test_cli_writes_the_scan(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "scan.json")
            code, out, _ = run(scan, ["--tickers"] + WATCH + ["--offline", D, "--now", "2026-11-17T16:30",
                                                              "--min-gap", "0.4", "--min-rvol", "0.9", "--write", path])
            self.assertEqual(code, 0)
            self.assertIn("3 passed: AMD, NVDA, KO", out)
            with open(path) as fh:
                self.assertEqual(json.load(fh)["passed"], ["AMD", "NVDA", "KO"])
        finally:
            shutil.rmtree(tmp)


class Entry(unittest.TestCase):
    def test_orb_waits_for_the_trigger(self):
        t = amd_trade("2026-11-17T10:00")
        e = t["entry"]
        self.assertEqual((e["range_low"], e["range_high"]), (500.5, 511.85))
        self.assertEqual((e["trigger"], e["limit"], e["order_type"]), (511.86, 512.37, "stop_limit"))
        self.assertEqual(e["status"], "waiting")
        self.assertEqual(t["atr14"], 47.66)

    def test_orb_is_not_ready_before_the_range_closes(self):
        bars = entry.get_bars("AMD", "5m", offline=D)
        setup = {"type": "orb", "minutes": 15}
        t = entry.build_trade("AMD", setup, bars, entry.parse_now("2026-11-17T09:40"))
        self.assertEqual(t["entry"]["status"], "not_ready")
        self.assertIn("09:45", t["entry"]["why"])

    def test_orb_expires(self):
        days = entry.by_day(entry.get_bars("AMD", "5m", offline=D))
        e = entry.plan_day({"type": "orb", "minutes": 15, "valid_until": "11:30"}, days, "2026-11-17")
        self.assertEqual(e["status"], "expired")

    def test_after_the_close_it_plans_the_next_session(self):
        bars = entry.get_bars("TSLA", "5m", offline=D)
        t = entry.build_trade("TSLA", {"type": "pdh"}, bars, entry.parse_now("2026-11-17T16:30"))
        self.assertEqual(t["session_date"], "2026-11-18")
        self.assertEqual(t["entry"]["prior_high"], max(b["h"] for b in entry.by_day(bars)["2026-11-17"]).__round__(2))
        self.assertEqual(t["entry"]["status"], "waiting")

    def test_a_trigger_that_already_traded_is_not_a_plan(self):
        days = entry.by_day(entry.get_bars("AMD", "5m", offline=D))
        e = entry.plan_day({"type": "orb", "minutes": 15}, days, "2026-11-09")
        self.assertEqual(e["status"], "filled")
        self.assertEqual(e["fill_time"], "09:45")

    def test_pdh_before_the_open_plans_against_yesterday(self):
        bars = entry.get_bars("TSLA", "5m", offline=D)
        t = entry.build_trade("TSLA", {"type": "pdh"}, bars, entry.parse_now("2026-11-17T09:00"))
        self.assertEqual(t["entry"]["prior_high"], 389.8)
        self.assertEqual(t["entry"]["trigger"], 389.81)
        self.assertEqual(t["entry"]["status"], "waiting")

    def test_weekend_plans_the_next_session(self):
        bars = entry.get_bars("AMD", "5m", offline=D)
        t = entry.build_trade("AMD", {"type": "pdh"}, bars, entry.parse_now("2026-11-14T12:00"))
        self.assertEqual(t["session_date"], "2026-11-16")

    def test_level_below_the_price_is_a_limit_buy(self):
        days = entry.by_day(entry.get_bars("AMD", "5m", offline=D))
        e = entry.plan_day({"type": "level", "price": 495}, days, "2026-11-17", "10:00")
        self.assertEqual((e["order_type"], e["limit"]), ("limit", 495.0))

    def test_minutes_must_fit_the_bars(self):
        days = entry.by_day(entry.get_bars("AMD", "5m", offline=D))
        with self.assertRaises(entry.ToolError):
            entry.plan_day({"type": "orb", "minutes": 7}, days, "2026-11-17")


class Exit(unittest.TestCase):
    def test_range_stop_and_2r_target(self):
        x = amd_trade()["exit"]
        self.assertEqual((x["stop"], x["target"], x["flat_by"]), (500.49, 534.6, "15:55"))
        self.assertEqual((x["risk_per_share"], x["worst_risk_per_share"]), (11.37, 11.88))

    def test_atr_and_pct_stops(self):
        self.assertEqual(amd_trade(stop="atr", atr_mult=0.5)["exit"]["stop"], 488.03)
        self.assertEqual(amd_trade(stop="pct", pct=1)["exit"]["stop"], 506.74)

    def test_flat_by_follows_an_early_close(self):
        self.assertEqual(exit_.flat_by("2026-11-27", 5), ("12:55", "13:00"))
        with self.assertRaises(exit_.ToolError):
            exit_.flat_by("2026-11-26", 5)

    def test_a_stop_over_the_entry_is_refused(self):
        with self.assertRaises(exit_.ToolError):
            amd_trade(stop="price", stop_price=520)

    def test_walk_is_conservative(self):
        bars = [{"time": "10:00", "o": 10, "h": 10.5, "l": 9.4, "c": 10.2},
                {"time": "10:05", "o": 10.2, "h": 12, "l": 9.0, "c": 11}]
        # the fill bar reached the stop: a loss
        self.assertEqual(exit_.walk_exit(bars, 0, 10, 9.5, 11, "15:55")["reason"], "stop")
        bars[0]["l"] = 9.8
        # the next bar touched both: the stop
        w = exit_.walk_exit(bars, 0, 10, 9.5, 11, "15:55")
        self.assertEqual((w["reason"], w["exit"], w["same_bar"]), ("stop", 9.5, True))
        # a gap through the stop fills at the open
        bars[1].update({"o": 9.0, "h": 9.2})
        self.assertEqual(exit_.walk_exit(bars, 0, 10, 9.5, 11, "15:55")["exit"], 9.0)
        # flat at the open of the first bar at the flat-by time
        bars[1].update({"time": "15:55", "o": 10.1, "l": 10.0})
        w = exit_.walk_exit(bars, 0, 10, 9.5, 11, "15:55")
        self.assertEqual((w["reason"], w["exit"]), ("flat", 10.1))


class TimeMachine(unittest.TestCase):
    def test_replays_the_amd_orb(self):
        res = tm.replay(amd_trade(), entry.get_bars("AMD", "5m", offline=D), entry, exit_, 60, 0.01)
        s = res["summary"]
        self.assertEqual((s["sessions"], s["trades"], s["wins"], s["losses"]), (21, 13, 6, 7))
        self.assertEqual((s["first"], s["last"]), ("2026-10-19", "2026-11-16"))
        self.assertEqual(s["avg_r"], -0.08)
        self.assertEqual(s["total_r"], -1.03)
        self.assertEqual(s["max_drawdown_r"], 3.8)
        self.assertEqual(s["longest_losing_streak"], 4)
        self.assertEqual(s["by_exit"], {"flat": 9, "stop": 3, "target": 1})
        self.assertNotIn("2026-11-17", [r["date"] for r in res["rows"]])

    def test_uses_the_same_functions_as_the_plan(self):
        e, x = tm.modules()
        self.assertTrue(hasattr(e, "plan_day") and hasattr(x, "walk_exit"))

    def test_refuses_what_cannot_be_replayed(self):
        bars = entry.get_bars("AMD", "5m", offline=D)
        t = amd_trade(stop="price", stop_price=500)
        with self.assertRaises(tm.ToolError):
            tm.replay(t, bars, entry, exit_)
        t = amd_trade()
        t["exit"] = None
        with self.assertRaises(tm.ToolError):
            tm.replay(t, bars, entry, exit_)

    def test_cli_saves_the_summary_into_the_trade(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "trade.json")
            with open(path, "w") as fh:
                json.dump(amd_trade(), fh)
            code, out, _ = run(tm, ["--trade", path, "--offline", D, "--write", "--out",
                                    os.path.join(tmp, "bt.json"), "--risk-usd", "50", "--config", "none"])
            self.assertEqual(code, 0)
            self.assertIn("13 trades in 21 sessions", out)
            self.assertIn("too few to tell skill from luck", out)
            self.assertIn("lost money", out)
            with open(path) as fh:
                self.assertEqual(json.load(fh)["backtest"]["trades"], 13)
        finally:
            shutil.rmtree(tmp)


def backtested():
    t = amd_trade()
    res = tm.replay(t, entry.get_bars("AMD", "5m", offline=D), entry, exit_, 60, 0.01)
    t["backtest"] = dict(res["summary"], rules={"setup": t["setup"], "exit": t["exit"]["rules"]})
    return t


CFG = {"budget": 5000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_usd": 2000,
       "risk_per_trade_pct": 1, "paper": True}


class Budget(unittest.TestCase):
    def test_sizes_one_bracket_from_the_risk(self):
        t = size.size_trade(backtested(), CFG)
        self.assertEqual(t["shares"], 2)
        self.assertEqual(t["binding"], "position cap")
        self.assertEqual(t["max_shares"]["risk"], 4.21)
        self.assertEqual(t["loss_at_stop"], 23.76)
        o = size.trade_order(t)
        self.assertEqual({k: o[k] for k in ("qty", "type", "stop_price", "limit_price", "order_class",
                                            "take_profit_limit_price", "stop_loss_stop_price")},
                         {"qty": "2", "type": "stop_limit", "stop_price": "511.86", "limit_price": "512.37",
                          "order_class": "bracket", "take_profit_limit_price": "534.60",
                          "stop_loss_stop_price": "500.49"})
        self.assertTrue(any("lost money" in w for w in t["warnings"]))

    def test_risk_binds_with_a_bigger_cap(self):
        t = size.size_trade(backtested(), dict(CFG, max_position_pct=60, max_order_usd=3000))
        self.assertEqual((t["shares"], t["binding"]), (4, "risk"))

    def test_refuses_a_stale_or_unfinished_plan(self):
        t = backtested()
        t["entry"]["status"] = "filled"
        with self.assertRaises(size.SizeError):
            size.size_trade(t, CFG)
        t = backtested()
        t["exit"] = None
        with self.assertRaises(size.SizeError):
            size.size_trade(t, CFG)

    def test_warns_when_not_replayed(self):
        t = amd_trade()
        self.assertTrue(any("not replayed" in w for w in size.size_trade(t, CFG)["warnings"]))


class RiskAndBroker(unittest.TestCase):
    def order(self, **kw):
        o = size.trade_order(size.size_trade(backtested(), CFG))
        o.update(kw)
        return o

    def test_the_bracket_passes(self):
        res = risk.check([self.order()], CFG, today="2026-11-17")
        self.assertEqual(res["results"][0]["verdict"], "PASS", res["results"][0]["reasons"])

    def test_too_much_risk_is_blocked(self):
        res = risk.check([self.order(qty="5")], dict(CFG, max_position_pct=60, max_order_usd=3000),
                         today="2026-11-17")
        self.assertIn("risk:", " ".join(res["results"][0]["reasons"]))

    def test_bad_brackets_are_blocked(self):
        bad = [self.order(stop_loss_stop_price="515"), self.order(qty="1.5"), self.order(side="sell")]
        for o in bad:
            r = risk.check([o], CFG, today="2026-11-17")["results"][0]
            self.assertEqual(r["verdict"], "BLOCK", o)

    def test_a_plan_from_another_day_is_stale(self):
        r = risk.check([self.order()], CFG, today="2026-11-18")["results"][0]
        self.assertIn("stale", " ".join(r["reasons"]))

    def test_the_ticket_says_every_leg(self):
        text = ticket.plain(self.order())
        self.assertIn("take profit at $534.60, stop loss at $500.49", text)
        self.assertIn("flat by 15:55", text)
        self.assertNotIn("plan", ticket.tool_input(self.order()))
        self.assertEqual(ticket.fingerprint(self.order()), risk.fingerprint(self.order()))

    def test_flatten_needs_an_explicit_yes(self):
        tmp = tempfile.mkdtemp()
        try:
            st = os.path.join(tmp, "broker.json")
            code, out, _ = run(ticket, ["flatten", "--symbol", "AMD", "--reply", "ok", "--state", st])
            self.assertEqual(code, 1)
            self.assertFalse(os.path.exists(st))
            code, out, _ = run(ticket, ["flatten", "--symbol", "AMD", "--reply", "yes", "--state", st])
            self.assertEqual(code, 0)
            self.assertIn("cancel_order_by_id", out)
            self.assertNotIn("close_all_positions", out)
        finally:
            shutil.rmtree(tmp)


class Alerts(unittest.TestCase):
    def test_from_trade_gives_three_rules_for_that_day(self):
        rules = alerts.trade_rules(amd_trade())
        self.assertEqual([r["type"] for r in rules], ["stop", "target", "flat_by"])
        self.assertTrue(all(r["date"] == "2026-11-17" for r in rules))

    def ring(self, rules, when):
        now = alerts.parse_now(when)
        return alerts.evaluate(rules, {}, alerts.market_status(now), {}, now)[0]

    def test_flat_by_rings_once_at_the_time(self):
        rules = [{"id": "amd-flat-by", "symbol": "AMD", "type": "flat_by", "minutes_before_close": 5}]
        self.assertEqual(self.ring(rules, "2026-11-17T15:54"), [])
        rings = self.ring(rules, "2026-11-17T15:55")
        self.assertEqual(len(rings), 1)
        self.assertIn("Flat by the close", rings[0]["message"])
        now = alerts.parse_now("2026-11-17T15:56")
        _, state = alerts.evaluate(rules, {}, alerts.market_status(now), {}, now)
        later = alerts.parse_now("2026-11-17T15:58")
        self.assertEqual(alerts.evaluate(rules, {}, alerts.market_status(later), state, later)[0], [])

    def test_flat_by_follows_the_early_close(self):
        rules = [{"id": "f", "symbol": "AMD", "type": "flat_by", "minutes_before_close": 5}]
        self.assertEqual(self.ring(rules, "2026-11-27T12:54"), [])
        self.assertEqual(len(self.ring(rules, "2026-11-27T12:55")), 1)
        fixed = [{"id": "g", "symbol": "AMD", "type": "flat_by", "time": "15:55"}]
        self.assertEqual(len(self.ring(fixed, "2026-11-27T12:54")), 0)
        self.assertEqual(len(self.ring(fixed, "2026-11-27T12:55")), 1)

    def test_dated_rules_only_count_on_their_day(self):
        rules = [dict(r) for r in alerts.trade_rules(amd_trade())]
        self.assertEqual(self.ring(rules, "2026-11-18T15:56"), [])


if __name__ == "__main__":
    unittest.main()
