"""The audit's correctness fixes: the band rule, prices that are not stale, the S5 split, messages he is owed."""

import asyncio
import json
import time

from synth import analyst, detectors, levels, notify, screen, service
from synth.config import Settings
from synth.store import Store
from tests.test_analyst import READ, make, seed
from tests.test_chat import response, text_block
from tests.test_levels import FakeHL
from tests.test_screen import LV


# ------------------------------------------------------------------ screen --
def test_a_long_under_its_band_gets_no_location_points_and_says_why():
    # over its 200-day average and on a swing low, but under the band: the trend is lost until reclaimed
    lv = {**LV, "ma200d": 80.0, "swing_lows": [[89.0, "30 Oct"]]}
    r = screen.coin_row("SOL", lv, 90.0, None, {"engine_code": "ACCUMULATE"})
    assert r["band"]["state"] == "below"
    assert r["why_long"] == ["under its band: trend lost until reclaimed", "engine: base forming"]
    assert r["score_long"] == 15


def test_a_short_far_under_its_band_is_chasing_unless_it_is_a_stretched_fade():
    lv = {**LV, "ma200d": 110.0}
    r = screen.coin_row("XYZ", lv, 70.0, None, {})                    # 24% under the band
    assert r["band"]["vs_pct"] <= -15 and r["score_short"] == 0
    assert r["why_short"] == ["far under its band: chasing a breakdown"]


def test_a_new_high_since_the_levels_were_read_gives_nothing_back():
    r = screen.coin_row("SOL", LV, 130.0, None, {})                    # the cached high is 125
    assert r["new_high"] and r["from_high_pct"] == 0.0 and r["retraced_pct"] == 0
    pa = levels.price_action(LV, 130.0)
    assert pa["new_high_today"] and pa["retraced_of_last_leg_pct"] == 0 and pa["from_recent_high_pct"] == 0.0


# ------------------------------------------------------------------ levels --
def test_a_failed_levels_night_is_tried_again_and_another_read_never_drops_coins(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    lv = levels.Levels(store)
    now = time.mktime(time.strptime("2026-11-03 06:00", "%Y-%m-%d %H:%M")) - time.timezone

    class Flaky(FakeHL):
        async def candles(self, coin, interval, start_ms, end_ms):
            if coin.startswith("BAD"):
                raise RuntimeError("429")
            return await super().candles(coin, interval, start_ms, end_ms)

    coins = ["A", "BAD1", "BAD2", "C"]
    assert asyncio.run(lv.refresh_all(Flaky(), coins, now, pace_s=0)) == 2
    st = store.get_snapshot("levels_refresh")
    assert st["day"] is None and st["errors"] == 2 and st["failed"] and st["failed_coins"] == ["BAD1", "BAD2"]
    assert not lv.refresh_due(now + 600) and lv.refresh_due(st["failed"] + 1801)    # half an hour later, again
    assert set(store.get_snapshot("trend_levels")) == {"A", "C"}
    # a second attempt reads only what is missing; on the third the day closes whatever is left
    asyncio.run(lv.refresh_all(Flaky(), coins, now + 1900, pace_s=0))
    asyncio.run(lv.refresh_all(Flaky(), coins, now + 3800, pace_s=0))
    st = store.get_snapshot("levels_refresh")
    assert st["attempt"] == 3 and st["day"] == "2026-11-03" and st["tried"] == 2


def test_hourly_prices_are_read_at_the_hour_asked(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    lv = levels.Levels(store)
    now = time.time()
    for h in range(5):
        store.add_series("hl_ctx", {"c": {"SOL": [100.0 + h, 1, 0]}}, now - (4 - h) * 3600)
    assert lv.price_at("SOL", now - 3 * 3600 - 300, now=now) == 101.0
    assert lv.price_at("SOL", now - 30 * 3600, now=now) is None          # nothing within two hours
    store.add_series("hl_ctx", {"c": {"SOL": [200.0, 1, 0]}}, now + 3600)  # a new hourly row is picked up
    assert lv.price_at("SOL", now + 3600, now=now + 3600) == 200.0


# --------------------------------------------------------------- scoring --
def test_picks_are_not_scored_or_logged_on_a_stale_price(tmp_path):
    s = Settings(db_path=str(tmp_path / "t.db"))
    store = Store(s.db_path)
    a = service.Agent(s, store)
    now = time.time()
    a.tracker.last_mids, a.tracker.last_mids_ts = {"SOL": 110.0}, now - 4 * 3600     # four hours old
    pid = store.add_pick("read", "SOL", "long", 1, 100.0, "why", {})
    with store._lock:
        store._db.execute("UPDATE picks SET ts=? WHERE id=?", (now - 86400 - 600, pid))
        store._db.commit()
    assert a.price_now("SOL", now) is None and a.score_picks({}) == 0
    a.tracker.last_mids_ts = now                                                      # fresh: scored
    assert a.score_picks({}) == 1 and store.picks(origin="read")[0]["ret_1d"] == 0.1


# ------------------------------------------------------------------- S5 --
def test_asked_reads_answer_him_and_never_use_up_the_cap_or_the_days_reads(tmp_path, monkeypatch):
    agent, sent = make(tmp_path, monkeypatch, [response([text_block(json.dumps(READ))]) for _ in range(3)],
                       per_day=1)
    agent.s = __import__("dataclasses").replace(agent.s, max_pushes_per_day=1)
    now = time.time()
    seed(agent.store, now)
    for _ in range(2):
        asyncio.run(agent.analyst.run("asked", now, force_send=True))
    assert len(sent) == 2 and agent.store.count_briefs("push", now - 86400) == 0
    reason, _ = agent.analyst.due(now + 3 * 3600, analyst.scan(agent, now))
    assert reason in ("market changed", None)
    today = agent.store.briefs_since("read", analyst._day_start(now))
    assert sum(1 for b in today if b["body"].get("sent") and b["body"].get("reason") != "asked") == 0


def test_a_view_on_a_coin_the_scan_never_showed_is_dropped():
    cur = {"candidates": [{"coin": "SOL"}], "direction": [{"coin": "BTC"}], "coins": [{"coin": "SUI"}],
           "my_positions": []}
    out = analyst.Analyst.clean({"views": [{"coin": "sui", "lean": "short", "strength": 2},
                                           {"coin": "PEPE", "lean": "long", "strength": 1}]}, cur)
    assert [v["coin"] for v in out["views"]] == ["SUI"] and out["dropped_views"] == ["PEPE"]


def test_a_coin_outside_the_feed_still_carries_the_engines_signals():
    every = {"TAO": {"signal_1d": "LIGHT_LONG", "signal_4h": "WAIT", "regime_1d": "MARKUP", "regime_4h": "REACC",
                     "z_1d": 1.2}}
    e = analyst._engine({}, {}, "TAO", every)
    assert e == {"daily": "leaning up", "4h": "no clear setup", "regime": "trending up", "regime_4h": "cooling off",
                 "z_daily": 1.2}


# ----------------------------------------------------------------- pushes --
def test_held_messages_are_listed_and_a_failed_send_does_not_count(tmp_path, monkeypatch):
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1", max_pushes_per_day=1)
    store = Store(s.db_path)
    out = []

    async def fake(token, chat_id, text, buttons=None, silent=False):
        out.append(text)
        return None if text == "lost" else {"message_id": len(out)}

    monkeypatch.setattr(notify, "telegram", fake)
    asyncio.run(notify.push(s, store, "lost", "alert"))                    # Telegram down: not counted
    asyncio.run(notify.push(s, store, "first", "alert"))
    asyncio.run(notify.push(s, store, "second", "conviction"))             # over the cap: held
    asyncio.run(notify.push(s, store, "against you", "conviction", must=True))
    asyncio.run(notify.push(s, store, "you asked", "read", reply=True))
    assert out == ["lost", "first", "against you", "you asked"]
    assert store.count_briefs("push", time.time() - 60) == 2
    held = notify.held_text(store)
    assert "(1)" in held and "conviction" in held and "second" in held


def test_a_stale_convergence_never_pairs_with_a_move_now():
    old = time.time() - 30 * 3600
    events = [{"kind": "smart_convergence", "coin": "SOL", "direction": "short", "data": {"source": "reflex", "ts": old}},
              {"kind": "wallet_move", "coin": "SOL", "direction": "short", "data": {"source": "cowboy"}}]
    got = detectors.cross_source(events, {})
    assert got == []                                                       # only the Derive move is now
    events[0]["data"]["ts"] = (time.time() - 600) * 1000                   # ten minutes old, in milliseconds
    (ev,) = detectors.cross_source(events, {})
    assert ev["score"] == 80 and ev["key"].endswith(":both")
