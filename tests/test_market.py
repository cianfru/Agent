import asyncio
import time

from synth import market
from synth.store import Store

COINS = [f"C{i}" for i in range(12)]


class FakeHL:
    """Every coin rises 1% an hour from its own starting price; BTC from 100,000."""

    def __init__(self, start: float):
        self.start = start
        self.asked = []

    async def top_coins(self, n):
        return COINS[:n]

    async def mids(self):
        return {}

    async def candles(self, coin, interval, start_ms, end_ms):
        self.asked.append(coin)
        base = 100_000.0 if coin == "BTC" else 10.0 * (1 + COINS.index(coin))
        bars, t = [], self.start
        while t * 1000 < end_ms:
            k = (t - self.start) / 3600
            if t * 1000 >= start_ms:
                bars.append([int(t * 1000), 0, 0, 0, base * (1.01 ** k)])
            t += 3600
        return bars


def test_basket_move_is_the_clipped_average_of_coins_priced_both_times():
    prev = {c: 10.0 for c in COINS}
    cur = {**{c: 11.0 for c in COINS[:11]}, "C11": 100.0}            # one bad print: clipped to double
    assert abs(market.basket_move(prev, cur) - (11 * 1.1 + 2.0) / 12) < 1e-12
    assert market.basket_move(prev, {c: 11.0 for c in COINS[:9]}) is None   # under 10 coins: no reading
    hours = {0: prev, 3600: {c: 9.0 for c in COINS[:3]}, 7200: {c: 12.0 for c in COINS}}
    assert market.chain(hours) == [(0, 1.0), (7200, 1.2)]            # the thin hour is skipped


def test_rebuild_then_hourly_steps_then_each_pick_gets_its_market_move(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    now = time.time()
    start = now - 30 * 3600
    pid = store.add_pick("read", "SUI", "short", 2, 1.8, "why", {})
    store._db.execute("UPDATE picks SET ts=? WHERE id=?", (start + 3 * 3600, pid))
    store._db.commit()
    idx = market.MarketIndex(store)
    hl = FakeHL(start)
    added = asyncio.run(idx.ensure(hl, now, pace_s=0))
    assert added > 20 and idx.ready and set(hl.asked) == set(COINS) | {"BTC"}
    first, last = store.market_first(), store.market_last()
    assert abs(last["basket"] / first["basket"] - 1.01 ** ((last["ts"] - first["ts"]) / 3600)) < 1e-9
    assert last["btc"] > 100_000
    # an hour later the live mids carry it on (every coin +1% again)
    mids = {c: px * 1.01 for c, px in store.get_snapshot("index_mids")["px"].items()}
    assert asyncio.run(idx.step({**mids, "BTC": 120_000.0}, last["ts"] + 3600))
    assert abs(store.market_last()["basket"] / last["basket"] - 1.01) < 1e-9
    assert not asyncio.run(idx.step(mids, last["ts"] + 3700))           # within the hour: nothing new
    # the pick's 1-day market move: 24 hours of +1%
    store.set_pick_return(pid, "1d", 0.03)
    assert idx.fill() == 1
    p = store.picks(origin="read")[0]
    assert abs(p["mkt_1d"] - (1.01 ** 24 - 1)) < 1e-4
    assert asyncio.run(idx.ensure(hl, now, pace_s=0)) == 0              # already reaches back: no second rebuild
