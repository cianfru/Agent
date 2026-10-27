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
    assert s3["short"]["vs_market"] == {"n": 2, "right": 0, "right_pct": 0, "median_pct": -1.5}
    assert s3["long"]["vs_market"] == {"n": 2, "right": 1, "right_pct": 50, "median_pct": 0.0}
    assert journal.text(rows[:1]).splitlines() == [
        "Outrider's views: 4 since " + read["since"] + " (2 long, 2 short)",
        "  3d: 2 of 4 right (50%), median +0.5%; beat the market 1 of 4 (25%), median -1.5%",
        "  longs 0/2 right, shorts 2/2 right"]
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
