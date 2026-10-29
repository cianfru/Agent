from synth import analyst, turns as T

COLS = ["day", "n", "uptrend", "overheated", "downtrend", "basing", "median_z", "btc"]


def body(shares, zs=None, fg=None, start=1000):
    return {"columns": COLS,
            "days": [[start + i, 100, s, 0.0, 0.0, 0.0, None if zs is None else zs[i], 100.0] for i, s in enumerate(shares)],
            "fear_greed": [[start + i, v] for i, v in enumerate(fg or [])]}


def test_runs_follow_reflex_rules():
    # the same case as Reflex's marketHeat.test.js: a 4-day dip stays inside, the 5th day under ends the run on the
    # first of the five, which then starts the downturn
    shares = [0.5] * 3 + [0.6] * 5 + [0.5] * 4 + [0.6] * 3 + [0.5] * 6 + [0.6]
    br = T.runs(T.rows_of(body(shares)))
    assert [(r["start"], r["end"]) for r in br["runs"]] == [(1003, 1015), (1021, None)]
    assert br["days"][1010]["age"] == 7
    assert 1015 not in br["days"] and br["down_days"][1015]["age"] == 0


def test_roll_over_turn_up_and_z_turns():
    shares = [0.6, 0.8, 0.95, 0.95, 0.76, 0.74, 0.3, 0.3, 0.3, 0.3, 0.3, 0.1, 0.35]
    zs = [0.2, 1.5, 1.2, 0.8, 0.74, 0.5, 0, -1.5, -1.0, -0.76, 0.0, 0.0, 0.0]
    br = T.runs(T.rows_of(body(shares, zs)))
    run, dn = br["runs"][0], br["downs"][0]
    assert run["roll"] == 1005 and run["roll_from"] == (0.95, 1002)       # 95 -> 74 is 21 points
    assert run["z_turn"] == 1004                                            # 1.5 -> 0.74 is 0.76
    assert dn["start"] == 1006 and dn["turn_up"] == 1012 and dn["low_day"] == 1011
    assert dn["z_turn_up"] == 1010                                          # -1.5 -> 0.0


def test_turns_in_a_broad_uptrend_and_in_a_downturn():
    past = [0.4] * 3 + [0.8] * 10 + [0.4] * 6
    now = [0.8] * 25
    fg = [50] * (len(past) + len(now))
    fg[-20] = 74
    t = T.turns(body(past + now, fg=fg))
    assert t["state"] == "broad uptrend" and t["run_day"] == 24
    assert t["longer_than"] == "1 of 1 past broad uptrends"
    assert t["fear_greed_30d_high"] == 74 and t["marks"]["greed"] is True       # greed 19 days ago counts
    assert T.with_crowd(t, 88.0)["marks"]["traders_long"] is True
    down = T.turns(body([0.8] * 10 + [0.3] * 8, fg=[50] * 15 + [20, 50, 50]))
    assert down["state"] == "broad downturn" and down["downturn_day"] == 7
    assert down["marks"] == {"fear": True, "median_z_turned_up": False, "turned_up": False}
    assert down["last_broad_uptrend"]["days"] == 10
    assert T.with_crowd(down, 25.0)["marks"]["traders_short"] is True
    assert T.turns({"columns": COLS, "days": []}) is None


def test_a_cycle_flip_or_a_new_mark_brings_a_read():
    up = {"state": "broad uptrend", "marks": {"greed": False, "median_z_turned_down": False, "rolled_over": False}}
    z = {**up, "marks": {**up["marks"], "median_z_turned_down": True}}
    got = analyst.changes({"market": {"turns": up}}, {"market": {"turns": z}})
    assert [(c["w"], c["text"]) for c in got] == [(3, "broad uptrend: median z turned down now")]
    flip = analyst.changes({"market": {"turns": z}}, {"market": {"turns": {"state": "broad downturn", "marks": {}}}})
    assert flip[0]["w"] == 3 and "into a broad downturn" in flip[0]["text"]
    assert analyst.changes({"market": {"turns": z}}, {"market": {"turns": z}}) == []
