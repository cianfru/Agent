"""The market read: overall smart-money lean, and coins where smart money and market data line up.

Replaces the hourly event list. A raw anomaly (open-interest surge, volume spike, buy/sell imbalance,
funding extreme) on its own is never shown here: a coin appears only when smart money is moving on it
AND either market data or a second smart-money signal says the same thing. Pure functions.

Reflex anomaly directions, as Reflex defines them (anomaly_detector.py):
  EXTREME_FUNDING  SHORT = funding negative (shorts pay: crowded short), LONG = crowded long
  OI_SURGE         value = open interest change %, direction = its sign
  LSR_EXTREME      LONG = most accounts long, SHORT = most accounts short
  CVD_EXTREME      LONG = aggressive buying, SHORT = aggressive selling
  VOLUME_SPIKE     value = volume vs normal, no direction
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from .brief import usd

LEAN_RATIO = 1.25  # shorts > 1.25x longs (in dollars) = leaning short, and vice versa


# ------------------------------------------------------------- sentiment --


def sentiment(reflex: dict | None) -> dict | None:
    t = (reflex or {}).get("totals")
    if not t or not (t["long_usd"] or t["short_usd"]):
        return None
    lo, sh = t["long_usd"], t["short_usd"]
    lean = "short" if sh > LEAN_RATIO * lo else "long" if lo > LEAN_RATIO * sh else "balanced"
    return {**t, "lean": lean}


def change(history: list[dict], since_ts: float) -> dict | None:
    """Totals now minus the reading closest to `since_ts` (history: [{ts, long_usd, short_usd, ...}])."""
    if len(history) < 2:
        return None
    base = min(history[:-1], key=lambda h: abs(h["ts"] - since_ts))
    now = history[-1]
    if now["ts"] - base["ts"] < 1800:
        return None
    return {"since": base["ts"], "long_usd": now["long_usd"] - base["long_usd"],
            "short_usd": now["short_usd"] - base["short_usd"]}


# --------------------------------------------------------------- stories --


def _market_signals(anoms: list[dict]) -> list[tuple[str, str | None]]:
    """(text, implied crowd side or None) per Reflex anomaly on one coin."""
    out = []
    for a in anoms:
        t, d, v = a.get("type"), str(a.get("direction") or "").upper(), a.get("value")
        if t == "OI_SURGE" and v is not None:
            out.append((f"open interest {v:+.0f}%", "oi_up" if v > 0 else "oi_down"))
        elif t == "EXTREME_FUNDING":
            out.append(("funding negative: shorts crowded" if d == "SHORT" else "funding high: longs crowded",
                        "crowd_short" if d == "SHORT" else "crowd_long"))
        elif t == "LSR_EXTREME":
            out.append(("most accounts short" if d == "SHORT" else "most accounts long",
                        "crowd_short" if d == "SHORT" else "crowd_long"))
        elif t == "CVD_EXTREME":
            out.append(("aggressive selling" if d == "SHORT" else "aggressive buying",
                        "flow_short" if d == "SHORT" else "flow_long"))
        elif t == "VOLUME_SPIKE" and v:
            out.append((f"volume {v:.1f}x normal", None))
    return out


def _tag(side: str, marks: set[str]) -> str | None:
    """One plain read of what the combination means."""
    if side == "short" and "oi_up" in marks:
        return "shorts piling in"
    if side == "long" and "oi_up" in marks:
        return "longs piling in"
    if side == "long" and "crowd_short" in marks:
        return "smart longs against a crowded short"
    if side == "short" and "crowd_long" in marks:
        return "smart shorts against a crowded long"
    if f"flow_{side}" in marks:
        return f"{'buyers' if side == 'long' else 'sellers'} in control"
    return None


def coin_stories(reflex: dict | None, groups: list[tuple[dict, list[dict]]], events: list[dict],
                 limit: int = 5) -> list[dict]:
    """Coins where smart money moves and something else agrees. `groups`: recent (group, members);
    `events`: recent stored events (wallet moves, cohort flips) for the read's window."""
    smart: dict[str, list[tuple[str, str, int]]] = defaultdict(list)  # coin -> [(side, text, weight)]
    for g, members in groups:
        n = len({m["address"] for m in members})
        word = "smart option wallets" if g["venue"] == "derive" else f"smart {g['side']}s"
        span = g["meta"].get("span_h")
        when = "" if span is None else " within minutes" if span < 0.25 else f" in {span:g}h"
        smart[g["coin"]].append((g["side"], f"{n} {word}{when} (#{g['id']})", 3))
    for coin, v in ((reflex or {}).get("coins") or {}).items():
        o = v.get("opens_24h") or {}
        lo, sh = o.get("long") or 0, o.get("short") or 0
        if lo + sh >= 3 and (sh >= 2 * max(lo, 1) or lo >= 2 * max(sh, 1)):
            side = "short" if sh > lo else "long"
            smart[coin].append((side, f"{sh if side == 'short' else lo} new {side}s vs {lo if side == 'short' else sh} today", 2))
    for e in events:
        if e.get("coin") and e.get("kind") in ("wallet_move", "cohort_flip") and e.get("direction") in ("long", "short"):
            smart[e["coin"]].append((e["direction"], e["title"].rsplit(" (", 1)[0], 1))

    anoms: dict[str, list[dict]] = defaultdict(list)
    for a in (reflex or {}).get("anomalies") or []:
        if a.get("coin"):
            anoms[a["coin"]].append(a)

    out = []
    for coin, sigs in smart.items():
        weight = {"long": 0, "short": 0}
        for side, _, w in sigs:
            weight[side] += w
        if weight["long"] == weight["short"]:
            continue  # smart money split on this coin
        side = "long" if weight["long"] > weight["short"] else "short"
        mine = [(t, w) for s, t, w in sigs if s == side]
        market = _market_signals(anoms.get(coin, []))
        if not market and len(mine) < 2:
            continue  # one smart signal alone: dashboard, not the read
        marks = {m for _, m in market if m}
        parts = [f"{coin} {side}"]
        if tag := _tag(side, marks):
            parts.append(tag)
        parts += [t for t, _ in sorted(mine, key=lambda x: -x[1])[:2]]
        parts += [t for t, _ in market[:2]]
        out.append({"coin": coin, "side": side, "score": weight[side] + len(market),
                    "text": " · ".join(parts)})
    return sorted(out, key=lambda s: -s["score"])[:limit]


# ---------------------------------------------------------------- format --


def format_read(sent: dict | None, delta: dict | None, derive: list[dict], stories: list[dict],
                now: dt.datetime, since: dt.datetime | None) -> str:
    head = f"Outrider · {now.strftime('%H:%M')} UTC"
    if since:
        h = max(1, round((now - since).total_seconds() / 3600))
        head += f" · last {h}h"
    lines = [head]
    if sent:
        lines.append(f"Smart money: {'balanced' if sent['lean'] == 'balanced' else 'leaning ' + sent['lean']}")
        lines.append(f"Hyperliquid: shorts {usd(sent['short_usd']).lstrip('+')} vs longs "
                     f"{usd(sent['long_usd']).lstrip('+')} · opened 24h: {sent['opens_short_24h']} short / "
                     f"{sent['opens_long_24h']} long")
        if delta:
            t = dt.datetime.fromtimestamp(delta["since"], dt.timezone.utc).strftime("%H:%M")
            lines.append(f"Since {t}: shorts {usd(delta['short_usd'])}, longs {usd(delta['long_usd'])}")
    if derive:
        lines.append("Derive options: " + " · ".join(f"{d['coin']} {d['lean']}" for d in derive))
    lines += ["", "Where it's crowding"]
    lines += [s["text"] for s in stories] or ["Nothing lining up: no coin where smart money and market data agree."]
    return "\n".join(lines)
