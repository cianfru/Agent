"""Pictures at no cost, sized for a phone, sent as one message; and the read's board and header built in code."""

import asyncio
import io
import math
import random
import threading
import time

from PIL import Image

from synth import analyst, chart, notify
from synth.config import Settings
from synth.store import Store

DAY = 86_400_000


def bars(n=400, seed=1):
    random.seed(seed)
    now = int(time.time() * 1000) // DAY * DAY
    out, p = [], 100.0
    for i in range(n):
        o = p
        p *= 1 + random.gauss(0.001, 0.03)
        out.append([now - (n - 1 - i) * DAY, o, max(o, p) * 1.01, min(o, p) * 0.99, p, 1000.0])
    return out


def size(png):
    return Image.open(io.BytesIO(png)).size


def test_charts_fit_a_phone_and_telegram():
    b = bars()
    risk = {"off": {"level": b[-1][4] * 0.95, "name": "band low", "pct": -5.0},
            "next": {"level": b[-1][4] * 1.1, "name": "swing high", "pct": 10.0}}
    z = [((b[-1][0] - (59 - i) * DAY) / 1000, 1 + math.sin(i / 9) * 1.8) for i in range(60)]
    crowd = [{"since": b[-6][0] / 1000, "entry": b[-6][4], "side": "long", "value": 2e6},
             {"since": None, "entry": b[-300][4], "side": "short", "value": 5e5}]
    for png in (chart.setup_chart("SOL", "long", "firm", b, risk, z, crowd),
                chart.setup_chart("SOL", "short", "light", b[-80:], None, z[:3]),           # short history, no z panel
                chart.market_map([{"coin": f"C{i}", "price": 100 + i, "band": {"low": 100, "high": 105},
                                   "z_1d": i / 20 - 1, "open_interest_usd": 1e6 * i} for i in range(60)],
                                 [{"coin": "C5", "side": "long"}], ["C9"])):
        w, h = size(png)
        assert 1600 <= w <= 1800 and h <= 2560            # sharp on a phone, within a Telegram photo


def test_the_record_chart_waits_for_a_scored_setup():
    assert chart.journal_chart([], {"n": 60, "right_pct": 62, "median_pct": 1.0}, 0) is None
    t0 = time.time() - 30 * 86400
    picks = [{"ts": t0 + i * 86400, "ret_3d": (-1) ** i * 0.02, "mkt_3d": 0.0, "direction": "long", "coin": f"C{i}"}
             for i in range(9)]
    png = chart.journal_chart(picks, {"n": 60, "right_pct": 62, "median_pct": 1.0}, t0)
    assert size(png)[0] == 1800


def test_renders_in_two_threads_do_not_mix():
    out = []
    b = bars()

    def run():
        out.append(chart.setup_chart("SOL", "long", "firm", b))

    ts = [threading.Thread(target=run) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(out) == 3 and all(size(p)[0] == 1800 for p in out)


def test_pictures_go_with_the_text_as_one_counted_message(tmp_path, monkeypatch):
    s = Settings(db_path=str(tmp_path / "t.db"), telegram_token="t", telegram_chat_id="1")
    store = Store(s.db_path)
    calls = []

    async def fake_tg(token, method, http_timeout=15, files=None, **params):
        calls.append((method, len(files or {}), params.get("caption", ""), params.get("disable_notification")))
        return [{"message_id": 1}] if method == "sendMediaGroup" else {"message_id": 1}

    monkeypatch.setattr(notify, "tg", fake_tg)
    asyncio.run(notify.push(s, store, "short read", "read", pngs=[b"a", b"b"]))
    assert calls == [("sendMediaGroup", 2, "", None)] or calls[0][0] == "sendMediaGroup"
    calls.clear()
    asyncio.run(notify.push(s, store, "x" * 2000, "read", pngs=[b"a", b"b"]))         # too long for a caption
    assert [c[0] for c in calls] == ["sendMessage", "sendMediaGroup"] and calls[1][3] is True   # pictures silent
    assert store.count_briefs("push", time.time() - 60) == 2                          # one message each time


def test_the_board_and_header_are_built_in_code():
    cur = {"market": {"turns": {"state": "broad uptrend", "run_day": 12, "longer_than": "7 of 13 past broad uptrends"},
                      "breadth": {"below": 47, "inside": 12, "just above": 20, "above": 15, "extended": 6,
                                  "over_200d": 38, "with_200d": 96},
                      "z_extremes": {"stretched": [["ENA", 2.8]], "washed_out": []}},
           "direction": [{"coin": "BTC", "price": 104_200.0, "change_24h_pct": 1.2, "screen": {"band": "just above"},
                          "engine": {"z_daily": 1.1}}]}
    assert analyst.board_text(cur).splitlines() == [
        "Broad uptrend · day 12 (longer than 7 of 13)",
        "BTC 104,200 +1.2% · just above its band · z 1.1",
        "Breadth: 41 over the band · 12 inside · 47 under · 38 of 96 over the 200-day",
        "Stretched (z 2.5+): ENA 2.8 · Washed out (z -1 or lower): none"]
    now = time.mktime(time.strptime("2026-11-03 14:05", "%Y-%m-%d %H:%M")) - time.timezone
    ch = [{"w": 3, "text": "BTC -4.2% since the last read", "owner": False}, {"w": 4, "text": "yours", "owner": True}]
    assert analyst.header(now, "market changed", {"market_lean": "short"}, ch) == (
        "Outrider · 14:05 UTC · market changed: BTC -4.2% since the last read · leaning short")
    assert analyst.header(now, "morning", {"market_lean": "neutral"}) == "Outrider · 14:05 UTC · morning"


def test_patterns_cycle_and_breadth_draw_for_a_phone():
    b = bars()
    t0 = b[-40][0] / 1000
    pats = [{"pattern": "inverse head and shoulders", "status": "forming", "direction": "up",
             "break_level": [b[-1][4] * 1.05], "fails_beyond": b[-1][4] * 0.9,
             "anchors": [[t0 + i * 6 * 86400, b[-40 + i * 6][4]] for i in range(5)]}]
    assert size(chart.setup_chart("SOL", "long", "firm", b, None, None, None, 120, pats))[0] == 1800
    import datetime as dt
    day0 = dt.date(2025, 9, 1)
    hist = {"rows": [[(day0 + dt.timedelta(days=i)).isoformat(), 40 + 20 * math.sin(i / 30), 60_000 + 50 * i,
                      math.sin(i / 20)] for i in range(400)],
            "cycle": {"start": (day0 + dt.timedelta(days=380)).isoformat(), "threshold": 0.55,
                      "runs": [[(day0 + dt.timedelta(days=100)).isoformat(), 40, 70]]}}
    png = chart.cycle_chart(hist, {"state": "broad uptrend", "run_day": 20})
    assert size(png)[0] == 1800 and chart.cycle_chart({"rows": hist["rows"][:5]}) is None
    days = [{"day": (day0 + dt.timedelta(days=i)).isoformat(), "below": 30, "inside": 10, "over": 60 - i % 5,
             "over_200d": 50, "with_200d": 90, "coins": 100} for i in range(90)]
    assert size(chart.breadth_chart(days))[0] == 1800 and chart.breadth_chart(days[:3]) is None


def test_the_crowd_reads_by_money_and_by_side():
    longs = [{"since": 1.0, "entry": 100.0, "side": "long", "value": 1e6},
             {"since": 3.0, "entry": 110.0, "side": "long", "value": 3e6}]
    shorts = [{"since": None, "entry": 130.0, "side": "short", "value": 2e6}]
    s = chart.crowd_summary(chart.crowd_positions(longs + shorts), 120.0)
    assert s["long"]["avg"] == 107.5                                  # weighted by money, so by coins held
    assert round(s["long"]["pnl"], 2) == 11.63                        # the price now over their average entry
    assert round(s["short"]["pnl"], 2) == 7.69                        # a short under its entry is in profit
    assert round(s["long_share"], 1) == 66.7 and s["n"] == 3 and s["sized"]
    assert s["long"]["half_in"] == 3.0                               # by day 3, half the longs' money was in
    assert s["short"]["half_in"] is None and s["short"]["before_tracking"]
    old = chart.crowd_positions([(1.0, 100.0, "long"), (2.0, 90.0, "short"), (3.0, 95.0, "sideways")])
    assert [p["value"] for p in old] == [1.0, 1.0]                    # no sizes: one each, said "by count"
    assert not chart.crowd_summary(old, 100.0)["sized"]
    assert chart.crowd_positions([{"entry": 0, "side": "long", "value": 5}]) == []


def test_crowded_triangles_merge_without_losing_money():
    to_pt = lambda c: (10 * c["x"], c["y"])                           # noqa: E731  ten points a day
    near = [{"x": 0.0, "y": 0.0, "v": 1.0, "n": 1}, {"x": 0.5, "y": 0.0, "v": 3.0, "n": 1},
            {"x": 30.0, "y": 0.0, "v": 2.0, "n": 1}]
    out = sorted(chart.crowd_clusters(near, to_pt, cap=4), key=lambda c: c["x"])
    assert len(out) == 2 and out[0]["n"] == 2 and out[0]["v"] == 4.0
    assert out[0]["x"] == 0.375                                       # their money-weighted mean day
    many = [{"x": 3.0 * i, "y": 0.0, "v": 1.0, "n": 1} for i in range(9)]       # 30 points apart, 3 days apart
    capped = chart.crowd_clusters(many, to_pt, cap=3)
    assert len(capped) == 3 and sum(c["n"] for c in capped) == 9 and sum(c["v"] for c in capped) == 9.0
    apart = [{"x": 40.0 * i, "y": 0.0, "v": 1.0, "n": 1} for i in range(6)]     # weeks apart: never merged
    assert len(chart.crowd_clusters(apart, to_pt, cap=2)) == 6


def test_every_crowd_fits_a_phone():
    b = bars()
    risk = {"off": {"level": b[-1][4] * 0.95, "name": "band low", "pct": -5.0},
            "next": {"level": b[-1][4] * 1.1, "name": "swing high", "pct": 10.0}}
    z = [((b[-1][0] - (59 - i) * DAY) / 1000, 1.0) for i in range(60)]
    t = lambda i: b[i][0] / 1000                                      # noqa: E731
    burst = [{"since": t(-1 - i % 10), "entry": b[-1 - i % 10][4], "side": "long" if i % 3 else "short",
              "value": 1e5 * (i + 1)} for i in range(60)]
    far = [{"since": None, "entry": b[-1][4] * 9, "side": "short", "value": 1e6},     # far outside the candles
           {"since": t(-350), "entry": b[-350][4], "side": "long", "value": 2e6}]     # before the days shown
    for crowd in ([], burst, far, burst[:1], [p for p in burst if p["side"] == "long"]):
        for zz in (z, None):
            w, h = size(chart.setup_chart("SOL", "long", "firm", b, risk, zz, crowd))
            assert 1600 <= w <= 1800 and h <= 2560


def test_the_pattern_shapes_never_reach_the_model():
    cur = {"coins": [{"coin": "SOL", "patterns": [{"pattern": "cup and handle", "anchors": [[1, 2.0]]}]}]}
    assert analyst._no_anchors(cur) == {"coins": [{"coin": "SOL", "patterns": [{"pattern": "cup and handle"}]}]}


def test_the_chart_gets_every_position_on_the_coin():
    from synth import pictures

    class Positions:
        def board(self, now):
            return {"traders": [
                {"tier": "proven", "positions": [{"coin": "SOL", "side": "long", "entry": 150.0, "value": 2_000_000,
                                                  "since": 1.0e9},
                                                 {"coin": "ETH", "side": "long", "entry": 3000.0, "value": 9}]},
                {"tier": "riding", "positions": [{"coin": "SOL", "side": "short", "entry": 170.0, "value": 500_000,
                                                  "since": None}]}]}

    class Agent:
        positions = Positions()

    got = pictures.entries(Agent(), "sol", 0)
    assert got == [{"since": 1.0e9, "entry": 150.0, "side": "long", "value": 2_000_000, "tier": "proven"},
                   {"since": None, "entry": 170.0, "side": "short", "value": 500_000, "tier": "riding"}]
