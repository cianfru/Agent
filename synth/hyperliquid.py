"""Hyperliquid's public info API (free, no key): positions, fills, mids and candles.

POST https://api.hyperliquid.xyz/info. Reads per wallet weigh ~2 of the 1,200/min per-IP budget, so a few
dozen tracked wallets every 5 minutes is far inside it. Coin names: Reflex upper-cases Hyperliquid's
"k" markets ("KPEPE"); Hyperliquid itself says "kPEPE". `names()` maps upper-case back to Hyperliquid's.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

INFO_URL = "https://api.hyperliquid.xyz/info"


def _num(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _impact_bps(c: dict) -> float | None:
    """What a market order of Hyperliquid's impact size pays across the book, bid to ask, in basis points of the mid:
    how thin the coin is to trade."""
    try:
        bid, ask = (float(x) for x in c.get("impactPxs") or [])
        mid = float(c.get("midPx") or c.get("markPx"))
    except (TypeError, ValueError):
        return None
    return round(1e4 * (ask - bid) / mid, 1) if mid and ask >= bid > 0 else None


def move(side: str, entry: float | None, size: float | None, upnl: float | None) -> float | None:
    """How far the price has moved the trader's way since entry (0.5 = +50%), leverage aside."""
    cost = (entry or 0) * (size or 0)
    return upnl / cost if cost and upnl is not None else None


class HL:
    def __init__(self, client: httpx.AsyncClient | None = None, url: str = INFO_URL):
        self.http = client or httpx.AsyncClient(timeout=20)
        self.url = url
        self._names: dict[str, str] = {}

    async def _post(self, body: dict, tries: int = 3) -> Any:
        """POST with backoff on 429/5xx (the info API rate-limits per IP)."""
        for i in range(tries):
            r = await self.http.post(self.url, json=body)
            if r.status_code == 429 or r.status_code >= 500:
                if i < tries - 1:
                    await asyncio.sleep(2 ** (i + 1))
                    continue
            r.raise_for_status()
            return r.json()

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

    async def contexts(self) -> dict[str, dict]:
        """Every listed perp, UPPER-case: mark price, open interest (in coins), funding (the hourly rate), 24-hour
        volume in dollars."""
        meta, ctxs = await self._post({"type": "metaAndAssetCtxs"})
        return {u["name"].upper(): {"px": _num(c.get("markPx")), "oi": _num(c.get("openInterest")),
                                    "funding": _num(c.get("funding")), "vol": _num(c.get("dayNtlVlm")),
                                    "impact_bps": _impact_bps(c)}
                for u, c in zip(meta.get("universe") or [], ctxs or []) if not u.get("isDelisted")}

    async def top_coins(self, n: int) -> list[str]:
        """The n most traded perps by 24-hour volume, UPPER-case (delisted ones left out)."""
        ctx = await self.contexts()
        return sorted(ctx, key=lambda c: -(ctx[c]["vol"] or 0))[:n]

    async def ensure_names(self) -> None:
        """The exchange's own spelling of each coin (kPEPE...), read once, before asking for candles."""
        if not self._names:
            await self.mids()

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
        """[[t_ms, open, high, low, close, volume in coins], ...]"""
        raw = await self._post({"type": "candleSnapshot", "req": {
            "coin": self.name(coin), "interval": interval, "startTime": int(start_ms), "endTime": int(end_ms)}})
        return [[c["t"], float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]), float(c.get("v") or 0)]
                for c in raw or []]

    async def aclose(self) -> None:
        await self.http.aclose()
