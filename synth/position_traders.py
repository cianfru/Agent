"""Tracked traders: who holds size and wins, what they hold now, and what they are adding.

Two groups (`synth/data/position_traders.json`, and riders found daily):
- **Proven** (study S3, with the owner's amendment): 5+ positions held 3+ days, open ones counted at their unrealised
  PnL, those positions at least half the size the trader trades, in profit with a profit factor of 1.5+.
- **Riding winners:** open positions with at least $250K unrealised profit, one of them at least 20% up on its value
  (and $50K+), in profit all-time, not a market maker. No history needed: the trader who opened NEAR at the bottom and
  never closed is in. Found once a day from Hyperliquid's leaderboard; a rider who stops qualifying for 14 days drops.
Leverage is shown (low under 3x, moderate 3-5x, high over 5x), never used to leave anyone out.

Each poll reads every tracked trader from Hyperliquid's public API (one light request each), and:
- keeps what each holds, since when, value, entry, unrealised PnL, and the trader's leverage now;
- records every add and cut (the "adding" view: which coins they are building, by how many traders);
- logs every new position as a pick scored at 14 and 30 days (the forward record);
- alerts on a **conviction** bet (a new position at least 2x the trader's usual size within 4 hours, S3's rule) and on a
  **build-up** (3+ tracked traders adding to one coin within 72 hours, $250K+ in all, while its price moved under 15%:
  the shape NEAR had before its move). Context, not a signal: neither is proven yet.
Positions held when the agent first sees a trader are the starting point: no alert, no pick, no flow.
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
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

CONVICTION_X = 2.0                 # times the trader's usual (median) position size
CONVICTION_WINDOW_S = 4 * 3600     # judged within 4 hours of the open, as a follower would see it
MIN_NEW_USD = 10_000               # smaller opens are not logged
MAX_ALERTS_PER_DAY = 5             # conviction alerts
MODERATE_LEVERAGE = 5.0

RIDE_MIN_OPEN_PNL = 250_000
RIDE_WINNER_SHARE = 0.2
RIDE_WINNER_MIN = 50_000
RIDE_GRACE_S = 14 * 86400
DISCOVER_MIN_AV = 500_000
DISCOVER_EVERY_S = 86400

BUILD_WINDOW_S = 72 * 3600
BUILD_MIN_TRADERS = 3
BUILD_MIN_TRADER_USD = 25_000
BUILD_MIN_TOTAL_USD = 250_000
BUILD_MAX_MOVE = 0.15
BUILD_COOLDOWN_S = 72 * 3600
MAX_BUILD_ALERTS_PER_DAY = 3


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


def signed_usd(x: float) -> str:
    return ("+" if x >= 0 else "-") + usd(x)


def leverage_band(lev: float | None) -> str | None:
    if lev is None:
        return None
    return "low" if lev < 3 else "moderate" if lev <= 5 else "high"


def record_line(t: dict, open_now: float | None = None) -> str:
    if t.get("tier") == "riding":
        tail = f", all-time {signed_usd(t['alltime_pnl'])}" if t.get("alltime_pnl") is not None else ""
        op = open_now if open_now is not None else (t.get("open_pnl") or 0)
        return f"riding · {signed_usd(op)} open profit{tail}"
    pf = t.get("profit_factor")
    paper = t.get("open_swing_pnl")
    paper_txt = f" ({usd(paper)} on paper)" if paper else ""
    return (f"proven · {t['swing_positions']} held 3d+, {round(100 * t['win_rate'])}% won, "
            f"{signed_usd(t['swing_pnl'])}{paper_txt}" + (f", PF {pf}" if pf else "")
            + f", holds ~{t['median_hold_days']:.0f}d")


def conviction_text(t: dict, p: dict, lev: float | None) -> str:
    x = p["value"] / t["median_size"] if t.get("median_size") else None
    head = f"Conviction · {p['coin']} {p['side']} {usd(p['value'])}" + (f" ({x:.1f}x their usual)" if x else "")
    tail = f"{short(t['address'])} · {record_line(t)}" + (f" · {lev:.1f}x lev now" if lev is not None else "")
    return f"{head}\n{tail}\nLogged; scored at 14d and 30d."


def build_text(b: dict) -> str:
    move = f"price {100 * b['move']:+.0f}%" if b.get("move") is not None else "price n/a"
    hold = f" · they hold {usd(b['held_usd'])} {b['side']}" if b.get("held_usd") else ""
    return (f"Building · {b['coin']} {b['side']}: {b['traders']} tracked traders added {usd(b['added_usd'])} in 3 days · "
            f"{move}{hold}\nContext, not a signal.")


def ride_qualifies(st: dict, info: dict) -> dict | None:
    """A trader riding big open winners, from its positions now and its leaderboard row. Pure."""
    if (info.get("alltime_pnl") or 0) <= 0:
        return None
    if info.get("av") and info.get("month_vlm") and info["month_vlm"] > 100 * info["av"]:
        return None                                  # market maker
    ps = st.get("positions") or {}
    up = sum(p["upnl"] for p in ps.values())
    winners = {c: p for c, p in ps.items() if p["upnl"] >= max(RIDE_WINNER_MIN, RIDE_WINNER_SHARE * p["notional"])}
    if up < RIDE_MIN_OPEN_PNL or not winners:
        return None
    coin, best = max(winners.items(), key=lambda kv: kv[1]["upnl"])
    vals = [p["notional"] for p in ps.values()]
    return {"tier": "riding", "open_pnl": round(up), "median_size": round(statistics.median(vals)),
            "long_share": round(sum(p["notional"] for p in ps.values() if p["side"] == "long") / (sum(vals) or 1), 2),
            "best": {"coin": coin, "side": best["side"], "pnl": round(best["upnl"]), "opened": None, "closed": None},
            "alltime_pnl": round(info.get("alltime_pnl") or 0), "month_pnl": round(info.get("month_pnl") or 0)}


def leaderboard_candidates(rows: list[dict], min_av: float = DISCOVER_MIN_AV) -> dict[str, dict]:
    """Leaderboard rows worth reading: account value at least `min_av`, in profit all-time, not a market maker. Pure."""
    out = {}
    for r in rows:
        try:
            w = {k: v for k, v in r["windowPerformances"]}
            av, mv = float(r["accountValue"]), float(w["month"]["vlm"])
            info = {"av": av, "month_vlm": mv, "month_pnl": float(w["month"]["pnl"]),
                    "alltime_pnl": float(w["allTime"]["pnl"])}
        except (KeyError, TypeError, ValueError):
            continue
        if av >= min_av and info["alltime_pnl"] > 0 and mv <= 100 * av:
            out[r["ethAddress"].lower()] = info
    return out


class PositionTraders:
    def __init__(self, settings: Settings, store: Store, seed: dict | None = None, hl: HL | None = None):
        self.s = settings
        self.store = store
        self.seed = seed if seed is not None else load_seed()
        self.seed_traders = {t["address"].lower(): t for t in self.seed.get("traders", [])}
        self._hl = hl
        self._mids: dict[str, float] = {}
        self.discovering = False
        self.enabled = True   # tests and offline agents switch it off

    @property
    def traders(self) -> dict[str, dict]:
        """Seed traders plus riders found since (a seed trader keeps its seed record)."""
        out = {a: {**t, "address": a} for a, t in self.store.pt_list().items()}
        out.update(self.seed_traders)
        return out

    # ------------------------------------------------------------------ step --
    async def step(self, now: float | None = None, mids: dict[str, float] | None = None) -> dict:
        """`mids`: Hyperliquid mids from the same poll; a new position's pick is entered at the mid (what a follower
        would pay when the agent sees it), else at the trader's own entry."""
        self._mids = mids or {}
        if not self.enabled or not self.traders:
            return {}
        if self._hl is not None:
            return await self._step(self._hl, now or time.time())
        async with httpx.AsyncClient(timeout=20) as http:
            return await self._step(HL(http), now or time.time())

    async def _step(self, hl: HL, now: float) -> dict:
        out = {"pt_read": 0, "pt_new": 0, "pt_closed": 0, "pt_conviction": 0}
        traders = self.traders
        addrs = list(traders)
        for i in range(0, len(addrs), 6):
            chunk = addrs[i:i + 6]
            states = await asyncio.gather(*(hl.account(a) for a in chunk), return_exceptions=True)
            for a, st in zip(chunk, states):
                if isinstance(st, Exception) or st is None:
                    log.warning("tracked trader %s read failed: %s", a, st)
                    continue
                out["pt_read"] += 1
                r = await self._update(traders[a], st, now)
                for k in ("new", "closed", "conviction"):
                    out[f"pt_{k}"] += r[k]
        out["pt_builds"] = await self._builds(now)
        return out

    def _px(self, coin: str, p: dict) -> float:
        return self._mids.get(coin) or (p["notional"] / p["size"] if p.get("size") else 0.0)

    async def _update(self, t: dict, st: dict, now: float) -> dict:
        a = t["address"].lower()
        res = {"new": 0, "closed": 0, "conviction": 0}
        first_look = self.store.pt_trader(a) is None
        gross = sum(p["notional"] for p in st["positions"].values())
        self.store.pt_set_trader(a, st["equity"], gross, now)
        lev = gross / st["equity"] if st["equity"] else None
        held = {row["coin"]: row for row in self.store.pt_open(a)}
        seed_since = {k.upper(): v for k, v in (t.get("open_since") or {}).items()}
        for coin, p in st["positions"].items():
            row = held.pop(coin, None)
            px = self._px(coin, p)
            if row and row["side"] != p["side"]:            # flipped: one closes, another opens
                self._close(row, now)
                res["closed"] += 1
                row = None
            if row:
                if row.get("size") is not None:
                    delta = p["size"] - row["size"]
                    if abs(delta) * px >= 1:
                        self.store.pt_add_flow(now, a, coin, p["side"], delta * px, px)
                self.store.pt_touch(row["id"], p["notional"], p["entry_px"], p["upnl"], now, p["size"])
                young = now - row["opened_ts"] <= CONVICTION_WINDOW_S if row["known_open"] else False
                if young and not row["conviction"] and not row["baseline"] and self._is_conviction(t, p["notional"]):
                    res["conviction"] += await self._conviction(t, row["id"], row.get("pick_id"),
                                                                {**p, "coin": coin, "value": p["notional"]}, lev)
                continue
            since = seed_since.get(f"{coin}:{p['side'].upper()}")
            baseline = first_look
            opened = since if (baseline and since) else now
            pick_id = None
            if not baseline:
                self.store.pt_add_flow(now, a, coin, p["side"], p["notional"], px)
                if p["notional"] >= MIN_NEW_USD:
                    pick_id = self.store.add_pick("position", coin, p["side"], 1, self._mids.get(coin) or p["entry_px"] or None,
                                                  f"pt {a} {coin} {p['side']}", {"wallet": a, "size_usd": round(p["notional"])})
            rid = self.store.pt_open_position(a, coin, p["side"], opened, int(not baseline or bool(since)), now,
                                              p["notional"], p["entry_px"], p["upnl"], int(baseline), pick_id, p["size"])
            if not baseline:
                res["new"] += 1
                if p["notional"] >= MIN_NEW_USD and self._is_conviction(t, p["notional"]):
                    res["conviction"] += await self._conviction(t, rid, pick_id, {**p, "coin": coin,
                                                                                   "value": p["notional"]}, lev)
        for row in held.values():                            # gone: closed
            self._close(row, now)
            res["closed"] += 1
        return res

    def _close(self, row: dict, now: float) -> None:
        if row.get("value"):
            self.store.pt_add_flow(now, row["address"], row["coin"], row["side"], -row["value"],
                                   self._mids.get(row["coin"]) or 0.0)
        self.store.pt_close(row["id"], now)

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

    # -------------------------------------------------------------- build-ups --
    def adding(self, hours: float, now: float | None = None) -> list[dict]:
        """Per coin and side over the last `hours`: traders who added (net), dollars added and cut, net."""
        now = now or time.time()
        per: dict[tuple[str, str], dict[str, float]] = {}
        for f in self.store.pt_flow_since(now - hours * 3600):
            k = (f["coin"], f["side"])
            per.setdefault(k, {}).setdefault(f["address"], 0.0)
            per[k][f["address"]] += f["usd"]
        out = []
        for (coin, side), by in per.items():
            added = sum(v for v in by.values() if v > 0)
            cut = -sum(v for v in by.values() if v < 0)
            out.append({"coin": coin, "side": side, "adders": sum(1 for v in by.values() if v >= 10_000),
                        "cutters": sum(1 for v in by.values() if v <= -10_000), "added_usd": round(added),
                        "cut_usd": round(cut), "net_usd": round(added - cut),
                        "strong_adders": sum(1 for v in by.values() if v >= BUILD_MIN_TRADER_USD)})
        out.sort(key=lambda r: -r["net_usd"])
        return out

    def _price_move(self, coin: str, now: float) -> float | None:
        rows = self.store.series("reflex", now - BUILD_WINDOW_S - 1800)
        if not rows or rows[0][0] > now - BUILD_WINDOW_S + 3600:
            return None
        then = (rows[0][1].get("c") or {}).get(coin)
        p0 = then[4] if then and len(then) > 4 else None
        p1 = self._mids.get(coin)
        return p1 / p0 - 1 if p0 and p1 else None

    async def _builds(self, now: float) -> int:
        sent = 0
        held = {}
        for t in self.board(now)["coins"]:
            held[(t["coin"], "long")] = t["long_usd"]
            held[(t["coin"], "short")] = t["short_usd"]
        for b in self.adding(BUILD_WINDOW_S / 3600, now):
            if b["strong_adders"] < BUILD_MIN_TRADERS or b["net_usd"] < BUILD_MIN_TOTAL_USD:
                continue
            move = self._price_move(b["coin"], now)
            if move is not None and abs(move) >= BUILD_MAX_MOVE:
                continue
            key = f"{b['coin']}:{b['side']}"
            if any(x["body"].get("key") == key for x in self.store.briefs_since("pt_build", now - BUILD_COOLDOWN_S)):
                continue
            if self.store.count_briefs("pt_build", now - 86400) >= MAX_BUILD_ALERTS_PER_DAY:
                break
            text = build_text({"coin": b["coin"], "side": b["side"], "traders": b["strong_adders"],
                               "added_usd": b["net_usd"], "move": move, "held_usd": held.get((b["coin"], b["side"]))})
            await notify.telegram(self.s.telegram_token, self.s.telegram_chat_id, text)
            self.store.add_brief("pt_build", {"key": key, "text": text})
            sent += 1
        return sent

    # ------------------------------------------------------------- discovery --
    def discover_due(self, now: float | None = None) -> bool:
        last = self.store.latest_brief("pt_discover")
        return not self.discovering and (last is None or (now or time.time()) - last["ts"] >= DISCOVER_EVERY_S)

    async def discover(self, now: float | None = None, rows: list[dict] | None = None, hl: HL | None = None) -> dict:
        """Find traders riding big open winners: leaderboard (daily download) -> positions now -> the riding rule."""
        self.discovering = True
        now = now or time.time()
        try:
            if rows is None:
                async with httpx.AsyncClient(timeout=120) as http:
                    r = await http.get(LEADERBOARD_URL)
                    r.raise_for_status()
                    rows = r.json().get("leaderboardRows") or []
            cands = {a: i for a, i in leaderboard_candidates(rows).items() if a not in self.seed_traders}
            del rows
            found = 0
            if hl is None:
                async with httpx.AsyncClient(timeout=20) as http:
                    found = await self._scan(HL(http), cands, now)
            else:
                found = await self._scan(hl, cands, now)
            dropped = self.store.pt_list_prune(now - RIDE_GRACE_S)
            out = {"candidates": len(cands), "riding": found, "dropped": dropped}
            self.store.add_brief("pt_discover", out)
            log.info("discover %s", out)
            return out
        finally:
            self.discovering = False

    async def _scan(self, hl: HL, cands: dict[str, dict], now: float) -> int:
        found, addrs = 0, list(cands)
        for i in range(0, len(addrs), 4):
            chunk = addrs[i:i + 4]
            states = await asyncio.gather(*(hl.account(a) for a in chunk), return_exceptions=True)
            for a, st in zip(chunk, states):
                if isinstance(st, Exception) or not st:
                    continue
                q = ride_qualifies(st, cands[a])
                if q:
                    self.store.pt_list_upsert(a, q, now)
                    found += 1
            await asyncio.sleep(1.0)   # about 4 reads a second: well inside the per-IP budget
        return found

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
            open_pnl = sum(p["upnl"] for p in pos)
            traders.append({"address": a, "tier": t.get("tier", "proven"), "record": {k: t.get(k) for k in (
                "swing_positions", "swing_pnl", "realized_swing_pnl", "open_swing_pnl", "profit_factor", "win_rate",
                "median_hold_days", "median_size", "long_share", "best", "open_pnl", "alltime_pnl")},
                "record_text": record_line(t, open_pnl if t.get("tier") == "riding" else None), "equity": round(eq),
                "open_pnl_now": round(open_pnl), "leverage": round(lev, 2) if lev is not None else None,
                "leverage_band": leverage_band(lev), "moderate": lev is not None and lev <= MODERATE_LEVERAGE,
                "positions": pos})
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
        traders.sort(key=lambda t: -((t["record"].get("swing_pnl") or 0) + (t["open_pnl_now"] if t["tier"] == "riding" else 0)))
        return {"as_of": self.seed.get("as_of"), "rule": self.seed.get("rule"), "traders": traders,
                "coins": coin_rows, "adding_24h": self.adding(24, now)[:15], "adding_7d": self.adding(168, now)[:15],
                "forward": self.forward(), "discover": self.store.latest_brief("pt_discover")}

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
        b = self.board()
        rows = b["coins"][:limit]
        if not rows:
            return "No positions read yet."
        lines = ["Tracked traders now (by money):"]
        for c in rows:
            held = f" · held ~{c['median_held_days']:.0f}d" if c["median_held_days"] is not None else ""
            lines.append(f"{c['coin']} {c['long_n']} long {usd(c['long_usd'])} / {c['short_n']} short "
                         f"{usd(c['short_usd'])}{held}")
        adds = [r for r in b["adding_7d"] if r["adders"] >= 2 and r["net_usd"] > 0][:5]
        if adds:
            lines.append("Adding this week:")
            lines += [f"{r['coin']} {r['side']} {r['adders']} traders, net {signed_usd(r['net_usd'])}" for r in adds]
        return "\n".join(lines)
