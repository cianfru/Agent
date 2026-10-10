"""S1 Part L (amendment 3): how do smart-trader convergences go when the engine agrees with them?

Each Hyperliquid convergence the agent sees within 2 hours of its last open is logged with the engine's
reading for that coin at that moment (Reflex's 1D and 4H signals). Two outcomes are scored:
- the price move in the group's direction 1, 3 and 7 days after the agent first saw it (its group pick);
- the traders' own result once all of them have closed (from their closing fills).
Rules: docs/studies/S1-engine-vs-traders.md. Descriptive until the declared minimums are met.
"""

from __future__ import annotations

import statistics
from typing import Any

# The engine's direction. "short" includes the signals that say not to be long (stretched, blocked).
ENGINE_LONG = {"STRONG_LONG", "LIGHT_LONG", "ACCUMULATE", "REVIVAL_SEED", "REVIVAL_SEED_CONFIRMED"}
ENGINE_SHORT = {"RISK_OFF", "LIGHT_SHORT", "TRIM", "TRIM_HARD", "NO_LONG"}
ALIGNMENTS = ("with", "against", "none", "unknown")
MIN_EACH = 30  # declared minimum per arm (with, and against + none together) before any verdict
MIN_COINS = 10  # ...from at least this many distinct coins each


def engine_side(sig: str | None) -> str | None:
    if sig in ENGINE_LONG:
        return "long"
    if sig in ENGINE_SHORT:
        return "short"
    return None


def alignment(side: str, sig: str | None, known: bool) -> str:
    """with / against / none (the engine has no direction) / unknown (no reading for the coin)."""
    if not known:
        return "unknown"
    es = engine_side(sig)
    if es is None:
        return "none"
    return "with" if es == side else "against"


def reading(rf: dict | None, coin: str, side: str) -> dict:
    """The engine's reading for a coin from a Reflex snapshot, and how it lines up with the group."""
    eng = ((rf or {}).get("engine") or {}).get(coin.upper())
    if eng is None:  # older snapshots only carry the top coins
        row = ((rf or {}).get("coins") or {}).get(coin.upper())
        eng = {"1d": row.get("signal_1d"), "4h": row.get("signal_4h")} if row else None
    known = eng is not None
    s1, s4 = (eng or {}).get("1d"), (eng or {}).get("4h")
    return {"signal_1d": s1, "signal_4h": s4,
            "align_1d": alignment(side, s1, known), "align_4h": alignment(side, s4, known)}


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "median_pct": None, "positive_pct": None}
    return {"n": len(values), "median_pct": round(100 * statistics.median(values), 2),
            "positive_pct": round(100 * sum(v > 0 for v in values) / len(values), 1)}


def report(groups: list[dict], members_by_group: dict[int, list[dict]], picks_by_group: dict[int, dict],
           fast_hours: float) -> dict:
    """Logged convergences by the engine's 1D alignment (headline) and 4H, all and fast only."""
    logged = [g for g in groups if g["venue"] == "hyperliquid" and (g["meta"].get("study") or {}).get("logged")]

    def arm(gs: list[dict]) -> dict:
        out: dict[str, Any] = {"groups": len(gs)}
        for h in ("1d", "3d", "7d"):
            vals = [picks_by_group[g["id"]][f"ret_{h}"] for g in gs
                    if g["id"] in picks_by_group and picks_by_group[g["id"]].get(f"ret_{h}") is not None]
            out[h] = _stats(vals)
        traders = []
        for g in gs:
            ms = members_by_group.get(g["id"], [])
            done = [m["pnl_pct"] for m in ms if m.get("pnl_pct") is not None]
            if g["status"] == "closed" and done:
                traders.append(statistics.median(done))
        out["traders_closed"] = _stats(traders)
        return out

    def split(key: str, gs: list[dict]) -> dict:
        return {a: arm([g for g in gs if g["meta"]["study"].get(key) == a]) for a in ALIGNMENTS}

    fast = [g for g in logged if (g["meta"].get("span_h") or 0) <= fast_hours]
    with_g = [g for g in logged if g["meta"]["study"].get("align_1d") == "with"]
    rest_g = [g for g in logged if g["meta"]["study"].get("align_1d") in ("against", "none")]
    with_n, rest_n = len(with_g), len(rest_g)
    coins_ok = len({g["coin"] for g in with_g}) >= MIN_COINS and len({g["coin"] for g in rest_g}) >= MIN_COINS
    return {
        "logged": len(logged),
        "headline_1d": split("align_1d", logged),
        "fast_1d": split("align_1d", fast),
        "by_4h": split("align_4h", logged),
        "ready": with_n >= MIN_EACH and rest_n >= MIN_EACH and coins_ok,
        "needs": {"with": max(0, MIN_EACH - with_n), "against_or_none": max(0, MIN_EACH - rest_n)},
        "note": "Context, not a signal, until both arms reach the declared minimum.",
    }
