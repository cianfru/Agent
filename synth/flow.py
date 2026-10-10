"""The live pulse: what Hyperliquid's profitable traders opened and closed in the last hour, 6 hours, 24 hours.

Reflex refreshes every tracked wallet's positions every few minutes while the agent polls it. Each poll the
agent stores one compact row per coin (long and short positions, their dollars, the price). Comparing
consecutive rows gives the flow:
- positions: the change in how many traders hold a long or a short (a close shows as a drop);
- dollars: the change in their size with the price move taken out, so a rally doesn't read as longs added.
Intervals where Reflex's roster changed (its daily refresh adds and drops wallets), the agent was down for over
30 minutes, or Reflex served stale data are left out, so the flow is only what traders did. Pure functions.
"""

from __future__ import annotations

from typing import Any

MAX_GAP_S = 30 * 60
MIN_USD_FLOW = 50_000  # a coin with no trader opening or closing still shows if this much was added or cut
WINDOWS = (1, 6, 24)


def record(reflex: dict | None) -> dict | None:
    """One stored row from a Reflex snapshot: {"w": tracked wallets, "stale": bool, "c": {coin: [ln, lu, sn, su, px]}}."""
    pos = (reflex or {}).get("positions")
    if not pos:
        return None
    return {"w": reflex.get("tracked_wallets"), "stale": bool(reflex.get("stale")), "c": pos}


def _usd_flow(u0: float, u1: float, p0: float | None, p1: float | None) -> float:
    """Change in a side's dollars beyond what the price move alone explains."""
    if p0 and p1:
        return u1 - u0 * (p1 / p0)
    return u1 - u0


def _usable(a: tuple[float, dict], b: tuple[float, dict]) -> bool:
    (t0, r0), (t1, r1) = a, b
    return (t1 - t0 <= MAX_GAP_S and not r0.get("stale") and not r1.get("stale")
            and r0.get("w") == r1.get("w"))


def window(rows: list[tuple[float, dict]], hours: float, now: float) -> dict:
    """Flow over the last `hours` from stored rows [(ts, row)] sorted by time."""
    start = now - hours * 3600
    rows = [r for r in rows if r[0] >= start - MAX_GAP_S]
    # the first row at or before the window start anchors it
    first = max((i for i, r in enumerate(rows) if r[0] <= start), default=0)
    rows = rows[first:]
    coins: dict[str, dict[str, float]] = {}
    covered = skipped = 0.0
    for a, b in zip(rows, rows[1:]):
        if b[0] <= start:
            continue
        if not _usable(a, b):
            skipped += 1
            continue
        covered += b[0] - max(a[0], start)
        c0, c1 = a[1]["c"], b[1]["c"]
        for coin in set(c0) | set(c1):
            ln0, lu0, sn0, su0, p0 = (c0.get(coin) or [0, 0, 0, 0, None])[:5]
            ln1, lu1, sn1, su1, p1 = (c1.get(coin) or [0, 0, 0, 0, None])[:5]
            d = coins.setdefault(coin, {"d_long_n": 0, "d_short_n": 0, "d_long_usd": 0.0, "d_short_usd": 0.0})
            d["d_long_n"] += ln1 - ln0
            d["d_short_n"] += sn1 - sn0
            d["d_long_usd"] += _usd_flow(lu0, lu1, p0, p1)
            d["d_short_usd"] += _usd_flow(su0, su1, p0, p1)
    latest = rows[-1][1]["c"] if rows else {}
    out_coins = []
    for coin, d in coins.items():
        if not (d["d_long_n"] or d["d_short_n"]) and abs(d["d_long_usd"]) + abs(d["d_short_usd"]) < MIN_USD_FLOW:
            continue
        now_row = latest.get(coin) or [0, 0, 0, 0, None]
        out_coins.append({
            "coin": coin, **{k: (round(v) if k.endswith("usd") else v) for k, v in d.items()},
            "shift": d["d_long_n"] - d["d_short_n"],  # > 0: the crowd moved toward long (longs opened or shorts closed)
            "net_usd": round(d["d_long_usd"] - d["d_short_usd"]),
            "long_n": now_row[0], "short_n": now_row[2],
        })
    out_coins.sort(key=lambda c: (-abs(c["shift"]), -abs(c["net_usd"])))
    tot = {k: (round(sum(d[k] for d in coins.values())) if k.endswith("usd") else sum(d[k] for d in coins.values()))
           for k in ("d_long_n", "d_short_n", "d_long_usd", "d_short_usd")}
    long_n = sum(v[0] for v in latest.values())
    short_n = sum(v[2] for v in latest.values())
    return {
        "hours": hours,
        "covered_h": round(covered / 3600, 2),
        "skipped_intervals": int(skipped),
        "totals": {**tot, "long_n": long_n, "short_n": short_n,
                   "long_usd": round(sum(v[1] for v in latest.values())),
                   "short_usd": round(sum(v[3] for v in latest.values())),
                   "long_share": round(100 * long_n / (long_n + short_n), 1) if long_n + short_n else None},
        "coins": out_coins,
    }


def series(rows: list[tuple[float, dict]], hours: float, now: float) -> list[dict]:
    """Share of positions that are long, per stored row, over the last `hours` (for the chart)."""
    out = []
    for ts, r in rows:
        if ts < now - hours * 3600:
            continue
        ln = sum(v[0] for v in r["c"].values())
        sn = sum(v[2] for v in r["c"].values())
        if ln + sn:
            out.append({"ts": ts, "long_share": round(100 * ln / (ln + sn), 2), "long_n": ln, "short_n": sn})
    return out


def grid(rows: list[tuple[float, dict]], now: float, top: int = 10) -> dict[str, Any]:
    """The pulse map: the coins with the most movement and their flow in each window (1h, 6h, 24h)."""
    wins = {h: window(rows, h, now) for h in WINDOWS}
    by = {h: {c["coin"]: c for c in w["coins"]} for h, w in wins.items()}
    # rank by recent movement first: what moved in the last 6h, then 24h
    score: dict[str, float] = {}
    for h, weight in ((1, 3.0), (6, 2.0), (24, 1.0)):
        for coin, c in by[h].items():
            score[coin] = score.get(coin, 0) + weight * (abs(c["d_long_n"]) + abs(c["d_short_n"]))
    coins = sorted(score, key=lambda c: -score[c])[:top]
    return {"windows": wins,
            "coins": [{"coin": c, **{f"{h}h": by[h].get(c) for h in WINDOWS}} for c in coins]}


def summary(wins: dict[int, dict]) -> str:
    """Two short lines for a caption: the overall lean now and how the crowd moved in 1h and 6h."""
    w = wins.get(1) or next(iter(wins.values()))
    t = w["totals"]
    lines = [f"Smart money: {t['long_n']} long · {t['short_n']} short ({t['long_share']}% long)"
             if t.get("long_share") is not None else "Smart money: no positions yet"]
    for h in (1, 6):
        if h not in wins or not wins[h]["covered_h"]:
            continue
        tt = wins[h]["totals"]
        movers = [c for c in wins[h]["coins"] if c["shift"]][:3]
        mv = ", ".join(f"{c['coin']} {'long' if c['shift'] > 0 else 'short'}" for c in movers)
        lines.append(f"{h}h: longs {tt['d_long_n']:+d}, shorts {tt['d_short_n']:+d}" + (f" · moving: {mv}" if mv else ""))
    return "\n".join(lines)
