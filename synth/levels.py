"""Open interest, funding, the 200-day average and the bull market support band, per coin, from Hyperliquid.

- Each poll, one read of every perp's context (price, open interest, funding, 24-hour volume); once an hour a compact
  copy goes into the series (source "hl_ctx", kept 8 days), so open interest and funding can be compared with a day
  and a week ago. Open interest is compared in coins, so a price move does not read as new positions.
- On demand (a read, a question about a coin): the coin's daily candles for its 200-day simple average, and its weekly
  candles for the bull market support band (the 20-week simple and 21-week exponential averages of weekly closes, the
  forming bar included as on TradingView). Kept a day per coin.
Context, not a signal: none of these is tested in the studies.
"""

from __future__ import annotations

import bisect
import logging
import time

log = logging.getLogger(__name__)

STEP_S = 3600          # one stored copy of every coin's context an hour
NEAR_S = 3 * 3600      # a copy within three hours stands for the time asked
DAY = 86400


def _sig(x: float | None) -> float | None:
    return float(f"{x:.5g}") if x else x


def _pct(now: float | None, then: float | None) -> float | None:
    return round(100 * (now / then - 1), 1) if now and then else None


def sma(xs: list[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def ema(xs: list[float], n: int) -> float | None:
    """Exponential average over every close, seeded with the simple average of the first n."""
    if len(xs) < n:
        return None
    k, e = 2 / (n + 1), sum(xs[:n]) / n
    for x in xs[n:]:
        e = x * k + e * (1 - k)
    return e


def trend_levels(daily: list[float], weekly: list[float]) -> dict:
    """Closes oldest first -> the 200-day simple average and the band's two averages (None when too short)."""
    return {"ma200d": _sig(sma(daily, 200)), "sma20w": _sig(sma(weekly, 20)), "ema21w": _sig(ema(weekly, 21)),
            "daily_bars": len(daily), "weekly_bars": len(weekly)}


def trend_view(lv: dict, price: float | None) -> dict | None:
    """Where the price sits against the levels: above or below the 200-day average and by how much, and above, inside
    or below the bull market support band (and how far from its nearer edge)."""
    if not lv or not price:
        return None
    out: dict = {}
    if lv.get("ma200d"):
        out["ma200d"] = lv["ma200d"]
        out["vs_ma200d_pct"] = _pct(price, lv["ma200d"])
    lo_hi = [x for x in (lv.get("sma20w"), lv.get("ema21w")) if x]
    if len(lo_hi) == 2:
        lo, hi = min(lo_hi), max(lo_hi)
        where = "above" if price > hi else "below" if price < lo else "inside"
        out["bull_market_support_band"] = {
            "sma20w": lv["sma20w"], "ema21w": lv["ema21w"], "price_vs_band": where,
            "vs_band_pct": _pct(price, hi) if where == "above" else _pct(price, lo) if where == "below" else 0.0}
    if not out:
        out["note"] = f"too little history ({lv.get('daily_bars', 0)} daily bars)"
    return out


class Levels:
    def __init__(self, store):
        self.store = store

    # ---------------------------------------------------------- open interest --
    async def step(self, hl, now: float) -> bool:
        """Each poll: every perp's context; once an hour a compact copy into the series. True when one was kept."""
        ctx = await hl.contexts()
        if not ctx:
            return False
        self.store.put_snapshot("hl_ctx", {"ts": now, "c": ctx})
        if self.store.series("hl_ctx", now - STEP_S + 60):
            return False
        self.store.add_series("hl_ctx", {"c": {c: [v["px"], v["oi"], v["funding"]] for c, v in ctx.items()
                                               if v.get("px") and v.get("oi") is not None}}, now)
        return True

    def _rows(self, now: float) -> tuple[list[float], list[dict]]:
        rows = self.store.series("hl_ctx", now - 8 * DAY)
        return [r[0] for r in rows], [r[1].get("c") or {} for r in rows]

    @staticmethod
    def _near(tss: list[float], rows: list[dict], ts: float) -> dict:
        if not tss:
            return {}
        i = bisect.bisect_left(tss, ts)
        j = min((k for k in (i - 1, i) if 0 <= k < len(tss)), key=lambda k: abs(tss[k] - ts))
        return rows[j] if abs(tss[j] - ts) <= NEAR_S else {}

    def oi_funding(self, coin: str, now: float | None = None) -> dict | None:
        """The coin's open interest (dollars now; change in coins over 24 hours and 7 days), funding (now and its
        24-hour average, both a year's worth in percent; above zero longs pay shorts) and 24-hour volume."""
        now = now or time.time()
        snap = self.store.get_snapshot("hl_ctx") or {}
        cur = (snap.get("c") or {}).get(coin)
        if not cur:
            return None
        tss, rows = self._rows(now)
        day = self._near(tss, rows, now - DAY).get(coin)
        week = self._near(tss, rows, now - 7 * DAY).get(coin)
        last_day = [r[coin][2] for t, r in zip(tss, rows) if t >= now - DAY and coin in r and r[coin][2] is not None]
        out = {"open_interest_usd": round(cur["oi"] * cur["px"]) if cur.get("oi") and cur.get("px") else None,
               "oi_change_24h_pct": _pct(cur.get("oi"), day[1] if day else None),
               "oi_change_7d_pct": _pct(cur.get("oi"), week[1] if week else None),
               "funding_apr_pct": round(cur["funding"] * 24 * 365 * 100, 1) if cur.get("funding") is not None else None,
               "funding_24h_avg_apr_pct": round(sum(last_day) / len(last_day) * 24 * 365 * 100, 1) if last_day else None,
               "volume_24h_usd": round(cur["vol"]) if cur.get("vol") else None}
        return {k: v for k, v in out.items() if v is not None}

    def market(self, now: float | None = None, top: int = 50) -> dict | None:
        """The market's open interest change over 24 hours (in coins, valued at today's prices, so a price move is not
        new money) and the median funding of the most traded perps."""
        now = now or time.time()
        cur = (self.store.get_snapshot("hl_ctx") or {}).get("c") or {}
        if not cur:
            return None
        busy = sorted(cur, key=lambda c: -(cur[c].get("vol") or 0))[:top]
        tss, rows = self._rows(now)
        day = self._near(tss, rows, now - DAY)
        both = [c for c in busy if c in day and cur[c].get("oi") and cur[c].get("px")]
        oi_now = sum(cur[c]["oi"] * cur[c]["px"] for c in both)
        oi_then = sum(day[c][1] * cur[c]["px"] for c in both)
        fund = sorted(cur[c]["funding"] for c in busy if cur[c].get("funding") is not None)
        return {"oi_change_24h_pct": _pct(oi_now, oi_then) if both else None,
                "median_funding_apr_pct": round(fund[len(fund) // 2] * 24 * 365 * 100, 1) if fund else None,
                "coins": len(busy)}

    # ------------------------------------------------------------------ trend --
    async def trend(self, hl, coin: str, now: float | None = None) -> dict | None:
        """The coin's 200-day average and bull market support band, read once a day (cached per coin)."""
        now = now or time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        cache = self.store.get_snapshot("trend_levels") or {}
        hit = cache.get(coin)
        if hit and hit.get("day") == day:
            return hit
        end = int(now * 1000)
        await hl.ensure_names()
        daily = await hl.candles(coin, "1d", end - 230 * DAY * 1000, end)
        weekly = await hl.candles(coin, "1w", end - 40 * 7 * DAY * 1000, end)
        if not daily:
            return None
        lv = {**trend_levels([b[4] for b in daily], [b[4] for b in weekly]), "day": day}
        cache = {c: v for c, v in cache.items() if v.get("day") == day}
        cache[coin] = lv
        self.store.put_snapshot("trend_levels", cache)
        return lv
