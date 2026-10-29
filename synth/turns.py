"""The market's turns, as Reflex's market history Chart view reads them (owner, 29 October 2026: "make sure the bot
gets this context as well"), computed from the /api/market-history response the agent already fetches once a day.

The same rules as Reflex's page (frontend/src/utils/marketHeat.js) and its studies (docs/reviews/late-uptrends-study.md,
turning-points-study.md in cianfru/RCCE_Scanner):
- a broad uptrend: 55% or more of coins in uptrend, until the 5th day in a row under 55% (shorter dips stay inside);
  its end is the first of those 5 days, which then start the downturn;
- rolled over: the share 20 points under the run's high so far; turned up: 20 points over the downturn's low so far;
- median z turned down: the typical coin's z-score 0.75 under its high in the run; turned up: 0.75 over its low in the
  downturn;
- greed: Fear & Greed at 70 or more in the last 30 days; fear: 25 or less; traders long: the crowd 86% or more long;
  traders short: 30% or less.
Context, not a signal: what the studies found rests on 3 to 10 cases (the evidence guide in synth/chat.py).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

ON = 0.55
OFF_DAYS = 5
ROLL = 0.20
Z_TURN = 0.75
GREED, FEAR, LOOKBACK = 70, 25, 30
CROWD_LONG, CROWD_SHORT = 86.0, 30.0
_EPOCH = dt.date(1970, 1, 1)


def _iso(d: int | None) -> str | None:
    return None if d is None else (_EPOCH + dt.timedelta(days=int(d))).isoformat()


def _pct(v: float | None) -> float | None:
    return None if v is None else round(100 * v, 1)


def rows_of(body: dict) -> list[dict]:
    """Reflex's market history days -> [{day, share (Uptrend + Overheated, 0-1), z (median z)}], oldest first."""
    cols = body.get("columns") or ["day", "n", "uptrend", "overheated", "downtrend", "basing", "median_z", "btc"]
    ix = {c: i for i, c in enumerate(cols)}
    out = []
    for d in body.get("days") or []:
        up = d[ix["uptrend"]]
        hot = (d[ix["overheated"]] or 0) if "overheated" in ix else 0
        share = None if up is None else up + hot
        out.append({"day": int(d[ix["day"]]), "share": share, "z": d[ix["median_z"]] if "median_z" in ix else None})
    return out


def runs(rows: list[dict]) -> dict[str, Any]:
    """Broad uptrends and the downturns between them, as Reflex's page draws them: {days (in a run), runs, down_days,
    downs}."""
    days: dict[int, dict] = {}
    out: list[dict] = []
    cur, below = None, 0
    for i, r in enumerate(rows):
        s, z = r["share"], r["z"]
        if cur is None:
            if s is None or s < ON:
                continue
            cur = {"start": r["day"], "end": None, "first": i == 0, "high": s, "high_day": r["day"], "roll": None,
                   "roll_from": None, "z_high": None, "z_turn": None}
            below = 0
        else:
            below = below + 1 if s is None or s < ON else 0
            if below >= OFF_DAYS:
                cur["end"] = rows[i - OFF_DAYS + 1]["day"]
                for k in range(i - OFF_DAYS + 1, i):          # the last dip belongs to the downturn once the run ends
                    days.pop(rows[k]["day"], None)
                out.append(cur)
                cur = None
                continue
        if s is not None and s > cur["high"]:
            cur["high"], cur["high_day"] = s, r["day"]
        if cur["roll"] is None and s is not None and cur["high"] - s >= ROLL - 1e-9:
            cur["roll"], cur["roll_from"] = r["day"], (cur["high"], cur["high_day"])
        if z is not None:
            cur["z_high"] = z if cur["z_high"] is None else max(cur["z_high"], z)
            if cur["z_turn"] is None and cur["z_high"] - z >= Z_TURN - 1e-9:
                cur["z_turn"] = r["day"]
        days[r["day"]] = {"start": cur["start"], "age": r["day"] - cur["start"], "rolled": cur["roll"] is not None,
                          "z_turned": cur["z_turn"] is not None}
    if cur is not None:
        out.append(cur)
    down_days: dict[int, dict] = {}
    downs = []
    for run in out:
        if run["end"] is None:
            continue
        nxt = next((x["start"] for x in out if x["start"] > run["end"]), None)
        dn = {"start": run["end"], "end": nxt, "low": None, "low_day": None, "turn_up": None, "turn_from": None,
              "z_low": None, "z_turn_up": None}
        for r in rows:
            if r["day"] < dn["start"] or (nxt is not None and r["day"] >= nxt):
                continue
            if r["share"] is not None:
                if dn["low"] is None or r["share"] < dn["low"]:
                    dn["low"], dn["low_day"] = r["share"], r["day"]
                if dn["turn_up"] is None and r["share"] - dn["low"] >= ROLL - 1e-9:
                    dn["turn_up"], dn["turn_from"] = r["day"], (dn["low"], dn["low_day"])
            if r["z"] is not None:
                dn["z_low"] = r["z"] if dn["z_low"] is None else min(dn["z_low"], r["z"])
                if dn["z_turn_up"] is None and r["z"] - dn["z_low"] >= Z_TURN - 1e-9:
                    dn["z_turn_up"] = r["day"]
            if r["day"] not in days:
                down_days[r["day"]] = {"start": dn["start"], "age": r["day"] - dn["start"],
                                       "turned": dn["turn_up"] is not None, "z_up": dn["z_turn_up"] is not None}
        downs.append(dn)
    return {"days": days, "runs": out, "down_days": down_days, "downs": downs}


def turns(body: dict) -> dict | None:
    """Where the market stands in its cycle on the latest close: a broad uptrend or a downturn, its day against past
    ones, its high or low, the marks, the median z, and the last turns. None without history."""
    rows = rows_of(body)
    if not rows or rows[-1]["share"] is None:
        return None
    br = runs(rows)
    last = rows[-1]
    fg = {int(d): v for d, v in body.get("fear_greed") or []}
    fgs = [fg[d] for d in range(last["day"] - LOOKBACK + 1, last["day"] + 1) if d in fg]
    z14 = next((r["z"] for r in rows if r["day"] == last["day"] - 14), None)
    base = {"day": _iso(last["day"]), "share_in_uptrend": _pct(last["share"]),
            "median_z": last["z"], "median_z_14d_change": None if last["z"] is None or z14 is None else round(last["z"] - z14, 2)}
    d = br["days"].get(last["day"])
    if d:
        run = br["runs"][-1]
        past = [x for x in br["runs"] if x["end"] is not None and not x["first"]]
        high = max(fgs) if fgs else None
        return {**base, "state": "broad uptrend", "run_day": d["age"], "started": _iso(run["start"]),
                "longer_than": f"{sum(x['end'] - x['start'] < d['age'] for x in past)} of {len(past)} past broad uptrends",
                "high": {"share": _pct(run["high"]), "on": _iso(run["high_day"])},
                "fear_greed_30d_high": high,
                "marks": {"greed": high is not None and high >= GREED, "median_z_turned_down": d["z_turned"],
                          "rolled_over": d["rolled"]},
                "median_z_turned_down_on": _iso(run["z_turn"]), "rolled_over_on": _iso(run["roll"])}
    dn = br["down_days"].get(last["day"])
    run = next((x for x in reversed(br["runs"]) if x["end"] is not None), None)
    prev = None if not run else {"started": _iso(run["start"]), "ended": _iso(run["end"]), "days": run["end"] - run["start"],
                                 "high": _pct(run["high"]), "high_on": _iso(run["high_day"]),
                                 "median_z_turned_down_on": _iso(run["z_turn"]), "rolled_over_on": _iso(run["roll"])}
    if not dn:
        return {**base, "state": "no broad uptrend yet", "last_broad_uptrend": prev}
    down = br["downs"][-1]
    past = [x for x in br["downs"] if x["end"] is not None]
    low = min(fgs) if fgs else None
    return {**base, "state": "broad downturn", "downturn_day": dn["age"], "started": _iso(down["start"]),
            "longer_than": f"{sum(x['end'] - x['start'] < dn['age'] for x in past)} of {len(past)} past downturns",
            "low": {"share": _pct(down["low"]), "on": _iso(down["low_day"])},
            "fear_greed_30d_low": low,
            "marks": {"fear": low is not None and low <= FEAR, "median_z_turned_up": dn["z_up"], "turned_up": dn["turned"]},
            "median_z_turned_up_on": _iso(down["z_turn_up"]), "turned_up_on": _iso(down["turn_up"]),
            "last_broad_uptrend": prev}


def with_crowd(t: dict | None, crowd_long_pct: float | None) -> dict | None:
    """Add the crowd's mark (profitable traders' share of positions long, as the agent reads it now)."""
    if not t or "marks" not in t or crowd_long_pct is None:
        return t
    marks = dict(t["marks"])
    if t["state"] == "broad uptrend":
        marks["traders_long"] = crowd_long_pct >= CROWD_LONG
    else:
        marks["traders_short"] = crowd_long_pct <= CROWD_SHORT
    return {**t, "marks": marks, "crowd_long_pct": crowd_long_pct}
