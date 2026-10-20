"""Outrider's read: the market scanned in code, reasoned over by the model, sent when it is worth reading.

Every poll the code builds one scan of the market (`scan`): BTC and ETH as direction, the market's mood, and the coins
where something is happening, each with what the crowd of profitable traders, the tracked traders (and what they added
or cut in 24 hours), big money by owner and the open winners hold, the engine's reading and Derive's options; plus the
owner's own positions (SYNTH_OWNER_WALLETS, read from Hyperliquid) with what each group does on those coins.
`changes` compares it with the scan behind the last read. When enough changed, when a group turns against one of the
owner's positions, or at the morning read, the model reads the scan (and may look closer with the chat's tools) and
writes a read: what it sees, what it makes of it, what it would do with his positions, and up to three views on coins.
It may also decide that nothing is worth his attention. Every view is logged and scored after 1, 3 and 7 days (study
S5), and that record goes back into the next read, so the agent knows how often it has been right.
"""

from __future__ import annotations

import asyncio
import bisect
import datetime as dt
import json
import logging
import time
from typing import Any

import anthropic

from . import desk, notify
from .chat import EVIDENCE, PRICES, TOOLS, cost_of
from .hyperliquid import HL
from .llm import FALLBACK_MODELS
from .position_traders import MAJORS
from .wallet_names import codename

log = logging.getLogger(__name__)

DAY = 86400
TOP_COINS = 14               # coins in a scan besides BTC, ETH and the owner's own
TRIGGER = 4                  # change points that make a read worth running
MIN_GAP_S = 2 * 3600         # between reads on market changes
OWNER_GAP_S = 30 * 60        # a group turning against one of his positions may come sooner
MAX_ROUNDS = 5               # tool rounds per read
REPEAT_S = DAY               # the same view (coin and side) is logged once a day, so repeats do not pad the record

SIGNAL = {"STRONG_LONG": "strong up-setup", "LIGHT_LONG": "leaning up", "ACCUMULATE": "base forming",
          "REVIVAL_SEED": "turning", "REVIVAL_SEED_CONFIRMED": "turn confirmed", "WAIT": "no clear setup",
          "TRIM": "stretched", "TRIM_HARD": "very stretched", "RISK_OFF": "leaning down", "LIGHT_SHORT": "leaning down",
          "NO_LONG": "up-setups blocked"}
REGIME = {"MARKUP": "trending up", "BLOWOFF": "overheated", "REACC": "cooling off", "ACCUM": "building a base",
          "MARKDOWN": "trending down", "CAP": "washed out", "FLAT": "no regime yet"}
STRENGTH = {1: "light", 2: "firm", 3: "strong"}

SYSTEM = """You are Outrider, the owner's private market agent. Through the day the code scans the market and hands \
you the scan: BTC and ETH as direction, the market's mood, the coins where something is happening (what each group of \
traders holds and how it changed, the engine's reading, Derive's options), his own positions with what each group does \
on those coins, and what changed since your last read. Think like a sharp desk analyst who knows this data cold: find \
what matters, connect it, and tell him in a few lines what you see and what you would do in his place.

""" + EVIDENCE + """

A good read:
- Leads with the one thing that matters most now, in plain words ("Tracked traders are piling into SUI shorts while \
the crowd buys the dip").
- Connects the groups: who is moving, against whom, and whether the price agrees. Calls out where tracked traders \
converge (several adding the same coin and side), where groups disagree, and where the big open winners sit.
- Weighs each piece by the evidence above and says how much it is worth. A crowd lean alone is never the story.
- Puts his positions first when a group turned against one: hold, trim, close or watch, each with the reason. He \
decides; you say what you would do and why.
- Gives at most three views on coins (long or short; light, firm or strong) and what would change each. Every view is \
logged and scored after 1, 3 and 7 days, so give only one you would stand behind. Strong needs independent groups \
agreeing before the price has run. No view is fine.
- Says what is new since your last read, and says so plainly when an earlier view of yours is now wrong.
- Sets send to false when nothing changed enough to be worth his attention (never for the morning read). He prefers \
silence to filler.

Style: plain text for a phone, short lines, no markdown, no emojis, no engine codes (say "trending up", not the code). \
Numbers only from the scan or the tools; never a price target. Name traders by codename. Headline under 90 \
characters, the read under 900. You may call the tools to look closer at a coin, a trader or the open winners before \
writing; do not call them for what the scan already holds."""

SCHEMA = {
    "type": "object",
    "properties": {
        "send": {"type": "boolean", "description": "false when nothing is worth his attention"},
        "headline": {"type": "string"},
        "read": {"type": "string"},
        "market_lean": {"type": "string", "enum": ["long", "short", "neutral"]},
        "positions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["coin", "side", "take", "why"],
            "properties": {"coin": {"type": "string"}, "side": {"type": "string", "enum": ["long", "short"]},
                           "take": {"type": "string", "enum": ["hold", "trim", "close", "watch"]},
                           "why": {"type": "string"}}}},
        "views": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["coin", "lean", "strength", "why", "changes_if"],
            "properties": {"coin": {"type": "string"}, "lean": {"type": "string", "enum": ["long", "short"]},
                           "strength": {"type": "integer", "enum": [1, 2, 3]}, "why": {"type": "string"},
                           "changes_if": {"type": "string"}}}},
    },
    "required": ["send", "headline", "read", "market_lean", "positions", "views"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------- scan --


def _at(rows: list[tuple[float, dict]], tss: list[float], ts: float, within: float = 3 * 3600) -> dict | None:
    """The series row nearest `ts`, or None when there is none within `within` seconds."""
    if not rows:
        return None
    i = bisect.bisect_left(tss, ts)
    best = min((j for j in (i - 1, i) if 0 <= j < len(rows)), key=lambda j: abs(tss[j] - ts))
    return rows[best][1] if abs(tss[best] - ts) <= within else None


def _sig(x: float | None) -> float | None:
    """A price to 5 significant figures: enough to read, no false precision for the model to repeat."""
    return float(f"{x:.5g}") if x else x


def _pct(now: float | None, then: float | None) -> float | None:
    return round(100 * (now / then - 1), 1) if now and then else None


def _crowd(v: list | None) -> dict | None:
    return {"long": v[0], "short": v[2], "long_usd": round(v[1]), "short_usd": round(v[3])} if v else None


def _engine(snap: dict, cow: dict, coin: str) -> dict | None:
    r = (snap.get("coins") or {}).get(coin) or (cow.get("coins") or {}).get(coin) or {}
    e = (snap.get("engine") or {}).get(coin) or {}
    out = {"daily": SIGNAL.get(r.get("signal_1d") or e.get("1d") or "", None),
           "4h": SIGNAL.get(r.get("signal_4h") or e.get("4h") or "", None),
           "regime": REGIME.get(r.get("regime_1d") or "", None), "heat": r.get("heat_1d")}
    out = {k: v for k, v in out.items() if v is not None}
    return out or None


def _options(cow: dict, coin: str) -> dict | None:
    c = (cow.get("coins") or {}).get(coin)
    if not c:
        return None
    lean = {"up": "long", "defensive": "short", "neutral": "flat"}
    w7, w30 = c.get("wallets_7d") or {}, c.get("wallets_30d") or {}
    out = {"wallets_7d": lean.get(w7.get("state") or ""), "wallets_30d": lean.get(w30.get("state") or ""),
           "wallets_net_delta_usd": w7.get("net_delta_usd") if w7.get("state") else w30.get("net_delta_usd"),
           "options_7d": lean.get((c.get("options_7d") or {}).get("state") or ""),
           "skew_30d": c.get("rr25_30d"), "iv_30d": c.get("atm_iv_30d"), "funding_ann": c.get("funding_ann")}
    return {k: v for k, v in out.items() if v is not None} or None


def _against(side: str, row: dict) -> list[str]:
    """What the groups that have tested better than the crowd do against one of his positions (S2: the crowd's lean
    moves against the last move, so it is shown but never counted here)."""
    other = "short" if side == "long" else "long"
    out = []
    t = row.get("tracked") or {}
    if t.get("lean") == other:
        out.append(f"tracked traders lean {other} ({t[other]} {other} vs {t[side]} {side})")
    a = row.get("tracked_24h") or {}
    if (a.get(other) or {}).get("adders", 0) >= 2:
        out.append(f"{a[other]['adders']} tracked traders added {other}s in 24h")
    if (a.get(side) or {}).get("cutters", 0) >= 2:
        out.append(f"{a[side]['cutters']} tracked traders cut their {side}s in 24h")
    if (row.get("big_money") or {}).get("lean") == other:
        out.append(f"big money leans {other}")
    w = row.get("open_winners") or {}
    if w.get(other, 0) >= 3 and w.get(other, 0) >= 2 * w.get(side, 0):
        out.append(f"{w[other]} of the big open winners are {other}")
    return out


def scan(agent, now: float | None = None) -> dict:
    """One picture of the market for a read, built from what the agent already stores."""
    now = now or time.time()
    store, pt = agent.store, agent.positions
    snap = store.get_snapshot("reflex") or {}
    cow = store.get_snapshot("cowboy") or {}
    rows = store.series("reflex", now - 7 * DAY - 3 * 3600)
    tss = [r[0] for r in rows]
    cur = (rows[-1][1].get("c") or {}) if rows else {}
    cur = cur or snap.get("positions") or {}
    day = (_at(rows, tss, now - DAY) or {}).get("c") or {}
    week = (_at(rows, tss, now - 7 * DAY) or {}).get("c") or {}
    mids = getattr(agent.tracker, "last_mids", {}) or {}

    board = pt.board(now)
    home = desk.home(agent, now, board)
    tracked = {c["coin"]: c for c in board["coins"]}
    bm = pt.big_money()
    add24: dict[str, dict] = {}
    for r in pt.adding(24, now):
        add24.setdefault(r["coin"], {})[r["side"]] = {k: r[k] for k in ("adders", "cutters", "net_usd")}
    winners: dict[str, dict] = {}
    for w in pt.open_winners(now, limit=500):
        x = winners.setdefault(w["coin"], {"long": 0, "short": 0, "long_upnl": 0, "short_upnl": 0})
        x[w["side"]] += 1
        x[f"{w['side']}_upnl"] += w["upnl"]
    alerts = _alerts(store, now - DAY)
    owner = store.get_snapshot("owner_positions") or {}
    held = {c: p for w in (owner.get("wallets") or {}).values() for c, p in (w.get("positions") or {}).items()}

    def row(coin: str) -> dict:
        c, d, wk = cur.get(coin), day.get(coin), week.get(coin)
        px = mids.get(coin) or (c[4] if c and len(c) > 4 else None)
        r: dict[str, Any] = {"coin": coin, "price": _sig(px),
                             "change_24h_pct": _pct(px, d[4] if d and len(d) > 4 else None),
                             "change_7d_pct": _pct(px, wk[4] if wk and len(wk) > 4 else None)}
        if c:
            r["crowd"] = {**_crowd(c), "lean": desk.lean(c[0], c[2])}
            if d:
                r["crowd"]["long_24h_ago"], r["crowd"]["short_24h_ago"] = d[0], d[2]
        if coin in tracked:
            t = tracked[coin]
            r["tracked"] = {"long": t["long_n"], "short": t["short_n"], "long_usd": round(t["long_usd"]),
                            "short_usd": round(t["short_usd"]), "lean": desk.lean(t["long_n"], t["short_n"]),
                            "median_held_days": t["median_held_days"]}
        if coin in add24:
            r["tracked_24h"] = add24[coin]
        if coin in bm:
            b = bm[coin]
            r["big_money"] = {"long": b["long_n"], "short": b["short_n"], "long_usd": round(b["long_usd"]),
                              "short_usd": round(b["short_usd"]), "lean": desk.lean(b["long_n"], b["short_n"])}
        if coin in winners:
            w = winners[coin]
            r["open_winners"] = {**w, "long_upnl": round(w["long_upnl"]), "short_upnl": round(w["short_upnl"])}
        for k, v in (("engine", _engine(snap, cow, coin)), ("options", _options(cow, coin)),
                     ("alerts_24h", alerts.get(coin))):
            if v:
                r[k] = v
        return r

    def interest(r: dict) -> float:
        a = r.get("tracked_24h") or {}
        cr = r.get("crowd") or {}
        swing = abs((cr.get("long", 0) - cr.get("long_24h_ago", cr.get("long", 0)))
                    - (cr.get("short", 0) - cr.get("short_24h_ago", cr.get("short", 0))))
        t, b, w = r.get("tracked") or {}, r.get("big_money") or {}, r.get("open_winners") or {}
        return (2 * sum(x.get("adders", 0) + x.get("cutters", 0) for x in a.values()) + 0.5 * swing
                + w.get("long", 0) + w.get("short", 0) + 3 * len(r.get("alerts_24h") or [])
                + 0.3 * (t.get("long", 0) + t.get("short", 0)) + 0.1 * (b.get("long", 0) + b.get("short", 0))
                + (3 if abs(r.get("change_24h_pct") or 0) >= 10 else 0))

    names = (set(cur) | set(tracked) | set(bm) | set(add24) | set(winners)) - set(MAJORS)
    ranked = sorted((row(c) for c in names), key=lambda r: -interest(r))
    coins = [r for r in ranked if interest(r) > 0][:TOP_COINS]
    shown = {r["coin"] for r in coins}
    coins += [row(c) for c in sorted(held) if c not in shown and c not in MAJORS]

    direction = []
    for d in home.get("direction") or []:
        r = row(d["coin"])
        r.update({"groups_agree": d["agree"], "crowd_change_24h_pts": d["crowd_change_24h"],
                  "tracked_added_24h_usd": d["tracked_added_24h"],
                  "lenses": {k: {x: v.get(x) for x in ("long_n", "short_n", "share_long", "lean")}
                             for k, v in (d.get("lenses") or {}).items()}})
        direction.append(r)

    mine = []
    by_coin = {r["coin"]: r for r in coins + direction}
    for addr, w in (owner.get("wallets") or {}).items():
        eq = w.get("equity") or 0
        for c, p in (w.get("positions") or {}).items():
            r = by_coin.get(c) or row(c)
            value = round(p.get("notional") or 0)
            mine.append({"wallet": addr[:6], "coin": c, "side": p["side"], "value": value,
                         "share_of_account": round(value / eq, 2) if eq else None, "upnl": round(p.get("upnl") or 0),
                         "entry": _sig(p.get("entry_px")), "price": r.get("price"), "lev": p.get("lev"),
                         "roe": p.get("roe"), "liq": _sig(p.get("liq")), "against": _against(p["side"], r)})

    k = home["kpi"]
    cy = k.get("cycle") or {}
    return {
        "ts": now,
        "time": time.strftime("%a %d %b %H:%M UTC", time.gmtime(now)),
        "market": {
            "crowd_long_pct": k["crowd"].get("share_long"), "crowd_change_24h_pts": k["crowd"].get("change_24h"),
            "big_money_long_pct": k["big_money"].get("share_long"), "tracked_long_pct": k["tracked"].get("share_long"),
            "tracked_traders": k["tracked"].get("traders"),
            "btc": {**(k.get("btc") or {}), "price": _sig((k.get("btc") or {}).get("price"))},
            "cycle": {x: cy.get(x) for x in ("type", "age_days", "uptrend", "median_days", "reached_age",
                                             "lasted_30_more")} if cy else None,
            "reflex": snap.get("market"), "cowboy": cow.get("market"),
        },
        "direction": direction,
        "coins": coins,
        "my_positions": mine,
        "alerts_24h": [a["text"] for a in sorted(home.get("alerts") or [], key=lambda a: -a["ts"])
                       if a["ts"] >= now - DAY][:12],
        "data_age_min": round((now - tss[-1]) / 60) if tss else None,
    }


def _alerts(store, since: float) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for kind in ("pt_build", "pt_alert"):
        for b in store.briefs_since(kind, since):
            body = b["body"]
            if not body.get("coin"):
                continue
            text = body.get("text") or (f"conviction: {codename(body.get('address', ''))} {body['side']} "
                                        f"{body.get('x') or ''}x usual size")
            out.setdefault(body["coin"], []).append(text.split("\n")[0][:140])
    for g in store.groups(limit=30):
        if g["ts"] >= since:
            out.setdefault(g["coin"], []).append(f"{(g['meta'] or {}).get('n') or ''} profitable traders opened "
                                                 f"{g['side']} together")
    return out


# ------------------------------------------------------------------- changes --


def _lean_word(x: str | None) -> str:
    return x or "no lean"


def changes(prev: dict | None, cur: dict) -> list[dict]:
    """What moved since the scan behind the last read, each with a weight; `owner` marks his positions."""
    if not prev:
        return [{"w": TRIGGER, "text": "first read", "owner": False}]
    out: list[dict] = []

    def add(w: int, text: str, owner: bool = False) -> None:
        out.append({"w": w, "text": text, "owner": owner})

    pm, cm = prev.get("market") or {}, cur.get("market") or {}
    for key, label, step in (("crowd_long_pct", "profitable traders", 5), ("big_money_long_pct", "big money", 8),
                             ("tracked_long_pct", "tracked traders", 8)):
        a, b = pm.get(key), cm.get(key)
        if a is not None and b is not None and abs(b - a) >= step:
            add(3 if abs(b - a) >= 2 * step else 2, f"{label} {b - a:+.0f} points long ({a:.0f}% to {b:.0f}%)")
    before = {r["coin"]: r for r in (prev.get("direction") or []) + (prev.get("coins") or [])}
    for d in cur.get("direction") or []:
        p = before.get(d["coin"]) or {}
        mv = _pct(d.get("price"), p.get("price"))
        if mv is not None and abs(mv) >= (3 if d["coin"] == "BTC" else 4):
            add(3 if abs(mv) >= 6 else 2, f"{d['coin']} {mv:+.1f}% since the last read")
        if p.get("groups_agree") and d.get("groups_agree") != p.get("groups_agree"):
            add(2, f"{d['coin']}: the groups now read {d['groups_agree']} (were {p['groups_agree']})")
    for r in cur.get("coins") or []:
        p = before.get(r["coin"])
        if not p:
            continue
        for group, w in (("tracked", 2), ("big_money", 1), ("crowd", 1)):
            a, b = (p.get(group) or {}).get("lean"), (r.get(group) or {}).get("lean")
            if a != b and b in ("long", "short"):
                add(w, f"{r['coin']}: {group.replace('_', ' ')} now lean {b} (were {_lean_word(a)})")
        for side in ("long", "short"):
            n = ((r.get("tracked_24h") or {}).get(side) or {}).get("adders", 0)
            n0 = ((p.get("tracked_24h") or {}).get(side) or {}).get("adders", 0)
            if n >= 3 and n0 < 3:
                add(2, f"{r['coin']}: {n} tracked traders added {side}s in 24h")
        mv = _pct(r.get("price"), p.get("price"))
        if mv is not None and abs(mv) >= 10:
            add(1, f"{r['coin']} {mv:+.0f}% since the last read")
    seen = set(prev.get("alerts_24h") or [])
    new_alerts = [a for a in cur.get("alerts_24h") or [] if a not in seen]
    if new_alerts:
        add(min(3, len(new_alerts)), f"{len(new_alerts)} new alerts: " + "; ".join(new_alerts[:3]))
    mine0 = {(m["coin"], m["side"]): m for m in prev.get("my_positions") or []}
    mine1 = {(m["coin"], m["side"]): m for m in cur.get("my_positions") or []}
    for k in mine1.keys() - mine0.keys():
        add(1, f"you opened {k[0]} {k[1]}", owner=True)
    for k in mine0.keys() - mine1.keys():
        add(1, f"you closed {k[0]} {k[1]}", owner=True)
    for k, m in mine1.items():
        old = set((mine0.get(k) or {}).get("against") or [])
        fresh = [a for a in m.get("against") or [] if a not in old]
        if fresh:
            add(4, f"your {k[0]} {k[1]}: " + "; ".join(fresh), owner=True)
        m0 = mine0.get(k)
        if m0 and m0.get("value") and m.get("value"):
            pnl_move = (m["upnl"] - m0["upnl"]) / m["value"]
            if abs(pnl_move) >= 0.05:
                add(2, f"your {k[0]} {k[1]} is {100 * pnl_move:+.0f}% on its value since the last read", owner=True)
    return out


# -------------------------------------------------------------------- record --


def record(store) -> dict:
    """How the read's views did: share right and the median move their way, by horizon (all, and firm or strong)."""
    picks = store.picks(limit=100_000, origin="read")
    out: dict[str, Any] = {"logged": len(picks)}
    for h in ("1d", "3d", "7d"):
        row = {}
        for name, sel in (("all", picks), ("firm_or_strong", [p for p in picks if (p.get("conviction") or 0) >= 2])):
            v = sorted(p[f"ret_{h}"] for p in sel if p.get(f"ret_{h}") is not None)
            row[name] = {"n": len(v), "right": sum(x > 0 for x in v),
                         "median_pct": round(100 * v[len(v) // 2], 2) if v else None}
        out[h] = row
    return out


def record_text(rec: dict) -> str | None:
    a = rec["3d"]["all"]
    if a["n"] < 5:
        return None
    s = rec["3d"]["firm_or_strong"]
    line = (f"My record: {a['right']} of {a['n']} views right after 3 days ({round(100 * a['right'] / a['n'])}%), "
            f"median {a['median_pct']:+.1f}% their way")
    if s["n"] >= 5:
        line += f"; firm or strong {s['right']} of {s['n']}"
    return line + "."


# --------------------------------------------------------------------- read --


def format_read(r: dict, now: float, rec: dict | None = None) -> str:
    lines = [f"Outrider · {time.strftime('%H:%M', time.gmtime(now))} UTC", r["headline"].strip(), "", r["read"].strip()]
    if r.get("positions"):
        lines += ["", "Your positions"]
        lines += [f"{p['coin']} {p['side']}: {p['take']}. {p['why'].strip()}" for p in r["positions"]]
    if r.get("views"):
        lines += ["", "Views (scored after 1, 3 and 7 days)"]
        for v in r["views"]:
            lines.append(f"{v['coin']} {v['lean']}, {STRENGTH.get(v['strength'], 'light')}: {v['why'].strip()} "
                         f"Changes if {v['changes_if'].strip().rstrip('.')}.")
    rt = record_text(rec) if rec else None
    if rt:
        lines += ["", rt]
    return "\n".join(lines)


def _day_start(now: float) -> float:
    d = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def quiet_hours(spec: str) -> set[int]:
    """'22-6' -> {22, 23, 0, ..., 5}; empty -> none."""
    if not spec or "-" not in spec:
        return set()
    a, b = (int(x) % 24 for x in spec.split("-", 1))
    return {h % 24 for h in range(a, a + ((b - a) % 24))}


class Analyst:
    def __init__(self, agent, model: str, effort: str = "high", daily_usd: float = 1.5, per_day: int = 6,
                 quiet: str = "22-6", client: anthropic.Anthropic | None = None):
        self.agent = agent
        self.store = agent.store
        self.model = model
        self.effort = effort
        self.daily_usd = daily_usd
        self.per_day = per_day
        self.quiet = quiet_hours(quiet)
        self.client = client or anthropic.Anthropic()

    # ------------------------------------------------------------- schedule --
    def spent_today(self, now: float | None = None) -> float:
        return sum(b["body"].get("cost") or 0 for b in self.store.briefs_since("read", _day_start(now or time.time())))

    def due(self, now: float, cur: dict) -> tuple[str | None, list[dict]]:
        """Why a read should run now (None when it should not), and what changed."""
        hour = dt.datetime.fromtimestamp(now, dt.timezone.utc).hour
        today = self.store.briefs_since("read", _day_start(now))
        ch = changes(self.store.get_snapshot("read_scan"), cur)
        mornings = [b["body"] for b in today if b["body"].get("reason") == "morning"]
        if hour >= self.agent.s.brief_hour_utc and not any(not m.get("error") for m in mornings) and len(mornings) < 3:
            return "morning", ch
        if self.spent_today(now) >= self.daily_usd or sum(1 for b in today if b["body"].get("sent")) >= self.per_day:
            return None, ch
        last = self.store.latest_brief("read")
        gap = now - last["ts"] if last else float("inf")
        if any(c["owner"] and c["w"] >= 4 for c in ch) and gap >= OWNER_GAP_S:
            return "your position", ch
        if hour not in self.quiet and sum(c["w"] for c in ch) >= TRIGGER and gap >= MIN_GAP_S:
            return "market changed", ch
        return None, ch

    async def step(self, now: float | None = None) -> dict | None:
        """Each poll: read his wallets, scan, and run a read when one is due."""
        now = now or time.time()
        await self.read_owner(now)
        cur = await asyncio.to_thread(scan, self.agent, now)
        reason, ch = self.due(now, cur)
        if not reason:
            return None
        return await self.run(reason, now, cur, ch)

    async def read_owner(self, now: float) -> None:
        wallets = self.agent.s.owner_wallets
        if not wallets:
            return
        hl = HL()
        try:
            got = {}
            for a in wallets:
                try:
                    got[a.lower()] = await hl.account(a)
                except Exception as e:  # noqa: BLE001 - one wallet down keeps the last reading
                    log.warning("owner wallet read failed: %s", e)
                    old = ((self.store.get_snapshot("owner_positions") or {}).get("wallets") or {}).get(a.lower())
                    if old:
                        got[a.lower()] = old
            self.store.put_snapshot("owner_positions", {"ts": now, "wallets": got})
        finally:
            await hl.aclose()

    # ----------------------------------------------------------------- read --
    async def run(self, reason: str, now: float | None = None, cur: dict | None = None,
                  ch: list[dict] | None = None, force_send: bool = False) -> dict:
        now = now or time.time()
        if cur is None:
            cur = await asyncio.to_thread(scan, self.agent, now)
        if ch is None:
            ch = changes(self.store.get_snapshot("read_scan"), cur)
        rec = record(self.store)
        task = self.task(reason, cur, ch, self.store.latest_brief("read"), rec)
        try:
            out, cost = await asyncio.to_thread(self.think, task, now)
        except Exception as e:  # noqa: BLE001 - logged; the next poll can try again
            log.warning("read failed: %s", e)
            self.store.add_brief("read", {"reason": reason, "sent": False, "error": str(e)[:300], "cost": 0})
            raise
        out = self.clean(out)
        send = bool(out["send"]) or reason == "morning" or force_send
        text = format_read(out, now, rec)
        logged = self.log_views(out["views"], now, reason) if send else []
        self.store.put_snapshot("read_scan", cur)
        self.store.add_brief("read", {**out, "reason": reason, "sent": send, "text": text, "cost": round(cost, 4),
                                      "changes": [c["text"] for c in ch], "logged": logged})
        if send:
            await notify.telegram(self.agent.s.telegram_token, self.agent.s.telegram_chat_id, text)
        return {**out, "reason": reason, "sent": send, "text": text, "cost": round(cost, 4), "logged": logged}

    def task(self, reason: str, cur: dict, ch: list[dict], last: dict | None, rec: dict) -> str:
        notes = self.store.chat_notes()
        prev = None
        if last and last.get("headline"):
            prev = {k: last.get(k) for k in ("headline", "read", "views", "positions", "market_lean", "sent")}
            prev["when"] = time.strftime("%a %H:%M UTC", time.gmtime(last["ts"]))
        parts = [f"Why this read runs: {reason}." + (" This is the morning read: always send it and set up the day."
                                                     if reason == "morning" else ""),
                 "<changes_since_last_read>\n" + ("\n".join(f"- {c['text']}" for c in ch) or "- nothing material")
                 + "\n</changes_since_last_read>",
                 f"<your_last_read>\n{json.dumps(prev) if prev else 'none yet'}\n</your_last_read>",
                 f"<your_record>\n{json.dumps(rec)}\n</your_record>",
                 "<notes_on_him>\n" + ("\n".join(n["text"] for n in notes) or "none") + "\n</notes_on_him>",
                 f"<scan>\n{json.dumps(cur, default=str, separators=(',', ':'))}\n</scan>"]
        if not cur.get("my_positions"):
            parts.append("His wallets are not connected (or hold nothing): use the notes for what he holds, if any.")
        return "\n\n".join(parts)

    def think(self, task: str, now: float) -> tuple[dict, float]:
        tools = [t for t in TOOLS if t["name"] not in ("remember", "forget")]
        messages: list[dict] = [{"role": "user", "content": task}]
        cost = 0.0
        for rnd in range(MAX_ROUNDS):
            resp = self._create(messages, tools)
            cost += cost_of(resp.model if resp.model in PRICES else self.model, resp.usage)
            if resp.stop_reason == "refusal":
                raise RuntimeError("the model declined this read")
            if resp.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": resp.content})
                results: list[dict] = [{"type": "tool_result", "tool_use_id": b.id,
                                        "content": self.agent.chat.execute(b.name, b.input, now)}
                                       for b in resp.content if b.type == "tool_use"]
                if rnd == MAX_ROUNDS - 2:
                    results.append({"type": "text", "text": "Write the read now with what you have; no more tools."})
                messages.append({"role": "user", "content": results})
                continue
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return json.loads(text), cost
        raise RuntimeError("the read took too many steps")

    def _create(self, messages: list[dict], tools: list[dict]):
        params = {"model": self.model, "max_tokens": 16000, "tools": tools, "messages": messages,
                  "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                  "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}}}
        if self.model in FALLBACK_MODELS:
            return self.client.beta.messages.create(**params, betas=["server-side-fallback-2026-07-01"],
                                                    fallbacks="default")
        return self.client.messages.create(**params)

    @staticmethod
    def clean(out: dict) -> dict:
        views = []
        for v in (out.get("views") or [])[:3]:
            coin = str(v.get("coin") or "").upper().lstrip("$")
            if coin and v.get("lean") in ("long", "short"):
                views.append({**v, "coin": coin, "strength": int(v.get("strength") or 1)})
        pos = [{**p, "coin": str(p.get("coin") or "").upper()} for p in out.get("positions") or [] if p.get("coin")]
        return {**out, "views": views, "positions": pos}

    def log_views(self, views: list[dict], now: float, reason: str) -> list[int]:
        """Each view as a pick at Hyperliquid's mid, scored after 1, 3 and 7 days; a repeat within a day is not
        logged again."""
        from .service import price_of
        snaps = {n: s for n in ("cowboy", "reflex") if (s := self.store.get_snapshot(n))}
        recent = {(p["coin"], p["direction"]) for p in self.store.picks(limit=200, origin="read", since=now - REPEAT_S)}
        ids = []
        for v in views:
            if (v["coin"], v["lean"]) in recent:
                continue
            px = (getattr(self.agent.tracker, "last_mids", {}) or {}).get(v["coin"]) or price_of(v["coin"], snaps)
            if not px:
                continue
            ids.append(self.store.add_pick("read", v["coin"], v["lean"], v["strength"], px,
                                           v["why"][:300], {"changes_if": v["changes_if"], "reason": reason}))
            recent.add((v["coin"], v["lean"]))
        return ids
