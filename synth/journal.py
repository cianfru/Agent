"""The journal's record: how each kind of logged pick did, over every pick logged, not only the latest page.

Each kind answers its own study at its own horizon: Outrider's views (S5, 3 days), tracked traders' new positions (S3,
14 and 30 days), profitable traders opening together (S1), smart-money alerts and desk posts. Returns are stored
already turned to the pick's side, so a pick is right when its return is above zero. Longs and shorts are kept apart:
when the market falls, shorts look right and longs wrong whatever the picking, and the split shows that.
"""

from __future__ import annotations

import statistics
import time

KINDS = [  # origin, label, study, horizons, the horizon its verdict uses
    ("read", "Outrider's views", "S5", ("1d", "3d", "7d"), "3d"),
    ("position", "Tracked traders' new positions", "S3", ("1d", "3d", "7d", "14d", "30d"), "30d"),
    ("group", "Profitable traders opening together", "S1", ("1d", "3d", "7d"), "7d"),
    ("alert", "Smart-money alerts", None, ("1d", "3d", "7d"), "3d"),
    ("post", "Desk posts", None, ("1d", "3d", "7d"), "7d"),
]


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "right": 0, "right_pct": None, "median_pct": None}
    right = sum(v > 0 for v in vals)
    return {"n": len(vals), "right": right, "right_pct": round(100 * right / len(vals)),
            "median_pct": round(100 * statistics.median(vals), 2)}


def record(store) -> list[dict]:
    picks = store.picks(limit=1_000_000)
    out = []
    for origin, label, study, horizons, main in KINDS:
        sel = [p for p in picks if p["origin"] == origin]
        if not sel:
            continue
        row = {"origin": origin, "label": label, "study": study, "main": main, "logged": len(sel),
               "since": time.strftime("%d %b", time.gmtime(min(p["ts"] for p in sel))),
               "longs": sum(p["direction"] == "long" for p in sel), "shorts": sum(p["direction"] == "short" for p in sel),
               "h": {}}
        for h in horizons:
            got = [p for p in sel if p.get(f"ret_{h}") is not None]
            row["h"][h] = {**_stats([p[f"ret_{h}"] for p in got]),
                           "long": _stats([p[f"ret_{h}"] for p in got if p["direction"] == "long"]),
                           "short": _stats([p[f"ret_{h}"] for p in got if p["direction"] == "short"])}
        # the study's horizon once anything has reached it, else the longest that has
        row["shown"] = main if row["h"][main]["n"] else next(
            (h for h in reversed(horizons) if row["h"][h]["n"]), main)
        out.append(row)
    return out


def _h(r: dict, h: str) -> str:
    s = r["h"][h]
    if not s["n"]:
        return f"{h}: none scored yet"
    return f"{h}: {s['right']} of {s['n']} right ({s['right_pct']}%), median {s['median_pct']:+.1f}%"


def text(rows: list[dict]) -> str:
    """For Telegram: one block per kind, the horizon its study uses first, longs and shorts apart."""
    if not rows:
        return "Nothing logged yet."
    lines = []
    for r in rows:
        lines.append(f"{r['label']}: {r['logged']} since {r['since']} ({r['longs']} long, {r['shorts']} short)")
        lines.append("  " + _h(r, r["shown"]))
        s = r["h"][r["shown"]]
        if s["n"]:
            lo, sh = s["long"], s["short"]
            lines.append(f"  longs {lo['right']}/{lo['n']} right, shorts {sh['right']}/{sh['n']} right")
    return "\n".join(lines)
