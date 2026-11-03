"""The screen as a quant reads it: volatility, weeks as TradingView draws them, strength, who is acting, two independent
things, a risk frame per setup, and a record that says what it cost and what went wrong on the way."""

import asyncio
import time

from synth import analyst, journal, levels, screen, service
from synth.config import Settings
from synth.store import Store
from tests.test_screen import LV

DAY = 86400


def test_weeks_start_on_monday_and_the_forming_week_counts():
    mon = time.mktime(time.strptime("2026-10-26", "%Y-%m-%d")) - time.timezone      # a Monday
    bars = [[(mon + i * DAY) * 1000, 0, 0, 0, 100.0 + i] for i in range(10)]       # Mon 26 Oct to Wed 4 Nov
    assert levels.weekly_closes(bars) == [106.0, 109.0]                            # Sunday's close, then the latest


def test_volatility_from_closed_days():
    bars = [[i * DAY * 1000, 0, 101.0 + i, 99.0 + i, 100.0 + i, 1000.0] for i in range(40)]
    bars[-1][5] = 3000.0
    v = levels.vol_stats(bars)
    assert v["atr14_pct"] == round(100 * 2 / 139, 2) and v["rel_vol"] == 3.0 and v["rv30_pct"] < 5


def test_open_interest_against_price_says_who_is_acting():
    assert screen.oi_class(4.0, 6.0, 1.0) == "longs building"
    assert screen.oi_class(-4.0, 6.0, 1.0) == "shorts building"
    assert screen.oi_class(4.0, -6.0, 1.0) == "short covering"
    assert screen.oi_class(-4.0, -6.0, 1.0) == "long liquidation"
    assert screen.oi_class(0.5, 6.0, 1.0) is None and screen.oi_class(4.0, 1.0, 1.0) is None


def test_near_is_in_the_coins_own_volatility():
    lv = {**LV, "ma200d": 90.0, "atr14_pct": 1.2, "swing_lows": []}
    quiet = screen.coin_row("PAXG", lv, 92.5, None, {})          # 2.8% over its 200-day: more than one range away
    assert quiet["near_pct"] == 1.2 and "at its 200-day average" not in quiet["why_long"]
    wild = screen.coin_row("WIF", {**lv, "atr14_pct": 6.0}, 92.5, None, {})
    assert wild["near_pct"] == 5.0


def test_a_setup_carries_where_it_is_wrong_the_next_level_and_the_room():
    pos = {"conviction_7d": {"long": 1}, "engine_code": "ACCUMULATE"}
    r = screen.coin_row("SOL", {**LV, "atr14_pct": 2.0}, 93.0, {"volume_24h_usd": 5e8, "spread_bps": 2.0}, pos)
    fr = r["levels_long"]
    assert fr["off"] == {"level": 92.0, "name": "band low", "pct": -1.1, "atr": 0.6}
    assert fr["next"] == {"level": 96.0, "name": "band high", "pct": 3.2, "atr": 1.6}   # the band's top comes first
    assert fr["room_over_risk"] == 2.9
    assert r["lined_up_long"].keys() == {"price", "positioning", "engine"} and r["independent_long"] == 3
    # the short fade's off level is the recent high
    hot = screen.coin_row("HYPE", {**LV, "ma200d": 60.0, "sma20w": 80.0, "ema21w": 84.0}, 105.0, None,
                          {"z_1d": 3.1})
    assert hot["levels_short"]["off"]["name"] == "recent high" and hot["levels_short"]["off"]["level"] == 125.0


def test_candidates_need_two_things_and_a_liquid_book():
    pos = {"conviction_7d": {"long": 1}, "engine_code": "ACCUMULATE"}
    liquid = {"volume_24h_usd": 5e8, "spread_bps": 2.0}
    rows = [screen.coin_row("SOL", LV, 93.0, liquid, pos),
            screen.coin_row("THIN", LV, 93.0, {"volume_24h_usd": 4e5, "spread_bps": 40.0}, pos),
            screen.coin_row("ONE", LV, 93.0, liquid, {})]                        # the band and nothing else
    assert rows[1]["thin"] and rows[2]["independent_long"] == 1
    cand = screen.candidates({"rows": rows})
    assert [c["coin"] for c in cand] == ["SOL"] and cand[0]["independent"] == 3
    assert cand[0]["off"]["name"] == "band low" and "engine_code" not in cand[0]


def test_strength_against_btc_ranks_and_measures_beta():
    days = 91
    btc = [100.0 * (1.001 ** i) * (1.02 if i % 2 else 0.98) for i in range(days)]
    alt = [100.0 * (1.004 ** i) * (1.04 if i % 2 else 0.96) for i in range(days)]     # twice BTC's swings, stronger
    lag = [100.0 * (0.998 ** i) for i in range(days)]
    lvs = {c: {"closes": cl, "closes_to": "2026-11-02"} for c, cl in (("BTC", btc), ("ALT", alt), ("LAG", lag))}
    got = screen.strength(lvs)
    assert got["ALT"]["rs_rank_30d"] == 100 and got["LAG"]["rs_rank_30d"] == 0
    assert 1.8 < got["ALT"]["beta_btc"] < 2.2 and got["ALT"]["corr_btc"] > 0.95
    r = screen.coin_row("ALT", LV, 93.0, None, {}, got["ALT"])
    assert any(w.startswith("among the strongest coins over 30 days") for w in r["why_long"])
    assert "BTC" not in got or "rs_rank_30d" not in got["BTC"]


def test_a_view_gets_its_risk_frame_in_code_and_reads_in_three_lines():
    cand = {"coin": "SOL", "side": "long", "price": 93.0, "band": {"low": 92.0, "high": 96.0, "state": "inside"},
            "ma200d": 85.0, "swing_low": 90.5, "high": 125.0, "z_1d": 0.6, "atr_pct": 2.0,
            "off": {"level": 92.0, "name": "band low", "pct": -1.1, "atr": 0.6},
            "next": {"level": 104.0, "name": "swing high", "pct": 11.8}, "room_over_risk": 10.7}
    cur = {"candidates": [cand], "direction": [], "coins": [], "my_positions": []}
    view = {"coin": "SOL", "lean": "long", "strength": 2, "why": "Two proven traders added into the band.",
            "chart": "the band at 92-96", "off_level": "swing low", "changes_if": "they cut"}
    out = analyst.Analyst.clean({"views": [view]}, cur)
    risk = out["views"][0]["risk"]
    assert risk["off"] == {"level": 90.5, "name": "swing low", "pct": -2.7, "atr": 1.4}   # the level he named
    assert risk["room_over_risk"] == 4.4 and risk["z"] == 0.6 and risk["band"] == "inside"
    lines = analyst.setup_lines(out["views"][0])
    assert lines[0] == "SOL long · firm · 93.00 · z 0.6 · inside band"
    assert lines[2] == ("Off below 90.50 (swing low, -2.7%, 1.4 ATR) · next level 104.00 (swing high, +11.8%) · room "
                        "4.4x the risk. Ends if they cut.")


def test_shadow_controls_are_logged_beside_a_read_and_never_twice_a_day(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"))
    store = Store(s.db_path)
    agent = service.Agent(s, store)
    now = time.time()
    agent.tracker.last_mids, agent.tracker.last_mids_ts = {"SOL": 93.0, "ETH": 3000.0, "ARB": 0.5, "OP": 1.2}, now
    an = analyst.Analyst(agent, "claude-sonnet-5-5", client=object())
    pos = {"conviction_7d": {"long": 1}, "engine_code": "ACCUMULATE"}
    liquid = {"volume_24h_usd": 5e8, "spread_bps": 2.0}
    table = {"rows": [screen.coin_row(c, LV, px, liquid, pos if c == "SOL" else {})
                      for c, px in (("SOL", 93.0), ("ETH", 3000.0), ("ARB", 0.5), ("OP", 1.2))]}
    views = [{"coin": "ETH", "lean": "short"}]
    ids = an.log_shadows(views, now, table)
    picks = {p["origin"]: p for p in store.picks(limit=10)}
    assert len(ids) == 2 and picks["screen"]["coin"] == "SOL" and picks["screen"]["direction"] == "long"
    assert picks["random"]["direction"] == "short" and picks["random"]["coin"] != "ETH"
    assert an.log_shadows(views, now + 60, table) == [] or len(store.picks(limit=10)) <= 3


def test_the_path_to_a_horizon_is_kept_net_of_costs(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"))
    store = Store(s.db_path)
    agent = service.Agent(s, store)
    now = time.time()
    t0 = now - DAY - 600
    for h, (px, btc) in enumerate([(100.0, 50_000.0), (97.0, 49_500.0), (99.0, 50_000.0), (104.0, 51_000.0)]):
        store.add_series("hl_ctx", {"c": {"SOL": [px, 1, 0.0001], "BTC": [btc, 1, 0.0]}}, t0 + h * 6 * 3600)
    store.add_series("hl_ctx", {"c": {"SOL": [105.0, 1, 0.0001], "BTC": [51_000.0, 1, 0.0]}}, t0 + DAY)
    pid = store.add_pick("read", "SOL", "long", 2, 100.0, "why", {"risk": {"off": {"level": 98.0}}, "beta_btc": 1.5})
    with store._lock:
        store._db.execute("UPDATE picks SET ts=? WHERE id=?", (t0, pid))
        store._db.commit()
    assert agent.score_picks({}) == 1
    p = store.picks(origin="read")[0]
    path = p["state"]["path_1d"]
    assert p["ret_1d"] == 0.05 and path["mae_pct"] == -3.0 and path["mfe_pct"] == 5.0
    assert path["off_hit"] and path["off_hit_h"] == 6.0 and path["funding_pct"] == 0.05
    assert path["net_pct"] == round(100 * (0.05 - 0.0009 - 0.0005), 2) and path["btc_pct"] == 2.0
    b = journal.block([p], "1d")
    assert b["off_hit"] == {"n": 1, "hit": 1} and b["net_mean_pct"] == path["net_pct"]
    assert b["beta_adjusted_median_pct"] == 2.0                          # 5% less 1.5 x BTC's 2%


def test_wilson_and_separate_bets():
    assert journal.wilson(6, 10) == (31, 83) and journal.wilson(0, 0) is None
    t = 1_790_000_000.0
    ps = [{"coin": "SOL", "direction": "long", "ts": t + i * DAY} for i in range(4)]       # daily repeats: one bet
    ps += [{"coin": "SOL", "direction": "long", "ts": t + 10 * DAY}, {"coin": "SOL", "direction": "short", "ts": t}]
    assert journal.episodes(ps) == 3


def test_the_screen_is_logged_once_a_day_as_it_was_seen(tmp_path):
    from tests.test_analyst import make, seed
    import pytest
    mp = pytest.MonkeyPatch()
    try:
        agent, _ = make(tmp_path, mp, [])
        now = time.time()
        seed(agent.store, now)
        agent.store.put_snapshot("trend_levels", {"SOL": LV})
        agent.store.put_snapshot("hl_ctx", {"ts": now, "c": {"SOL": {"px": 93.0, "oi": 1e6, "funding": 0.0,
                                                                     "vol": 3e8}}})
        assert screen.log_day(agent, now) == 1
        rows = {r["coin"]: r for r in agent.store.screen_log()}
        assert rows["SOL"]["band"] == "inside" and rows["SOL"]["long"]["score"] > 0 and rows["*"]["inside"] == 1
    finally:
        mp.undo()


def test_the_engine_list_keeps_the_daily_context_it_already_carries():
    from synth.sources import reflex

    class Rc:
        async def get(self, path):
            return {"results": [{"symbol": "ENA/USDT", "zscore": 2.7, "signal": "TRIM", "regime": "BLOWOFF",
                                 "heat": 90, "sector": "DeFi", "atr_regime": "Elevated", "rel_vol": 2.345,
                                 "exhaustion_state": "CLIMAX_TOP", "is_climax": True, "divergence": "BEARISH"}]}

    got = asyncio.run(reflex.engine_all(Rc()))["ENA"]
    assert got["sector"] == "DeFi" and got["atr_regime"] == "elevated" and got["rel_vol"] == 2.35
    assert got["exhaustion"] == "climax top" and got["climax"] and got["divergence"] == "bearish"


def test_review_fixes_risk_frames_verdict_and_controls(tmp_path):
    from synth import journal
    # a candidate carries the engine's word, not a dict: a view on it reads its frame, on either side
    pos = {"conviction_7d": {"long": 1}, "engine_code": "ACCUMULATE", "engine": "base forming", "z_1d": 0.4}
    row = screen.coin_row("SOL", {**LV, "atr14_pct": 2.0}, 93.0, {"volume_24h_usd": 5e8, "spread_bps": 2.0}, pos)
    cur = {"candidates": screen.candidates({"rows": [row]}), "direction": [], "coins": [], "my_positions": []}
    assert cur["candidates"][0]["engine"] == "base forming"
    views = [{"coin": "SOL", "lean": "long", "strength": 2, "why": "w", "chart": "c", "off_level": "band low",
              "changes_if": "x"},
             {"coin": "SOL", "lean": "short", "strength": 1, "why": "w", "chart": "c", "off_level": "event",
              "changes_if": "x"}]
    out = analyst.Analyst.clean({"views": views}, cur)
    long_, short = (v["risk"] for v in out["views"])
    assert long_["z"] == 0.4 and long_["off"]["name"] == "band low" and long_["next"]["name"] == "band high"
    assert short["off"]["name"] == "band high" and short["next"]["name"] == "band low" and "room_over_risk" in short
    # the bar on unrounded figures: 37 of 60 is 61.7%, under 62% however it rounds
    a = journal._stats([0.03] * 37 + [-0.01] * 23)
    assert a["right_pct"] == 62 and journal.verdict(a) == "context, not a signal"
    assert journal.verdict(journal._stats([0.03] * 38 + [-0.01] * 22)) == "meets the declared bar"
    assert journal.verdict(journal._stats([0.009951] * 40 + [-0.01] * 20)) == "context, not a signal"


def test_one_random_control_per_logged_view_a_day(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"))
    store = Store(s.db_path)
    agent = service.Agent(s, store)
    now = time.time()
    coins = ["SOL", "ETH", "ARB", "OP", "SUI", "TIA"]
    agent.tracker.last_mids, agent.tracker.last_mids_ts = {c: 10.0 for c in coins}, now
    an = analyst.Analyst(agent, "claude-sonnet-5-5", client=object())
    liquid = {"volume_24h_usd": 5e8, "spread_bps": 2.0}
    table = {"rows": [screen.coin_row(c, LV, 10.0, liquid, {}) for c in coins]}
    for k in range(6):                                   # the same view in six reads that day
        an.log_shadows([{"coin": "ETH", "lean": "short"}], now + k * 7200, table)
    assert len(store.picks(origin="random")) == 1
