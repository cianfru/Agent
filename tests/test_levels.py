import asyncio
import time

from synth import levels
from synth.store import Store

DAY = 86400


class FakeHL:
    """Daily closes rising 1 a day from 100; weekly closes rising 5 a week from 100; contexts as given."""

    def __init__(self, ctx=None, daily=250, weekly=30):
        self.ctx, self.daily, self.weekly, self.calls = ctx or {}, daily, weekly, 0

    async def contexts(self):
        return self.ctx

    async def ensure_names(self):
        pass

    async def candles(self, coin, interval, start_ms, end_ms):
        """Daily bars ending with today's forming bar (at end_ms), closes 100, 101, ... (the weeks come from them)."""
        self.calls += 1
        n, step = (self.daily, 1.0) if interval == "1d" else (self.weekly, 5.0)
        ms = (86_400_000 if interval == "1d" else 7 * 86_400_000)
        last = end_ms // ms * ms
        return [[last - (n - 1 - i) * ms, 0, 100 + step * i + 1, 100 + step * i - 1, 100 + step * i, 1000.0]
                for i in range(n)]

    async def aclose(self):
        pass


def test_moving_averages_and_where_the_price_sits():
    daily = [100 + i for i in range(250)]                      # the last 200 closes run 150 .. 349
    weekly = [100 + 5 * i for i in range(30)]
    lv = levels.trend_levels(daily, weekly)
    assert lv["ma200d"] == 249.5 and lv["sma20w"] == 197.5     # weekly closes 150 .. 245
    assert abs(lv["ema21w"] - 197.5) < 5                       # an exponential average leans to the latest closes
    v = levels.trend_view(lv, 260.0)
    assert v["vs_ma200d_pct"] == 4.2 and v["bull_market_support_band"]["price_vs_band"] == "above"
    assert levels.trend_view(lv, 150.0)["bull_market_support_band"]["price_vs_band"] == "below"
    band = levels.trend_view(lv, (lv["sma20w"] + lv["ema21w"]) / 2)["bull_market_support_band"]
    assert band["price_vs_band"] == "inside" and band["vs_band_pct"] == 0.0
    young = levels.trend_view(levels.trend_levels(daily[:50], weekly[:8]), 120.0)
    assert young == {"note": "too little history (50 daily bars)"}


def test_open_interest_and_funding_against_a_day_and_a_week_ago(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    lv = levels.Levels(store)
    now = time.time()
    store.add_series("hl_ctx", {"c": {"SOL": [100.0, 1000.0, 0.00001]}}, ts=now - 7 * DAY)
    store.add_series("hl_ctx", {"c": {"SOL": [95.0, 1200.0, 0.00002]}}, ts=now - DAY)
    store.add_series("hl_ctx", {"c": {"SOL": [92.0, 1500.0, 0.00004]}}, ts=now - 1800)
    hl = FakeHL({"SOL": {"px": 90.0, "oi": 1500.0, "funding": 0.00003, "vol": 2.5e8},
                 "BTC": {"px": 80_000.0, "oi": 30_000.0, "funding": 0.00001, "vol": 2e9}})
    assert asyncio.run(lv.step(hl, now)) is False              # a copy within the hour already: none added
    sol = lv.oi_funding("SOL", now)
    assert sol == {"open_interest_usd": 135_000, "oi_change_24h_pct": 25.0, "oi_change_7d_pct": 50.0,
                   "price_change_24h_pct": -5.3, "price_change_7d_pct": -10.0,
                   "funding_apr_pct": 26.3, "funding_24h_avg_apr_pct": 26.3, "volume_24h_usd": 250_000_000}
    assert lv.oi_funding("NOPE", now) is None
    m = lv.market(now)
    assert m["oi_change_24h_pct"] == 25.0 and m["coins"] == 2          # BTC has no day-old copy: left out


def test_trend_levels_are_read_once_a_day(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    lv, hl = levels.Levels(store), FakeHL()
    now = time.time()
    a = asyncio.run(lv.trend(hl, "SOL", now))
    b = asyncio.run(lv.trend(hl, "SOL", now + 60))
    assert a["ma200d"] == 249.5 and a == b and hl.calls == 1           # one request a day: days and weeks
    assert a["closes"][-1] == 348.0 and len(a["closes"]) == 91          # closed days only: today's bar left out
    assert a["atr14_pct"] == round(100 * 2 / 348, 2) and a["rv30_pct"] is not None and a["weekly_bars"] >= 35


def test_price_action_says_how_much_of_the_last_leg_is_given_back_and_what_is_near():
    # a leg up from 50 (day 30) to 150 (day 80), then a pullback; swing lows on the way
    closes = [60.0] * 30 + [50 + 2 * i for i in range(51)] + [150 - 1.5 * i for i in range(40)]
    bars = [[i * 86_400_000, c, c * 1.01, c * 0.99, c] for i, c in enumerate(closes)]
    st = levels.price_structure(bars)
    assert st["high"] == 151.5 and st["leg_low"] == 49.5
    lv = {**st, "ma200d": None, "sma20w": 92.0, "ema21w": 95.0}
    pa = levels.price_action(lv, 93.0)
    assert pa["retraced_of_last_leg_pct"] == 57 and pa["from_recent_high_pct"] == -38.6
    assert pa["near"] == ["20-week SMA 92 (-1.1%)", "21-week EMA 95 (+2.2%)"]
    view = levels.trend_view(lv, 93.0)
    assert view["bull_market_support_band"]["price_vs_band"] == "inside" and view["price_action"] == pa
