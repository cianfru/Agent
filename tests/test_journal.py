from synth import journal
from synth.store import Store


def test_record_by_kind_over_every_pick_with_longs_and_shorts_apart(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    for side, r1, r3 in (("short", 0.02, 0.04), ("short", 0.01, -0.01), ("long", -0.03, None), ("long", 0.01, None)):
        pid = store.add_pick("read", "SUI", side, 2, 1.8, "why", {})
        store.set_pick_return(pid, "1d", r1)
        if r3 is not None:
            store.set_pick_return(pid, "3d", r3)
    for _ in range(300):                                     # more than a page of tracked traders' positions
        store.add_pick("position", "HYPE", "long", 1, 40.0, "opened", {})
    rows = journal.record(store)
    assert [r["origin"] for r in rows] == ["read", "position"] and rows[1]["logged"] == 300
    read = rows[0]
    assert (read["logged"], read["longs"], read["shorts"], read["shown"]) == (4, 2, 2, "3d")
    assert read["h"]["1d"] == {"n": 4, "right": 3, "right_pct": 75, "median_pct": 1.0,
                               "long": {"n": 2, "right": 1, "right_pct": 50, "median_pct": -1.0},
                               "short": {"n": 2, "right": 2, "right_pct": 100, "median_pct": 1.5}}
    assert read["h"]["3d"]["n"] == 2 and read["h"]["7d"]["n"] == 0
    assert rows[1]["shown"] == "30d" and rows[1]["h"]["30d"]["n"] == 0     # nothing scored yet: the study's horizon
    assert journal.text(rows).splitlines() == [
        "Outrider's views: 4 since " + read["since"] + " (2 long, 2 short)",
        "  3d: 1 of 2 right (50%), median +1.5%",
        "  longs 0/0 right, shorts 1/2 right",
        "Tracked traders' new positions: 300 since " + rows[1]["since"] + " (300 long, 0 short)",
        "  30d: none scored yet"]
