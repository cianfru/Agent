"""The owner's KuCoin futures positions, read only, kept in the shape his Hyperliquid wallets are (owner, 5 November
2026: he trades on KuCoin while Hyperliquid's funding runs high), so the reads, /mine and the notes when a trader goes
against one of his positions treat them alike.

A KuCoin API key with the General permission only (it reads; it cannot trade or withdraw), set in Railway as
KUCOIN_API_KEY, KUCOIN_API_SECRET and KUCOIN_API_PASSPHRASE. Requests are signed as KuCoin documents
(kucoin.com/docs-new/authentication): KC-API-SIGN is the base64 HMAC-SHA256, keyed by the secret, of timestamp (ms) +
METHOD + path with its query + body; KC-API-PASSPHRASE is the passphrase HMAC-SHA256 signed the same way; the key
version is 2 or 3 (KUCOIN_API_KEY_VERSION, else 2, then 3 if KuCoin refuses 2).

Endpoints (api-futures.kucoin.com, checked 5 November 2026): GET /api/v1/positions (open positions: symbol, currentQty
in lots, avgEntryPrice, markPrice, markValue, unrealisedPnl, unrealisedRoePcnt, realLeverage, liquidationPrice,
settleCurrency, isInverse), GET /api/v1/account-overview?currency=USDT (accountEquity), and the public
GET /api/v1/contracts/{symbol} (baseCurrency, multiplier: coins per lot, fundingFeeRate per fundingRateGranularity ms).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE = "https://api-futures.kucoin.com"
YEAR_MS = 365 * 24 * 3600 * 1000


class KuCoinError(RuntimeError):
    pass


def _b64_hmac(secret: str, msg: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()).decode()


def headers(key: str, secret: str, passphrase: str, method: str, path: str, body: str = "",
            version: str = "2", now_ms: int | None = None) -> dict[str, str]:
    """KuCoin's signed headers for one request. `path` includes its query string, as sent."""
    ts = str(now_ms if now_ms is not None else int(time.time() * 1000))
    return {"KC-API-KEY": key, "KC-API-SIGN": _b64_hmac(secret, ts + method.upper() + path + body),
            "KC-API-TIMESTAMP": ts, "KC-API-PASSPHRASE": _b64_hmac(secret, passphrase),
            "KC-API-KEY-VERSION": version, "Content-Type": "application/json"}


def _f(x: Any) -> float | None:
    try:
        return float(x) if x is not None and x != "" else None
    except (TypeError, ValueError):
        return None


def coin_name(base: str, known: set[str]) -> tuple[str, float]:
    """KuCoin's base currency as the agent names coins (Hyperliquid's names), and the factor that turns KuCoin's
    prices into Hyperliquid's. XBT is BTC; 1000BONK is kBONK (both priced per 1,000); PEPE, priced per coin, is kPEPE
    when Hyperliquid lists it only per 1,000 (prices times 1,000). A coin Hyperliquid does not list keeps its name."""
    b = base.upper()
    b = {"XBT": "BTC"}.get(b, b)
    if b.startswith("1000") and not b.startswith("10000"):
        rest = b[4:]
        if rest in known:
            return rest, 0.001
        return f"k{rest}", 1.0
    if b in known or not known:
        return b, 1.0
    if f"k{b}" in known:
        return f"k{b}", 1000.0
    return b, 1.0


def parse(positions: list[dict], contracts: dict[str, dict], known: set[str]) -> list[tuple[str, dict]]:
    """KuCoin's open positions as (coin, position) in the agent's shape: side, size in coins, entry, value in dollars,
    open profit, return on margin, leverage, liquidation price, KuCoin's mark price and its funding as a year's
    rate (positive: longs pay)."""
    out = []
    for p in positions or []:
        qty = _f(p.get("currentQty")) or 0.0
        if not qty or p.get("isOpen") is False:
            continue
        sym = str(p.get("symbol") or "")
        c = contracts.get(sym) or {}
        coin, k = coin_name(str(c.get("baseCurrency") or sym.removesuffix("USDTM").removesuffix("USDCM")
                                .removesuffix("USDM")), known)
        mult = abs(_f(c.get("multiplier")) or 0.0)
        inverse = bool(p.get("isInverse") if p.get("isInverse") is not None else c.get("isInverse"))
        mark = _f(p.get("markPrice"))
        if inverse:                 # a coin-margined contract: each lot is worth `multiplier` dollars
            value = abs(qty) * (mult or 1.0)
        else:
            value = abs(_f(p.get("markValue")) or (abs(qty) * mult * (mark or 0)))
        rate, every = _f(c.get("fundingFeeRate")), _f(c.get("fundingRateGranularity"))
        lev = _f(p.get("realLeverage")) or _f(p.get("leverage"))
        roe = _f(p.get("unrealisedRoePcnt"))
        scale = lambda x: x * k if x else x  # noqa: E731 - KuCoin's price in Hyperliquid's units
        out.append((coin, {
            "side": "long" if qty > 0 else "short", "size": abs(qty) * mult / (k or 1.0) if mult and not inverse
            else None, "entry_px": scale(_f(p.get("avgEntryPrice")) or 0.0), "notional": round(value, 2),
            "upnl": _f(p.get("unrealisedPnl")) or 0.0, "roe": round(roe, 4) if roe is not None else None,
            "lev": round(lev, 2) if lev else None, "liq": scale(_f(p.get("liquidationPrice"))),
            "mark": scale(mark), "symbol": sym, "settle": p.get("settleCurrency"),
            "funding_apr_pct": round(100 * rate * YEAR_MS / every, 1) if rate is not None and every else None}))
    return out


class KuCoin:
    def __init__(self, key: str, secret: str, passphrase: str, version: str = "", http: httpx.AsyncClient | None = None):
        self.key, self.secret, self.passphrase = key, secret, passphrase
        self.versions = [version] if version else ["2", "3"]
        self.http = http or httpx.AsyncClient(timeout=15, headers={"User-Agent": "outrider-agent"})
        self._own = http is None
        self.contracts: dict[str, dict] = {}

    async def aclose(self) -> None:
        if self._own:
            await self.http.aclose()

    async def _get(self, path: str, signed: bool = True) -> Any:
        last = None
        for v in self.versions if signed else [""]:
            h = headers(self.key, self.secret, self.passphrase, "GET", path, "", v) if signed else {}
            r = await self.http.get(BASE + path, headers=h)
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.status_code == 200 and str(body.get("code")) == "200000":
                if signed and self.versions[0] != v:
                    self.versions = [v]            # remember the version KuCoin accepted
                return body.get("data")
            last = f"HTTP {r.status_code}, code {body.get('code')}: {str(body.get('msg') or '')[:120]}"
            if r.status_code != 401:               # only a refused signature is worth the other version
                break
        raise KuCoinError(last or "no answer")

    async def contract(self, symbol: str) -> dict:
        """One contract's details (public): its base currency, coins per lot and funding now."""
        c = await self._get(f"/api/v1/contracts/{symbol}", signed=False) or {}
        self.contracts[symbol] = c
        return c

    async def account(self, known: set[str]) -> dict:
        """His futures account: equity (USDT and USDC margined together) and open positions, keyed by coin. A second
        position on a coin (hedge mode, or one in USDT and one in USDC) goes under `extra`."""
        raw = await self._get("/api/v1/positions") or []
        equity = 0.0
        for cur in ("USDT", "USDC"):
            try:
                o = await self._get(f"/api/v1/account-overview?currency={cur}") or {}
                equity += _f(o.get("accountEquity")) or 0.0
            except KuCoinError as e:
                if cur == "USDT":
                    raise
                log.info("KuCoin %s account not read: %s", cur, e)
        for sym in {str(p.get("symbol")) for p in raw if p.get("symbol")}:
            try:
                await self.contract(sym)
            except (KuCoinError, httpx.HTTPError) as e:   # the position still shows, without its funding
                log.warning("KuCoin contract %s not read: %s", sym, e)
        positions: dict[str, dict] = {}
        extra: dict[str, dict] = {}
        for coin, p in parse(raw, self.contracts, known):
            (extra if coin in positions else positions)[coin] = p
        out = {"venue": "KuCoin", "equity": round(equity, 2), "positions": positions}
        if extra:
            out["extra"] = extra
        return out


def accounts(acct: dict) -> dict[str, dict]:
    """The KuCoin account as entries of the owner's snapshot: one, or two when a coin holds two positions."""
    out = {"kucoin": {k: v for k, v in acct.items() if k != "extra"}}
    if acct.get("extra"):
        out["kucoin:2"] = {"venue": "KuCoin", "equity": acct["equity"], "positions": acct["extra"]}
    return out

