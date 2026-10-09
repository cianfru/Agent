"""Reflex / RCCE_Scanner backend (FastAPI on Railway).

Reflex enforces an access token on /api/*: POST /api/auth/login {"code": ...} returns
a 30-day HMAC token sent as `Authorization: Bearer`. Give the agent its own invite
code (revocable) as REFLEX_ACCESS_CODE, or a ready token as REFLEX_TOKEN.

Endpoints read each poll (all unrate-limited GETs; ETag/304 keeps egress low):
  /api/hyperlens/positioning?cohort=profitable  per-coin smart-trader long/short, opens 24h,
                                                convergence, engine 4h/1d reading
  /api/notifications/convergence?hours=48       profitable traders converging on a coin
  /api/notifications/anomalies                  extreme funding / OI surge / volume / LSR / CVD
  /api/notifications/market-setups              OI-vs-price divergence setups
  /api/notifications/exhaustion-opportunities   exhaustion floors / climax reversals
  /api/market-pulse?timeframe=1d                consensus, BTC regime, funding mood, ETF flows

Note: every request marks Reflex "active" (faster synthesis = more Railway cost), so
keep the poll interval at a few minutes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

TOP_COINS = 40  # coins kept in the snapshot, by number of smart traders positioned


class ReflexAuthError(RuntimeError):
    pass


class ReflexClient:
    def __init__(self, base_url: str, access_code: str = "", token: str = "",
                 client: httpx.AsyncClient | None = None):
        self.base = base_url.rstrip("/")
        self.code = access_code
        self.token = token
        self.http = client or httpx.AsyncClient(timeout=30)
        self._etag: dict[str, tuple[str, Any]] = {}

    async def login(self) -> None:
        if not self.code:
            raise ReflexAuthError("Reflex needs REFLEX_ACCESS_CODE or REFLEX_TOKEN")
        r = await self.http.post(f"{self.base}/api/auth/login", json={"code": self.code})
        if r.status_code != 200:
            raise ReflexAuthError(f"Reflex login failed: HTTP {r.status_code}")
        self.token = r.json().get("token") or ""

    async def get(self, path: str) -> Any:
        if not self.token and self.code:
            await self.login()
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            cached = self._etag.get(path)
            if cached:
                headers["If-None-Match"] = cached[0]
            r = await self.http.get(f"{self.base}{path}", headers=headers)
            if r.status_code == 401 and attempt == 0 and self.code:
                await self.login()
                continue
            if r.status_code == 304 and cached:
                return cached[1]
            if r.status_code == 401:
                raise ReflexAuthError("Reflex rejected the token (401)")
            r.raise_for_status()
            body = r.json()
            if et := r.headers.get("etag"):
                self._etag[path] = (et, body)
            return body
        raise ReflexAuthError("unreachable")

    async def aclose(self) -> None:
        await self.http.aclose()


def _coin(sym: str) -> str:
    return sym.split("/")[0].split("-")[0].upper()


def _lean(row: dict) -> str | None:
    lo, sh = row.get("long") or {}, row.get("short") or {}
    lu, su = lo.get("usd") or 0, sh.get("usd") or 0
    if (lo.get("n") or 0) + (sh.get("n") or 0) < 2:
        return None
    if lu > 2 * su:
        return "long"
    if su > 2 * lu:
        return "short"
    return "neutral"


def _row(r: dict) -> dict:
    eng = r.get("engine") or {}
    e4, e1 = eng.get("4h") or {}, eng.get("1d") or {}
    lo, sh = r.get("long") or {}, r.get("short") or {}
    out = {
        "price": r.get("mark") or e1.get("price") or e4.get("price"),
        "signal_4h": e4.get("signal"),
        "signal_1d": e1.get("signal"),
        "regime_4h": e4.get("regime"),
        "regime_1d": e1.get("regime"),
        "z_4h": e4.get("zscore"),
        "z_1d": e1.get("zscore"),
        "heat_4h": e4.get("heat"),
        "heat_1d": e1.get("heat"),
        "smart_long": {"n": lo.get("n"), "usd": lo.get("usd"), "in_profit": lo.get("in_profit"),
                       "median_entry": lo.get("median_entry")},
        "smart_short": {"n": sh.get("n"), "usd": sh.get("usd"), "in_profit": sh.get("in_profit"),
                        "median_entry": sh.get("median_entry")},
        "smart_lean": _lean(r),
        "opens_24h": r.get("opens_24h"),
        "day_ago": r.get("day_ago"),
        "convergence": r.get("convergence"),
    }
    return {k: v for k, v in out.items() if v is not None}


async def fetch(rc: ReflexClient) -> dict:
    """Return a normalised Reflex snapshot."""
    paths = {
        "positioning": "/api/hyperlens/positioning?cohort=profitable",
        "convergence": "/api/notifications/convergence?hours=48",
        "anomalies": "/api/notifications/anomalies",
        "setups": "/api/notifications/market-setups",
        "exhaustion": "/api/notifications/exhaustion-opportunities",
        "pulse": "/api/market-pulse?timeframe=1d",
    }
    results = await asyncio.gather(*(rc.get(p) for p in paths.values()), return_exceptions=True)
    got: dict[str, Any] = {}
    errors = {}
    for name, res in zip(paths, results):
        if isinstance(res, ReflexAuthError):
            raise res
        if isinstance(res, Exception):
            errors[name] = repr(res)[:200]
            log.warning("reflex %s failed: %s", name, res)
        else:
            got[name] = res
    if "positioning" not in got:
        raise RuntimeError(f"reflex positioning unavailable: {errors.get('positioning')}")

    pos = got["positioning"]
    rows = sorted(
        pos.get("rows") or [],
        key=lambda r: -(((r.get("long") or {}).get("n") or 0) + ((r.get("short") or {}).get("n") or 0)),
    )[:TOP_COINS]
    coins = {_coin(r["coin"]): _row(r) for r in rows}

    pulse = got.get("pulse") or {}
    market = {k: pulse.get(k) for k in
              ("consensus", "strength", "btc_regime", "btc_zscore", "funding_mood", "etf_7d_net", "cb_premium")
              if pulse.get(k) is not None}

    convergences = [
        {"coin": _coin(i["symbol"]), "side": i.get("side"), "n": i.get("n"), "ts": i.get("timestamp"),
         "detail": i.get("detail"), "key": i.get("dedup_key")}
        for i in (got.get("convergence") or {}).get("items") or []
    ]
    anomalies = [
        {"coin": _coin(a.get("symbol", "")), "type": a.get("anomaly_type"), "severity": a.get("severity"),
         "direction": a.get("direction"), "value": a.get("current_value"), "z": a.get("z_score"),
         "context": a.get("context"), "key": a.get("dedup_key")}
        for a in (got.get("anomalies") or {}).get("anomalies") or []
    ]
    setups = [
        {"coin": s.get("coin") or _coin(s.get("symbol", "")), "type": s.get("type"),
         "severity": s.get("severity"), "title": s.get("title"), "signal": s.get("signal"),
         "regime": s.get("regime"), "heat": s.get("heat")}
        for key in ("setups", "exhaustion")
        for s in (got.get(key) or {}).get("setups" if key == "setups" else "opportunities") or []
    ]

    return {
        "source": "reflex",
        "generated_at": pos.get("updated_at"),
        "stale": bool(pos.get("stale")),
        "tracked_wallets": pos.get("wallets"),
        "market": market,
        "coins": coins,
        "convergences": convergences,
        "anomalies": anomalies,
        "setups": setups,
        "errors": errors,
    }
