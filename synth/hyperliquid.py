"""Hyperliquid's public info API (free, no key): positions, fills, mids and candles.

POST https://api.hyperliquid.xyz/info. Hyperliquid allows 1,200 request weight per IP a minute: an account or the
mids weigh 2, other reads 20 plus 1 per 20 items returned (60 for candles). Every read in this process draws on one
budget (`BUDGET`, 900 a minute: three quarters of the limit), so the daily scan, the background refresh, tracking and
scoring together can never exceed it; a 429 waits as long as Hyperliquid asks. Coin names: Reflex upper-cases
Hyperliquid's "k" markets ("KPEPE"); Hyperliquid itself says "kPEPE". `names()` maps upper-case back to Hyperliquid's.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

INFO_URL = "https://api.hyperliquid.xyz/info"


def _num(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def move(side: str, entry: float | None, size: float | None, upnl: float | None) -> float | None:
    """How far the price has moved the trader's way since entry (0.5 = +50%), leverage aside."""
    cost = (entry or 0) * (size or 0)
    return upnl / cost if cost and upnl is not None else None


LIGHT = {"clearinghouseState", "allMids", "l2Book", "orderStatus", "spotClearinghouseState", "exchangeStatus"}


def weight(body: dict) -> int:
    """A read's weight before its answer (Hyperliquid's info rate limits)."""
    return 2 if body.get("type") in LIGHT else 20


def extra_weight(body: dict, res: Any) -> int:
    """What the items returned add: 1 per 20 (per 60 for candles)."""
    if not isinstance(res, list):
        return 0
    return len(res) // (60 if body.get("type") == "candleSnapshot" else 20)


class Budget:
    """One request-weight budget for every Hyperliquid read in this process: refills at `per_min`, holds at most a
    quarter of a minute's worth, so a burst cannot spend the minute at once. Single event loop: no lock needed."""

    def __init__(self, per_min: float, clock=time.monotonic):
        self.rate = per_min / 60
        self.cap = per_min / 4
        self.tokens = self.cap
        self.clock = clock
        self.t = clock()
        self.spent = 0.0

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate)
        self.t = now

    async def take(self, w: float) -> None:
        while True:
            self._refill()
            if self.tokens >= min(w, self.cap) - 1e-6:   # rounding must not leave it a hair short for ever
                self.tokens -= w
                self.spent += w
                return
            await asyncio.sleep((min(w, self.cap) - self.tokens) / self.rate)

    def charge(self, w: float) -> None:
        """Weight known only from the answer: owed, so the next reads wait for it."""
        self._refill()
        self.tokens -= w
        self.spent += w


BUDGET = Budget(900)


class HL:
    def __init__(self, client: httpx.AsyncClient | None = None, url: str = INFO_URL, budget: Budget | None = None):
        self.http = client or httpx.AsyncClient(timeout=20)
        self.url = url
        self.budget = budget or BUDGET
        self._names: dict[str, str] = {}

    async def _post(self, body: dict, tries: int = 3) -> Any:
        """POST within the shared budget; on 429 wait as long as Hyperliquid says (else back off), and on 5xx back
        off."""
        for i in range(tries):
            await self.budget.take(weight(body))
            r = await self.http.post(self.url, json=body)
            if r.status_code == 429 or r.status_code >= 500:
                if i < tries - 1:
                    try:
                        wait = float(r.headers.get("retry-after") or 0)
                    except ValueError:
                        wait = 0.0
                    await asyncio.sleep(min(max(wait, 2 ** (i + 1)), 60))
                    continue
            r.raise_for_status()
            res = r.json()
            self.budget.charge(extra_weight(body, res))
            return res

    async def mids(self) -> dict[str, float]:
        """Mid price per coin, keyed by UPPER-case name (also refreshes the name map)."""
        raw = await self._post({"type": "allMids"})
        out = {}
        for k, v in (raw or {}).items():
            if k.startswith("@"):
                continue  # spot pairs
            self._names[k.upper()] = k
            try:
                out[k.upper()] = float(v)
            except (TypeError, ValueError):
                pass
        return out

    def name(self, coin: str) -> str:
        return self._names.get(coin.upper(), coin)

    async def positions(self, address: str) -> dict[str, dict]:
        """Open perp positions keyed by UPPER coin: {side, size, entry_px, notional, upnl}."""
        st = await self._post({"type": "clearinghouseState", "user": address})
        out = {}
        for ap in (st or {}).get("assetPositions") or []:
            p = ap.get("position") or {}
            szi = float(p.get("szi") or 0)
            if not szi:
                continue
            out[str(p.get("coin", "")).upper()] = {
                "side": "long" if szi > 0 else "short",
                "size": abs(szi),
                "entry_px": float(p.get("entryPx") or 0),
                "notional": abs(float(p.get("positionValue") or 0)),
                "upnl": float(p.get("unrealizedPnl") or 0),
            }
        return out

    @staticmethod
    def parse_positions(st: dict) -> dict[str, dict]:
        """Open perp positions from a clearinghouseState, keyed by UPPER coin: {side, size, entry_px, notional, upnl,
        roe (return on margin, as Hyperliquid shows it), lev, liq}."""
        out = {}
        for ap in (st or {}).get("assetPositions") or []:
            p = ap.get("position") or {}
            szi = float(p.get("szi") or 0)
            if not szi:
                continue
            out[str(p.get("coin", "")).upper()] = {
                "side": "long" if szi > 0 else "short", "size": abs(szi),
                "entry_px": float(p.get("entryPx") or 0), "notional": abs(float(p.get("positionValue") or 0)),
                "upnl": float(p.get("unrealizedPnl") or 0), "roe": _num(p.get("returnOnEquity")),
                "lev": _num((p.get("leverage") or {}).get("value")), "liq": _num(p.get("liquidationPx"))}
        return out

    async def account(self, address: str) -> dict:
        """Account value and open perp positions: {"equity": float, "positions": {COIN: {side, size, ...}}}."""
        st = await self._post({"type": "clearinghouseState", "user": address}) or {}
        return {"equity": float((st.get("marginSummary") or {}).get("accountValue") or 0),
                "positions": self.parse_positions(st)}

    async def closing_fills(self, address: str, coin: str, since_ms: int) -> list[dict]:
        """Fills that reduced/closed a position in `coin` since `since_ms`: [{time, px, sz, pnl}]."""
        fills = await self._post({"type": "userFillsByTime", "user": address, "startTime": int(since_ms),
                                  "aggregateByTime": True})
        out = []
        for f in fills or []:
            if str(f.get("coin", "")).upper() != coin.upper():
                continue
            if not str(f.get("dir", "")).startswith("Close"):
                continue
            out.append({"time": int(f.get("time") or 0), "px": float(f.get("px") or 0),
                        "sz": float(f.get("sz") or 0), "pnl": float(f.get("closedPnl") or 0)})
        return sorted(out, key=lambda f: f["time"])

    async def candles(self, coin: str, interval: str, start_ms: int, end_ms: int) -> list[list[float]]:
        """[[t_ms, open, high, low, close], ...]"""
        raw = await self._post({"type": "candleSnapshot", "req": {
            "coin": self.name(coin), "interval": interval, "startTime": int(start_ms), "endTime": int(end_ms)}})
        return [[c["t"], float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])] for c in raw or []]

    async def aclose(self) -> None:
        await self.http.aclose()
