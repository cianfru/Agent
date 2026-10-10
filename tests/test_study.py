import asyncio

from synth import notify, study, tracking
from synth.config import Settings
from synth.store import Store
from tests.test_groups import FakeHL, FakeReflex


def test_alignment_and_reading():
    assert study.alignment("long", "LIGHT_LONG", True) == "with"
    assert study.alignment("long", "TRIM", True) == "against"
    assert study.alignment("short", "LIGHT_SHORT", True) == "with"
    assert study.alignment("short", "WAIT", True) == "none"
    assert study.alignment("short", None, False) == "unknown"
    rf = {"engine": {"STRK": {"1d": "RISK_OFF", "4h": "REVIVAL_SEED_CONFIRMED"}}, "coins": {}}
    assert study.reading(rf, "strk", "short") == {"signal_1d": "RISK_OFF", "signal_4h": "REVIVAL_SEED_CONFIRMED",
                                                  "align_1d": "with", "align_4h": "against"}
    # older snapshots: fall back to the top coins' rows
    assert study.reading({"coins": {"BTC": {"signal_1d": "WAIT"}}}, "BTC", "long")["align_1d"] == "none"
    assert study.reading({}, "BTC", "long")["align_1d"] == "unknown"


def test_report_splits_by_engine_and_counts_traders():
    def g(i, align, span, status="closed"):
        return {"id": i, "venue": "hyperliquid", "status": status, "coin": f"C{i}",
                "meta": {"span_h": span, "study": {"logged": True, "align_1d": align, "align_4h": "none"}}}
    groups = [g(1, "with", 2), g(2, "with", 10), g(3, "against", 1, "open"),
              {"id": 4, "coin": "X", "venue": "hyperliquid", "status": "open", "meta": {"span_h": 1}}]  # not logged
    members = {1: [{"pnl_pct": 0.04}, {"pnl_pct": -0.01}], 2: [{"pnl_pct": -0.02}], 3: [{"pnl_pct": None}]}
    picks = {1: {"ret_1d": 0.02, "ret_3d": 0.05, "ret_7d": None}, 2: {"ret_1d": -0.01, "ret_3d": None, "ret_7d": None},
             3: {"ret_1d": -0.03, "ret_3d": None, "ret_7d": None}}
    r = study.report(groups, members, picks, fast_hours=6)
    assert r["logged"] == 3 and not r["ready"] and r["needs"] == {"with": 28, "against_or_none": 29}
    w = r["headline_1d"]["with"]
    assert w["groups"] == 2 and w["1d"] == {"n": 2, "median_pct": 0.5, "positive_pct": 50.0}
    assert w["traders_closed"]["n"] == 2 and w["traders_closed"]["median_pct"] == -0.25
    assert r["fast_1d"]["with"]["groups"] == 1 and r["fast_1d"]["against"]["1d"]["median_pct"] == -3.0
    assert r["by_4h"]["none"]["groups"] == 3


def test_new_group_logs_engine_reading_on_its_pick(tmp_path, monkeypatch):
    async def fake(*a, **k):
        return {"message_id": 1}

    monkeypatch.setattr(notify, "telegram", fake)
    monkeypatch.setattr(notify, "telegram_photo", fake)
    s = Settings(db_path=str(tmp_path / "t.db"))
    store = Store(s.db_path)
    tr = tracking.GroupTracker(s, store, FakeReflex(), FakeHL())
    conv = {"coin": "STRK", "side": "short", "n": 2, "span_h": 0.3, "ts": 2000.0, "key": "conv:STRK:short:1000:2"}
    snaps = {"reflex": {"convergences": [conv], "coins": {}, "engine": {"STRK": {"1d": "TRIM", "4h": "WAIT"}}}}
    asyncio.run(tr.step(snaps, {}, set(), now=2100.0))
    st = store.get_group(1)["meta"]["study"]
    assert st["logged"] and st["align_1d"] == "with" and st["align_4h"] == "none"
    assert tr.last_mids == {"STRK": 0.07}

    # seen long after it formed (e.g. after a restart): kept as a group, not logged for the study
    s2 = Settings(db_path=str(tmp_path / "t2.db"))
    store2 = Store(s2.db_path)
    asyncio.run(tracking.GroupTracker(s2, store2, FakeReflex(), FakeHL()).step(snaps, {}, set(), now=20_000.0))
    assert store2.get_group(1)["meta"]["study"]["logged"] is False and not store2.picks(origin="group")
