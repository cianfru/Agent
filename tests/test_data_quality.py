"""Funding against its norm and other venues, band and breadth history, the read's figures checked, reads that come
from setups rather than crowd swings, exact highs and lows on the way to a horizon."""

import asyncio
import time

from synth import analyst, hyperliquid, levels, screen, service
from synth.config import Settings
from synth.store import Store
from tests.test_screen import LV

DAY = 86400


def test_other_venues_funding_is_a_year_in_percent(monkeypatch):
    async def post(self, body, tries=3):
        assert body == {"type": "predictedFundings"}
        return [["ETH", [["BinPerp", {"fundingRate": "0.0001", "fundingIntervalHours": 8}],
                         ["HlPerp", {"fundingRate": "0.0000125", "fundingIntervalHours": 1}],
                         ["BybitPerp", {"fundingRate": "0.0002", "fundingIntervalHours": 8}]]]]

    monkeypatch.setattr(hyperliquid.HL, "_post", post)
    got = asyncio.run(hyperliquid.HL(object()).predicted_fundings())
    assert got == {"ETH": {"binance": 11.0, "hl": 11.0, "bybit": 21.9}}


def test_funding_against_its_own_norm_and_the_other_venues(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    lv = levels.Levels(store)
    now = time.time()
    store.put_snapshot("hl_ctx", {"ts": now, "c": {"ENA": {"px": 1.0, "oi": 1e6, "funding": 0.0001, "vol": 1e8}}})
    store.put_snapshot("trend_levels", {"ENA": {"funding_20d_mean_apr": 10.0, "funding_20d_sd_apr": 10.0}})
    store.put_snapshot("funding_venues", {"ts": now, "c": {"ENA": {"binance": 10.0, "bybit": 12.0, "hl": 87.6}}})
    f = lv.oi_funding("ENA", now)
    assert f["funding_apr_pct"] == 87.6 and f["funding_z"] == 7.8 and f["funding_vs_cex_apr_pct"] == 76.6
    r = screen.coin_row("ENA", LV, 93.0, {**f, "funding_24h_avg_apr_pct": 40.0}, {})
    assert any(w.startswith("crowded: funding +40% a year (") for w in r["why_long"])   # 3 sd over its norm


def test_band_history_rebuilds_breadth_for_past_days():
    now = time.time()
    end = int(now // DAY) * DAY
    bars = [[(end - (399 - i) * DAY) * 1000, 0, 0, 0, 100.0 + i, 0] for i in range(400)]   # a steady rise
    h = levels.band_history(bars, now, days=30)
    assert len(h["band_hist"]) == 30 and set(h["band_hist"]) <= {"j", "a", "e"} and h["over200_hist"] == "1" * 30
    falling = [[b[0], 0, 0, 0, 500.0 - i, 0] for i, b in enumerate(bars)]
    lvs = {"UP": h, "DOWN": levels.band_history(falling, now, days=30)}
    days = screen.breadth_history(lvs)
    assert len(days) == 30 and days[-1]["coins"] == 2 and days[-1]["below"] == 1 and days[-1]["over_200d"] == 1


def test_the_reads_figures_are_checked_against_the_scan():
    cur = {"candidates": [{"coin": "SOL", "side": "long", "price": 93.0, "band": {"low": 92.0, "high": 96.0},
                           "swing_low": 90.5}],
           "direction": [{"coin": "BTC", "price": 104_200.0, "change_24h_pct": -4.2}], "coins": [], "my_positions": []}
    out = {"headline": "BTC -4.2% to 104,200", "read": "BTC fell to 99,000 overnight.",
           "views": [{"coin": "SOL", "lean": "long", "chart": "the band low at 92 and the swing low 90.5",
                      "changes_if": "it closes under 88.1 on a 3-day basis"}]}
    got = analyst.read_lint(out, cur)
    assert got["views"][0]["chart"] == "the band low at 92 and the swing low 90.5"      # real levels stay
    assert got["views"][0]["changes_if"] == ""                                          # an invented one goes
    assert got["lint"] == ["SOL changes_if: 88.1 is not one of its levels", "read: 99000 not found in the scan"]


def test_crowd_swings_never_bring_a_read_but_a_new_setup_does():
    prev = {"market": {"crowd_long_pct": 60.0}, "direction": [], "coins": [], "candidates": []}
    cur = {"market": {"crowd_long_pct": 40.0}, "direction": [], "coins": [], "candidates": []}
    ch = analyst.changes(prev, cur)
    assert ch and all(c.get("context") for c in ch)
    cur["candidates"] = [{"coin": "SOL", "side": "long", "score": 62}, {"coin": "ENA", "side": "short", "score": 55}]
    ch = analyst.changes(prev, cur)
    new = [c for c in ch if c["text"].startswith("new setups")]
    assert new and new[0]["w"] == 4 and not new[0].get("context")


def test_engine_codes_reach_the_read_in_words():
    assert analyst.in_words({"btc_regime": "MARKUP", "consensus": "RISK-OFF", "strength": 70}) == {
        "btc_regime": "trending up", "consensus": "risk-off", "strength": 70}
    assert analyst._words("REACC + z<2.5, LIGHT_LONG held") == "cooling off + z<2.5, leaning up held"


def test_the_worst_move_comes_from_hourly_highs_and_lows(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"))
    store = Store(s.db_path)
    a = service.Agent(s, store)
    hour = 1_792_998_000.0                               # on the hour; the pick comes at :50
    t0 = hour + 3000
    pid = store.add_pick("read", "SOL", "short", 2, 100.0, "why", {"risk": {"off": {"level": 104.0}},
                                                                   "path_1d": {"mae_pct": -1.0, "net_pct": 1.0}})
    with store._lock:
        store._db.execute("UPDATE picks SET ts=? WHERE id=?", (t0, pid))
        store._db.commit()

    def bar(h):
        hi = 106.0 if h == 0 else 104.5 if h == 5 else 101.0     # a wick before the pick, in its own hour
        lo = 90.0 if h == 24 else 97.0 if h == 9 else 99.0      # a drop after the horizon
        return [(hour + h * 3600) * 1000, 100, hi, lo, 100, 0]

    class HL:
        async def ensure_names(self):
            pass

        async def candles(self, coin, interval, start_ms, end_ms):
            return [bar(h) for h in range(26)]

    a._refine.append((pid, "1d", t0 + DAY))
    assert asyncio.run(a.refine_paths(HL())) == 1
    p = store.pick(pid)["state"]["path_1d"]
    assert p["mae_pct"] == -4.5 and p["mfe_pct"] == 3.0                # neither the wick nor the later drop
    assert p["off_hit"] and p["off_hit_h"] == round((hour + 5 * 3600 - t0) / 3600, 1)
    assert p["exact"] and p["net_pct"] == 1.0                           # what the hourly copy gave stays


def test_review_fixes_lint_keeps_dates_counts_and_z_lines():
    cur = {"candidates": [{"coin": "SOL", "side": "long", "price": 93.0, "band": {"low": 92.0, "high": 96.0},
                           "swing_low": 90.5}], "direction": [], "coins": [], "my_positions": []}
    notes = ["band low 92 holding for 48 hours", "back over 92 with 12 traders adding",
             "the band low 92 over the next 20 weeks", "the 28 Oct swing low at 90.5", "the Oct 28 low at 90.5",
             "the 2025 low under 90.5", "since 2025-10-28 above 92", "z 2.8, past its 2.5 blow-off line"]
    for n in notes:
        out = analyst.read_lint({"views": [{"coin": "SOL", "lean": "long", "chart": n, "changes_if": ""}]}, cur)
        assert out["views"][0]["chart"] == n and "lint" not in out, n
    out = analyst.read_lint({"views": [{"coin": "SOL", "lean": "long", "chart": "the band low at 92, then 88.1",
                                        "changes_if": ""}]}, cur)
    assert out["views"][0]["chart"] == "the band low at 92"              # only the clause with the invented level goes


def test_review_fixes_funding_at_rest_is_not_crowding():
    r = screen.coin_row("XYZ", LV, 70.0, {"funding_apr_pct": 12.0, "funding_24h_avg_apr_pct": 11.5, "funding_z": 2.5},
                        {})
    assert not any("funding" in w or "paying" in w for w in r["why_short"] + r["why_long"])
    assert "positioning" not in r["lined_up_short"]


def test_review_fixes_breadth_compares_the_same_coins_on_the_same_day(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "t.db"))
    now = time.time()
    to = time.strftime("%Y-%m-%d", time.gmtime(now - DAY))
    # ten coins over the band every day for 30 days
    store.put_snapshot("trend_levels", {f"C{i}": {"band_hist": "a" * 30, "over200_hist": "1" * 30, "hist_to": to}
                                        for i in range(10)})
    got = analyst.breadth_then(store)
    assert got["over_band_pct_week_ago"] == 100
    cur = {"market": {"breadth": {"below": 0, "inside": 0, "just above": 0, "above": 10, "extended": 0,
                                  "over_200d": 10, "with_200d": 10, "coins": 15, **got}}, "direction": []}
    assert "(100% now, 100% a week ago)" in analyst.board_text(cur)        # five coins with no band left out of now
