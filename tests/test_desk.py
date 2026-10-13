from synth import desk, service
from synth.config import Settings
from synth.store import Store

HISTORY = {"columns": ["day", "n", "uptrend", "overheated", "downtrend", "basing", "median_z", "btc"],
           "days": [[20719, 140, 0.70, 0.02, 0.1, 0.18, 1.0, 82000.0], [20720, 141, 0.79, 0.02, 0.1, 0.09, 1.2, 83000.0]],
           "today": {"day": 20720, "percentile": 75, "band": "70-85%", "n": 141},
           "cycle": {"type": "on", "start": 20685, "day": 36, "threshold": 0.55, "past": 12, "median": 47.0, "reached": 7,
                     "lasted_more": 5, "runs": [[20000, 40, 0.9]]}}


def test_compact_history_keeps_share_and_cycle():
    c = desk.compact_history(HISTORY)
    assert c["rows"][-1] == ["2026-09-24", 81.0, 83000.0]
    assert c["cycle"]["start"] == "2026-08-20" and c["cycle"]["age_days"] == 36 and c["cycle"]["lasted_30_more"] == 5
    assert c["cycle"]["runs"] == [["2024-10-04", 40, 0.9]] and c["today"]["percentile"] == 75


def test_lean_and_agreement():
    assert [desk.lean(3, 1), desk.lean(1, 3), desk.lean(2, 2), desk.lean(1, 1), desk.lean(0, 0)] == [
        "long", "short", "split", None, None]                                  # under 3 heads: no lean
    assert desk.agreement(["long", "long", None]) == "all long"
    assert desk.agreement(["long", "short", "long"]) == "disagree"
    assert desk.agreement(["long", "split"]) == "mixed" and desk.agreement(["short"]) == "one lens"


def test_home_and_research_on_a_fresh_store(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"))
    store = Store(s.db_path)
    a = service.Agent(s, store)
    a.positions.enabled = False
    store.add_series("reflex", {"w": 1, "stale": False, "c": {"BTC": [3, 3e6, 1, 1e6, 80000.0], "XPL": [2, 2e5, 1, 9e5, 0.087]}}, ts=1_000.0)
    store.add_series("reflex", {"w": 1, "stale": False, "c": {"BTC": [4, 4e6, 1, 1e6, 81000.0], "XPL": [2, 2e5, 1, 9e5, 0.088]}}, ts=1_300.0)
    store.put_snapshot("reflex", {"positions": {"BTC": [4, 4e6, 1, 1e6, 81000.0], "XPL": [2, 2e5, 1, 9e5, 0.088]}})
    store.add_brief("pt_bigmoney", {"coins": {"XPL": {"long_n": 22, "short_n": 23, "long_usd": 7.4e6, "short_usd": 18.4e6}}})
    store.put_snapshot("reflex_history", desk.compact_history(HISTORY))
    h = desk.home(a, now=1_400.0)
    assert h["kpi"]["crowd"]["share_long"] == round(100 * 6 / 8, 2)
    assert h["kpi"]["big_money"]["share_long"] == round(100 * 22 / 45, 1)        # owners, not dollars
    assert h["kpi"]["big_money"]["long_usd"] == 7_400_000
    assert h["kpi"]["cycle"]["type"] == "on" and h["kpi"]["cycle"]["uptrend"] == 81.0
    assert [r["coin"] for r in h["lenses"]] == ["XPL"]                       # BTC and ETH read as direction
    btc, eth = h["direction"]
    assert btc["coin"] == "BTC" and btc["price"] == 81000.0 and btc["change_24h"] == 1.25
    assert btc["lenses"]["crowd"]["share_long"] == 80.0 and btc["lenses"]["crowd"]["lean"] == "long"
    assert btc["crowd_change_24h"] == 5.0 and btc["agree"] == "one lens" and len(btc["series"]) == 2
    assert eth["price"] is None and eth["lenses"] == {} and eth["agree"] == "one lens"
    xpl = next(r for r in h["lenses"] if r["coin"] == "XPL")
    assert xpl["leans"] == {"crowd": "long", "big_money": "split"} and xpl["agree"] == "mixed"
    assert len(h["crowd_series"]) == 2 and h["crowd_series"][-1][2] == 81000.0
    r = desk.research(a)
    assert [x["id"] for x in r["studies"]] == ["S1", "S2", "S3", "S4", "S5"] and r["market_cycle"]["cycle"]["type"] == "on"


def test_basket_index_is_an_equal_weight_index_of_the_alerted_coins():
    rows = [(0, {"c": {"AAA": [1, 1, 0, 0, 10.0], "BBB": [1, 1, 0, 0, 2.0]}}),
            (1, {"c": {"AAA": [1, 1, 0, 0, 20.0]}}),                                  # BBB missing: keeps its last price
            (2, {"c": {"AAA": [1, 1, 0, 0, 20.0], "BBB": [1, 1, 0, 0, 8.0], "CCC": [1, 1, 0, 0, 5.0]}})]
    idx, used = desk.basket_index(rows, ["AAA", "BBB", "ZZZ"])
    assert used == ["AAA", "BBB"]                                                     # never priced: left out
    assert idx == [100.0, round(100 * (2 * 1) ** 0.5, 3), round(100 * (2 * 4) ** 0.5, 3)]
    late, used = desk.basket_index(rows, ["CCC"])                                     # appears late: flat before
    assert used == ["CCC"] and late == [100.0, 100.0, 100.0]
    assert desk.basket_index(rows, []) == ([None, None, None], [])


def test_home_puts_the_alerted_coins_beside_the_crowd(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"))
    store = Store(s.db_path)
    a = service.Agent(s, store)
    a.positions.enabled = False
    store.add_series("reflex", {"w": 1, "stale": False, "c": {"BTC": [3, 3e6, 1, 1e6, 80000.0], "XPL": [2, 2e5, 1, 9e5, 0.087]}}, ts=1_000.0)
    store.add_series("reflex", {"w": 1, "stale": False, "c": {"BTC": [4, 4e6, 1, 1e6, 81000.0], "XPL": [2, 2e5, 1, 9e5, 0.090]}}, ts=1_300.0)
    store.add_brief("pt_build", {"key": "k", "text": "Building · XPL long", "coin": "XPL", "side": "long"})
    h = desk.home(a, now=1_400.0)
    assert h["basket"] == {"coins": ["XPL"], "window_days": 3}
    assert h["alerts"][0]["coin"] == "XPL"
    assert [p[3] for p in h["crowd_series"]] == [100.0, round(100 * 0.090 / 0.087, 3)]
    assert h["kpi"]["crowd"]["spark_btc"] == [80000.0, 81000.0]
    assert h["kpi"]["crowd"]["spark_basket"] == [100.0, round(100 * 0.090 / 0.087, 3)]
