"""The market as one number an hour, so every pick can be scored against it, not only on whether its price went its way.

The basket is the 50 most traded Hyperliquid perps (24-hour volume, chosen again each day), equal-weight and
chain-linked: each hour it moves by the average move of the coins priced both then and now, each move clipped to
between half and double so one bad print cannot swing it. BTC is kept beside it. The hours before the agent kept the
index are rebuilt once from Hyperliquid's hourly candles, back to the first pick. A pick beats the market when its move
its way is better than the basket's move its way over the same days.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)

BASKET = 50
STEP_S = 3600              # one reading an hour
NEAR_S = 2 * 3600          # a reading within two hours of the time asked stands for it
COINS_S = 86400            # the basket's coins are chosen again once a day
RETRY_S = 1800             # a failed rebuild is tried again after half an hour
HORIZONS = {"1d": 86400, "3d": 3 * 86400, "7d": 7 * 86400, "14d": 14 * 86400, "30d": 30 * 86400}


def basket_move(prev: dict[str, float], cur: dict[str, float]) -> float | None:
    """The basket's move between two sets of prices: the average move of the coins in both (None under 10)."""
    moves = [min(2.0, max(0.5, cur[c] / prev[c])) for c in cur if prev.get(c) and cur.get(c)]
    return sum(moves) / len(moves) if len(moves) >= 10 else None


def chain(hours: dict[float, dict[str, float]]) -> list[tuple[float, float]]:
    """{ts: {coin: price}} -> [(ts, level)], the level starting at 1. An hour with too few coins is skipped."""
    out: list[tuple[float, float]] = []
    level, prev = 1.0, None
    for ts in sorted(hours):
        cur = hours[ts]
        if prev is not None:
            m = basket_move(prev, cur)
            if m is None:
                continue
            level *= m
        out.append((ts, level))
        prev = cur
    return out


class MarketIndex:
    def __init__(self, store):
        self.store = store
        self.ready = False
        self.building = False
        self.next_try = 0.0

    def coins(self) -> list[str]:
        return (self.store.get_snapshot("index_coins") or {}).get("coins") or []

    async def _pick_coins(self, hl, now: float) -> list[str]:
        coins = await hl.top_coins(BASKET)
        self.store.put_snapshot("index_coins", {"ts": now, "coins": coins})
        return coins

    # --------------------------------------------------------------- history --
    def due(self, now: float) -> bool:
        return not self.ready and not self.building and now >= self.next_try

    async def ensure(self, hl, now: float | None = None, pace_s: float = 2.0) -> int:
        """Rebuild the hours back to the first pick from hourly candles, once; returns the hours added."""
        now = now or time.time()
        self.building = True
        try:
            since = min(self.store.first_pick_ts() or now, now)
            first = self.store.market_first()
            if (first and first["ts"] <= since + NEAR_S) or since >= now - STEP_S:
                self.ready = True
                return 0
            coins = await self._pick_coins(hl, now)
            await hl.mids()                                  # the exchange's own spelling of each name
            end = first["ts"] if first else now
            hours: dict[float, dict[str, float]] = {}
            for c in dict.fromkeys(coins + ["BTC"]):
                bars = await hl.candles(c, "1h", int((since - 2 * STEP_S) * 1000), int(end * 1000))
                for t, _o, _h, _l, close in bars:
                    hours.setdefault(t / 1000 + STEP_S, {})[c] = close
                await asyncio.sleep(pace_s)
            basket = {ts: {c: px for c, px in v.items() if c in coins} for ts, v in hours.items()}
            rows = [(ts, hours[ts].get("BTC"), level) for ts, level in chain(basket) if ts < end]
            if not rows:
                raise RuntimeError("no candles to rebuild the market from")
            if first:                                        # join the rebuilt hours onto the kept ones
                scale = first["basket"] / rows[-1][2]
                rows = [(ts, btc, level * scale) for ts, btc, level in rows]
            else:                                            # the hourly steps carry on from the last candles
                last_ts = max(t for t in basket if basket[t])
                self.store.put_snapshot("index_mids", {"ts": last_ts, "px": basket[last_ts]})
            self.store.market_add(rows)
            self.ready = True
            return len(rows)
        except Exception as e:  # noqa: BLE001 - tried again later; picks wait for their market move
            log.warning("market history rebuild failed: %s", e)
            self.next_try = now + RETRY_S
            return 0
        finally:
            self.building = False

    # ------------------------------------------------------------------ hourly --
    async def step(self, mids: dict[str, float], now: float, hl=None) -> bool:
        """Each poll: a new reading once an hour has passed since the last one."""
        if not self.ready or not mids:
            return False
        last = self.store.market_last()
        if last and now - last["ts"] < STEP_S:
            return False
        snap = self.store.get_snapshot("index_coins") or {}
        coins = snap.get("coins") or []
        if hl is not None and (not coins or now - (snap.get("ts") or 0) >= COINS_S):
            try:
                coins = await self._pick_coins(hl, now)
            except Exception as e:  # noqa: BLE001 - yesterday's coins do
                log.warning("basket coins refresh failed: %s", e)
        cur = {c: mids[c] for c in coins if mids.get(c)}
        prev = (self.store.get_snapshot("index_mids") or {}).get("px") or {}
        level = 1.0
        if last:
            m = basket_move(prev, cur)
            if m is None:
                return False
            level = last["basket"] * m
        self.store.market_add([(now, mids.get("BTC"), level)])
        self.store.put_snapshot("index_mids", {"ts": now, "px": cur})
        return True

    # ------------------------------------------------------------------ moves --
    def move(self, t0: float, t1: float) -> float | None:
        """The basket's move from t0 to t1, from the readings nearest each (None when either has none near)."""
        a, b = self.store.market_near(t0, NEAR_S), self.store.market_near(t1, NEAR_S)
        return b["basket"] / a["basket"] - 1 if a and b and a["basket"] else None

    def fill(self) -> int:
        """Each scored pick gets the market's move over its own days (from when it was logged to the horizon)."""
        last = self.store.market_last()
        if not last:
            return 0
        n = 0
        for p in self.store.picks_missing_market():
            for h, secs in HORIZONS.items():
                if p[f"ret_{h}"] is None or p[f"mkt_{h}"] is not None or p["ts"] + secs > last["ts"]:
                    continue
                m = self.move(p["ts"], p["ts"] + secs)
                if m is not None:
                    self.store.set_pick_market(p["id"], h, round(m, 5))
                    n += 1
        return n
