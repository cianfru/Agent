import asyncio
import json
import time

from synth import levels, screen
from tests.test_analyst import make, seed
from tests.test_levels import FakeHL

LV = {"sma20w": 92.0, "ema21w": 96.0, "ma200d": 85.0, "high": 125.0, "leg_low": 60.0, "high_bars_ago": 30,
      "low": 60.0, "low_bars_ago": 100, "drop_high": 70.0, "swing_lows": [[90.5, "30 Oct"]], "swing_highs": [[104.0, "28 Oct"]]}


def test_a_pullback_inside_the_band_in_an_uptrend_scores_and_says_why():
    pos = {"conviction_7d": {"long": 1}, "added_24h": {"long": 3}, "tracked": {"lean": "long"}, "engine_code": "ACCUMULATE"}
    r = screen.coin_row("SOL", LV, 93.0, {"funding_apr_pct": 11.0}, pos)
    assert r["band"] == {"low": 92.0, "high": 96.0, "state": "inside", "vs_pct": 0.0}
    assert (r["vs_ma200d_pct"], r["retraced_pct"], r["swing_low_pct"]) == (9.4, 49, -2.7)
    assert r["score_long"] == 35 + 10 + 20 + 10 + 5 + 15
    assert r["why_long"] == ["inside its band", "gave back 49% of its last leg", "1 conviction bet long in 7 days",
                             "3 tracked traders added longs in 24h", "tracked traders lean long", "engine: base forming"]
    assert r["score_short"] == 0                                     # over the 200-day: no short location; engine against
    far = screen.coin_row("SOL", LV, 120.0, None, {})                # 25% over the band
    assert far["band"]["state"] == "extended" and far["score_long"] == 0 and far["why_long"] == ["extended over its band"]


def test_a_bounce_into_the_band_from_under_scores_a_short():
    lv = {**LV, "ma200d": 110.0, "high": 125.0, "high_bars_ago": 100, "low": 80.0, "low_bars_ago": 10, "drop_high": 125.0}
    r = screen.coin_row("XYZ", lv, 90.0, {"funding_apr_pct": -60.0}, {"engine_code": "RISK_OFF"})
    assert r["band"]["state"] == "below" and r["bounced_of_drop_pct"] == 22
    assert r["score_short"] == 35 + 15 - 5 and r["why_short"] == ["just under its band", "engine: leaning down",
                                                                  "crowded: funding -60% a year"]


def test_pick_sorts_and_filters_the_whole_table():
    rows = [screen.coin_row(c, LV, px, None, {}) for c, px in (("A", 93.0), ("B", 97.0), ("C", 120.0), ("D", 80.0))]
    assert [r["coin"] for r in screen.pick(rows, "band")] == ["A", "B", "D", "C"]      # inside, 1% over, under, extended
    assert [r["coin"] for r in screen.pick(rows, "band", band="near")] == ["A", "B"]
    assert [r["coin"] for r in screen.pick(rows, "score", side="long", limit=2)] == ["A", "B"]
    assert [r["coin"] for r in screen.pick(rows, "ma200", trend="down")] == ["D"]


def test_the_table_reads_every_coin_with_levels_and_the_chat_can_screen_it(tmp_path, monkeypatch):
    agent, _ = make(tmp_path, monkeypatch, [])
    now = time.time()
    seed(agent.store, now)
    agent.store.put_snapshot("trend_levels", {"SUI": {**LV, "sma20w": 1.7, "ema21w": 1.9, "ma200d": 1.5, "high": 2.6,
                                                      "leg_low": 1.0, "swing_lows": [], "swing_highs": []},
                                              "SOL": LV})
    agent.store.put_snapshot("hl_ctx", {"ts": now, "c": {"SUI": {"px": 1.8, "oi": 1e7, "funding": 0.0, "vol": 1e8},
                                                         "SOL": {"px": 120.0, "oi": 1e6, "funding": 0.0, "vol": 3e8}}})
    t = screen.build(agent, now)
    assert t["coins"] == 2 and t["breadth"]["inside"] == 1 and t["breadth"]["extended"] == 1
    assert t["breadth"]["over_200d"] == 2
    sui = next(r for r in t["rows"] if r["coin"] == "SUI")
    assert sui["tracked"] == {"long": 0, "short": 4, "lean": "short"} and sui["added_24h"] == {"short": 4}
    got = json.loads(agent.chat.execute("screen", {"sort": "band", "limit": 5}, now))
    assert [r["coin"] for r in got["rows"]] == ["SUI", "SOL"] and got["breadth"]["inside"] == 1
    # SUI long has the price at its band but nothing else lining up (tracked traders are short): no candidate
    assert sui["why_long"][:2] == ["inside its band", "gave back 50% of its last leg"] and sui["independent_long"] == 1
    assert screen.candidates(t) == []


def test_levels_for_every_liquid_coin_are_read_once_a_day(tmp_path):
    from synth.store import Store
    store = Store(str(tmp_path / "t.db"))
    lv = levels.Levels(store)
    store.put_snapshot("hl_ctx", {"ts": 1, "c": {"BTC": {"vol": 9e9}, "SOL": {"vol": 3e8}, "TINY": {"vol": 1.0}}})
    assert lv.universe(["hype"]) == ["BTC", "SOL", "TINY"]               # not listed on Hyperliquid: left out
    store.put_snapshot("hl_ctx", {"ts": 1, "c": {"BTC": {"vol": 9e9}, "SOL": {"vol": 3e8}, "TINY": {"vol": 1.0},
                                                 "HYPE": {"vol": 5e8}}})
    assert lv.universe(["tiny"]) == ["BTC", "HYPE", "SOL", "TINY"]
    now = time.mktime(time.strptime("2026-11-03 06:00", "%Y-%m-%d %H:%M")) - time.timezone
    assert lv.refresh_due(now)
    hl = FakeHL()
    assert asyncio.run(lv.refresh_all(hl, ["BTC", "SOL"], now, pace_s=0)) == 2 and hl.calls == 2
    assert not lv.refresh_due(now + 60)                                  # done for today
    assert asyncio.run(lv.refresh_all(hl, ["BTC", "SOL"], now + 60, pace_s=0)) == 0 and hl.calls == 2


def test_a_stretched_z_score_with_shorts_building_is_a_short_setup_even_in_an_uptrend():
    lv = {**LV, "ma200d": 60.0, "sma20w": 80.0, "ema21w": 84.0}            # far over the 200-day, 25% over the band
    pos = {"z_1d": 2.8, "conviction_7d": {"short": 1}, "tracked": {"lean": "short"}, "engine_code": "TRIM"}
    r = screen.coin_row("HYPE", lv, 105.0, {"funding_apr_pct": 40.0}, pos)
    assert r["band"]["state"] == "extended" and r["z_1d"] == 2.8
    assert r["score_short"] == 35 + 10 + 20 + 5 + 5
    assert r["why_short"] == ["stretched: daily z 2.8, blow-off (2.5); the engine is trimming", "25% over its band",
                              "1 conviction bet short in 7 days", "tracked traders lean short", "longs paying 40% a year"]
    assert r["score_long"] == 0 and "stretched: daily z 2.8, buying here is chasing" in r["why_long"]
    past_trim = screen.coin_row("HYPE", lv, 105.0, None, {"z_1d": 3.2})
    assert past_trim["score_short"] == 50 and past_trim["why_short"][0] == "stretched: daily z 3.2, past trim (3)"
    hard = screen.coin_row("HYPE", lv, 105.0, None, {"z_1d": 3.6})
    assert hard["score_short"] == 55 and hard["z_line"] == "past hard trim (3.5)"
    calm = screen.coin_row("HYPE", lv, 105.0, None, {"z_1d": 1.2})
    assert calm["score_short"] == 0                                     # not stretched, not under the 200-day


def test_every_coins_engine_reading_comes_from_one_list_per_timeframe():
    from synth.sources import reflex

    class Rc:
        async def get(self, path):
            tf = path.split("timeframe=")[1].split("&")[0]
            return {"results": [{"symbol": "HYPE/USDT", "market_kind": "perpetual", "zscore": 2.81234 if tf == "1d" else 1.5,
                                 "signal": "TRIM", "regime": "BLOWOFF", "heat": 88},
                                {"symbol": "HYPE/USDC", "market_kind": "spot", "zscore": 9.9}]}

    got = asyncio.run(reflex.engine_all(Rc()))
    assert got == {"HYPE": {"z_1d": 2.81, "signal_1d": "TRIM", "regime_1d": "BLOWOFF", "heat_1d": 88,
                            "z_4h": 1.5, "signal_4h": "TRIM", "regime_4h": "BLOWOFF"}}
