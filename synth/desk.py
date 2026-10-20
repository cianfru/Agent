"""The desk's two views, assembled from what the agent already stores (no new reads of anyone's API).

- `home(agent)`: what is happening now: headline readings with their short history, the crowd against price,
  the direction of BTC and ETH (the three lenses as one reading each, like Cowboy's wallets layer), where money is
  moving and the three lenses per coin for the other coins (Reflex's profitable traders, tracked traders, big
  money), and the latest alerts. BTC and ETH read as direction; the other coins are where the money can run, so they
  come first everywhere else (owner, 10 October 2026).
- `research(agent)`: the studies as charts (`synth/data/research.json`) and the market cycle from Reflex's
  market history (fetched once a day, `compact_history`).
Every lens reads by headcount, one vote per trader (big money: per owner), with dollars beside: one giant account
or a hedge cannot decide a reading (owner, 10 October 2026). Context, not signals: every finding carries its verdict.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import time
from pathlib import Path
from typing import Any

from . import flow
from .position_traders import MAJORS

RESEARCH = Path(__file__).parent / "data" / "research.json"
DAY_S = 86400
LEAN_RATIO = 1.5          # a lens leans one way when its heads on that side are 1.5x the other side's
MIN_HEADS = 3             # fewer than 3 traders or owners in a coin: no lean


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


def lean(long_n: int, short_n: int) -> str | None:
    if long_n + short_n < MIN_HEADS:
        return None
    if long_n >= LEAN_RATIO * short_n:
        return "long"
    if short_n >= LEAN_RATIO * long_n:
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


def _share(long_n: float, short_n: float) -> float | None:
    return round(100 * long_n / (long_n + short_n), 1) if long_n + short_n else None


def direction(coin: str, rows: list[tuple[float, dict]], lenses: dict, adding_24h: list[dict], now: float,
              points: int = 120) -> dict:
    """BTC or ETH as one directional reading: each lens's share of heads long and its lean, whether they agree,
    the crowd's share over the last 7 days against price, and what tracked traders added in 24 hours."""
    series = []
    for ts, r in rows:
        v = (r.get("c") or {}).get(coin)
        if v and v[0] + v[2]:
            series.append([round(ts), _share(v[0], v[2]), v[4] if len(v) > 4 else None])
    if len(series) > points:
        step = len(series) / points
        series = [series[int(i * step)] for i in range(points)] + [series[-1]]
    day_ago = next((p for p in series if p[0] >= now - DAY_S), None)
    last = series[-1] if series else None
    ls = {k: {**x, "share_long": _share(x["long_n"], x["short_n"]), "lean": lean(x["long_n"], x["short_n"])}
          for k, x in lenses.items()}
    added = {r["side"]: r["net_usd"] for r in adding_24h if r["coin"] == coin}
    return {"coin": coin, "price": last[2] if last else None,
            "change_24h": round(100 * (last[2] / day_ago[2] - 1), 2) if last and day_ago and day_ago[2] and last[2]
            else None,
            "lenses": ls, "agree": agreement([x["lean"] for x in ls.values()]),
            "crowd_change_24h": round(last[1] - day_ago[1], 1) if last and day_ago else None,
            "series": series, "tracked_added_24h": {"long": added.get("long", 0), "short": added.get("short", 0)}}


def home(agent, now: float | None = None, board: dict | None = None) -> dict:
    now = now or time.time()
    store, pt = agent.store, agent.positions
    rows = store.series("reflex", now - 7 * DAY_S - 1800)
    pts = _crowd_points(rows, every=max(1, len(rows) // 400))
    day_ago = next((p for p in pts if p[0] >= now - DAY_S), None)
    crowd_now = pts[-1] if pts else None
    snap = store.get_snapshot("reflex") or {}
    positions = snap.get("positions") or {}

    board = board or pt.board(now)
    bm = pt.big_money()
    bm_long = sum(v["long_usd"] for v in bm.values())
    bm_short = sum(v["short_usd"] for v in bm.values())
    bm_ln, bm_sn = sum(v["long_n"] for v in bm.values()), sum(v["short_n"] for v in bm.values())
    tr_ln, tr_sn = sum(c["long_n"] for c in board["coins"]), sum(c["short_n"] for c in board["coins"])
    hist = store.get_snapshot("reflex_history") or {}

    # the three lenses per coin
    coins: dict[str, dict] = {}
    for coin, v in positions.items():
        coins.setdefault(coin, {})["crowd"] = {"long_n": v[0], "long_usd": v[1], "short_n": v[2], "short_usd": v[3]}
    for c in board["coins"]:
        coins.setdefault(c["coin"], {})["tracked"] = {k: c[k] for k in ("long_n", "short_n", "long_usd", "short_usd")}
    for coin, v in bm.items():
        coins.setdefault(coin, {})["big_money"] = v
    adding_24h = pt.adding(24, now)
    directions = [direction(m, rows, coins.get(m, {}), adding_24h, now) for m in MAJORS]
    lens_rows = []
    for coin, ls in coins.items():
        if coin in MAJORS:
            continue
        size = sum((x["long_usd"] + x["short_usd"]) for x in ls.values())
        leans = {k: lean(x["long_n"], x["short_n"]) for k, x in ls.items()}
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

    read = next((b for b in reversed(store.briefs_since("read", now - 2 * DAY_S)) if b["body"].get("sent")), None)
    return {
        "ts": now,
        "read": {"ts": read["ts"], "text": read["body"].get("text")} if read else None,
        "kpi": {
            "crowd": {"share_long": crowd_now[1] if crowd_now else None,
                      "change_24h": round(crowd_now[1] - day_ago[1], 1) if crowd_now and day_ago else None,
                      "spark": [p[1] for p in pts[-96:]]},
            "big_money": {"share_long": _share(bm_ln, bm_sn), "long_n": bm_ln, "short_n": bm_sn,
                          "long_usd": round(bm_long), "short_usd": round(bm_short),
                          "owners": (store.latest_brief("pt_bigmoney") or {}).get("owners"),
                          "ts": (store.latest_brief("pt_bigmoney") or {}).get("ts")},
            "tracked": {"share_long": _share(tr_ln, tr_sn),
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
        "direction": directions,
        "moving": {"tracked_24h": [r for r in adding_24h if r["coin"] not in MAJORS][:15],
                   "tracked_7d": [r for r in pt.adding(168, now) if r["coin"] not in MAJORS][:15],
                   "crowd_24h": [c for c in win24["coins"] if c["coin"] not in MAJORS][:20],
                   "crowd_totals_24h": win24.get("totals")},
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
