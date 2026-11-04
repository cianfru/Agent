import time

from synth import journal
from synth.store import Store


def test_record_by_kind_with_longs_shorts_and_the_market_apart(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    # Outrider's views in a falling market (the basket fell 5% over 3 days): two shorts right, two longs wrong
    for side, r1, r3 in (("short", 0.02, 0.04), ("short", 0.01, 0.03), ("long", -0.03, -0.02), ("long", 0.01, -0.08)):
        pid = store.add_pick("read", "SUI", side, 2, 1.8, "why", {})
        store.set_pick_return(pid, "1d", r1)
        store.set_pick_return(pid, "3d", r3)
        store.set_pick_market(pid, "3d", -0.05)
    for i in range(300):                                     # more than a page of tracked traders' positions
        store.add_pick("position", "HYPE", "long", 2 if i < 4 else 1, 40.0, "opened", {})
    rows = journal.record(store)
    assert [r["origin"] for r in rows] == ["read", "conviction", "position"]
    assert [r["logged"] for r in rows] == [4, 4, 296]
    read = rows[0]
    assert (read["longs"], read["shorts"], read["shown"]) == (2, 2, "3d")
    s3 = read["h"]["3d"]
    assert (s3["n"], s3["right"], s3["median_pct"]) == (4, 2, 0.5)
    # against the market: shorts made 4% and 3% while shorting the basket made 5%; the longs lost less than it, once
    drop = lambda d: {k: v for k, v in d.items() if k != "median"}    # noqa: E731  (the unrounded median, for the bar)
    assert drop(s3["short"]["vs_market"]) == {"n": 2, "right": 0, "right_pct": 0, "median_pct": -1.5}
    assert drop(s3["long"]["vs_market"]) == {"n": 2, "right": 1, "right_pct": 50, "median_pct": 0.0}
    assert journal.text(rows[:1]).splitlines() == [
        "Outrider's setups (since 3 Nov): 4 since " + read["since"] + " (2 long, 2 short)",
        "  3d: 2 of 4 right (50%), median +0.5%; beat the market 1 of 4 (25%), median -1.5%",
        "  longs 0/2 right, shorts 2/2 right",
        "  95% interval for right 15-85% · 2 separate bets over 1 day"]     # the same coin and side on one day: one bet
    assert rows[1]["shown"] == "30d" and journal.text(rows[1:2]).splitlines()[1] == "  30d: none scored yet"


def test_reads_summary_tells_a_thin_record_from_a_broken_one(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.add_brief("read", {"reason": "morning", "sent": True, "views": [{"coin": "SUI"}], "logged": [1]})
    store.add_brief("read", {"reason": "market changed", "sent": True, "views": [], "logged": []})
    store.add_brief("read", {"reason": "market changed", "sent": False, "views": [{"coin": "ENA"}], "logged": []})
    store.add_brief("read", {"reason": "market changed", "sent": False, "error": "Expecting value: line 1", "cost": 0})
    r = journal.reads(store, time.time())
    assert (r["run"], r["sent"], r["held_back"], r["failed"], r["sent_without_view"], r["views_logged"]) == (4, 2, 1, 1, 1, 1)
    assert journal.reads_text(r) == ("Outrider's reads, 7 days: 4 ran (3 market changed, 1 morning) · 2 sent · 1 held "
                                     "back · 1 failed · 1 sent with no view · 1 views logged. Last failure: Expecting "
                                     "value: line 1")


def test_s5_judges_separate_bets_beyond_the_market_with_an_interval():
    t = 1_793_000_000.0
    # 70 bets on 35 days, each beating a flat market by 2%: meets the bar
    ok = [{"coin": f"C{i}", "direction": "long", "ts": t + (i // 2) * 86400, "ret_3d": 0.02, "mkt_3d": 0.0}
          for i in range(70)]
    v = journal.s5(ok)
    assert v["bets"] == 70 and v["beat_pct"] == 100 and v["mean_low_pct"] == 2.0 and v["verdict"] == "meets the declared bar"
    # the same coin and side repeated on following days is one bet, scored from its first entry
    rep = [{"coin": "SOL", "direction": "short", "ts": t + i * 86400, "ret_3d": 0.03, "mkt_3d": -0.05}
           for i in range(5)]
    assert journal.s5(rep)["bets"] == 1 and journal.s5(rep)["beat"] == 0     # shorting a falling market is no skill
    # a few big days carry the mean, most bets lose: under the bar
    mixed = [{"coin": f"C{i}", "direction": "long", "ts": t + i * 86400, "ret_3d": 0.30 if i < 5 else -0.01,
              "mkt_3d": 0.0} for i in range(65)]
    assert journal.s5(mixed)["verdict"] == "context, not a signal"
    assert journal.s5_text(journal.s5(ok)).startswith("Setups since 3 Nov (S5): 70 separate bets scored at 3 days")


def test_s6_ranks_the_screen_and_compares_the_read_with_the_code_and_chance(tmp_path):
    from synth import s6
    store = Store(str(tmp_path / "t.db"))
    days = [time.strftime("%Y-%m-%d", time.gmtime(1_793_750_400 + i * 86400)) for i in range(8)]
    # ten coins; a higher long score goes with a bigger rise three days later
    for i, d in enumerate(days):
        rows = []
        for c in range(10):
            price = 100 * (1 + 0.01 * c) ** i
            rows.append((f"C{c}", {"price": price, "z_1d": c / 5, "long": {"score": 10 * c, "lined_up": ["price"]},
                                   "short": {"score": 100 - 10 * c, "lined_up": []}}))
        store.screen_log_add(d, rows)
    rep = s6.screen_ranking(store)
    assert rep["long"]["days"] == 5 and rep["long"]["mean_ic"] == 1.0 and rep["short"]["mean_ic"] == 1.0
    assert rep["long"]["verdict"] == "context, not a signal"            # five days of 60
    assert rep["long"]["top_minus_bottom_pct"] > 0
    txt = s6.text(s6.report(store))
    assert txt.startswith("S6, does the screen rank: 5 days of 60") and "the read against the code" in txt
