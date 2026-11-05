"""Outrider's read: the market scanned in code, reasoned over by the model, sent when it is worth reading.

Every poll the code builds one scan of the market (`scan`): BTC and ETH as direction, the market's mood, and the coins
where something is happening, each with what the crowd of profitable traders, the tracked traders (and what they added
or cut in 24 hours), big money by owner and the open winners hold, the engine's reading and Derive's options; plus the
owner's own positions (SYNTH_OWNER_WALLETS, read from Hyperliquid) and watch list. When a read runs, each coin also gets
its price action (levels.py: 200-day average, bull market support band, recent high, swing low, how much of the last
leg it has retraced) and the chart patterns Reflex finds on its daily chart, with their track record.
`changes` compares the scan with the one behind the last read. When enough changed in the market, or at the morning
read, the model reads the scan in one call (no tools: the scan holds what a read needs, which keeps a read to a few
cents) and writes a read: what it sees and up to two setups worth a look. His positions are his decisions (owner,
3 November 2026): reads do not brief him on them or tell him what to do with them; he asks in the chat (/mine), and a
big move against one (a proven trader's conviction bet, a build-up of tracked traders) is a short note of its own
(position_traders.py).
It may also decide that nothing is worth his attention. Every view is logged and scored after 1, 3 and 7 days (study
S5), and that record goes back into the next read, so the agent knows how often it has been right.
"""

from __future__ import annotations

import asyncio
import bisect
import datetime as dt
import json
import logging
import random
import re
import time
from typing import Any

import anthropic

from . import desk, journal, notify, screen
from .sources import reflex as reflex_src
from .chat import EVIDENCE, PRICES, cost_of
from .hyperliquid import HL
from .levels import trend_view
from .llm import FALLBACK_MODELS
from .position_traders import MAJORS
from .wallet_names import codename

log = logging.getLogger(__name__)

DAY = 86400
TOP_COINS = 10               # coins in a scan besides BTC, ETH and the owner's own
TRIGGER = 4                  # change points that make a read worth running
MIN_GAP_S = 2 * 3600         # between reads on market changes
REPEAT_S = DAY               # the same view (coin and side) is logged once a day, so repeats do not pad the record

SIGNAL = {"STRONG_LONG": "strong up-setup", "LIGHT_LONG": "leaning up", "ACCUMULATE": "base forming",
          "REVIVAL_SEED": "turning", "REVIVAL_SEED_CONFIRMED": "turn confirmed", "WAIT": "no clear setup",
          "TRIM": "stretched", "TRIM_HARD": "very stretched", "RISK_OFF": "leaning down", "LIGHT_SHORT": "leaning down",
          "NO_LONG": "up-setups blocked"}
REGIME = {"MARKUP": "trending up", "BLOWOFF": "overheated", "REACC": "cooling off", "ACCUM": "building a base",
          "MARKDOWN": "trending down", "CAP": "washed out", "FLAT": "no regime yet"}
STRENGTH = {1: "light", 2: "firm", 3: "strong"}
OFF_LEVELS = ("band low", "band high", "200-day average", "swing low", "swing high", "recent high", "pattern", "event")
TURN_MARKS = {"median_z_turned_down", "rolled_over", "median_z_turned_up", "turned_up"}   # the cycle's own turns

SYSTEM = """You are Outrider, the owner's market companion. Through the day the code scans the market and hands \
you the scan: BTC and ETH as direction, the market's mood and where it stands in its cycle (market.turns: a broad \
uptrend or a downturn, its day, its marks), the coins where something is happening (what each group of traders holds \
and how it changed, the engine's reading, open interest and funding, price action: where the price sits against its \
200-day average, its bull market support band, its recent high and swing low and how much of its last leg it has \
retraced; chart patterns forming or just confirmed, with their track record; Derive's options), the candidates (the \
best setups the code found across the 100 most traded coins: where the price sits against its band, its 200-day \
average and its last leg, who is positioning, the engine, with a score and the reasons; a ranking, not a signal), the \
market's breadth (how many coins sit under, inside and over their band and over their 200-day average), his \
positions and watch list, and what changed since your last read. Think like a sharp trader friend who knows this data cold: find \
what matters, connect it, and tell him in a few lines what is happening and which setups are worth a look.

""" + EVIDENCE + """

A good read:
- Leads with the one thing that matters most now, in plain words.
- Connects the traders and the chart: who is moving, on which side, and what the price is doing where.
- Weighs each piece by the evidence above. A crowd lean alone is never the story, nor is the tracked traders' lean.
- Picks its setups mostly from the candidates, checking the code's reasons against the rest of the scan; a coin \
from elsewhere in the scan is fine when it is better and two things line up for it too.
- Gives up to two setups worth a look (views): a coin, long or short, why, and what to look at on the chart (the band, \
the 200-day average, the swing low, a pattern's neckline). A setup is worth sending when at least two independent \
things line up: traders positioning (above all a proven trader's conviction bet, or several tracked traders adding; \
crowded funding or open interest against the price), price at a level (a pullback into the band, the 200-day average \
or a swing low after a real retracement; a stretched z-score for a short; a pattern forming or just confirmed), or \
either of those with the engine. The code counts them for the candidates (lined_up, independent); every candidate \
has two or more. A short the owner watches most: z past its blow-off line with shorts building. Do not wait for \
everything to line up: that almost never happens. Light, firm or strong says how convinced you are. Every setup is \
logged and scored after 1, 3 and 7 days. When nothing lines up, no setup; when the market is weak, say so: a setup \
still needs two things.
- Each setup names where it is wrong (off_level: the band, the 200-day average, a swing level, the recent high; the \
code fills the price and its distance from the coin's risk frame) and changes_if: the event that would end it (a \
conviction bet closed, the engine turning). Prefer setups whose room to the next level is at least the risk to the \
off level; if not, say why it is still worth a look. Two setups that are the same bet (same side, coins that move \
together, beta over 1.5 to BTC) count as one: give the better one.
- His positions are his decisions. Do not brief him on them and never tell him to close, trim or hold: he asks you in \
the chat when he wants that. Mention one only when it is part of a setup, in one factual sentence.
- Says what is new since your last read, and says so plainly when an earlier setup of yours is now wrong.
- Sets send to false when nothing changed enough to be worth his attention (never for the morning read). He prefers \
silence to filler.

Style: he reads it on a phone in ten seconds. Plain text, no markdown, no emojis, no engine codes (say "trending \
up", not the code). The headline under 80 characters. The read at most three short sentences, under 350 characters: \
the story and what it means, not a list of figures. Each setup's why under 140 characters and its chart note under \
90. Never repeat in one part what another already says. Numbers only from the scan. A level on the chart is fine; \
never say where the price will go. If he wants more, he asks you in the chat."""

SCHEMA = {
    "type": "object",
    "properties": {
        "send": {"type": "boolean", "description": "false when nothing is worth his attention"},
        "headline": {"type": "string"},
        "read": {"type": "string"},
        "market_lean": {"type": "string", "enum": ["long", "short", "neutral"]},
        "views": {"type": "array", "description": "setups worth a look", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["coin", "lean", "strength", "why", "chart", "off_level", "changes_if"],
            "properties": {"coin": {"type": "string"}, "lean": {"type": "string", "enum": ["long", "short"]},
                           "strength": {"type": "integer", "enum": [1, 2, 3]}, "why": {"type": "string"},
                           "chart": {"type": "string", "description": "what to look at on the chart"},
                           "off_level": {"type": "string", "enum": list(OFF_LEVELS),
                                         "description": "the level where the setup is wrong; the code fills the price"},
                           "changes_if": {"type": "string", "description": "the event that would end it"}}}},
    },
    "required": ["send", "headline", "read", "market_lean", "views"],
    "additionalProperties": False,
}


class ReadFailed(RuntimeError):
    """A read that failed after the model was called: what it cost still counts."""

    def __init__(self, msg: str, cost: float = 0.0):
        super().__init__(msg)
        self.cost = cost


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


def _move(side: str | None, price: float | None, entry: float | None) -> float | None:
    """How far a position has moved its way since entry, in percent."""
    if not side or not price or not entry:
        return None
    return round(100 * (price / entry - 1) * (1 if side == "long" else -1), 1)


def _engine(snap: dict, cow: dict, coin: str, every: dict | None = None) -> dict | None:
    r = (snap.get("coins") or {}).get(coin) or (cow.get("coins") or {}).get(coin) or {}
    e = (snap.get("engine") or {}).get(coin) or {}
    a = (every or {}).get(coin) or {}                       # every coin's reading, hourly: the z-score above all
    out = {"daily": SIGNAL.get(r.get("signal_1d") or e.get("1d") or a.get("signal_1d") or "", None),
           "4h": SIGNAL.get(r.get("signal_4h") or e.get("4h") or a.get("signal_4h") or "", None),
           "regime": REGIME.get(r.get("regime_1d") or a.get("regime_1d") or "", None),
           "regime_4h": REGIME.get(a.get("regime_4h") or "", None),
           "heat": r.get("heat_1d") if r.get("heat_1d") is not None else a.get("heat_1d"),
           "z_daily": r.get("z_1d") if r.get("z_1d") is not None else a.get("z_1d"),
           "z_4h": r.get("z_4h") if r.get("z_4h") is not None else a.get("z_4h")}
    out = {k: v for k, v in out.items() if v is not None}
    return out or None


def _words(text: str) -> str:
    """The engine's codes inside a sentence turned into words (MARKUP -> trending up)."""
    for code, word in sorted({**REGIME, **SIGNAL}.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"\b{code}\b", word, text)
    return text


def engine_words(rows: dict) -> dict:
    """Reflex's scan rows for one coin (`reflex.engine_rows`) in plain words, as the scan carries the engine."""
    d, h = rows.get("1d") or {}, rows.get("4h") or {}
    out = {"daily": SIGNAL.get(d.get("signal") or "", None), "4h": SIGNAL.get(h.get("signal") or "", None),
           "regime": REGIME.get(d.get("regime") or "", None), "regime_4h": REGIME.get(h.get("regime") or "", None),
           "heat": d.get("heat"), "zscore": round(d["zscore"], 2) if d.get("zscore") is not None else None,
           "exhaustion": str(d["exhaustion_state"]).lower().replace("_", " ")
           if d.get("exhaustion_state") not in (None, "NEUTRAL") else None,
           "why_daily": _words((d.get("signal_reason") or "")[:120]) or None, "sector": d.get("sector") or h.get("sector"),
           # the engine's own z lines for this coin: the owner's 2.5 / 3 / 3.5 scaled by its volatility (0.85x-1.25x)
           "engine_z_lines": {"blow_off": round(2.5 * d["vol_scale"], 2), "trim": round(3 * d["vol_scale"], 2),
                              "hard_trim": round(3.5 * d["vol_scale"], 2)} if d.get("vol_scale") else None}
    return {k: v for k, v in out.items() if v is not None}


def _options(cow: dict, coin: str) -> dict | None:
    c = (cow.get("coins") or {}).get(coin)
    if not c:
        return None
    lean = {"up": "long", "defensive": "short", "neutral": "flat"}
    w7, w30 = c.get("wallets_7d") or {}, c.get("wallets_30d") or {}
    iv = c.get("atm_iv_30d")
    pct = lambda x: round(100 * x, 1) if x is not None else None   # noqa: E731
    out = {"wallets_7d": lean.get(w7.get("state") or ""), "wallets_30d": lean.get(w30.get("state") or ""),
           "wallets_net_delta_usd": w7.get("net_delta_usd") if w7.get("state") else w30.get("net_delta_usd"),
           "options_7d": lean.get((c.get("options_7d") or {}).get("state") or ""),
           "skew_30d_pct": pct(c.get("rr25_30d")), "iv_30d_pct": pct(iv),
           # the move option prices imply (one standard deviation), never a forecast
           "priced_move_3d_pct": round(100 * iv * (3 / 365) ** 0.5, 1) if iv else None,
           "priced_move_7d_pct": round(100 * iv * (7 / 365) ** 0.5, 1) if iv else None,
           "derive_funding_apr_pct": pct(c.get("funding_ann"))}
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
    mids = agent.tracker.fresh_mids(now) if hasattr(agent.tracker, "fresh_mids") else {}
    every = (store.get_snapshot("engine_all") or {}).get("c") or {}

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
    watch = store.watch_list()
    held.update({w["coin"]: w for w in watch if w["coin"] not in held})

    def row(coin: str) -> dict:
        c, d, wk = cur.get(coin), day.get(coin), week.get(coin)
        px = mids.get(coin) or (c[4] if c and len(c) > 4 else None)
        r: dict[str, Any] = {"coin": coin, "price": _sig(px),
                             "change_24h_pct": _pct(px, d[4] if d and len(d) > 4 else None),
                             "change_7d_pct": _pct(px, wk[4] if wk and len(wk) > 4 else None)}
        if c:
            r["crowd"] = {"long": c[0], "short": c[2], "lean": desk.lean(c[0], c[2])}   # heads: S2 says dollars add little
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
        for k, v in (("engine", _engine(snap, cow, coin, every)), ("oi_funding", agent.levels.oi_funding(coin, now)),
                     ("options", _options(cow, coin)),
                     ("alerts_24h", (alerts.get(coin) or [])[-2:])):
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
        r.update({"groups_agree": d["agree"], "crowd_change_24h_pts": d["crowd_change_24h"]})
        direction.append(r)

    mine = []
    by_coin = {r["coin"]: r for r in coins + direction}
    for addr, w in (owner.get("wallets") or {}).items():
        eq = w.get("equity") or 0
        for c, p in (w.get("positions") or {}).items():
            r = by_coin.get(c) or row(c)
            value = round(p.get("notional") or 0)
            price = r.get("price") or _sig(p.get("mark"))   # a coin Hyperliquid does not list: the venue's own mark
            m = {"source": w.get("venue") or f"wallet {addr[:6]}", "coin": c, "side": p["side"], "value": value,
                 "share_of_account": round(value / eq, 2) if eq else None, "upnl": round(p.get("upnl") or 0),
                 "entry": _sig(p.get("entry_px")), "price": price,
                 "move_pct": _move(p["side"], price, p.get("entry_px")), "lev": p.get("lev"),
                 "roe": p.get("roe"), "liq": _sig(p.get("liq")), "against": _against(p["side"], r)}
            if p.get("funding_apr_pct") is not None:     # the venue's funding, as a year's rate (+: longs pay)
                m["funding_apr_pct"] = p["funding_apr_pct"]
            mine.append(m)
    for w in watch:
        r = by_coin.get(w["coin"]) or row(w["coin"])
        m = _move(w["side"], r.get("price"), w["entry"])
        mine.append({"source": "watch list", "coin": w["coin"], "side": w["side"], "entry": _sig(w["entry"]),
                     "price": r.get("price"), "move_pct": m, "value": w["size_usd"],
                     "upnl": round(w["size_usd"] * m / 100) if w["size_usd"] and m is not None else None,
                     "since": time.strftime("%d %b", time.gmtime(w["ts"])), "note": w["note"],
                     "against": _against(w["side"], r) if w["side"] else []})

    k = home["kpi"]
    cy = k.get("cycle") or {}
    table = screen.build(agent, now, board)
    by_row = {r["coin"]: r for r in table["rows"]}
    for r in direction + coins:
        q = by_row.get(r["coin"])
        if q:
            r["screen"] = quant(q)
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
            "turns": k.get("turns"),
            "reflex": in_words(snap.get("market")), "cowboy": in_words(cow.get("market")),
            "oi_funding": agent.levels.market(now),
            "breadth": {**table["breadth"], "coins": table["coins"], **breadth_then(store)} if table["coins"] else None,
            "z_extremes": {
                "stretched": [[r["coin"], r["z_1d"]] for r in sorted(
                    (r for r in table["rows"] if (r.get("z_1d") or 0) >= screen.Z_BLOWOFF and not r.get("thin")),
                    key=lambda r: -r["z_1d"])[:6]],
                "washed_out": [[r["coin"], r["z_1d"]] for r in sorted(
                    (r for r in table["rows"] if r.get("z_1d") is not None and r["z_1d"] <= screen.Z_CAPITULATION
                     and not r.get("thin")), key=lambda r: r["z_1d"])[:6]]} if table["coins"] else None,
        },
        "candidates": screen.candidates(table),
        "direction": direction,
        "coins": coins,
        "my_positions": mine,
        "alerts_24h": [a["text"].split("\n")[0][:100] for a in sorted(home.get("alerts") or [], key=lambda a: -a["ts"])
                       if a["ts"] >= now - DAY][:8],
        "data_age_min": round((now - tss[-1]) / 60) if tss else None,
    }


QUANT_KEYS = ("z_line", "atr_pct", "rv30_pct", "rel_vol", "near_pct", "rs_30d_pct", "rs_rank_30d", "beta_btc",
              "corr_btc", "oi_class_24h", "oi_class_7d", "spread_bps", "thin", "sector", "exhaustion", "climax",
              "new_high", "new_low", "funding_z", "funding_vs_cex_apr_pct")


def quant(row: dict) -> dict:
    """A screen row's figures a read needs beside a coin: volatility, strength against BTC, who is acting, liquidity,
    each side's score and risk frame."""
    out = {k: row[k] for k in QUANT_KEYS if row.get(k) is not None}
    if row.get("band"):
        out["band"] = row["band"]["state"]
    out["at"] = levels_at(row)
    for side in ("long", "short"):
        out[side] = {"score": row[f"score_{side}"], "independent": row[f"independent_{side}"],
                     **row.get(f"levels_{side}", {})}
    return out


def breadth_then(store) -> dict:
    """How many coins (in %) sat over their band and over their 200-day a week and a month ago, rebuilt from each
    coin's history: whether breadth is improving or rolling over."""
    by_day = {d["day"]: d for d in screen.breadth_history(store.get_snapshot("trend_levels") or {})}
    out = {}
    now = time.time()
    for label, back in (("week_ago", 7), ("month_ago", 30)):
        d = by_day.get(time.strftime("%Y-%m-%d", time.gmtime(now - back * DAY)))
        if not d:
            continue
        if d["coins"]:
            out[f"over_band_pct_{label}"] = round(100 * d["over"] / d["coins"])
        if d["with_200d"]:
            out[f"over_200d_pct_{label}"] = round(100 * d["over_200d"] / d["with_200d"])
    return out


def levels_at(row: dict) -> dict:
    """A screen row's named levels, so a view's off level can be named and priced in code."""
    band = row.get("band") or {}
    at = {"band low": band.get("low"), "band high": band.get("high"), "200-day average": row.get("ma200d"),
          "swing low": row.get("swing_low"), "swing high": row.get("swing_high"), "recent high": row.get("high")}
    return {k: v for k, v in at.items() if v}


def risk_frame(cur: dict, coin: str, side: str, name: str | None) -> dict | None:
    """The view's risk in code: the price, where it is wrong (the level the model named when the coin has it on the
    wrong side of the price, else the screen's own), the next level the other way, the room over the risk, and the
    coin's z-score and band state."""
    row, frame, at = None, None, {}
    for r in (cur.get("direction") or []) + (cur.get("coins") or []):    # a scan row carries both sides' frames
        if r["coin"] == coin and r.get("screen"):
            row = r
            frame = {k: v for k, v in r["screen"][side].items() if k in ("off", "next", "room_over_risk")}
            at = r["screen"].get("at") or {}
            break
    for c in cur.get("candidates") or []:
        if c["coin"] == coin:
            if c["side"] == side:
                row, frame = c, {k: c[k] for k in ("off", "next", "room_over_risk") if c.get(k)}
            elif frame is None:                # the other side of a candidate: its frame from the same levels
                row, frame = c, {k: v for k, v in screen.frame(c, side, None).items()
                                 if k in ("off", "next", "room_over_risk")}
            at = levels_at(c)
            break
    if row is None:
        return None
    price = row.get("price")
    q = row.get("screen") or row
    out: dict[str, Any] = {"price": price, **(frame or {})}
    lv = at.get(name or "")
    if price and lv and (lv < price if side == "long" else lv > price):
        out["off"] = screen._level(price, lv, name, q.get("atr_pct"))
        nxt = out.get("next")
        out.pop("room_over_risk", None)
        if nxt and out["off"]["pct"]:
            out["room_over_risk"] = round(abs(nxt["pct"]) / abs(out["off"]["pct"]), 1)
    eng = row.get("engine")                    # a dict on scan rows, the engine's word on candidates
    z = eng.get("z_daily") if isinstance(eng, dict) else None
    z = row.get("z_1d") if z is None else z
    if z is not None:
        out["z"] = z
    st = (row.get("band") or {}).get("state") or (((row.get("trend") or {}).get("bull_market_support_band") or {})
                                                  .get("state"))
    if st:
        out["band"] = st
    return out


NUM = re.compile(r"(?<![\w.:-])[-+]?\$?(\d[\d,]*(?:\.\d+)?)(?:\s*([kKmMbB](?![a-zA-Z])|%))?")
NOT_A_LEVEL = ("-day", "-week", "day", "week", "month", "hour", "h ", "h,", "h.", "h)", "x ", "x,", "x.", "of ",
               "trader", "tracked", "setup", "coin", "bet", "st ", "nd ", "rd ", "th ", "utc", "points", "pts",
               "sd ", "atr", "times")
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
DATE_OR_TIME = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}")


def _numbers(obj, out: list[float] | None = None) -> list[float]:
    """Every number in a scan row (any depth): what a level or figure the model writes may be."""
    out = [] if out is None else out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(abs(float(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, out)
    return out


def _written(text: str, levels_only: bool = False) -> list[float]:
    """The figures in a sentence the model wrote, $K/$M/$B scaled; with `levels_only`, only what reads as a price
    level (not a count of days, weeks, hours, traders or a percentage)."""
    out = []
    text = text or ""
    skip = [(m.start(), m.end()) for m in DATE_OR_TIME.finditer(text)]       # dates and clock times are not levels
    for m in NUM.finditer(text):
        if any(a <= m.start() < b for a, b in skip):
            continue
        tail = text[m.end():m.end() + 12].lower().lstrip()
        head = text[max(0, m.start() - 6):m.start()].lower().rstrip()
        unit = m.group(2) or ""
        raw = m.group(1).replace(",", "")
        v = float(raw)
        if levels_only:
            if (unit == "%" or tail.startswith(NOT_A_LEVEL) or tail.startswith(MONTHS) or head.endswith(MONTHS)
                    or head.endswith(("z", "z of", "beta")) or (v < 10 and "." not in raw)
                    or ("." not in raw and not unit and "$" not in text[max(0, m.start() - 1):m.start() + 1]
                        and 2000 <= v <= 2100)):
                continue
        out.append(v * {"k": 1e3, "m": 1e6, "b": 1e9}.get(unit.lower(), 1.0))
    return out


def _drop_clauses(text: str, bad: list[float]) -> str:
    """The sentence without the clauses that carry a figure not in the scan; the rest of it stays."""
    parts = [c for c in re.split(r"\s*[;,]\s*|(?<=[a-z0-9])\.\s+", text or "") if c.strip()]
    keep = [c for c in parts if not any(_known(x, bad, 1e-9) for x in _written(c, levels_only=True))]
    return ", ".join(keep)


def _known(v: float, pool: list[float], tol: float) -> bool:
    return any(abs(v - p) <= tol * max(abs(p), 1e-12) for p in pool if p)


def read_lint(out: dict, cur: dict) -> dict:
    """Checks the read's figures against the scan, in code: a level in a setup's chart note or in what would end it
    must be one of that coin's own figures (within 0.75%); a sentence with a level that is not is dropped. Figures in
    the headline and the read that match nothing in the scan are noted in the brief. No second model call."""
    problems = []
    every = _numbers(cur)
    for v in out.get("views") or []:
        rows = [r for r in (cur.get("candidates") or []) + (cur.get("direction") or []) + (cur.get("coins") or [])
                if r.get("coin") == v["coin"]]
        pool = _numbers(rows) + _numbers(v.get("risk") or {}) + [2.5, 3.0, 3.5, 1.0]   # and the owner's z lines
        for field in ("chart", "changes_if"):
            bad = [x for x in _written(v.get(field) or "", levels_only=True) if not _known(x, pool, 0.0075)]
            if bad:
                problems.append(f"{v['coin']} {field}: {', '.join(f'{x:g}' for x in bad)} is not one of its levels")
                v[field] = _drop_clauses(v.get(field) or "", bad)
    for field in ("headline", "read"):
        bad = [x for x in _written(out.get(field) or "") if x >= 10 and not _known(x, every, 0.02)
               and not _known(x / 100, every, 0.02)]
        if bad:
            problems.append(f"{field}: {', '.join(f'{x:g}' for x in bad[:4])} not found in the scan")
    if problems:
        out["lint"] = problems
    return out


def _fmt_px(x: float | None) -> str:
    from .groups import px
    return px(x) if x else "-"


def setup_lines(v: dict) -> list[str]:
    """A setup in three short lines: the facts (built in code), the why and the chart, where it is wrong and the next
    level the other way."""
    k = v.get("risk") or {}
    head = [f"{v['coin']} {v['lean']}", STRENGTH.get(v["strength"], "light")]
    if k.get("price"):
        head.append(_fmt_px(k["price"]))
    if k.get("z") is not None:
        zw = screen.z_word(k["z"])
        head.append(f"z {k['z']:.1f}" + (f" {zw.split(' (')[0]}" if zw else ""))
    if k.get("band"):
        head.append(f"{k['band']} band" if k["band"] in ("inside", "below") else f"{k['band']} its band")
    lines = [" · ".join(head)]
    body = v["why"].strip().rstrip(".") + "."
    if (v.get("chart") or "").strip():
        body += f" Chart: {v['chart'].strip().rstrip('.')}."
    lines.append(body)
    tail = []
    off, nxt = k.get("off"), k.get("next")
    if off:
        tail.append(f"Off {'below' if v['lean'] == 'long' else 'above'} {_fmt_px(off['level'])} ({off['name']}, "
                    f"{off['pct']:+.1f}%" + (f", {off['atr']:g} ATR" if off.get("atr") is not None else "") + ")")
    if nxt:
        tail.append(f"next level {_fmt_px(nxt['level'])} ({nxt['name']}, {nxt['pct']:+.1f}%)")
    if k.get("room_over_risk") is not None:
        room = k["room_over_risk"]
        tail.append(f"room {'10x+' if room >= 10 else f'{room:g}x'} the risk")
    end = (v.get("changes_if") or "").strip().rstrip(".")
    lines.append((" · ".join(tail) + ". " if tail else "") + (f"Ends if {end}." if end else ""))
    return [ln.strip() for ln in lines if ln.strip()]


CONSENSUS = {"RISK-ON": "risk-on", "RISK-OFF": "risk-off", "RISK_ON": "risk-on", "RISK_OFF": "risk-off",
             "NEUTRAL": "neutral", "MIXED": "mixed", "ACCUMULATION": "accumulating", "DISTRIBUTION": "distributing"}


def in_words(m: dict | None) -> dict | None:
    """A source's market block with the engine's codes in words (the read is told to write no codes, so it is not fed
    them): regimes, signals and consensus."""
    if not m:
        return m
    out = {}
    for k, v in m.items():
        if isinstance(v, str) and (k.endswith("regime") or "regime_" in k):
            out[k] = REGIME.get(v, v.lower().replace("_", " "))
        elif isinstance(v, str) and ("signal" in k):
            out[k] = SIGNAL.get(v, v.lower().replace("_", " "))
        elif isinstance(v, str) and k.startswith("consensus"):
            out[k] = CONSENSUS.get(v.upper(), v.lower().replace("_", " "))
        else:
            out[k] = v
    return out


def _alerts(store, since: float) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for kind in ("pt_build", "pt_alert"):
        for b in store.briefs_since(kind, since):
            body = b["body"]
            if not body.get("coin"):
                continue
            text = body.get("text") or (f"conviction: {codename(body.get('address', ''))} {body['side']} "
                                        f"{body.get('x') or ''}x usual size")
            out.setdefault(body["coin"], []).append(text.split("\n")[0][:100])
    for g in store.groups(limit=30):
        if g["ts"] >= since:
            out.setdefault(g["coin"], []).append(f"{(g['meta'] or {}).get('n') or ''} profitable traders opened "
                                                 f"{g['side']} together")
    return out


def parse_watch(words: list[str]) -> dict | None:
    """'ENA long 0.09 5k' -> coin, side, entry, size in dollars (side, entry and size optional)."""
    if not words:
        return None
    out: dict[str, Any] = {"coin": words[0].upper().lstrip("$"), "side": None, "entry": None, "size_usd": None}
    nums = []
    for w in words[1:]:
        lw = w.lower().strip(",")
        if lw in ("long", "short"):
            out["side"] = lw
            continue
        mult = 1000 if lw.endswith("k") else 1_000_000 if lw.endswith("m") else 1
        try:
            nums.append(float(lw.lstrip("$").rstrip("km").replace(",", "")) * mult)
        except ValueError:
            continue
    if nums:
        out["entry"] = nums[0]
    if len(nums) > 1:
        out["size_usd"] = nums[1]
    return out


def mine_text(agent, now: float | None = None) -> str:
    """His positions and watch list in code (no model, no cost), one line each: entry to now, size and share of the
    account, leverage and how far the liquidation is, where the price sits against its band and its z-score; and
    under it only what the studies count against it (a conviction bet the other way in 7 days, two or more tracked
    traders adding the other way). Facts, never what to do: his positions are his decisions."""
    from .groups import px
    s = scan(agent, now)
    items = s["my_positions"]
    kc = kucoin_line(agent)
    if not items:
        return ("No open positions on KuCoin or in your wallets, and an empty watch list. /watch ENA long 0.09 5k "
                "adds one." + (f"\n{kc}" if kc else ""))
    by_coin = {r["coin"]: r for r in s["coins"] + s["direction"]}
    table = {r["coin"]: r for r in screen.build(agent, now)["rows"]}
    lines = ["Your positions"]
    for m in items:
        side = m.get("side")
        head = f"{m['coin']}" + (f" {side}" if side else "")
        if m.get("entry"):
            head += f" {px(m['entry'])}"
        head += f" → {px(m['price'])}" if m.get("price") else " · no price"
        if m.get("move_pct") is not None:
            head += f" ({m['move_pct']:+.1f}%)"
        bits = [head]
        if m.get("value"):
            bits.append(f"${m['value']:,.0f}" + (f", {round(100 * m['share_of_account'])}% of the account"
                                                 if m.get("share_of_account") else ""))
        if m.get("lev"):
            bits.append(f"{m['lev']:g}x")
        if m.get("funding_apr_pct") is not None and side:
            f = m["funding_apr_pct"]
            pays = (f > 0) == (side == "long")
            q0 = table.get(m["coin"]) or {}
            hl_f = q0.get("funding_apr_pct")
            bits.append(f"{m['source']} funding {f:+.0f}% a year ({'you pay' if pays else 'you receive'})"
                        + (f", Hyperliquid {hl_f:+.0f}%" if hl_f is not None else ""))
        if m.get("liq") and m.get("price"):
            bits.append(f"liquidation {px(m['liq'])} ({100 * (m['liq'] / m['price'] - 1):+.0f}%)")
        q = table.get(m["coin"]) or {}
        if q.get("band"):
            st = q["band"]["state"]
            bits.append(f"{st} band" if st in ("inside", "below") else f"{st} its band")
        if q.get("z_1d") is not None:
            bits.append(f"z {q['z_1d']:.1f}")
        if m["source"] == "watch list":
            bits.append("watched")
        lines.append(" · ".join(bits))
        if side:
            other = "short" if side == "long" else "long"
            ctx = []
            conv = (q.get("conviction_7d") or {}).get(other, 0)
            if conv:
                ctx.append(f"{conv} conviction bet{'s' if conv > 1 else ''} {other} in 7 days")
            add = (((by_coin.get(m["coin"]) or {}).get("tracked_24h") or {}).get(other) or {}).get("adders", 0)
            if add >= 2:
                ctx.append(f"{add} tracked traders added {other}s in 24h")
            if ctx:
                lines.append("  against it: " + "; ".join(ctx))
    if kc:
        lines.append(kc)
    lines.append("Ask me about any of them, or /mine more for my read on them.")
    return "\n".join(lines)


def kucoin_line(agent) -> str:
    """How his KuCoin account was last read, when it is set up: refused (and why, with what to check), read with
    no open futures positions, or nothing to add when its positions are listed above."""
    st = agent.s
    if not (st.kucoin_key and st.kucoin_secret and st.kucoin_passphrase):
        return ""
    own = agent.store.get_snapshot("owner_positions") or {}
    err = own.get("kucoin_error")
    if err:
        hint = {"400003": "the key (or the KuCoin site it belongs to)",
                "400004": "the passphrase and the secret (KuCoin checks the passphrase with the secret)",
                "400005": "the secret", "400006": "the key's IP restriction (remove it)",
                "400007": "the key's permissions (General)"}
        what = next((v for k, v in hint.items() if k in err), "")
        return f"KuCoin could not be read: {err[:120]}." + (f" Check {what} in Railway." if what else "")
    kc = [w for a, w in (own.get("wallets") or {}).items() if a.startswith("kucoin")]
    if not kc:
        return "KuCoin: not read yet (each poll, about every 5 minutes)."
    if not any(w.get("positions") for w in kc):
        eq = sum(w.get("equity") or 0 for w in kc)
        return (f"KuCoin: connected, no open futures positions" + (f" (futures equity ${eq:,.0f})" if eq else "")
                + ". Positions in a unified trading account or a sub-account are not read yet.")
    return ""


def watch_text(agent, now: float | None = None) -> str:
    """The watch list built in code, one line each: the price and move since entry, the groups, what is against it."""
    from .groups import px
    s = scan(agent, now)
    items = [m for m in s["my_positions"] if m["source"] == "watch list"]
    if not items:
        return "Your watch list is empty. Add one: /watch ENA long 0.09 5k (side, entry and size are optional)."
    by_coin = {r["coin"]: r for r in s["coins"] + s["direction"]}
    lines = ["Watching"]
    for m in items:
        r = by_coin.get(m["coin"]) or {}
        head = m["coin"] + (f" {m['side']}" if m["side"] else "") + (f" from {px(m['entry'])}" if m["entry"] else "")
        head += f" · now {px(m['price'])}" if m["price"] else " · no price"
        if m["move_pct"] is not None:
            head += f" ({m['move_pct']:+.1f}%)"
        groups = []
        t = r.get("tracked")
        if t:
            groups.append(f"tracked {t['long']} long / {t['short']} short")
        for side in ("long", "short"):
            a = (r.get("tracked_24h") or {}).get(side) or {}
            if a.get("adders", 0) >= 2:
                groups.append(f"{a['adders']} added {side}s in 24h")
        if (r.get("big_money") or {}).get("lean"):
            groups.append(f"big money {r['big_money']['lean']}")
        lines.append(head + (" · " + ", ".join(groups) if groups else ""))
        if m["against"]:
            lines.append("  against you: " + "; ".join(m["against"]))
    return "\n".join(lines)


# ------------------------------------------------------------------- changes --


def _kind(reason: str) -> str:
    """What a reason against a position is about, without its counts: '4 tracked traders added shorts in 24h' and
    '5 tracked traders added shorts in 24h' are the same reason, so a count moving does not bring a new read."""
    return re.sub(r"\d[\d.,]*", "#", reason)


def _lean_word(x: str | None) -> str:
    return x or "no lean"


def changes(prev: dict | None, cur: dict) -> list[dict]:
    """What moved since the scan behind the last read, each with a weight; `owner` marks his positions."""
    if not prev:
        return [{"w": TRIGGER, "text": "first read", "owner": False}]
    out: list[dict] = []

    def add(w: int, text: str, owner: bool = False, **more) -> None:
        out.append({"w": w, "text": text, "owner": owner, **more})

    pm, cm = prev.get("market") or {}, cur.get("market") or {}
    # the crowd's and big money's leans are context (S2: the crowd leans against the last move): they are told to the
    # read when one runs, but never bring a read on their own
    for key, label, step, ctx in (("crowd_long_pct", "profitable traders", 5, True),
                                  ("big_money_long_pct", "big money", 8, True),
                                  ("tracked_long_pct", "tracked traders", 8, False)):
        a, b = pm.get(key), cm.get(key)
        if a is not None and b is not None and abs(b - a) >= step:
            add(3 if abs(b - a) >= 2 * step else 2, f"{label} {b - a:+.0f} points long ({a:.0f}% to {b:.0f}%)",
                context=ctx)
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
                add(w, f"{r['coin']}: {group.replace('_', ' ')} now lean {b} (were {_lean_word(a)})",
                    context=group != "tracked")
        for side in ("long", "short"):
            n = ((r.get("tracked_24h") or {}).get(side) or {}).get("adders", 0)
            n0 = ((p.get("tracked_24h") or {}).get(side) or {}).get("adders", 0)
            if n >= 3 and n0 < 3:
                add(2, f"{r['coin']}: {n} tracked traders added {side}s in 24h")
        mv = _pct(r.get("price"), p.get("price"))
        if mv is not None and abs(mv) >= 10:
            add(1, f"{r['coin']} {mv:+.0f}% since the last read")
    t0, t1 = pm.get("turns") or {}, cm.get("turns") or {}
    if t0.get("state") and t1.get("state") and t0["state"] != t1["state"]:
        add(3, f"the market moved from a {t0['state']} into a {t1['state']}")
    elif t0.get("state") == t1.get("state"):
        for mark, on in (t1.get("marks") or {}).items():
            if on and not (t0.get("marks") or {}).get(mark):
                add(3 if mark in TURN_MARKS else 2, f"{t1['state']}: {mark.replace('_', ' ')} now")
    # a new setup in the screen (two independent things lining up, liquid): what a read is for
    had = {(c["coin"], c["side"]) for c in prev.get("candidates") or []}
    fresh = [c for c in cur.get("candidates") or [] if (c["coin"], c["side"]) not in had]
    if fresh:
        add(min(4, 2 * len(fresh)), "new setups in the screen: " + ", ".join(
            f"{c['coin']} {c['side']} ({c['score']})" for c in fresh[:3]))
    seen = set(prev.get("alerts_24h") or [])
    new_alerts = [a for a in cur.get("alerts_24h") or [] if a not in seen]
    if new_alerts:
        add(min(3, len(new_alerts)), f"{len(new_alerts)} new alerts: " + "; ".join(new_alerts[:3]))
    def key(m: dict) -> tuple:
        return m["coin"], m.get("side"), "watch" if m.get("source") == "watch list" else "wallet"

    def name(k: tuple) -> str:
        return f"{k[0]} {k[1]}" if k[1] else k[0]

    mine0 = {key(m): m for m in prev.get("my_positions") or []}
    mine1 = {key(m): m for m in cur.get("my_positions") or []}
    for k in mine1.keys() - mine0.keys():
        add(1, f"you {'opened' if k[2] == 'wallet' else 'are now watching'} {name(k)}", owner=True)
    for k in mine0.keys() - mine1.keys():
        add(1, f"you {'closed' if k[2] == 'wallet' else 'stopped watching'} {name(k)}", owner=True)
    for k, m in mine1.items():
        old = {_kind(a) for a in (mine0.get(k) or {}).get("against") or []}
        fresh = [a for a in m.get("against") or [] if _kind(a) not in old]
        if fresh:
            add(4, f"your {name(k)}: " + "; ".join(fresh), owner=True, pos=name(k), kinds=[_kind(a) for a in fresh])
        m0 = mine0.get(k) or {}
        if m.get("move_pct") is not None and m0.get("move_pct") is not None:
            d = m["move_pct"] - m0["move_pct"]
            if abs(d) >= 5:
                add(2, f"your {name(k)} moved {d:+.0f} points since the last read ({m['move_pct']:+.0f}% from entry)",
                    owner=True)
        elif not k[1]:
            mv = _pct(m.get("price"), m0.get("price"))
            if mv is not None and abs(mv) >= 8:
                add(2, f"{k[0]} (watched) {mv:+.0f}% since the last read", owner=True)
    return out


# -------------------------------------------------------------------- record --


def record(store) -> dict:
    """How the read's setups did, scored as the journal scores them (synth/journal.py): per horizon, right and beat the
    market, for all, the firm or strong ones, and longs and shorts apart. Only the setups since S5's Amendment 2
    (3 November 2026); the views before it are reported apart, as the study says. The model reads it before each
    read."""
    every = store.picks(limit=100_000, origin="read")
    picks = [p for p in every if p["ts"] >= journal.S5_SETUPS_FROM]
    before = [p for p in every if p["ts"] < journal.S5_SETUPS_FROM]
    out: dict[str, Any] = {"since": "3 Nov 2026", "logged": len(picks),
                           "longs": sum(p["direction"] == "long" for p in picks),
                           "shorts": sum(p["direction"] == "short" for p in picks),
                           "bar": journal.S5_BAR, "views_before_3_nov": journal.block(before, "3d"),
                           "s5": journal.s5(picks)}          # the verdict as Amendment 3 judges it
    for h in ("1d", "3d", "7d"):
        out[h] = {"all": journal.block(picks, h),
                  "firm_or_strong": journal.block([p for p in picks if (p.get("conviction") or 0) >= 2], h),
                  "long": journal.block([p for p in picks if p["direction"] == "long"], h),
                  "short": journal.block([p for p in picks if p["direction"] == "short"], h)}
    return out


def record_text(rec: dict) -> str | None:
    """The setups' record against S5's declared bar (Amendment 3: separate bets, beyond the market), so a share is
    never read as a verdict on its own."""
    v = rec.get("s5") or {}
    b = rec.get("views_before_3_nov") or {}
    if not v.get("scored_bets_raw") and not b.get("n") and rec["3d"]["all"]["n"] < 3:
        return None
    line = journal.s5_text(v) if v else f"Setups since 3 Nov (S5): {rec['3d']['all']['n']} scored at 3 days"
    if b.get("n"):
        line += f". Views before 3 Nov: {b['right']} of {b['n']} right"
    return line + "."


# --------------------------------------------------------------------- read --


def _no_anchors(obj):
    """The scan without the patterns' shapes (drawn on the charts, not read by the model: they would only cost
    tokens)."""
    if isinstance(obj, dict):
        return {k: _no_anchors(v) for k, v in obj.items() if k != "anchors"}
    if isinstance(obj, list):
        return [_no_anchors(v) for v in obj]
    return obj


def board_text(cur: dict) -> str:
    """The market at a glance, built in code (no model), the same lines in the same place every day: where the cycle
    stands, BTC and ETH against their band with their z-score, the breadth, and the coins at the z-score's extremes."""
    m = cur.get("market") or {}
    lines = []
    t = m.get("turns") or {}
    if t.get("state"):
        day = t.get("run_day") if t["state"] == "broad uptrend" else t.get("downturn_day")
        head = t["state"][0].upper() + t["state"][1:] + (f" · day {day}" if day is not None else "")
        if t.get("longer_than"):
            head += f" (longer than {t['longer_than'].split(' past')[0]})"
        lines.append(head)
    for d in cur.get("direction") or []:
        bits = [f"{d['coin']} {_fmt_px(d.get('price'))}" + (f" {d['change_24h_pct']:+.1f}%"
                                                         if d.get("change_24h_pct") is not None else "")]
        band = (d.get("screen") or {}).get("band")
        if band:
            bits.append(f"{band} band" if band in ("inside", "below") else f"{band} its band")
        z = (d.get("engine") or {}).get("z_daily")
        if z is not None:
            bits.append(f"z {z:.1f}")
        lines.append(" · ".join(bits))
    b = m.get("breadth")
    if b:
        over = b["above"] + b["just above"] + b["extended"]
        banded = sum(b.get(s, 0) for s in screen.BAND_STATES)            # coins with a band, as the history counts
        then = ""
        if b.get("over_band_pct_week_ago") is not None and banded:
            then = f" ({round(100 * over / banded)}% now, {b['over_band_pct_week_ago']}% a week ago)"
        lines.append(f"Breadth: {over} over the band{then} · {b['inside']} inside · "
                     f"{b['below']} under · {b['over_200d']} of {b['with_200d']} over the 200-day")
    zx = m.get("z_extremes")
    if zx:
        word = lambda xs: ", ".join(f"{c} {z:.1f}" for c, z in xs) or "none"   # noqa: E731
        lines.append(f"Stretched (z 2.5+): {word(zx['stretched'])} · Washed out (z -1 or lower): "
                     f"{word(zx['washed_out'])}")
    return "\n".join(lines)


def header(now: float, reason: str, r: dict, ch: list[dict] | None = None) -> str:
    """The read's first line: when, why it came (built in code) and the model's lean on the market, if any."""
    why = {"morning": "morning", "asked": "you asked"}.get(reason)
    if why is None:
        top = sorted((c for c in ch or [] if not c.get("owner")), key=lambda c: -c["w"])
        why = (f"market changed: {top[0]['text'][:60]}" if top else "market changed")
    lean = r.get("market_lean")
    return f"Outrider · {time.strftime('%H:%M', time.gmtime(now))} UTC · {why}" + (
        f" · leaning {lean}" if lean in ("long", "short") else "")


def format_read(r: dict, now: float, rec: dict | None = None, held: int = 0, reason: str = "",
                ch: list[dict] | None = None, board: str = "") -> str:
    """The header (why it came), the board on the morning read, the headline, the read, then the setups worth a look;
    the record only when `rec` is passed (the morning read)."""
    lines = [header(now, reason, r, ch)]
    if board:
        lines += [board, ""]
    lines += [r["headline"].strip(), "", r["read"].strip()]
    if r.get("views"):
        lines += ["", "Setups to look at"]
        for i, v in enumerate(r["views"]):
            if i:
                lines.append("")
            lines += setup_lines(v)
    rt = record_text(rec) if rec else None
    if rt:
        lines += ["", rt]
    if held:
        lines += ["", f"Held back by the daily cap in the last 24 hours: {held} · /held lists them."]
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
    def __init__(self, agent, model: str, effort: str = "medium", daily_usd: float = 0.5, per_day: int = 6,
                 quiet: str = "22-6", client: anthropic.Anthropic | None = None):
        self.agent = agent
        self.store = agent.store
        self.model = model
        self.effort = effort
        self.daily_usd = daily_usd
        self.per_day = per_day
        self.quiet = quiet_hours(quiet)
        self.client = client or anthropic.Anthropic(timeout=120, max_retries=1)   # a slow day never stalls the poll
        self.busy = False                     # a read is running: the poll and /read do not start a second
        self.pending_ask = False              # he asked (/read) while one ran: his comes after unless that one reached him
        self._tasks: set = set()

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
        if self.spent_today(now) >= self.daily_usd:
            return None, ch
        last = self.store.latest_brief("read")
        gap = now - last["ts"] if last else float("inf")
        if sum(1 for b in today if b["body"].get("sent") and b["body"].get("reason") != "asked") >= self.per_day:
            return None, ch
        # his positions never bring a read of their own, nor do the crowd's swings (context, S2); they count only
        # beside something that does
        market = sum(c["w"] for c in ch if not c["owner"] and not c.get("context"))
        if hour not in self.quiet and market >= TRIGGER and gap >= MIN_GAP_S:
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
        """His Hyperliquid wallets and his KuCoin futures account, each poll; one that fails keeps its last reading."""
        s = self.agent.s
        wallets = s.owner_wallets
        on_kucoin = bool(s.kucoin_key and s.kucoin_secret and s.kucoin_passphrase)
        if not wallets and not on_kucoin:
            return
        before = self.store.get_snapshot("owner_positions") or {}
        old = before.get("wallets") or {}
        got: dict[str, dict] = {}
        snap: dict[str, Any] = {"ts": now, "wallets": got}
        if wallets:
            hl = HL()
            try:
                for a in wallets:
                    try:
                        got[a.lower()] = await hl.account(a)
                    except Exception as e:  # noqa: BLE001 - one wallet down keeps the last reading
                        log.warning("owner wallet read failed: %s", e)
                        if old.get(a.lower()):
                            got[a.lower()] = old[a.lower()]
            finally:
                await hl.aclose()
        if on_kucoin:
            from . import kucoin
            known = set(((self.store.get_snapshot("hl_ctx") or {}).get("c") or {}))
            kc = kucoin.KuCoin(s.kucoin_key, s.kucoin_secret, s.kucoin_passphrase, s.kucoin_key_version)
            try:
                got.update(kucoin.accounts(await kc.account(known)))
                self.agent.mark("kucoin", True, now=now)
            except Exception as e:  # noqa: BLE001 - KuCoin down or the key refused: the last reading stays
                log.warning("KuCoin read failed: %s", e)
                self.agent.mark("kucoin", False, e, now=now)
                got.update({a: w for a, w in old.items() if a.startswith("kucoin")})
                snap["kucoin_error"] = str(e)[:200]
            finally:
                await kc.aclose()
        self.store.put_snapshot("owner_positions", snap)

    # ----------------------------------------------------------------- read --
    async def run(self, reason: str, now: float | None = None, cur: dict | None = None,
                  ch: list[dict] | None = None, force_send: bool = False) -> dict:
        self.busy = True
        delivered = False
        try:
            out = await self._run(reason, now, cur, ch, force_send)
            delivered = bool(out.get("delivered"))
            return out
        finally:
            self.busy = False
            if self.pending_ask:
                self.pending_ask = False
                if not delivered:             # the read that ran did not reach him: the one he asked for runs now
                    t = asyncio.get_running_loop().create_task(self._asked())
                    self._tasks.add(t)
                    t.add_done_callback(self._tasks.discard)

    async def _asked(self) -> None:
        try:
            await self.run("asked", force_send=True)
        except Exception as e:  # noqa: BLE001 - logged; he can ask again
            log.warning("the read he asked for failed: %s", e)

    async def _run(self, reason: str, now: float | None, cur: dict | None, ch: list[dict] | None,
                   force_send: bool) -> dict:
        now = now or time.time()
        if cur is None:
            cur = await asyncio.to_thread(scan, self.agent, now)
        if ch is None:
            ch = changes(self.store.get_snapshot("read_scan"), cur)
        await self.fill_reflex(cur)
        await self.fill_trend(cur, now)
        rec = record(self.store)
        task = self.task(reason, cur, ch, self.store.latest_brief("read"), rec)
        try:
            out, cost = await asyncio.to_thread(self.think, task, now)
        except Exception as e:  # noqa: BLE001 - logged; the next poll can try again
            log.warning("read failed: %s", e)
            self.store.add_brief("read", {"reason": reason, "sent": False, "error": str(e)[:300],
                                          "cost": round(getattr(e, "cost", 0.0), 4)})
            if reason == "morning":       # the third failed morning: say so once, instead of going quiet
                failed = [b for b in self.store.briefs_since("read", _day_start(now))
                          if b["body"].get("reason") == "morning" and b["body"].get("error")]
                if len(failed) == 3:
                    await notify.push(self.agent.s, self.store, f"The morning read failed three times "
                                      f"({type(e).__name__}). /read tries again.", "read", must=True)
            raise
        try:
            out = read_lint(self.clean(out, cur), cur)
            send = bool(out["send"]) or reason == "morning" or force_send
            held = self.store.count_briefs("push_held", now - DAY) if reason == "morning" else 0
            text = format_read(out, now, rec if reason == "morning" else None, held, reason, ch,
                               board_text(cur) if reason == "morning" else "")
        except Exception as e:  # noqa: BLE001 - after the paid call: its cost and the error are kept all the same
            log.warning("read failed after the model answered: %s", e)
            self.store.add_brief("read", {"reason": reason, "sent": False, "error": f"after the answer: {e}"[:300],
                                          "cost": round(cost, 4)})
            raise
        logged = self.log_views(out["views"], now, reason, cur) if send else []
        if send:
            try:
                table = await asyncio.to_thread(screen.build, self.agent, now)
                done = {(p["coin"], p["direction"]) for p in self.store.picks(limit=10, origin="read", since=now - 60)
                        if p["id"] in logged}
                self.log_shadows([v for v in out["views"] if (v["coin"], v["lean"]) in done], now, table)
            except Exception as e:  # noqa: BLE001 - the controls never hold up a read
                log.warning("shadow picks failed: %s", e)
        self.store.put_snapshot("read_scan", cur)
        self.store.add_brief("read", {**out, "reason": reason, "sent": send, "text": text, "cost": round(cost, 4),
                                      "changes": [c["text"] for c in ch], "logged": logged})
        msg = None
        if send:   # a read he asked for answers him: it does not count against the unprompted messages
            pngs = await self.pictures(out["views"], now, reason, cur)
            msg = await notify.push(self.agent.s, self.store, text, "read", must=reason == "morning" or force_send,
                                    reply=reason == "asked", pngs=pngs)
        return {**out, "reason": reason, "sent": send, "text": text, "cost": round(cost, 4), "logged": logged,
                "delivered": msg is not None}

    async def pictures(self, views: list[dict], now: float, reason: str, cur: dict | None = None) -> list[bytes]:
        """The read's pictures, sent with it as one message: each setup on its daily chart (with the chart patterns
        Reflex found on it), on the morning read the market map, and on Monday's morning read the market cycle and the
        breadth. None of them holds the read up."""
        from . import pictures
        out: list[bytes] = []
        rows = (cur or {}).get("candidates", []) + (cur or {}).get("direction", []) + (cur or {}).get("coins", [])
        for v in views[:2]:
            pats = next((r.get("patterns") for r in rows if r.get("coin") == v["coin"] and r.get("patterns")), None)
            png = await pictures.setup_png(self.agent, v["coin"], v["lean"], v["strength"], v.get("risk"), now,
                                           patterns=pats)
            if png:
                out.append(png)
        if reason == "morning":
            m = await pictures.map_png(self.agent, now, [v["coin"] for v in views])
            if m:
                out.append(m[0])
            if time.gmtime(now).tm_wday == 0:           # Monday: where the cycle and the breadth stand
                for make in (pictures.cycle_png, pictures.breadth_png):
                    got = await make(self.agent)
                    if got:
                        out.append(got[0])
        return out

    async def fill_reflex(self, cur: dict) -> None:
        """From Reflex, only when a read runs: the engine reading for coins without a full one (outside the 40 its
        positioning feed carries), and the chart patterns on every coin's daily chart."""
        rc = self.agent.reflex
        if not rc:
            return
        rows = [r for r in cur["direction"] + cur["coins"]
                if not ((r.get("engine") or {}).get("daily") and (r.get("engine") or {}).get("regime"))]
        got = await asyncio.gather(*(reflex_src.engine_rows(rc, r["coin"]) for r in rows), return_exceptions=True)
        for r, g in zip(rows, got):
            if isinstance(g, dict):
                r["engine"] = engine_words(g)
            elif isinstance(g, Exception):
                log.warning("engine row for %s failed: %s", r["coin"], g)
        # chart patterns on each coin's daily chart, as Reflex's chart shows them (forming or recent, with their record)
        rows = list({r["coin"]: r for r in cur.get("candidates", []) + cur["direction"] + cur["coins"]}.values())
        got = await asyncio.gather(*(reflex_src.chart_patterns(rc, r["coin"]) for r in rows), return_exceptions=True)
        for r, g in zip(rows, got):
            if isinstance(g, list) and g:
                r["patterns"] = g
            elif isinstance(g, Exception):
                log.warning("chart patterns for %s failed: %s", r["coin"], g)

    async def fill_trend(self, cur: dict, now: float) -> None:
        """Each coin in the scan: where its price sits against its 200-day average and its bull market support band
        (levels read once a day per coin), only when a read runs."""
        hl = HL()
        try:
            for r in cur["direction"] + cur["coins"]:
                try:
                    view = trend_view(await self.agent.levels.trend(hl, r["coin"], now), r.get("price"))
                except Exception as e:  # noqa: BLE001 - the read goes on without that coin's levels
                    log.warning("trend levels for %s failed: %s", r["coin"], e)
                    continue
                if view:
                    r["trend"] = view
        finally:
            await hl.aclose()

    def task(self, reason: str, cur: dict, ch: list[dict], last: dict | None, rec: dict) -> str:
        notes = self.store.chat_notes()
        prev = None
        if last and last.get("headline"):
            prev = {k: last.get(k) for k in ("headline", "read", "views", "market_lean", "sent")}
            prev["views"] = [{k: v.get(k) for k in ("coin", "lean", "strength", "why")} for v in prev["views"] or []]
            prev["when"] = time.strftime("%a %H:%M UTC", time.gmtime(last["ts"]))
        # his positions are his decisions (owner, 3 November 2026): the read sees what he holds, not a briefing on it
        scan_for_read = {**_no_anchors(cur), "my_positions": [{k: m.get(k) for k in ("coin", "side", "source")}
                                                               for m in cur.get("my_positions") or []]}
        market_changes = [c for c in ch if not c.get("owner")]
        parts = [f"Why this read runs: {reason}." + (" This is the morning read: always send it and set up the day."
                                                     if reason == "morning" else ""),
                 "<changes_since_last_read>\n" + ("\n".join(f"- {c['text']}" for c in market_changes)
                                                   or "- nothing material") + "\n</changes_since_last_read>",
                 f"<your_last_read>\n{json.dumps(prev) if prev else 'none yet'}\n</your_last_read>",
                 "<your_record>\n" + (record_text(rec) or "Fewer than 3 setups scored at 3 days: too few to say "
                                       "anything.") + " Thin samples swing: do not chase the last week's side."
                 + "\n</your_record>",
                 "<notes_on_him>\n" + ("\n".join(n["text"] for n in notes) or "none") + "\n</notes_on_him>",
                 f"<scan>\n{json.dumps(scan_for_read, default=str, separators=(',', ':'))}\n</scan>"]
        if not cur.get("my_positions"):
            parts.append("No wallet positions and an empty watch list: use the notes for what he holds, if any.")
        return "\n\n".join(parts)

    def think(self, task: str, now: float) -> tuple[dict, float]:
        """One call: the scan in, the read out as JSON. A failure after the call carries what it cost."""
        resp = self._create([{"role": "user", "content": task}])
        cost = cost_of(resp.model if resp.model in PRICES else self.model, resp.usage)
        if resp.stop_reason == "refusal":
            raise ReadFailed("the model declined this read", cost)
        if resp.stop_reason == "max_tokens":
            raise ReadFailed("the read ran out of room", cost)
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        try:
            return json.loads(text), cost
        except ValueError as e:
            raise ReadFailed(f"the read was not valid JSON: {e}", cost) from e

    def _create(self, messages: list[dict]):
        # no cache breakpoint: reads run hours apart, past the cache's 5 minutes, so writing it only cost more
        params = {"model": self.model, "max_tokens": 16000, "messages": messages,
                  "system": [{"type": "text", "text": SYSTEM}],
                  "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}}}
        if self.model in FALLBACK_MODELS:
            return self.client.beta.messages.create(**params, betas=["server-side-fallback-2026-07-01"],
                                                    fallbacks="default")
        return self.client.messages.create(**params)

    @staticmethod
    def clean(out: dict, cur: dict | None = None) -> dict:
        """At most two views, each on a coin the scan showed (a view on a coin with no data behind it is dropped)."""
        shown = None
        if cur is not None:
            shown = {str(r.get("coin") or "").upper() for r in (cur.get("candidates") or []) + (cur.get("direction")
                     or []) + (cur.get("coins") or []) + (cur.get("my_positions") or [])}
        views, dropped = [], []
        for v in (out.get("views") or [])[:2]:
            coin = str(v.get("coin") or "").upper().lstrip("$")
            if coin and v.get("lean") in ("long", "short"):
                if shown is not None and coin not in shown:
                    dropped.append(coin)
                    continue
                v = {**v, "coin": coin, "strength": int(v.get("strength") or 1)}
                if cur is not None:
                    risk = risk_frame(cur, coin, v["lean"], v.get("off_level"))
                    if risk:
                        v["risk"] = risk
                views.append(v)
        return {**out, "views": views, **({"dropped_views": dropped} if dropped else {})}

    def log_views(self, views: list[dict], now: float, reason: str, cur: dict | None = None) -> list[int]:
        """Each view as a pick at Hyperliquid's mid, scored after 1, 3 and 7 days; a repeat within a day is not
        logged again. With it, what the setup said (chart, where it is wrong, the next level) and the coin's figures
        then (z, band, its rank among the candidates, beta, volatility), so the record can be read by them later."""
        from .service import price_of
        snaps = {n: s for n in ("cowboy", "reflex") if (s := self.store.get_snapshot(n))}
        recent = {(p["coin"], p["direction"]) for p in self.store.picks(limit=200, origin="read", since=now - REPEAT_S)}
        rank = {(c["coin"], c["side"]): i + 1 for i, c in enumerate((cur or {}).get("candidates") or [])}
        ids = []
        for v in views:
            if (v["coin"], v["lean"]) in recent:
                continue
            px = self.agent.price_now(v["coin"], now) or price_of(v["coin"], snaps)
            if not px:
                continue
            q = self._quant(cur, v["coin"])
            state = {"changes_if": v["changes_if"], "reason": reason, "chart": v.get("chart"),
                     "off_level": v.get("off_level"), "risk": v.get("risk"),
                     "candidate_rank": rank.get((v["coin"], v["lean"])),
                     **{k: q[k] for k in ("beta_btc", "atr_pct", "rs_30d_pct", "sector") if q.get(k) is not None}}
            ids.append(self.store.add_pick("read", v["coin"], v["lean"], v["strength"], px, v["why"][:300],
                                           {k: x for k, x in state.items() if x is not None}))
            recent.add((v["coin"], v["lean"]))
        return ids

    @staticmethod
    def _quant(cur: dict | None, coin: str) -> dict:
        for r in ((cur or {}).get("candidates") or []) + ((cur or {}).get("direction") or []) + \
                ((cur or {}).get("coins") or []):
            if r["coin"] == coin:
                return r.get("screen") or r
        return {}

    def log_shadows(self, views: list[dict], now: float, table: dict) -> list[int]:
        """Matched controls, never sent to him: the code's own two best candidates ('screen') and, for each view, a
        liquid coin drawn at random from the screen on the same side ('random'). Scored like the views, so how much the
        read adds over the code, and the code over chance, can be measured (study S6). Once a day per coin
        and side, as the views."""
        rows = [r for r in table.get("rows") or [] if not r.get("thin") and r.get("price")]
        recent = {(p["origin"], p["coin"], p["direction"]) for o in ("screen", "random")
                  for p in self.store.picks(limit=500, origin=o, since=now - REPEAT_S)}
        matched = {((p.get("state") or {}).get("matched_to"), p["direction"])
                   for p in self.store.picks(limit=500, origin="random", since=now - REPEAT_S)}
        ids: list[int] = []

        def log_one(origin: str, coin: str, side: str, state: dict) -> None:
            if (origin, coin, side) in recent:
                return
            px = self.agent.price_now(coin, now)
            if px:
                ids.append(self.store.add_pick(origin, coin, side, None, px, f"shadow {origin}", state))
                recent.add((origin, coin, side))

        for i, c in enumerate(screen.candidates(table)[:2]):
            log_one("screen", c["coin"], c["side"], {"rank": i + 1, "score": c["score"], "why": c["why"][:4],
                                                     "risk": {k: c[k] for k in ("off", "next") if c.get(k)}})
        for v in views:                          # one control per logged view, like the view, once a day
            if (v["coin"], v["lean"]) in matched:
                continue
            pool = [r["coin"] for r in rows if r["coin"] != v["coin"]]
            if pool:
                rng = random.Random(f"{int(now) // 3600}:{v['coin']}:{v['lean']}")
                log_one("random", rng.choice(pool), v["lean"], {"matched_to": v["coin"]})
                matched.add((v["coin"], v["lean"]))
        return ids
