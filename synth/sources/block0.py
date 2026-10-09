"""Block0 (Robinhood Chain launches) weekly numbers.

Block0's Orbio agent publishes `stats.json` on the public `agent-data` branch of cianfru/Block0:
Orbio launches and graduations over 7 days, repeat vs single owners, fee concentration, early-wallet
sales and how concentrated launch holders are. Facts about what happened, never a signal.
"""

from __future__ import annotations

import httpx

DEFAULT_URL = "https://raw.githubusercontent.com/cianfru/Block0/agent-data/stats.json"


async def fetch(url: str = DEFAULT_URL, client: httpx.AsyncClient | None = None) -> dict:
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        r = await client.get(url)
        r.raise_for_status()
        s = r.json()
    finally:
        if own:
            await client.aclose()
    o, ev, reads = s.get("orbio") or {}, s.get("events") or {}, s.get("reads") or {}
    return {
        "source": "block0",
        "generated_at": (s.get("at") or 0) / 1000,
        "window_days": s.get("windowDays"),
        "orbio_launched": o.get("launched"),
        "orbio_graduated": o.get("graduated"),
        "orbio_grad_pct": o.get("gradPct"),
        "orbio_fees_usd": o.get("feesUsd"),
        "orbio_fees_top10_pct": o.get("feesTop10Pct"),
        "repeat_owner_launches": o.get("repeatLaunches"),
        "repeat_owner_graduated": o.get("repeatGraduated"),
        "pons_graduated": (s.get("pons") or {}).get("graduated"),
        "early_wallet_sales": ev.get("earlySales"),
        "early_wallet_sales_launches": ev.get("earlySalesLaunches"),
        "launches_read": reads.get("launches"),
        "bundled_pct": reads.get("bundledPct"),
        "curve_top10_pct": (reads.get("curve") or {}).get("top10"),
    }
