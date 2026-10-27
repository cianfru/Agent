"""The journal's record: how each kind of logged pick did, over every pick logged, not only the latest page.

Each kind answers its own study at its own horizon: Outrider's views (S5, 3 days), tracked traders' conviction bets
(S3, 30 days) and their other new positions, profitable traders opening together (S1), smart-money alerts and desk
posts. Returns are stored already turned to the pick's side, so a pick is right when its return is above zero.

Two guards against the market's own direction (when everything falls, shorts look right whatever the picking):
- longs and shorts are kept apart;
- each pick is also scored against the market (synth/market.py): it beats the market when its move its way is better
  than the basket's move its way over the same days. Reported beside the raw result; the studies' bars are unchanged.

`reads` says how Outrider's reads went (run, sent, held back, failed, with or without views), so a thin record can be
told apart from a broken one.
"""

from __future__ import annotations

import statistics
import time

H5 = ("1d", "3d", "7d", "14d", "30d")
H3 = ("1d", "3d", "7d")
KINDS = [  # key, label, study, horizons, the horizon its verdict uses, which picks
    ("read", "Outrider's views", "S5", H3, "3d", lambda p: p["origin"] == "read"),
    ("conviction", "Tracked traders' conviction bets (2x usual size)", "S3", H5, "30d",
     lambda p: p["origin"] == "position" and (p.get("conviction") or 0) >= 2),
    ("position", "Tracked traders' other new positions", None, H5, "30d",
     lambda p: p["origin"] == "position" and (p.get("conviction") or 0) < 2),
    ("group", "Profitable traders opening together", "S1", H3, "7d", lambda p: p["origin"] == "group"),
    ("alert", "Smart-money alerts", None, H3, "3d", lambda p: p["origin"] == "alert"),
    ("post", "Desk posts", None, H3, "7d", lambda p: p["origin"] == "post"),
]


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "right": 0, "right_pct": None, "median_pct": None}
    right = sum(v > 0 for v in vals)
    return {"n": len(vals), "right": right, "right_pct": round(100 * right / len(vals)),
            "median_pct": round(100 * statistics.median(vals), 2)}


def excess(p: dict, h: str) -> float | None:
    """The pick's move its way less the market's move its way over the same days."""
    ret, mkt = p.get(f"ret_{h}"), p.get(f"mkt_{h}")
    if ret is None or mkt is None:
        return None
    return ret - (mkt if p["direction"] == "long" else -mkt)


def block(picks: list[dict], h: str) -> dict:
    got = [p for p in picks if p.get(f"ret_{h}") is not None]
    vs = [x for p in got if (x := excess(p, h)) is not None]
    return {**_stats([p[f"ret_{h}"] for p in got]), "vs_market": _stats(vs)}


def record(store) -> list[dict]:
    picks = store.picks(limit=1_000_000)
    out = []
    for key, label, study, horizons, main, pick in KINDS:
        sel = [p for p in picks if pick(p)]
        if not sel:
            continue
        row = {"origin": key, "label": label, "study": study, "main": main, "logged": len(sel),
               "since": time.strftime("%d %b", time.gmtime(min(p["ts"] for p in sel))),
               "longs": sum(p["direction"] == "long" for p in sel), "shorts": sum(p["direction"] == "short" for p in sel),
               "h": {}}
        for h in horizons:
            row["h"][h] = {**block(sel, h), "long": block([p for p in sel if p["direction"] == "long"], h),
                           "short": block([p for p in sel if p["direction"] == "short"], h)}
        # the study's horizon once anything has reached it, else the longest that has
        row["shown"] = main if row["h"][main]["n"] else next(
            (h for h in reversed(horizons) if row["h"][h]["n"]), main)
        out.append(row)
    return out


def reads(store, now: float | None = None, days: float = 7) -> dict:
    """Outrider's reads over the last `days`: how many ran, went out, were held back or failed, and their views."""
    now = now or time.time()
    bs = [b["body"] for b in store.briefs_since("read", now - days * 86400)]
    failed = [b for b in bs if b.get("error")]
    sent = [b for b in bs if b.get("sent")]
    reasons: dict[str, int] = {}
    for b in bs:
        reasons[b.get("reason") or "?"] = reasons.get(b.get("reason") or "?", 0) + 1
    return {"days": days, "run": len(bs), "sent": len(sent), "held_back": len(bs) - len(sent) - len(failed),
            "failed": len(failed), "by_reason": reasons,
            "sent_without_view": sum(1 for b in sent if not b.get("views")),
            "views_given": sum(len(b.get("views") or []) for b in sent),
            "views_logged": sum(len(b.get("logged") or []) for b in sent),
            "last_error": failed[-1]["error"] if failed else None}


def reads_text(r: dict) -> str:
    if not r["run"]:
        return f"Outrider's reads, {r['days']:g} days: none ran."
    why = ", ".join(f"{n} {k}" for k, n in sorted(r["by_reason"].items(), key=lambda kv: -kv[1]))
    line = (f"Outrider's reads, {r['days']:g} days: {r['run']} ran ({why}) · {r['sent']} sent · {r['held_back']} held "
            f"back · {r['failed']} failed · {r['sent_without_view']} sent with no view · {r['views_logged']} views logged")
    if r["views_given"] > r["views_logged"]:
        line += f" ({r['views_given'] - r['views_logged']} repeats or without a price)"
    if r["last_error"]:
        line += f". Last failure: {r['last_error'][:160]}"
    return line


def _h(r: dict, h: str) -> str:
    s = r["h"][h]
    if not s["n"]:
        return f"{h}: none scored yet"
    line = f"{h}: {s['right']} of {s['n']} right ({s['right_pct']}%), median {s['median_pct']:+.1f}%"
    v = s["vs_market"]
    if v["n"]:
        line += f"; beat the market {v['right']} of {v['n']} ({v['right_pct']}%), median {v['median_pct']:+.1f}%"
    return line


def text(rows: list[dict], rd: dict | None = None) -> str:
    """For Telegram: one block per kind at the horizon its study uses, longs and shorts apart, then the reads."""
    lines = []
    for r in rows:
        lines.append(f"{r['label']}: {r['logged']} since {r['since']} ({r['longs']} long, {r['shorts']} short)")
        lines.append("  " + _h(r, r["shown"]))
        s = r["h"][r["shown"]]
        if s["n"]:
            lo, sh = s["long"], s["short"]
            lines.append(f"  longs {lo['right']}/{lo['n']} right, shorts {sh['right']}/{sh['n']} right")
    if not lines:
        lines.append("Nothing logged yet.")
    if rd:
        lines += ["", reads_text(rd)]
    return "\n".join(lines)
