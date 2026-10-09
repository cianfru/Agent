"""Cowboy / derive_scan feed.

derive_scan's `feed` workflow (in cianfru/cowboy-feed) rebuilds static JSON every
~5 minutes and force-pushes it to the `site-data` branch. It is public, so we read
it straight from raw.githubusercontent.com. Files used:

  manifest.json        publication_revision + list of artifacts
  markets.json         market consensus, sentiment, per-coin RCCE engine + options summary
  traders.json         ranked Derive option traders and per-cohort net delta per coin
  flow.json            24h taker flow (call/put/perp buy vs sell notional & premium)
  wallets/{COIN}.json  top holders per coin (address, rank, cohort, net delta)
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

# Wallet cohorts that count as "smart" when deciding which positions matter.
SMART_PNL_COHORTS = {"Money Printer", "Smart Money", "Grinder"}


async def _get(client: httpx.AsyncClient, base: str, path: str) -> Any:
    r = await client.get(f"{base}/{path}")
    r.raise_for_status()
    return r.json()


def _r(x: Any, nd: int = 3) -> Any:
    return round(x, nd) if isinstance(x, float) else x


def _coin(c: dict, flow: dict | None) -> dict:
    align = (c.get("align") or {}).get("readings") or {}
    a7, a30 = align.get("7d") or {}, align.get("30d") or {}
    out = {
        "price": c.get("price"),
        "chg_24h": _r(c.get("chg_24h"), 2),
        "chg_7d": _r(c.get("chg_7d"), 2),
        "signal_4h": c.get("signal_4h"),
        "signal_1d": c.get("signal_1d"),
        "unified": c.get("unified"),
        "regime_4h": c.get("regime_4h"),
        "regime_1d": c.get("regime_1d"),
        "z_4h": c.get("z_4h"),
        "z_1d": c.get("z_1d"),
        "heat_4h": c.get("heat_4h"),
        "heat_1d": c.get("heat_1d"),
        "funding_ann": _r(c.get("funding_ann")),
        "atm_iv_30d": _r(c.get("atm_iv_30d")),
        "rr25_30d": _r(c.get("rr25_30d"), 4),
        "lean": c.get("lean"),
        "options_7d": _pick(a7.get("options"), "state", "score"),
        "wallets_7d": _pick(a7.get("wallets"), "state", "score", "net_delta_usd"),
        "wallets_30d": _pick(a30.get("wallets"), "state", "score", "net_delta_usd"),
    }
    if flow:
        net = {}
        for leg in ("call", "put", "perp"):
            f = flow.get(leg)
            if f:
                net[leg] = round(f["buy_notional_usd"] - f["sell_notional_usd"])
        out["taker_net_24h_usd"] = net
    return {k: v for k, v in out.items() if v is not None}


def _pick(d: dict | None, *keys: str) -> dict | None:
    if not d:
        return None
    out = {k: _r(d.get(k)) for k in keys if d.get(k) is not None}
    return out or None


def _holders(doc: dict) -> list[dict]:
    out = []
    for h in doc.get("holders") or []:
        out.append(
            {
                "address": h["address"].lower(),
                "rank": h.get("rank"),
                "cohort": h.get("pnl_cohort"),
                "size": h.get("size_cohort"),
                "smart": h.get("pnl_cohort") in SMART_PNL_COHORTS,
                "win_rate": h.get("win_rate"),
                "pnl_usd": h.get("option_pnl"),
                "net_delta_usd": round(h.get("net_delta_usd") or 0),
                "legs": [
                    {"instrument": lg.get("instrument"), "net": lg.get("net"), "entry": lg.get("entry"),
                     "mark": lg.get("mark"), "delta_usd": round(lg.get("delta_usd") or 0), "expiry": lg.get("expiry")}
                    for lg in h.get("legs") or [] if lg.get("instrument")
                ],
            }
        )
    return out


async def trader_book(base_url: str, address: str, client: httpx.AsyncClient) -> dict | None:
    """One trader's open options keyed by instrument, or None if Cowboy does not publish the trader."""
    r = await client.get(f"{base_url}/traders/{address.lower()}.json")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return {lg["instrument"]: lg for lg in (r.json().get("book") or []) if lg.get("instrument")}


async def fetch(base_url: str, client: httpx.AsyncClient | None = None) -> dict:
    """Return a normalised Cowboy snapshot."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        manifest, markets, traders, flow = await asyncio.gather(
            _get(client, base_url, "manifest.json"),
            _get(client, base_url, "markets.json"),
            _get(client, base_url, "traders.json"),
            _get(client, base_url, "flow.json"),
        )
        wallet_paths = sorted(
            p for p in (manifest.get("artifacts") or {}) if p.startswith("wallets/")
        )
        wallet_docs = await asyncio.gather(
            *(_get(client, base_url, p) for p in wallet_paths), return_exceptions=True
        )
    finally:
        if own:
            await client.aclose()

    by_flow = flow.get("by_coin") or {}
    coins = {c["und"]: _coin(c, by_flow.get(c["und"])) for c in markets.get("coins") or []}

    wallets: dict[str, list[dict]] = {}
    for path, doc in zip(wallet_paths, wallet_docs):
        if isinstance(doc, Exception):
            continue
        wallets[doc.get("und") or path.split("/")[1].removesuffix(".json")] = _holders(doc)

    ctx = markets.get("context") or {}
    detail = markets.get("consensus_detail") or {}
    market = {
        "consensus_4h": (detail.get("4h") or {}).get("consensus"),
        "consensus_4h_strength": (detail.get("4h") or {}).get("strength"),
        "consensus_1d": (detail.get("1d") or {}).get("consensus"),
        "consensus_1d_strength": (detail.get("1d") or {}).get("strength"),
        "fear_greed": (ctx.get("sentiment") or {}).get("fear_greed_value"),
        "btc_dominance": (ctx.get("global_metrics") or {}).get("btc_dominance"),
        "stablecoin_trend": (ctx.get("stablecoin") or {}).get("trend"),
        "stablecoin_7d_pct": (ctx.get("stablecoin") or {}).get("change_7d_pct"),
    }

    cohorts = {}
    for group in (traders.get("cohorts") or {}).get("pnl") or []:
        if group["name"] in SMART_PNL_COHORTS:
            cohorts[group["name"]] = {
                coin: _pick(v, "state", "score", "net_delta_usd")
                for coin, v in (group.get("coins") or {}).items()
            }

    return {
        "source": "cowboy",
        "revision": manifest.get("publication_revision"),
        "generated_at": markets.get("generated_at"),
        "market": {k: v for k, v in market.items() if v is not None},
        "coins": coins,
        "smart_cohorts": cohorts,
        "wallets": wallets,
    }
