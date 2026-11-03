"""Open interest, funding, the 200-day average and the bull market support band, per coin, from Hyperliquid.

- Each poll, one read of every perp's context (price, open interest, funding, 24-hour volume); once an hour a compact
  copy goes into the series (source "hl_ctx", kept 8 days), so open interest and funding can be compared with a day
  and a week ago. Open interest is compared in coins, so a price move does not read as new positions.
- On demand (a read, a question about a coin) and daily for the screen: one request for the coin's last 1,000 daily
  candles gives its 200-day simple average, its price action (the high of the last 120 days, where the leg up to it
  started, the low since, the recent swing highs and lows, from closed days), its volatility (14-day average true
  range, 30-day realised volatility, the last day's volume against its 20-day median) and its last 91 daily closes
  (for its strength against BTC and its beta), and the bull market support band: the 20-week simple and 21-week
  exponential averages of weekly closes, weeks starting Monday as on TradingView's crypto charts (Hyperliquid's own
  weekly candles start on Thursday), built from the daily closes with the forming week included. Kept a day per coin;
  where the price sits against them is worked out with the price at hand.
Context, not a signal: none of these is tested in the studies.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import math
import statistics
import time

log = logging.getLogger(__name__)

STEP_S = 3600          # one stored copy of every coin's context an hour
NEAR_S = 3 * 3600      # a copy within three hours stands for the time asked
DAY = 86400
UNIVERSE = 100         # the most traded perps whose levels are read each day for the screen
KEEP_S = 3 * DAY       # a coin's levels stand until read again, for up to 3 days


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
    out = {"ma200d": _sig(sma(daily, 200)), "sma20w": _sig(sma(weekly, 20)), "ema21w": _sig(ema(weekly, 21)),
           "daily_bars": len(daily), "weekly_bars": len(weekly)}
    if 21 <= len(weekly) < 60:
        out["band_short_history"] = True       # the 21-week EMA has not forgotten its seed yet
    return out


WEEK = 7 * DAY


def weekly_closes(bars: list[list[float]]) -> list[float]:
    """Daily bars [t_ms, o, h, l, c, ...] oldest first -> weekly closes, weeks starting Monday 00:00 UTC (TradingView's
    crypto weeks; 5 January 1970 was a Monday). The forming week's close is the latest daily close."""
    out: list[float] = []
    key = None
    for b in bars:
        wk = int((b[0] / 1000 - 4 * DAY) // WEEK)
        if wk != key:
            out.append(b[4])
            key = wk
        else:
            out[-1] = b[4]
    return out


def vol_stats(bars: list[list[float]]) -> dict:
    """Closed daily bars oldest first -> the 14-day average true range (Wilder) as a share of the last close, the
    30-day realised volatility (yearly, from daily log returns) and the last day's volume over its 20-day median."""
    out: dict = {}
    if len(bars) >= 15:
        trs = [max(b[2] - b[3], abs(b[2] - a[4]), abs(b[3] - a[4])) for a, b in zip(bars, bars[1:])]
        atr = sum(trs[:14]) / 14
        for tr in trs[14:]:
            atr = (atr * 13 + tr) / 14
        if bars[-1][4]:
            out["atr14_pct"] = round(100 * atr / bars[-1][4], 2)
    closes = [b[4] for b in bars[-31:] if b[4]]
    if len(closes) >= 21:
        rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
        out["rv30_pct"] = round(100 * statistics.pstdev(rets) * math.sqrt(365), 1)
    vols = [b[5] for b in bars[-21:-1] if len(b) > 5 and b[5]]
    if len(vols) >= 10 and len(bars[-1]) > 5:
        med = statistics.median(vols)
        if med:
            out["rel_vol"] = round(bars[-1][5] / med, 2)
    return out


PIVOT_N = 3            # a swing high (low) is the highest (lowest) of the 3 days either side
NEAR_PCT = 3.0         # a level this close to the price is "near"


def _day(ms: float) -> str:
    return time.strftime("%d %b", time.gmtime(ms / 1000))


def price_structure(bars: list[list[float]], window: int = 120) -> dict:
    """Daily bars [t, open, high, low, close], oldest first -> the high of the last `window` days and its date, the low
    the leg up to it started from (in the `window` days before it), the low since, and the last swing highs and lows."""
    if len(bars) < 20:
        return {}
    recent = bars[-window:]
    i_hi = max(range(len(recent)), key=lambda i: recent[i][2])
    hi = recent[i_hi]
    k = len(bars) - len(recent) + i_hi                      # the high's place in all bars
    before = bars[max(0, k - window):k + 1]
    leg = min(before, key=lambda b: b[3])
    since = bars[k:]
    i_lo = min(range(len(recent)), key=lambda i: recent[i][3])          # the same for the last drop
    lo = recent[i_lo]
    j = len(bars) - len(recent) + i_lo
    drop = max(bars[max(0, j - window):j + 1], key=lambda b: b[2])
    pivots_hi, pivots_lo = [], []
    for i in range(PIVOT_N, len(recent) - PIVOT_N):
        w = recent[i - PIVOT_N:i + PIVOT_N + 1]
        if recent[i][2] == max(b[2] for b in w):
            pivots_hi.append([_sig(recent[i][2]), _day(recent[i][0])])
        if recent[i][3] == min(b[3] for b in w):
            pivots_lo.append([_sig(recent[i][3]), _day(recent[i][0])])
    return {"high": _sig(hi[2]), "high_date": _day(hi[0]), "high_bars_ago": len(recent) - 1 - i_hi,
            "leg_low": _sig(leg[3]), "leg_low_date": _day(leg[0]), "low_since_high": _sig(min(b[3] for b in since)),
            "low": _sig(lo[3]), "low_date": _day(lo[0]), "low_bars_ago": len(recent) - 1 - i_lo,
            "drop_high": _sig(drop[2]), "drop_high_date": _day(drop[0]),
            "swing_highs": pivots_hi[-6:], "swing_lows": pivots_lo[-6:]}


def price_action(lv: dict, price: float) -> dict | None:
    """Where the price sits in its last move: off the recent high, how much of the leg up to it it has given back, the
    nearest swing low under it and swing high over it, and the levels within 3% (band, 200-day average, swings)."""
    if not lv.get("high"):
        return None
    hi, lo = lv["high"], lv.get("leg_low")
    out: dict = {"recent_high": hi, "recent_high_date": lv.get("high_date"), "from_recent_high_pct": _pct(price, hi)}
    if price > hi:                            # the levels were read at 00:30; the price has gone past that high since
        hi = out["recent_high"] = price
        out.update({"recent_high_date": "today", "from_recent_high_pct": 0.0, "new_high_today": True})
    if lo and hi > lo:
        out["leg_low"] = lo
        out["leg_low_date"] = lv.get("leg_low_date")
        out["retraced_of_last_leg_pct"] = round(100 * (hi - price) / (hi - lo))
    low, top = lv.get("low"), lv.get("drop_high")
    if low and price < low:                   # the same for a new low
        low, out["new_low_today"] = price, True
    if low and top and top > low and (lv.get("low_bars_ago") or 0) < (lv.get("high_bars_ago") or 0):
        out["recent_low"] = low                              # the last move was down: how much of the drop came back
        out["recent_low_date"] = lv.get("low_date")
        out["bounced_of_last_drop_pct"] = round(100 * (price - low) / (top - low))
    under = [p for p in lv.get("swing_lows") or [] if p[0] < price]
    over = [p for p in lv.get("swing_highs") or [] if p[0] > price]
    if under:
        sl = max(under, key=lambda p: p[0])
        out["support_swing_low"] = {"price": sl[0], "date": sl[1], "below_pct": _pct(sl[0], price)}
    if over:
        sh = min(over, key=lambda p: p[0])
        out["resistance_swing_high"] = {"price": sh[0], "date": sh[1], "above_pct": _pct(sh[0], price)}
    levels = {"200-day average": lv.get("ma200d"), "20-week SMA": lv.get("sma20w"), "21-week EMA": lv.get("ema21w")}
    if under:
        levels["swing low"] = max(under, key=lambda p: p[0])[0]
    near = [f"{name} {v:g} ({_pct(v, price):+.1f}%)" for name, v in levels.items()
            if v and abs(100 * (v / price - 1)) <= NEAR_PCT]
    if near:
        out["near"] = near
    return out


def band_state(price: float, lo: float, hi: float) -> str:
    """Where the price sits against the bull market support band (owner, 3 November 2026): inside it is testing the
    band, a zone to watch, not a block; under it the trend is lost until it is reclaimed; 15% or more over it is
    extended, not an entry."""
    if price < lo:
        return "below"
    if price <= hi:
        return "inside"
    over = 100 * (price / hi - 1)
    return "just above" if over <= 5 else "above" if over < 15 else "extended"


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
            "sma20w": lv["sma20w"], "ema21w": lv["ema21w"], "price_vs_band": where, "state": band_state(price, lo, hi),
            "vs_band_pct": _pct(price, hi) if where == "above" else _pct(price, lo) if where == "below" else 0.0}
    pa = price_action(lv, price)
    if pa:
        out["price_action"] = pa
    if not out:
        out["note"] = f"too little history ({lv.get('daily_bars', 0)} daily bars)"
    return out


class Levels:
    def __init__(self, store):
        self.store = store
        self._memo: tuple | None = None    # (last series ts, when parsed, tss, rows)

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
        """The last 8 days of hourly contexts, parsed once per new hourly row (a screen asks for every coin, and
        parsing the series each time cost seconds of CPU per poll)."""
        last = self.store.series_last_ts("hl_ctx")
        if self._memo and self._memo[0] == last and now - self._memo[1] < 2 * STEP_S:
            return self._memo[2], self._memo[3]
        rows = self.store.series("hl_ctx", now - 8 * DAY)
        tss, cs = [r[0] for r in rows], [r[1].get("c") or {} for r in rows]
        self._memo = (last, now, tss, cs)
        return tss, cs

    def path(self, coin: str, t0: float, t1: float, now: float | None = None) -> list[tuple[float, float, float]]:
        """The coin's hourly copies from t0 to t1: [(ts, price, hourly funding rate)], oldest first."""
        tss, rows = self._rows(now or time.time())
        i, j = bisect.bisect_left(tss, t0), bisect.bisect_right(tss, t1)
        return [(tss[k], v[0], v[2] or 0.0) for k in range(i, j) if (v := rows[k].get(coin)) and v[0]]

    def price_at(self, coin: str, ts: float, within: float = 2 * 3600, now: float | None = None) -> float | None:
        """The coin's price in the hourly copy nearest `ts` (within `within` seconds), from the last 8 days."""
        tss, rows = self._rows(now or time.time())
        if not tss:
            return None
        i = bisect.bisect_left(tss, ts)
        j = min((k for k in (i - 1, i) if 0 <= k < len(tss)), key=lambda k: abs(tss[k] - ts))
        v = rows[j].get(coin) if abs(tss[j] - ts) <= within else None
        return v[0] if v else None

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
               "price_change_24h_pct": _pct(cur.get("px"), day[0] if day else None),
               "price_change_7d_pct": _pct(cur.get("px"), week[0] if week else None),
               "spread_bps": cur.get("impact_bps"),
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
        daily = await hl.candles(coin, "1d", end - 1000 * DAY * 1000, end)    # one request: days and weeks
        if not daily:
            return None
        closed = [b for b in daily if b[0] / 1000 + DAY <= now] or daily      # today's forming bar left out
        lv = {**trend_levels([b[4] for b in daily], weekly_closes(daily)), **price_structure(closed),
              **vol_stats(closed), "closes": [_sig(b[4]) for b in closed[-91:]],
              "closes_to": time.strftime("%Y-%m-%d", time.gmtime(closed[-1][0] / 1000)), "day": day, "ts": now}
        # read again after the awaits: another read (the daily refresh, a question) may have written meanwhile, and
        # writing back the copy read before them would drop its coins
        cache = self.store.get_snapshot("trend_levels") or {}
        cache = {c: v for c, v in cache.items() if now - (v.get("ts") or 0) < KEEP_S}   # levels move slowly: kept
        cache[coin] = lv                                                                # until read again
        self.store.put_snapshot("trend_levels", cache)
        return lv

    # ------------------------------------------------------------ every coin --
    def universe(self, extra: list[str] | tuple = (), cap: int = 180) -> list[str]:
        """The most traded perps by 24-hour volume (from the last context read, so it changes with the market each
        day), plus the extra coins (where traders are, his watch list) that are listed on Hyperliquid."""
        cur = (self.store.get_snapshot("hl_ctx") or {}).get("c") or {}
        busy = sorted(cur, key=lambda c: -(cur[c].get("vol") or 0))[:UNIVERSE]
        more = [c.upper() for c in extra if not cur or c.upper() in cur]
        return list(dict.fromkeys(busy + more))[:cap]

    def refresh_due(self, now: float) -> bool:
        """Once a day after 00:30 UTC (yesterday's daily and weekly bars closed), and half an hour after a failure."""
        st = self.store.get_snapshot("levels_refresh") or {}
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        return (not st.get("running") or now - (st.get("ts") or 0) > 3600) and st.get("day") != day \
            and time.gmtime(now).tm_hour * 60 + time.gmtime(now).tm_min >= 30 and now - (st.get("failed") or 0) > 1800

    async def refresh_all(self, hl, coins: list[str], now: float | None = None, pace_s: float = 6.0) -> int:
        """Read the levels of every coin not read today, a few seconds apart (about 45 weight each of the 1,200 a
        minute Hyperliquid allows an address). Returns how many were read. The day counts as done only when at most
        one coin in ten failed (or after a third attempt), so a bad night is tried again half an hour later for the
        coins it missed, not left on old levels until tomorrow."""
        now = now or time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        prev = self.store.get_snapshot("levels_refresh") or {}
        attempt = (prev.get("attempt") or 0) + 1 if prev.get("attempt_day") == day else 1
        self.store.put_snapshot("levels_refresh", {"day": None, "running": True, "ts": now, "attempt": attempt,
                                                   "attempt_day": day})
        read, tried, failed = 0, 0, []
        try:
            for c in coins:
                if ((self.store.get_snapshot("trend_levels") or {}).get(c) or {}).get("day") == day:
                    continue
                tried += 1
                try:
                    if await self.trend(hl, c, now):
                        read += 1
                except Exception as e:  # noqa: BLE001 - one coin failing leaves the rest
                    log.warning("levels for %s failed: %s", c, e)
                    failed.append(c)
                await asyncio.sleep(pace_s)
            done = len(failed) <= 0.1 * max(tried, 1) or attempt >= 3
            if failed:
                log.warning("levels refresh: %d of %d coins failed (attempt %d)", len(failed), tried, attempt)
            self.store.put_snapshot("levels_refresh", {
                "day": day if done else None, "running": False, "ts": time.time(), "read": read, "tried": tried,
                "errors": len(failed), "failed_coins": failed[:20], "attempt": attempt, "attempt_day": day,
                **({} if done else {"failed": time.time()})})
        except Exception:
            self.store.put_snapshot("levels_refresh", {"day": None, "running": False, "failed": time.time(),
                                                       "attempt": attempt, "attempt_day": day})
            raise
        return read
