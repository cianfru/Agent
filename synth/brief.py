"""The daily brief's fixed sections, built in code from the snapshots (no model, so no wrong numbers).

  Derive      BTC / ETH / HYPE: which way smart option wallets lean, plus the number behind it
  Hyperliquid where profitable traders are opening today: fast convergences first, then busy coins
The model only adds up to 3 setups on top (synth/llm.py `Setups`).
"""

from __future__ import annotations

import datetime as dt
import time

DERIVE_COINS = ("BTC", "ETH", "HYPE")
LEAN = {"up": "long", "defensive": "short", "neutral": "flat"}


def usd(x: float | None) -> str:
    if x is None:
        return "-"
    a = abs(x)
    s = f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:.0f}"
    return ("+" if x > 0 else "-" if x < 0 else "") + s


def derive_lines(cowboy: dict | None, coins: tuple[str, ...] = DERIVE_COINS) -> list[dict]:
    """One line per coin: smart-wallet lean (7d, else 30d), net delta, and today's call vs put buying."""
    out = []
    for coin in coins:
        c = ((cowboy or {}).get("coins") or {}).get(coin)
        if not c:
            continue
        w7, w30 = c.get("wallets_7d") or {}, c.get("wallets_30d") or {}
        w = w7 if w7.get("state") else w30
        lean = LEAN.get(w.get("state") or "", "flat")
        parts = [f"{coin} {lean}"]
        if w.get("net_delta_usd") is not None:
            parts.append(f"wallets {usd(w['net_delta_usd'])} {'7d' if w is w7 else '30d'}")
        flow = c.get("taker_net_24h_usd") or {}
        call, put = flow.get("call"), flow.get("put")
        if call is not None and put is not None and max(abs(call), abs(put)) >= 250_000:
            parts.append("calls bought" if call - put > 0 else "puts bought")
        out.append({"coin": coin, "lean": lean, "net_usd": w.get("net_delta_usd"),
                    "text": " · ".join(parts)})
    return out


def hl_today(reflex: dict | None, fast_hours: float = 6, limit: int = 5, now: float | None = None) -> list[dict]:
    """Where Hyperliquid's profitable traders are opening: convergences of the last 24h, then busy coins."""
    if not reflex:
        return []
    now = now or time.time()
    rows: dict[str, dict] = {}
    for c in reflex.get("convergences") or []:
        if not c.get("coin") or (c.get("ts") and now - c["ts"] > 86400):
            continue
        span = c.get("span_h")
        fast = span is not None and span <= fast_hours
        when = f" in {span:g}h" if span is not None else ""
        w = 100 + 10 * (c.get("n") or 2) + (50 if fast else 0)
        if c["coin"] not in rows or rows[c["coin"]]["weight"] < w:
            rows[c["coin"]] = {"coin": c["coin"], "side": c.get("side"), "weight": w, "fast": fast,
                               "text": f"{c['coin']} {c.get('side')} · {c.get('n')} traders converged{when}"}
    for coin, v in (reflex.get("coins") or {}).items():
        o = v.get("opens_24h") or {}
        lo, sh = o.get("long") or 0, o.get("short") or 0
        if lo + sh < 3 or coin in rows:
            continue
        side = "long" if lo >= 2 * max(sh, 1) else "short" if sh >= 2 * max(lo, 1) else "mixed"
        rows[coin] = {"coin": coin, "side": side, "weight": lo + sh, "fast": False,
                      "text": f"{coin} {side} · {lo} new longs / {sh} shorts today"}
    return sorted(rows.values(), key=lambda r: -r["weight"])[:limit]


def format_brief(body: dict, day: dt.date | None = None) -> str:
    day = day or dt.datetime.now(dt.timezone.utc).date()
    lines = [f"Outrider · {day.strftime('%-d %b')}"]
    if body.get("derive"):
        lines += ["", "Derive smart wallets"] + [d["text"] for d in body["derive"]]
    if body.get("hyperliquid"):
        lines += ["", "Hyperliquid today"] + [h["text"] for h in body["hyperliquid"]]
    setups = body.get("setups") or []
    lines += ["", "Setups"]
    if setups:
        lines += [f"{s['coin']} {s['direction']}: {s['why']} Off if: {s['off_if']}" for s in setups]
    else:
        lines.append("None today: the data does not line up.")
    return "\n".join(lines)
