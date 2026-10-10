"""The desk's two views, assembled from what the agent already stores (no new reads of anyone's API).

- `home(agent)`: what is happening now: headline readings with their short history, the crowd against price,
  where money is moving, the three lenses per coin (Reflex's profitable traders, tracked traders, big money) and
  the latest alerts.
- `research(agent)`: the studies as charts (`synth/data/research.json`) and the market cycle from Reflex's
  market history (fetched once a day, `compact_history`).
Context, not signals: every finding carries its verdict.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import time
from pathlib import Path
from typing import Any

from . import flow

RESEARCH = Path(__file__).parent / "data" / "research.json"
DAY_S = 86400
LEAN_RATIO = 1.5          # a lens leans one way when its dollars on that side are 1.5x the other side's


def _day(d: int) -> str:
    return (dt.date(1970, 1, 1) + dt.timedelta(days=int(d))).isoformat()


def compact_history(body: dict, keep_days: int = 400) -> dict:
    """Reflex's /api/market-history, kept small: the uptrend share per day (Uptrend + Overheated, Reflex's
    risk-on line), today's reading and the current run against past runs."""
    cols = body.get("columns") or ["day", "n", "uptrend", "overheated", "downtrend", "basing", "median_z", "btc"]
    ix = {c: i for i, c in enumerate(cols)}
    days = body.get("days") or []
    rows = [[_day(d[ix["day"]]), round(100 * (d[ix["uptrend"]] + d[ix["overheated"]]), 1),
             d[ix["btc"]] if "btc" in ix else None] for d in days[-keep_days:]]
    cyc = body.get("cycle") or {}
    today = body.get("today") or {}
    return {
        "rows": rows,
        "today": {"day": _day(today["day"]) if today.get("day") else None, "percentile": today.get("percentile"),
                  "band": today.get("band"), "coins": today.get("n")},
        "cycle": {"type": cyc.get("type"), "start": _day(cyc["start"]) if cyc.get("start") else None,
                  "age_days": cyc.get("day"), "threshold": cyc.get("threshold"), "past_runs": cyc.get("past"),
                  "median_days": cyc.get("median"), "reached_age": cyc.get("reached"),
                  "lasted_30_more": cyc.get("lasted_more"),
                  "runs": [[_day(r[0]), r[1], r[2]] for r in (cyc.get("runs") or [])]} if cyc else None,
    }


def lean(long_usd: float, short_usd: float) -> str | None:
    if not long_usd and not short_usd:
        return None
    if long_usd >= LEAN_RATIO * short_usd:
        return "long"
    if short_usd >= LEAN_RATIO * long_usd:
        return "short"
    return "split"


def agreement(leans: list[str | None]) -> str:
    seen = [x for x in leans if x]
    if len(seen) < 2:
        return "one lens"
    if all(x == "long" for x in seen):
        return "all long"
    if all(x == "short" for x in seen):
        return "all short"
    if "long" in seen and "short" in seen:
        return "disagree"
    return "mixed"


def _crowd_points(rows: list[tuple[float, dict]], every: int = 1) -> list[list]:
    out = []
    for i, (ts, r) in enumerate(rows):
        if i % every and i != len(rows) - 1:
            continue
        c = r.get("c") or {}
        ln = sum(v[0] for v in c.values())
        sn = sum(v[2] for v in c.values())
        btc = (c.get("BTC") or [None] * 5)[4]
        if ln + sn:
            out.append([round(ts), round(100 * ln / (ln + sn), 2), btc])
    return out


def home(agent, now: float | None = None) -> dict:
    now = now or time.time()
    store, pt = agent.store, agent.positions
    rows = store.series("reflex", now - 7 * DAY_S - 1800)
    pts = _crowd_points(rows, every=max(1, len(rows) // 400))
    day_ago = next((p for p in pts if p[0] >= now - DAY_S), None)
    crowd_now = pts[-1] if pts else None
    snap = store.get_snapshot("reflex") or {}
    positions = snap.get("positions") or {}

    board = pt.board(now)
    bm = pt.big_money()
    bm_long = sum(v["long_usd"] for v in bm.values())
    bm_short = sum(v["short_usd"] for v in bm.values())
    tr_long = sum(c["long_usd"] for c in board["coins"])
    tr_short = sum(c["short_usd"] for c in board["coins"])
    hist = store.get_snapshot("reflex_history") or {}

    # the three lenses per coin
    coins: dict[str, dict] = {}
    for coin, v in positions.items():
        coins.setdefault(coin, {})["crowd"] = {"long_n": v[0], "long_usd": v[1], "short_n": v[2], "short_usd": v[3]}
    for c in board["coins"]:
        coins.setdefault(c["coin"], {})["tracked"] = {k: c[k] for k in ("long_n", "short_n", "long_usd", "short_usd")}
    for coin, v in bm.items():
        coins.setdefault(coin, {})["big_money"] = v
    lens_rows = []
    for coin, ls in coins.items():
        size = sum((x["long_usd"] + x["short_usd"]) for x in ls.values())
        leans = {k: lean(x["long_usd"], x["short_usd"]) for k, x in ls.items()}
        lens_rows.append({"coin": coin, **ls, "leans": leans, "agree": agreement(list(leans.values())),
                          "size": round(size), "price": (positions.get(coin) or [None] * 5)[4]})
    lens_rows.sort(key=lambda r: -r["size"])

    win24 = flow.window(rows, 24, now) if rows else {"coins": [], "totals": {}}
    alerts = []
    for kind, label in (("pt_build", "build-up"), ("pt_alert", "conviction"), ("alert_sent", "smart money")):
        for b in store.briefs_since(kind, now - 3 * DAY_S):
            body = b["body"]
            text = body.get("text")
            if kind == "alert_sent":
                ev = store.event(body.get("event_id"))
                text = ev["title"] if ev else None
            elif kind == "pt_alert":
                text = f"{body.get('coin')} {body.get('side')} by {body.get('address', '')[:8]}"
            if text:
                alerts.append({"ts": b["ts"], "kind": label, "text": text})
    for g in store.groups(limit=10):
        if now - g["ts"] <= 3 * DAY_S:
            alerts.append({"ts": g["ts"], "kind": "convergence", "text": f"#{g['id']} {g['coin']} {g['side']} · "
                           f"{(g['meta'] or {}).get('n') or ''} profitable traders"})
    alerts.sort(key=lambda a: -a["ts"])

    return {
        "ts": now,
        "kpi": {
            "crowd": {"share_long": crowd_now[1] if crowd_now else None,
                      "change_24h": round(crowd_now[1] - day_ago[1], 1) if crowd_now and day_ago else None,
                      "spark": [p[1] for p in pts[-96:]]},
            "big_money": {"share_long": round(100 * bm_long / (bm_long + bm_short), 1) if bm_long + bm_short else None,
                          "long_usd": round(bm_long), "short_usd": round(bm_short),
                          "ts": (store.latest_brief("pt_bigmoney") or {}).get("ts")},
            "tracked": {"share_long": round(100 * tr_long / (tr_long + tr_short), 1) if tr_long + tr_short else None,
                        "traders": len(board["traders"]),
                        "riding": sum(1 for t in board["traders"] if t["tier"] == "riding")},
            "cycle": {**(hist.get("cycle") or {}), "uptrend": (hist.get("rows") or [[None, None]])[-1][1],
                      "percentile": (hist.get("today") or {}).get("percentile")} if hist else None,
            "alerts_24h": sum(1 for a in alerts if a["ts"] >= now - DAY_S),
            "btc": {"price": crowd_now[2] if crowd_now else None,
                    "change_24h": round(100 * (crowd_now[2] / day_ago[2] - 1), 2)
                    if crowd_now and day_ago and day_ago[2] and crowd_now[2] else None},
        },
        "crowd_series": pts,
        "moving": {"tracked_24h": board["adding_24h"], "tracked_7d": board["adding_7d"],
                   "crowd_24h": win24["coins"][:20], "crowd_totals_24h": win24.get("totals")},
        "lenses": lens_rows[:24],
        "alerts": alerts[:20],
    }


def research(agent) -> dict:
    try:
        out = json.loads(RESEARCH.read_text())
    except (OSError, ValueError):
        out = {"studies": []}
    out["market_cycle"] = agent.store.get_snapshot("reflex_history")
    picks = agent.positions.forward()
    out["forward"] = picks
    return out
