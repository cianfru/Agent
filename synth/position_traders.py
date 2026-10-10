"""Proven position traders (study S3): who holds size for weeks, what they hold now, and their new bets.

The list comes from S3 (`synth/data/position_traders.json`): traders with 5+ positions held 3+ days, those positions at
least half the size they trade, profitable with a profit factor of 1.5+. Each poll reads every trader's positions from
Hyperliquid's public API (one light request each), and:
- keeps what each holds, since when, its value, entry and unrealised PnL, and the trader's leverage now;
- logs every new position as a pick, scored at 14 and 30 days (the forward record S3 needs);
- alerts once when a new position is a **conviction** bet: worth at least twice the trader's usual position size within
  4 hours of opening (S3's rule; the one result that met its bar, pending this forward record).
Positions held when the agent first sees a trader are the starting point: no alert, no pick.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

from . import notify
from .config import Settings
from .hyperliquid import HL
from .store import Store

log = logging.getLogger(__name__)

SEED = Path(__file__).parent / "data" / "position_traders.json"
CONVICTION_X = 2.0                 # times the trader's usual (median) position size
CONVICTION_WINDOW_S = 4 * 3600     # judged within 4 hours of the open, as a follower would see it
MIN_NEW_USD = 10_000               # smaller opens are not logged
MAX_ALERTS_PER_DAY = 5
MODERATE_LEVERAGE = 5.0


def load_seed(path: Path = SEED) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"traders": []}


def short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


def usd(x: float) -> str:
    a = abs(x)
    return f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:.0f}"


def record_line(t: dict) -> str:
    pf = t.get("profit_factor")
    return (f"{t['swing_positions']} swing trades, {round(100 * t['win_rate'])}% won, "
            f"{'+' if t['swing_pnl'] >= 0 else '-'}{usd(t['swing_pnl'])}" + (f", PF {pf}" if pf else "")
            + f", holds ~{t['median_hold_days']:.0f}d")


def conviction_text(t: dict, p: dict, lev: float | None) -> str:
    x = p["value"] / t["median_size"] if t.get("median_size") else None
    head = f"Conviction · {p['coin']} {p['side']} {usd(p['value'])}" + (f" ({x:.1f}x their usual)" if x else "")
    tail = f"{short(t['address'])} · {record_line(t)}" + (f" · {lev:.1f}x lev now" if lev is not None else "")
    return f"{head}\n{tail}\nLogged; scored at 14d and 30d."


class PositionTraders:
    def __init__(self, settings: Settings, store: Store, seed: dict | None = None, hl: HL | None = None):
        self.s = settings
        self.store = store
        self.seed = seed if seed is not None else load_seed()
        self.traders = {t["address"].lower(): t for t in self.seed.get("traders", [])}
        self._hl = hl
        self._mids: dict[str, float] = {}

    # ------------------------------------------------------------------ step --
    async def step(self, now: float | None = None, mids: dict[str, float] | None = None) -> dict:
        """`mids`: Hyperliquid mids from the same poll; a new position's pick is entered at the mid (what a follower
        would pay when the agent sees it), else at the trader's own entry."""
        self._mids = mids or {}
        if not self.traders:
            return {}
        if self._hl is not None:
            return await self._step(self._hl, now or time.time())
        async with httpx.AsyncClient(timeout=20) as http:
            return await self._step(HL(http), now or time.time())

    async def _step(self, hl: HL, now: float) -> dict:
        out = {"pt_read": 0, "pt_new": 0, "pt_closed": 0, "pt_conviction": 0}
        addrs = list(self.traders)
        for i in range(0, len(addrs), 6):
            chunk = addrs[i:i + 6]
            states = await asyncio.gather(*(hl.account(a) for a in chunk), return_exceptions=True)
            for a, st in zip(chunk, states):
                if isinstance(st, Exception) or st is None:
                    log.warning("position trader %s read failed: %s", a, st)
                    continue
                out["pt_read"] += 1
                r = await self._update(a, st, now)
                for k in ("new", "closed", "conviction"):
                    out[f"pt_{k}"] += r[k]
        return out

    async def _update(self, a: str, st: dict, now: float) -> dict:
        t = self.traders[a]
        res = {"new": 0, "closed": 0, "conviction": 0}
        first_look = self.store.pt_trader(a) is None
        gross = sum(p["notional"] for p in st["positions"].values())
        self.store.pt_set_trader(a, st["equity"], gross, now)
        lev = gross / st["equity"] if st["equity"] else None
        held = {row["coin"]: row for row in self.store.pt_open(a)}
        seed_since = {k.upper(): v for k, v in (t.get("open_since") or {}).items()}
        for coin, p in st["positions"].items():
            row = held.pop(coin, None)
            if row and row["side"] != p["side"]:            # flipped: one closes, another opens
                self.store.pt_close(row["id"], now)
                res["closed"] += 1
                row = None
            if row:
                self.store.pt_touch(row["id"], p["notional"], p["entry_px"], p["upnl"], now)
                young = now - row["opened_ts"] <= CONVICTION_WINDOW_S if row["known_open"] else False
                if young and not row["conviction"] and not row["baseline"] and self._is_conviction(t, p["notional"]):
                    res["conviction"] += await self._conviction(t, row["id"], row.get("pick_id"),
                                                                {**p, "coin": coin, "value": p["notional"]}, lev)
                continue
            since = seed_since.get(f"{coin}:{p['side'].upper()}")
            baseline = first_look
            opened = since if (baseline and since) else now
            pick_id = None
            if not baseline and p["notional"] >= MIN_NEW_USD:
                pick_id = self.store.add_pick("position", coin, p["side"], 1, self._mids.get(coin) or p["entry_px"] or None,
                                              f"pt {a} {coin} {p['side']}",
                                              {"wallet": a, "size_usd": round(p["notional"])})
            rid = self.store.pt_open_position(a, coin, p["side"], opened, int(not baseline or bool(since)), now,
                                              p["notional"], p["entry_px"], p["upnl"], int(baseline), pick_id)
            if not baseline:
                res["new"] += 1
                if p["notional"] >= MIN_NEW_USD and self._is_conviction(t, p["notional"]):
                    res["conviction"] += await self._conviction(t, rid, pick_id, {**p, "coin": coin,
                                                                                   "value": p["notional"]}, lev)
        for row in held.values():                            # gone: closed
            self.store.pt_close(row["id"], now)
            res["closed"] += 1
        return res

    def _is_conviction(self, t: dict, value: float) -> bool:
        return bool(t.get("median_size")) and value >= CONVICTION_X * t["median_size"]

    async def _conviction(self, t: dict, row_id: int, pick_id: int | None, p: dict, lev: float | None) -> int:
        self.store.pt_mark_conviction(row_id)
        if pick_id:
            self.store.set_pick_conviction(pick_id, 2)
        if self.store.count_briefs("pt_alert", time.time() - 86400) >= MAX_ALERTS_PER_DAY:
            return 1
        await notify.telegram(self.s.telegram_token, self.s.telegram_chat_id, conviction_text(t, p, lev))
        self.store.add_brief("pt_alert", {"address": t["address"], "coin": p["coin"], "side": p["side"]})
        return 1

    # ----------------------------------------------------------------- board --
    def board(self, now: float | None = None) -> dict:
        now = now or time.time()
        traders, coins = [], {}
        for a, t in self.traders.items():
            info = self.store.pt_trader(a) or {}
            rows = sorted(self.store.pt_open(a), key=lambda r: -(r["value"] or 0))
            eq = info.get("equity") or 0
            lev = (info.get("gross") or 0) / eq if eq else None
            seed_keys = {k.upper() for k in (t.get("open_since") or {})}
            pos = [{"coin": r["coin"], "side": r["side"], "value": round(r["value"] or 0), "entry": r["entry_px"],
                    "upnl": round(r["upnl"] or 0), "since": r["opened_ts"] if r["known_open"] else None,
                    "held_days": round((now - r["opened_ts"]) / 86400, 1) if r["known_open"] else None,
                    "held_note": None if r["known_open"] else (
                        "before Oct 2025" if f"{r['coin']}:{r['side'].upper()}" in seed_keys else "unknown"),
                    "conviction": bool(r["conviction"]), "new": not r["baseline"]} for r in rows]
            traders.append({"address": a, "record": {k: t.get(k) for k in (
                "swing_positions", "swing_pnl", "profit_factor", "win_rate", "median_hold_days", "median_size",
                "long_share", "best")}, "record_text": record_line(t), "equity": round(eq),
                "leverage": round(lev, 2) if lev is not None else None,
                "moderate": lev is not None and lev <= MODERATE_LEVERAGE, "positions": pos})
            for p in pos:
                c = coins.setdefault(p["coin"], {"coin": p["coin"], "long_n": 0, "short_n": 0, "long_usd": 0,
                                                 "short_usd": 0, "held": []})
                c[f"{p['side']}_n"] += 1
                c[f"{p['side']}_usd"] += p["value"]
                if p["held_days"] is not None:
                    c["held"].append(p["held_days"])
        coin_rows = []
        for c in coins.values():
            held = c.pop("held")
            c["median_held_days"] = round(statistics.median(held), 1) if held else None
            coin_rows.append(c)
        coin_rows.sort(key=lambda c: -(c["long_usd"] + c["short_usd"]))
        traders.sort(key=lambda t: -(t["record"]["swing_pnl"] or 0))
        return {"as_of": self.seed.get("as_of"), "rule": self.seed.get("rule"), "traders": traders,
                "coins": coin_rows, "forward": self.forward()}

    def forward(self) -> dict:
        """The forward record of positions opened since tracking began (S3's confirmation)."""
        picks = self.store.picks(limit=100_000, origin="position")
        out = {}
        for name, sel in (("all", picks), ("conviction", [p for p in picks if (p.get("conviction") or 0) >= 2])):
            row: dict[str, Any] = {"logged": len(sel)}
            for h in ("14d", "30d"):
                v = [p[f"ret_{h}"] for p in sel if p.get(f"ret_{h}") is not None]
                row[h] = {"n": len(v), "median_pct": round(100 * statistics.median(v), 2) if v else None,
                          "positive_pct": round(100 * sum(x > 0 for x in v) / len(v), 1) if v else None}
            out[name] = row
        return out

    def coins_text(self, limit: int = 8) -> str:
        rows = self.board()["coins"][:limit]
        if not rows:
            return "No positions read yet."
        lines = ["Proven position traders now (by money):"]
        for c in rows:
            held = f" · held ~{c['median_held_days']:.0f}d" if c["median_held_days"] is not None else ""
            lines.append(f"{c['coin']} {c['long_n']} long {usd(c['long_usd'])} / {c['short_n']} short "
                         f"{usd(c['short_usd'])}{held}")
        return "\n".join(lines)
