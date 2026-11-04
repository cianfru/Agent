"""Every liquid coin against its levels, in one table: the read's setup candidates and the chat's screen.

Built in code from what the agent already keeps: each coin's levels (synth/levels.py: the bull market support band, the
200-day average, the last leg and the swing levels, its volatility and last 91 daily closes, read once a day for the 100
most traded perps and wherever traders are), its price, open interest, funding and spread now, the engine's daily
reading, and what tracked traders hold, added in 24 hours and bet with conviction in 7 days. So a question like "which
coins sit closest to their band" is one look at one table, and a read chooses its setups from the whole market.

The z-score (owner, 4 November 2026: "for shorting, one of the most important aspects"): the engine's daily z-score,
how far the price sits above its long-run trend in its own volatility, read for every coin once an hour. The owner's
lines, used to rank: 2.5 a blow-off, 3 trim, 3.5 hard trim, -1 capitulation. The engine itself scales its lines by
volatility (0.85x to 1.25x) and trims only in its blow-off regime, so "the engine is trimming" is said only when its own
reading says so.

The band (owner, 3 November 2026): inside it, the price is testing it, a zone to watch, not a block; up to 5% over it is
just above; 15% or more over it is extended, not an entry; under it the trend is lost until the band is reclaimed.

"Near" is in the coin's own volatility: within one 14-day average true range (at least 1%, at most 5%), so a 3% move
is a test of a level for a quiet coin and noise for a wild one.

Each coin gets a score for a long and for a short setup, to rank what is worth a look. A ranking, not a tested signal:
the setups a read sends are what study S5 scores.
  where the price sits, up to 55  long: over its 200-day average (or no 200-day yet) and inside its band or just above
                                  it 35; else near its 200-day average from above 25; else, still over the 200-day, on a
                                  swing low 20; plus 10 when it has given back 30% to 70% of its last leg.
                                  short: the mirror (under the 200-day, inside the band or just under it, a bounce of
                                  30% to 70% of the last drop), or stretched: a daily z-score of 2.5 35, of 3 40, of
                                  3.5 45, plus 10 when 15%+ over the band; this route fades a run, so it needs no
                                  downtrend. A long under its band, a long extended over it, or a short 15%+ under it
                                  (not a stretched fade): 0 (the owner's band rule).
  who is positioning, up to 40    a proven trader's conviction bet that side in 7 days 20, two or more tracked traders
                                  adding that side in 24 hours 10, tracked traders leaning that side 5, open interest
                                  against price 5 (a short with longs piling in; a long after longs were flushed out)
  the engine, up to 15            its daily setup that way 15; the other way -10; a long on a stretched z (2.5+) -15
  crowding, -5 or +5              24-hour average funding over 50% a year against a long, under -50% against a short;
                                  over 30% a year (longs paying up) +5 for a short
  strength, -5 or +5              among the strongest 30% of coins against BTC over 30 days +5 for a long (weakest 30%
                                  +5 for a short), the other end -5

A setup needs two independent things lining up (the owner's rule, S5 Amendment 2): the price at a level (band,
200-day, swing, or a stretched z), traders positioning (a conviction bet, two or more adding, crowded funding, open
interest against price), and the engine, which counts only beside one of the other two. Candidates are the coins with
two or more, liquid enough to trade (at least $3M a day and a book no more than 15 basis points wide).

Every row carries, for each side, where the setup would be wrong (the level the price sits on, with its distance in %
and in average true ranges), the next level the other way, and the room to it over the risk. Plain levels, never a
forecast.
"""

from __future__ import annotations

import math
import statistics
import time
from typing import Any

from . import desk
from .levels import NEAR_PCT, band_state

Z_BLOWOFF, Z_TRIM, Z_TRIM_HARD, Z_CAPITULATION = 2.5, 3.0, 3.5, -1.0     # the owner's lines (the engine's, unscaled)

UP = {"STRONG_LONG", "LIGHT_LONG", "ACCUMULATE", "REVIVAL_SEED", "REVIVAL_SEED_CONFIRMED"}
DOWN = {"RISK_OFF", "LIGHT_SHORT", "TRIM_HARD", "NO_LONG"}
TRIMMING = {"TRIM", "TRIM_HARD"}
SIGNAL_WORD = {"STRONG_LONG": "strong up-setup", "LIGHT_LONG": "leaning up", "ACCUMULATE": "base forming",
               "REVIVAL_SEED": "turning", "REVIVAL_SEED_CONFIRMED": "turn confirmed", "WAIT": "no clear setup",
               "TRIM": "stretched", "TRIM_HARD": "very stretched", "RISK_OFF": "leaning down",
               "LIGHT_SHORT": "leaning down", "NO_LONG": "up-setups blocked"}
SORTS = ("score", "band", "ma200", "retrace", "oi", "funding", "z", "rs")
BAND_STATES = ("below", "inside", "just above", "above", "extended")
MIN_VOL_USD = 3_000_000        # a day's volume under this: too thin to be a setup
MAX_SPREAD_BPS = 15.0          # an impact-size order paying more than this across the book: too thin
ENGINE_KEEP = ("sector", "atr_regime", "rel_vol", "exhaustion", "climax", "divergence", "floor_confirmed")


def _pct(a: float | None, b: float | None) -> float | None:
    return round(100 * (a / b - 1), 1) if a and b else None


def z_word(z: float | None) -> str | None:
    """Where a daily z-score sits against the owner's lines."""
    if z is None:
        return None
    if z >= Z_TRIM_HARD:
        return "past hard trim (3.5)"
    if z >= Z_TRIM:
        return "past trim (3)"
    if z >= Z_BLOWOFF:
        return "blow-off (2.5)"
    if z <= Z_CAPITULATION:
        return "capitulation (-1)"
    return None


def oi_class(price_pct: float | None, oi_pct: float | None, dead_px: float) -> str | None:
    """Open interest against price over a window: who is acting. A move under `dead_px`% or open interest under 2% is
    no class. Context, untested."""
    if price_pct is None or oi_pct is None or abs(oi_pct) < 2 or abs(price_pct) < dead_px:
        return None
    return {(True, True): "longs building", (False, True): "shorts building", (True, False): "short covering",
            (False, False): "long liquidation"}[(price_pct > 0, oi_pct > 0)]


def _level(price: float, level: float | None, name: str, atr: float | None) -> dict | None:
    if not level or not price:
        return None
    pct = round(100 * (level / price - 1), 1)
    out = {"level": float(f"{level:.5g}"), "name": name, "pct": pct}
    if atr:
        out["atr"] = round(abs(pct) / atr, 1)
    return out


def frame(r: dict, side: str, route: str | None) -> dict:
    """Where a setup on this side would be wrong (the level its price sits on, by the route that scored it, else the
    nearest level behind the price), the next level the other way, and the room to it over the risk."""
    price, atr = r["price"], r.get("atr_pct")
    band = r.get("band") or {}
    lo, hi = band.get("low"), band.get("high")
    if side == "long":
        by_route = {"band": ("band low", lo), "ma200d": ("200-day average", r.get("ma200d")),
                    "swing": ("swing low", r.get("swing_low"))}
        behind = [("band low", lo), ("band high", hi), ("swing low", r.get("swing_low")),
                  ("200-day average", r.get("ma200d"))]
        ahead = [("swing high", r.get("swing_high")), ("recent high", r.get("high")), ("band high", hi),
                 ("band low", lo), ("200-day average", r.get("ma200d"))]
        below = True
    else:
        by_route = {"band": ("band high", hi), "ma200d": ("200-day average", r.get("ma200d")),
                    "swing": ("swing high", r.get("swing_high")), "stretch": ("recent high", r.get("high"))}
        behind = [("band high", hi), ("band low", lo), ("swing high", r.get("swing_high")),
                  ("200-day average", r.get("ma200d")), ("recent high", r.get("high"))]
        ahead = [("swing low", r.get("swing_low")), ("band high", hi), ("band low", lo),
                 ("200-day average", r.get("ma200d"))]
        below = False

    def wrong_side(v: float | None) -> bool:      # behind the price: under it for a long, over it for a short
        return bool(v) and (v < price if below else v > price)

    off = None
    if route in by_route and wrong_side(by_route[route][1]):
        off = _level(price, by_route[route][1], by_route[route][0], atr)
    if off is None:
        cands = [(n, v) for n, v in behind if wrong_side(v)]
        if cands:
            n, v = (max if below else min)(cands, key=lambda x: x[1])
            off = _level(price, v, n, atr)
    tgt = [(n, v) for n, v in ahead if v and (v > price * 1.002 if below else v < price * 0.998)]
    nxt = None
    if tgt:
        n, v = (min if below else max)(tgt, key=lambda x: x[1])
        nxt = _level(price, v, n, atr)
    out: dict[str, Any] = {}
    if off:
        out["off"] = off
    if nxt:
        out["next"] = nxt
    if off and nxt and off["pct"]:
        out["room_over_risk"] = round(abs(nxt["pct"]) / abs(off["pct"]), 1)
    return out


def coin_row(coin: str, lv: dict, price: float, ctx: dict | None, pos: dict, extra: dict | None = None) -> dict:
    """One coin's line: its levels against the price now, what is near, volatility, liquidity, positioning, the
    engine, strength against BTC, the two scores and each side's risk frame."""
    lo_hi = [x for x in (lv.get("sma20w"), lv.get("ema21w")) if x]
    r: dict[str, Any] = {"coin": coin, "price": float(f"{price:.5g}")}
    if len(lo_hi) == 2:
        lo, hi = min(lo_hi), max(lo_hi)
        st = band_state(price, lo, hi)
        r["band"] = {"low": lo, "high": hi, "state": st,
                     "vs_pct": 0.0 if st == "inside" else _pct(price, hi) if price > hi else _pct(price, lo)}
    if lv.get("ma200d"):
        r["ma200d"], r["vs_ma200d_pct"] = lv["ma200d"], _pct(price, lv["ma200d"])
    for k in ("atr14_pct", "rv30_pct", "rel_vol"):
        if lv.get(k) is not None:
            r[{"atr14_pct": "atr_pct"}.get(k, k)] = lv[k]
    r["near_pct"] = round(min(5.0, max(1.0, r["atr_pct"])), 1) if r.get("atr_pct") else NEAR_PCT
    hi, leg = lv.get("high"), lv.get("leg_low")
    if hi and price > hi:                     # past the high read at 00:30: a new high today, nothing given back
        hi, r["new_high"] = price, True
    if hi:
        r["high"] = float(f"{hi:.5g}")
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
        r.update({k: ctx[k] for k in ("oi_change_24h_pct", "oi_change_7d_pct", "funding_apr_pct",
                                      "funding_24h_avg_apr_pct", "funding_z", "funding_vs_cex_apr_pct",
                                      "volume_24h_usd", "open_interest_usd", "spread_bps")
                  if ctx.get(k) is not None})
        dead = r.get("atr_pct") or 2.0
        c24 = oi_class(ctx.get("price_change_24h_pct"), ctx.get("oi_change_24h_pct"), 0.5 * dead)
        c7 = oi_class(ctx.get("price_change_7d_pct"), ctx.get("oi_change_7d_pct"), 1.0 * dead)
        if c24:
            r["oi_class_24h"] = c24
        if c7:
            r["oi_class_7d"] = c7
        vol, spread = ctx.get("volume_24h_usd"), ctx.get("spread_bps")
        if (vol is not None and vol < MIN_VOL_USD) or (spread is not None and spread > MAX_SPREAD_BPS):
            r["thin"] = True
    r.update({k: v for k, v in pos.items() if v not in (None, {}, "")})
    r.update({k: v for k, v in (extra or {}).items() if v is not None})
    zw = z_word(r.get("z_1d"))
    if zw:
        r["z_line"] = zw
    for side in ("long", "short"):
        pts, why, fam, route = _score(r, side)
        r[f"score_{side}"], r[f"why_{side}"] = pts, why
        r[f"lined_up_{side}"] = {k: v for k, v in fam.items() if v}
        r[f"independent_{side}"] = independent(fam)
        r[f"levels_{side}"] = frame(r, side, route)
    return r


def independent(fam: dict) -> int:
    """How many independent things line up: the price at a level, traders positioning, and the engine, which counts
    only beside one of the other two."""
    n = int(bool(fam.get("price"))) + int(bool(fam.get("positioning")))
    return n + int(bool(fam.get("engine")) and n > 0)


def score(r: dict, side: str) -> tuple[int, list[str]]:
    pts, why, _, _ = _score(r, side)
    return pts, why


def _score(r: dict, side: str) -> tuple[int, list[str], dict, str | None]:
    """The ranking described at the top: where the price sits, who is positioning, the engine, crowding, strength.
    Returns the points, the reasons, the reasons by family, and the route the price scored by."""
    long = side == "long"
    why: list[str] = []
    fam: dict[str, list[str]] = {"price": [], "positioning": [], "engine": []}
    pts, route = 0, None
    st = (r.get("band") or {}).get("state")
    vs200 = r.get("vs_ma200d_pct")
    near = r.get("near_pct") or NEAR_PCT
    trend_ok = vs200 is None or (vs200 > 0 if long else vs200 < 0)
    near_band = st in ("inside", "just above") if long else (st == "inside" or (
        st == "below" and (r["band"]["vs_pct"] or 0) >= -5))
    if near_band and trend_ok:
        pts, route = 35, "band"
        why.append("inside its band" if st == "inside" else "just over its band" if long else "just under its band")
    elif vs200 is not None and (0 <= vs200 <= near if long else -near <= vs200 <= 0):
        pts, route = 25, "ma200d"
        why.append("at its 200-day average")
    elif trend_ok and long and r.get("swing_low_pct") is not None and r["swing_low_pct"] >= -near:
        pts, route = 20, "swing"
        why.append("on a swing low")
    elif trend_ok and not long and r.get("swing_high_pct") is not None and r["swing_high_pct"] <= near:
        pts, route = 20, "swing"
        why.append("under a swing high")
    back = r.get("retraced_pct") if long else r.get("bounced_of_drop_pct")
    if pts and back is not None and 30 <= back <= 70:
        pts += 10
        why.append(f"gave back {back}% of its last leg" if long else f"bounced {back}% of its last drop")
    z = r.get("z_1d")
    if not long and z is not None and z >= Z_BLOWOFF:      # the fade: stretched, whatever the trend
        stretch = 45 if z >= Z_TRIM_HARD else 40 if z >= Z_TRIM else 35
        if stretch >= pts:
            line = z_word(z)
            engine = r.get("engine_code") in TRIMMING or r.get("regime_1d") == "BLOWOFF"
            pts, route = stretch, "stretch"
            why = [f"stretched: daily z {z:.1f}, {line}" + ("; the engine is trimming" if engine else "")]
            if st == "extended":
                pts += 10
                why.append(f"{r['band']['vs_pct']:.0f}% over its band")
    if long and st == "extended":
        pts, why, route = 0, ["extended over its band"], None
    elif long and st == "below":                          # the band rule: under it the trend is lost until reclaimed
        pts, why, route = 0, ["under its band: trend lost until reclaimed"], None
    elif not long and st == "below" and (r["band"]["vs_pct"] or 0) <= -15 and route != "stretch":
        pts, why, route = 0, ["far under its band: chasing a breakdown"], None
    if route:
        fam["price"] = list(why)
    if long and z is not None and z >= Z_BLOWOFF:
        pts -= 15
        why.append(f"stretched: daily z {z:.1f}, buying here is chasing")
    conv = (r.get("conviction_7d") or {}).get(side, 0)
    if conv:
        pts += 20
        why.append(f"{conv} conviction bet{'s' if conv > 1 else ''} {side} in 7 days")
        fam["positioning"].append(why[-1])
    adders = (r.get("added_24h") or {}).get(side, 0)
    if adders >= 2:
        pts += 10
        why.append(f"{adders} tracked traders added {side}s in 24h")
        fam["positioning"].append(why[-1])
    if (r.get("tracked") or {}).get("lean") == side:
        pts += 5
        why.append(f"tracked traders lean {side}")
    classes = (r.get("oi_class_24h"), r.get("oi_class_7d"))
    if not long and "longs building" in classes:
        pts += 5
        why.append("longs piling in (open interest up with the price)")
        fam["positioning"].append(why[-1])
    elif long and "long liquidation" in classes:
        pts += 5
        why.append("longs flushed out (open interest down with the price)")
        fam["positioning"].append(why[-1])
    sig = r.get("engine_code")
    if sig in (UP if long else DOWN):
        pts += 15
        why.append(f"engine: {SIGNAL_WORD.get(sig, sig)}")
        fam["engine"].append(why[-1])
    elif sig in (DOWN if long else UP):
        pts -= 10
        why.append(f"engine against: {SIGNAL_WORD.get(sig, sig)}")
    f = r.get("funding_24h_avg_apr_pct")
    f = r.get("funding_apr_pct") if f is None else f        # the day's average, not one hour's print
    fz = r.get("funding_z")                                   # against the coin's own last 20 days
    hot = fz is not None and fz >= 2 and (f or 0) > 10
    cold = fz is not None and fz <= -2 and (f or 0) < -10
    norm = f" ({fz:+.1f} sd from its 20-day norm)" if fz is not None and abs(fz) >= 2 else ""
    if f is not None and ((f > 50 or hot) if long else (f < -50 or cold)):
        pts -= 5
        why.append(f"crowded: funding {f:+.0f}% a year{norm}")
    elif f is not None and not long and (f >= 30 or hot):
        pts += 5
        why.append(f"longs paying {f:.0f}% a year{norm}")
        if f >= 50 or (fz is not None and fz >= 2.5):
            fam["positioning"].append(why[-1])
    rank = r.get("rs_rank_30d")
    if rank is not None and r.get("rs_30d_pct") is not None:
        strong, weak = rank >= 70, rank <= 30
        word = (f"among the strongest coins over 30 days" if strong else f"among the weakest coins over 30 days") + \
            f" ({r['rs_30d_pct']:+.0f} points against BTC)"
        if (strong and long) or (weak and not long):
            pts += 5
            why.append(word)
        elif (weak and long) or (strong and not long):
            pts -= 5
            why.append(word)
    return max(0, pts), why, fam, route


def positioning(agent, now: float, board: dict | None = None) -> dict[str, dict]:
    """Per coin: tracked traders long and short and their lean, who added in 24 hours, conviction bets in 7 days (every
    one, sent or held by the alert cap), and the engine's daily reading."""
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
        for k in ("z_1d", "z_4h", "heat_1d", "regime_1d", *ENGINE_KEEP):
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


def _ret(cl: list[float], n: int) -> float | None:
    return cl[-1] / cl[-1 - n] - 1 if len(cl) > n and cl[-1 - n] and cl[-1] else None


def strength(lvs: dict[str, dict]) -> dict[str, dict]:
    """Per coin from its daily closes: its 30-day move against BTC's in points, its rank among the coins (0 the
    weakest, 100 the strongest), and its beta and correlation to BTC over the last 60 days (same closing day only)."""
    btc = (lvs.get("BTC") or {}).get("closes") or []
    b30 = _ret(btc, 30)
    out: dict[str, dict] = {}
    rs = {}
    for c, lv in lvs.items():
        r30 = _ret(lv.get("closes") or [], 30)
        if c != "BTC" and r30 is not None and b30 is not None:      # BTC is the yardstick, not ranked against itself
            rs[c] = round(100 * (r30 - b30), 1)
    order = sorted(rs, key=lambda c: rs[c])
    for i, c in enumerate(order):
        out[c] = {"rs_30d_pct": rs[c], "rs_rank_30d": round(100 * i / max(1, len(order) - 1))}
    bto = (lvs.get("BTC") or {}).get("closes_to")
    br = [math.log(b / a) for a, b in zip(btc[-61:], btc[-60:]) if a and b]
    for c, lv in lvs.items():
        cl = lv.get("closes") or []
        if c == "BTC" or lv.get("closes_to") != bto or len(cl) < 61 or len(br) < 60:
            continue
        cr = [math.log(b / a) for a, b in zip(cl[-61:], cl[-60:]) if a and b]
        if len(cr) != len(br):
            continue
        vb = statistics.pvariance(br)
        if not vb:
            continue
        mb, mc = statistics.fmean(br), statistics.fmean(cr)
        cov = sum((x - mb) * (y - mc) for x, y in zip(br, cr)) / len(br)
        sc = statistics.pstdev(cr)
        out.setdefault(c, {}).update({"beta_btc": round(cov / vb, 2),
                                      "corr_btc": round(cov / (math.sqrt(vb) * sc), 2) if sc else None})
    return out


def build(agent, now: float | None = None, board: dict | None = None) -> dict:
    """The whole table, from the levels read today (or in the last 3 days) and the prices of the last poll, with the
    market's breadth: how many coins sit under, inside and over their band, and over their 200-day average."""
    now = now or time.time()
    lvs = agent.store.get_snapshot("trend_levels") or {}
    cur = (agent.store.get_snapshot("hl_ctx") or {}).get("c") or {}
    pos = positioning(agent, now, board)
    rel = strength(lvs)
    rows = []
    for coin, lv in lvs.items():
        price = (cur.get(coin) or {}).get("px")
        if price:
            rows.append(coin_row(coin, lv, price, agent.levels.oi_funding(coin, now), pos.get(coin, {}),
                                 rel.get(coin)))
    breadth = {s: sum(1 for r in rows if (r.get("band") or {}).get("state") == s) for s in BAND_STATES}
    with200 = [r for r in rows if r.get("vs_ma200d_pct") is not None]
    breadth["over_200d"] = sum(1 for r in with200 if r["vs_ma200d_pct"] > 0)
    breadth["with_200d"] = len(with200)
    zs = [r["z_1d"] for r in rows if r.get("z_1d") is not None]
    breadth["stretched"] = sum(1 for z in zs if z >= Z_BLOWOFF)
    breadth["washed_out"] = sum(1 for z in zs if z <= Z_CAPITULATION)
    return {"ts": now, "coins": len(rows), "breadth": breadth, "rows": rows}


def pick(rows: list[dict], sort: str = "score", band: str = "any", trend: str = "any", side: str = "any",
         limit: int = 15) -> list[dict]:
    """Filter and sort the table: band state (or near: inside or within 3% either side), trend (over or under the
    200-day average), and the sort (best setup score, closest to the band, to the 200-day, to a half retracement,
    biggest open interest change, most extreme funding, highest daily z, strongest against BTC; weakest first for a
    short side)."""
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
        "z": lambda r: -r["z_1d"] if r.get("z_1d") is not None else big,
        "rs": lambda r: (r["rs_30d_pct"] if side == "short" else -r["rs_30d_pct"]) if r.get("rs_30d_pct") is not None
        else big,
    }[sort if sort in SORTS else "score"]
    return sorted(out, key=key)[:max(1, min(limit, 40))]


def candidates(table: dict, n: int = 6, floor: int = 40) -> list[dict]:
    """The best setups the table holds, either side: at least `floor` points, two independent things lining up, and
    liquid enough to trade. What a read chooses from."""
    out = []
    for r in table["rows"]:
        if r.get("thin"):
            continue
        sides = [s for s in ("long", "short") if r[f"score_{s}"] >= floor and r[f"independent_{s}"] >= 2]
        if not sides:
            continue
        side = max(sides, key=lambda s: r[f"score_{s}"])
        keep = {k: v for k, v in r.items()
                if not k.startswith(("score_", "why_", "lined_up_", "independent_", "levels_"))
                and k not in ("engine_code", "regime_1d")}
        out.append({**keep, "side": side, "score": r[f"score_{side}"], "why": r[f"why_{side}"],
                    "lined_up": r[f"lined_up_{side}"], "independent": r[f"independent_{side}"],
                    **r[f"levels_{side}"]})
    return sorted(out, key=lambda r: -r["score"])[:n]


LOG_KEYS = ("price", "z_1d", "z_4h", "vs_ma200d_pct", "atr_pct", "rs_30d_pct", "rs_rank_30d", "beta_btc",
            "oi_class_24h", "oi_class_7d", "funding_24h_avg_apr_pct", "volume_24h_usd", "thin", "engine_code")


def log_row(r: dict) -> dict:
    """What a later test of the screen needs from a row, as the screen saw it that day: each side's score, its parts
    by family and its risk frame, the band state, the z-score and the coin's figures."""
    out = {k: r[k] for k in LOG_KEYS if r.get(k) is not None}
    if r.get("band"):
        out["band"] = r["band"]["state"]
        out["band_vs_pct"] = r["band"]["vs_pct"]
    for side in ("long", "short"):
        out[side] = {"score": r[f"score_{side}"], "independent": r[f"independent_{side}"],
                     "lined_up": sorted(r[f"lined_up_{side}"]), **{k: v for k, v in r[f"levels_{side}"].items()
                                                                   if k in ("off", "next", "room_over_risk")}}
    return out


def log_day(agent, now: float | None = None) -> int:
    """Once a day after the levels refresh: every coin's screen row, and the market's breadth (coin '*'), kept as the
    screen saw them, so the screen can be tested against what came next without hindsight (study S6)."""
    now = now or time.time()
    t = build(agent, now)
    if not t["rows"]:
        return 0
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    agent.store.screen_log_add(day, [(r["coin"], log_row(r)) for r in t["rows"]]
                               + [("*", {**t["breadth"], "coins": t["coins"]})])
    return len(t["rows"])


def breadth_history(lvs: dict[str, dict], days: int = 90) -> list[dict]:
    """The market's breadth day by day, rebuilt from each coin's band and 200-day history read with its levels: per day,
    the coins under, inside and over their band and over their 200-day average. Today's universe only, so it carries
    survivorship (coins that fell out of the most traded are missing)."""
    import calendar
    out: dict[str, dict] = {}
    for lv in lvs.values():
        hist, over, to = lv.get("band_hist") or "", lv.get("over200_hist") or "", lv.get("hist_to")
        if not hist or not to:
            continue
        end = calendar.timegm(time.strptime(to, "%Y-%m-%d"))
        for k, code in enumerate(hist):
            day = time.strftime("%Y-%m-%d", time.gmtime(end - (len(hist) - 1 - k) * 86400))
            d = out.setdefault(day, {"day": day, "below": 0, "inside": 0, "over": 0, "over_200d": 0,
                                     "with_200d": 0, "coins": 0})
            if code == "-":
                continue
            d["coins"] += 1
            d["below" if code == "b" else "inside" if code == "i" else "over"] += 1
            o = over[k] if k < len(over) else "-"
            if o != "-":
                d["with_200d"] += 1
                d["over_200d"] += o == "1"
    return [out[d] for d in sorted(out)][-days:]
