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
        self.calls += 1
        n, step = (self.daily, 1.0) if interval == "1d" else (self.weekly, 5.0)
        return [[i, 0, 0, 0, 100 + step * i] for i in range(n)]

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
    assert a["ma200d"] == 249.5 and a == b and hl.calls == 2           # daily and weekly, once
