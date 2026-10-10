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
    assert [desk.lean(3, 1), desk.lean(1, 3), desk.lean(1, 1.2), desk.lean(0, 0)] == ["long", "short", "split", None]
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
    assert h["kpi"]["big_money"]["share_long"] == round(100 * 7.4 / 25.8, 1)
    assert h["kpi"]["cycle"]["type"] == "on" and h["kpi"]["cycle"]["uptrend"] == 81.0
    xpl = next(r for r in h["lenses"] if r["coin"] == "XPL")
    assert xpl["leans"] == {"crowd": "short", "big_money": "short"} and xpl["agree"] == "all short"
    assert len(h["crowd_series"]) == 2 and h["crowd_series"][-1][2] == 81000.0
    r = desk.research(a)
    assert [x["id"] for x in r["studies"]] == ["S1", "S2", "S3", "S4"] and r["market_cycle"]["cycle"]["type"] == "on"
