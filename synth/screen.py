"""Every liquid coin against its levels, in one table: the read's setup candidates and the chat's screen.

Built in code from what the agent already keeps: each coin's levels (synth/levels.py: the bull market support band, the
200-day average, the last leg and the swing levels, read once a day for the 100 most traded perps), its price, open
interest and funding now, the engine's daily signal, and what tracked traders hold, added in 24 hours and bet with
conviction in 7 days. So a question like "which coins sit closest to their band" is one look at one table, not one
coin at a time, and a read chooses its setups from the whole market, not only from where traders were busy.

The z-score (owner, 4 November 2026: "for shorting, one of the most important aspects"): the engine's daily z-score,
how far the price sits above its long-run trend in its own volatility, read for every coin once an hour. The engine's
own lines: 2.5 a blow-off, 3 it trims, 3.5 it trims hard, -1 capitulation.

The band (owner, 3 November 2026): inside it, the price is testing it, a zone to watch, not a block; up to 5% over it is
just above; 15% or more over it is extended, not an entry; under it the trend is lost until the band is reclaimed.

Each coin gets a score for a long and for a short setup, to rank what is worth a look. A ranking, not a tested signal:
the setups a read sends are what study S5 scores.
  where the price sits, up to 45  long: over its 200-day average (or no 200-day yet) and inside its band or just above
                                  it 35; else within 3% of its 200-day average from above 25; else, still over the
                                  200-day, on a swing low (within 3% over it) 20; plus 10 when it has given back 30% to
                                  70% of its last leg.
                                  short: the mirror (under the 200-day, inside the band or just under it, a bounce of
                                  30% to 70% of the last drop), or stretched: a daily z-score at the engine's blow-off
                                  line (2.5) 35, at its trim line (3) 40, plus 10 when 15%+ over the band; this route
                                  fades a run, so it needs no downtrend. A long under its band, a long extended over
                                  it, or a short 15%+ under it: 0 (owner's band rule).
  who is positioning, up to 35    a proven trader's conviction bet that side in 7 days 20, two or more tracked traders
                                  adding that side in 24 hours 10, tracked traders leaning that side 5
  the engine, up to 15            its daily setup that way 15; the other way -10; a long on a stretched z (2.5+) -15
  crowding, -5 or +5              funding over 50% a year against a long, under -50% against a short; over 30% a
                                  year (longs paying up) +5 for a short
"""

from __future__ import annotations

import time
from typing import Any

from . import desk
from .levels import NEAR_PCT, band_state

Z_BLOWOFF, Z_TRIM, Z_CAPITULATION = 2.5, 3.0, -1.0     # the engine's own lines (Reflex rcce_engine.py)

UP = {"STRONG_LONG", "LIGHT_LONG", "ACCUMULATE", "REVIVAL_SEED", "REVIVAL_SEED_CONFIRMED"}
DOWN = {"RISK_OFF", "LIGHT_SHORT", "TRIM_HARD", "NO_LONG"}
SIGNAL_WORD = {"STRONG_LONG": "strong up-setup", "LIGHT_LONG": "leaning up", "ACCUMULATE": "base forming",
               "REVIVAL_SEED": "turning", "REVIVAL_SEED_CONFIRMED": "turn confirmed", "WAIT": "no clear setup",
               "TRIM": "stretched", "TRIM_HARD": "very stretched", "RISK_OFF": "leaning down",
               "LIGHT_SHORT": "leaning down", "NO_LONG": "up-setups blocked"}
SORTS = ("score", "band", "ma200", "retrace", "oi", "funding")
BAND_STATES = ("below", "inside", "just above", "above", "extended")


def _pct(a: float | None, b: float | None) -> float | None:
    return round(100 * (a / b - 1), 1) if a and b else None


def coin_row(coin: str, lv: dict, price: float, ctx: dict | None, pos: dict) -> dict:
    """One coin's line: its levels against the price now, what is near, positioning, engine, and the two scores."""
    lo_hi = [x for x in (lv.get("sma20w"), lv.get("ema21w")) if x]
    r: dict[str, Any] = {"coin": coin, "price": float(f"{price:.5g}")}
    if len(lo_hi) == 2:
        lo, hi = min(lo_hi), max(lo_hi)
        st = band_state(price, lo, hi)
        r["band"] = {"low": lo, "high": hi, "state": st,
                     "vs_pct": 0.0 if st == "inside" else _pct(price, hi) if price > hi else _pct(price, lo)}
    if lv.get("ma200d"):
        r["ma200d"], r["vs_ma200d_pct"] = lv["ma200d"], _pct(price, lv["ma200d"])
    hi, leg = lv.get("high"), lv.get("leg_low")
    if hi and price > hi:                     # past the high read at 00:30: a new high today, nothing given back
        hi, r["new_high"] = price, True
    if hi and leg and hi > leg:
        r["from_high_pct"] = _pct(price, hi)
        r["retraced_pct"] = round(100 * (hi - price) / (hi - leg))
    low, top = lv.get("low"), lv.get("drop_high")
    if low and price < low:                   # under the low read at 00:30: a new low today, no bounce yet
        low, r["new_low"] = price, True
    if low and top and top > low and (lv.get("low_bars_ago") or 0) < (lv.get("high_bars_ago") or 0):
        r["bounced_of_drop_pct"] = round(100 * (price - low) / (top - low))
    lows = [p[0] for p in lv.get("swing_lows") or [] if p[0] < price]
    highs = [p[0] for p in lv.get("swing_highs") or [] if p[0] > price]
    if lows:
        r["swing_low"], r["swing_low_pct"] = max(lows), _pct(max(lows), price)
    if highs:
        r["swing_high"], r["swing_high_pct"] = min(highs), _pct(min(highs), price)
    if ctx:
        r.update({k: ctx[k] for k in ("oi_change_24h_pct", "funding_apr_pct") if ctx.get(k) is not None})
    r.update({k: v for k, v in pos.items() if v not in (None, {}, "")})
    r["score_long"], r["why_long"] = score(r, "long")
    r["score_short"], r["why_short"] = score(r, "short")
    return r


def score(r: dict, side: str) -> tuple[int, list[str]]:
    """The ranking described at the top: where the price sits, who is positioning, the engine, crowding."""
    long = side == "long"
    why: list[str] = []
    pts = 0
    st = (r.get("band") or {}).get("state")
    vs200 = r.get("vs_ma200d_pct")
    trend_ok = vs200 is None or (vs200 > 0 if long else vs200 < 0)
    near_band = st in ("inside", "just above") if long else (st == "inside" or (
        st == "below" and (r["band"]["vs_pct"] or 0) >= -5))
    if near_band and trend_ok:
        pts += 35
        why.append("inside its band" if st == "inside" else "just over its band" if long else "just under its band")
    elif vs200 is not None and (0 <= vs200 <= NEAR_PCT if long else -NEAR_PCT <= vs200 <= 0):
        pts += 25
        why.append("at its 200-day average")
    elif trend_ok and long and r.get("swing_low_pct") is not None and r["swing_low_pct"] >= -NEAR_PCT:
        pts += 20
        why.append("on a swing low")
    elif trend_ok and not long and r.get("swing_high_pct") is not None and r["swing_high_pct"] <= NEAR_PCT:
        pts += 20
        why.append("under a swing high")
    back = r.get("retraced_pct") if long else r.get("bounced_of_drop_pct")
    if pts and back is not None and 30 <= back <= 70:
        pts += 10
        why.append(f"gave back {back}% of its last leg" if long else f"bounced {back}% of its last drop")
    z = r.get("z_1d")
    if not long and z is not None and z >= Z_BLOWOFF:      # the fade: stretched, whatever the trend
        stretch = 40 if z >= Z_TRIM else 35
        if stretch >= pts:
            line = "the engine trims from 3" if z >= Z_TRIM else "the engine's blow-off line is 2.5"
            pts, why = stretch, [f"stretched: daily z {z:.1f} ({line})"]
            if st == "extended":
                pts += 10
                why.append(f"{r['band']['vs_pct']:.0f}% over its band")
    if long and st == "extended":
        pts, why = 0, ["extended over its band"]
    elif long and st == "below":                          # the band rule: under it the trend is lost until reclaimed
        pts, why = 0, ["under its band: trend lost until reclaimed"]
    elif not long and st == "below" and (r["band"]["vs_pct"] or 0) <= -15 and not (z is not None and z >= Z_BLOWOFF):
        pts, why = 0, ["far under its band: chasing a breakdown"]
    if long and z is not None and z >= Z_BLOWOFF:
        pts -= 15
        why.append(f"stretched: daily z {z:.1f}, buying here is chasing")
    conv = (r.get("conviction_7d") or {}).get(side, 0)
    if conv:
        pts += 20
        why.append(f"{conv} conviction bet{'s' if conv > 1 else ''} {side} in 7 days")
    adders = (r.get("added_24h") or {}).get(side, 0)
    if adders >= 2:
        pts += 10
        why.append(f"{adders} tracked traders added {side}s in 24h")
    if (r.get("tracked") or {}).get("lean") == side:
        pts += 5
        why.append(f"tracked traders lean {side}")
    sig = r.get("engine_code")
    if sig in (UP if long else DOWN):
        pts += 15
        why.append(f"engine: {SIGNAL_WORD.get(sig, sig)}")
    elif sig in (DOWN if long else UP):
        pts -= 10
        why.append(f"engine against: {SIGNAL_WORD.get(sig, sig)}")
    f = r.get("funding_apr_pct")
    if f is not None and (f > 50 if long else f < -50):
        pts -= 5
        why.append(f"crowded: funding {f:+.0f}% a year")
    elif f is not None and not long and f >= 30:
        pts += 5
        why.append(f"longs paying {f:.0f}% a year")
    return max(0, pts), why


def positioning(agent, now: float, board: dict | None = None) -> dict[str, dict]:
    """Per coin: tracked traders long and short and their lean, who added in 24 hours, conviction bets in 7 days, and
    the engine's daily signal."""
    pt, store = agent.positions, agent.store
    out: dict[str, dict] = {}
    for c in (board or pt.board(now))["coins"]:
        out.setdefault(c["coin"], {})["tracked"] = {"long": c["long_n"], "short": c["short_n"],
                                                    "lean": desk.lean(c["long_n"], c["short_n"])}
    for a in pt.adding(24, now):
        if a["adders"]:
            out.setdefault(a["coin"], {}).setdefault("added_24h", {})[a["side"]] = a["adders"]
    for b in store.briefs_since("pt_alert", now - 7 * 86400):
        c, side = b["body"].get("coin"), b["body"].get("side")
        if c and side:
            cv = out.setdefault(c, {}).setdefault("conviction_7d", {})
            cv[side] = cv.get(side, 0) + 1
    for c, e in ((store.get_snapshot("reflex") or {}).get("engine") or {}).items():
        if e.get("1d"):
            out.setdefault(c, {})["engine_code"] = e["1d"]
            out[c]["engine"] = SIGNAL_WORD.get(e["1d"], e["1d"])
    for c, e in ((store.get_snapshot("engine_all") or {}).get("c") or {}).items():   # every coin, hourly
        d = out.setdefault(c, {})
        if e.get("signal_1d"):
            d["engine_code"], d["engine"] = e["signal_1d"], SIGNAL_WORD.get(e["signal_1d"], e["signal_1d"])
        for k in ("z_1d", "z_4h", "heat_1d"):
            if e.get(k) is not None:
                d[k] = e[k]
    return out


def trader_coins(agent, now: float, min_crowd: int = 5) -> list[str]:
    """Coins where traders are, whatever their volume: what tracked traders hold or added in 7 days, conviction bets in
    7 days, and coins at least `min_crowd` profitable traders hold. Read daily beside the most traded."""
    pt, store = agent.positions, agent.store
    out = {c["coin"] for c in pt.board(now)["coins"]}
    out |= {a["coin"] for a in pt.adding(168, now) if a["adders"]}
    out |= {b["body"]["coin"] for b in store.briefs_since("pt_alert", now - 7 * 86400) if b["body"].get("coin")}
    for c, v in ((store.get_snapshot("reflex") or {}).get("positions") or {}).items():
        if v[0] + v[2] >= min_crowd:
            out.add(c)
    return sorted(out)


def build(agent, now: float | None = None, board: dict | None = None) -> dict:
    """The whole table, from the levels read today (or in the last 3 days) and the prices of the last poll, with the
    market's breadth: how many coins sit under, inside and over their band, and over their 200-day average."""
    now = now or time.time()
    lvs = agent.store.get_snapshot("trend_levels") or {}
    cur = (agent.store.get_snapshot("hl_ctx") or {}).get("c") or {}
    pos = positioning(agent, now, board)
    rows = []
    for coin, lv in lvs.items():
        price = (cur.get(coin) or {}).get("px")
        if price:
            rows.append(coin_row(coin, lv, price, agent.levels.oi_funding(coin, now), pos.get(coin, {})))
    breadth = {s: sum(1 for r in rows if (r.get("band") or {}).get("state") == s) for s in BAND_STATES}
    with200 = [r for r in rows if r.get("vs_ma200d_pct") is not None]
    breadth["over_200d"] = sum(1 for r in with200 if r["vs_ma200d_pct"] > 0)
    breadth["with_200d"] = len(with200)
    return {"ts": now, "coins": len(rows), "breadth": breadth, "rows": rows}


def pick(rows: list[dict], sort: str = "score", band: str = "any", trend: str = "any", side: str = "any",
         limit: int = 15) -> list[dict]:
    """Filter and sort the table: band state (or near: inside or within 3% either side), trend (over or under the
    200-day average), and the sort (best setup score, closest to the band, to the 200-day, to a half retracement,
    biggest open interest change, most extreme funding)."""
    out = rows
    if band == "near":
        out = [r for r in out if r.get("band") and abs(r["band"]["vs_pct"] or 0) <= NEAR_PCT]
    elif band != "any":
        out = [r for r in out if (r.get("band") or {}).get("state") == band]
    if trend in ("up", "down"):
        out = [r for r in out if r.get("vs_ma200d_pct") is not None and (r["vs_ma200d_pct"] > 0) == (trend == "up")]
    big = 1e9
    key = {
        "score": lambda r: -(r["score_long"] if side == "long" else r["score_short"] if side == "short"
                             else max(r["score_long"], r["score_short"])),
        "band": lambda r: abs(r["band"]["vs_pct"]) if r.get("band") else big,
        "ma200": lambda r: abs(r["vs_ma200d_pct"]) if r.get("vs_ma200d_pct") is not None else big,
        "retrace": lambda r: abs(r["retraced_pct"] - 50) if r.get("retraced_pct") is not None else big,
        "oi": lambda r: -abs(r.get("oi_change_24h_pct") or 0),
        "funding": lambda r: -abs(r.get("funding_apr_pct") or 0),
    }[sort if sort in SORTS else "score"]
    return sorted(out, key=key)[:max(1, min(limit, 40))]


def candidates(table: dict, n: int = 6, floor: int = 40) -> list[dict]:
    """The best setups the table holds, either side, at least `floor` points: what a read chooses from."""
    out = []
    for r in table["rows"]:
        side = "long" if r["score_long"] >= r["score_short"] else "short"
        s = r[f"score_{side}"]
        if s >= floor:
            out.append({**{k: v for k, v in r.items() if not k.startswith(("score_", "why_"))},
                        "side": side, "score": s, "why": r[f"why_{side}"]})
    return sorted(out, key=lambda r: -r["score"])[:n]
