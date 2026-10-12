"""The proven list, kept current: study S3's rule (with the owner's live-list amendment) rerun on a rolling basis.

The study fixed the rule, not the list (owner, 10 October 2026): new players are found and read the same way, and
traders who stop qualifying drop.

Pool, rebuilt daily from Hyperliquid's leaderboard (downloaded anyway for big money):
- Reflex's profitable-trader rule (`roster_candidates`): monthly ROI of 30%+ from a month that did not start under
  $1K, $10K+ monthly profit, all-time PnL at least this month's, $10K+ account, not a market maker; the best 600;
- accounts of $500K+ in profit all-time, not market makers (the big money scan);
- the traders already listed (proven and riding).
Checks run in the background, paced to at most 30% of Hyperliquid's per-IP budget: wallets never checked first (a
new name is read within a day or two once the first pass is done), then the listed ones every 2 days (their records
stay current), the rest every 7 days. A first check reads the last 30 days to rule out bots, then the last 365 days
of fills (regular and TWAP slices, which Hyperliquid serves apart); later checks read only the fills since, and
positions are rebuilt as a running fold, so nothing is read twice.

The rule (S3 with the amendment): positions from flat back to flat, by average cost; a swing position is held 3+
days; open positions held 3+ days count at what was already taken off them plus their unrealised PnL; proven = 5+
swing positions, swing positions at least half the size traded, swing PnL above zero with a profit factor of 1.5+.
Bot-like wallets (S1 amendment 2) and
wallets too active to read (over 20,000 order fills in 30 days, 600+ a day) are left out. Fills are read merged
per order (one big order fills in many pieces: a count of pieces says nothing about the trader), and pages are
joined on the boundary millisecond with duplicates dropped (joining one millisecond later loses the rest of an
order filled in that millisecond). A proven trader who stops qualifying stays
30 days, then drops.

Who owns what: weekly, each big account's sub-accounts (Hyperliquid's own record) and wallets moving in the same
minutes (study E1, link 3a, on the last 90 days of moves) feed the owner map that big money is counted by.
"""

from __future__ import annotations

import asyncio
import logging
import math
import statistics
import time
from collections import Counter, defaultdict
from typing import Any

import httpx

from .hyperliquid import HL, move
from .store import Store

log = logging.getLogger(__name__)

DAY = 86400
DAY_MS = DAY * 1000
WINDOW_DAYS = 365
PROBE_DAYS = 30
PROBE_PAGES = 10                     # 20,000 order fills in 30 days (600+ a day): no position trader
MAX_PAGES = 60                       # a year's read stops at 120,000 order fills
PAGE = 2000                          # Hyperliquid's page size for fills
RECHECK_LISTED_S = 2 * DAY
RECHECK_OTHERS_S = 7 * DAY
RECHECK_SKIPPED_S = 30 * DAY         # bots and wallets too active to read
RECHECK_ERROR_S = DAY
PROVEN_GRACE_S = 30 * DAY
POOL_KEEP_S = 30 * DAY               # a candidate gone from the pool for 30 days is forgotten
WEIGHT_PER_MIN = 360                 # of Hyperliquid's 1,200 per IP a minute
BIG_AV = 500_000
MOVES_DAYS = 90
SUBS_EVERY_S = 7 * DAY
LINKS_EVERY_S = 7 * DAY
ROSTER_SIZE = 600
# Riding a long-held winner, any account size (owner, 12 October 2026: "if a guy is up 200% unrealized, I need to
# know"): a position open 14+ days, up 30%+ on price the trader's way, $10K+ in profit, in an account in profit
# all-time. Price, not return on margin, so leverage cannot make a small move look large; both are shown.
RIDE_HELD_DAYS = 14
RIDE_MOVE = 0.30
RIDE_ROE = 2.0                       # or 200%+ on margin (owner: "up 674%" is how traders see it)
RIDE_MOVE_MIN_USD = 10_000


def big_open_winner(p: dict) -> bool:
    """A position up 30%+ on price or 200%+ on margin, $10K+ in profit (how long it has been open aside)."""
    m = p.get("move")
    if m is None:
        m = move(p["side"], p.get("entry_px", p.get("entry")), p.get("size"), p["upnl"])
    return p["upnl"] >= RIDE_MOVE_MIN_USD and ((m or 0) >= RIDE_MOVE or (p.get("roe") or 0) >= RIDE_ROE)


# ------------------------------------------------------------------ the pool --
def roster_candidates(rows: list[dict], size: int = ROSTER_SIZE) -> dict[str, dict]:
    """Reflex's profitable-trader rule on the leaderboard (its perp-equity check aside: the check reads it live). Pure."""
    out = []
    for r in rows:
        try:
            w = {k: v for k, v in r["windowPerformances"]}
            av = float(r["accountValue"])
            pnl, roi, vlm = float(w["month"]["pnl"]), 100 * float(w["month"]["roi"]), float(w["month"]["vlm"])
            allt = float(w["allTime"]["pnl"])
        except (KeyError, TypeError, ValueError):
            continue
        if vlm > 100 * av or not roi or abs(pnl / (roi / 100)) < 1_000:
            continue                                  # market maker, or a month that began nearly empty
        if roi >= 30 and av >= 10_000 and pnl >= 10_000 and allt >= pnl:
            out.append((roi, r["ethAddress"].lower(), {"av": av, "month_vlm": vlm, "month_pnl": pnl, "alltime_pnl": allt}))
    out.sort(key=lambda x: -x[0])
    return {a: i for _, a, i in out[:size]}


# ------------------------------------------------------------- positions --
def compact(raw: list[dict], twap: bool = False) -> list[list]:
    """Hyperliquid fills -> [COIN, t_ms, px, sz, side, start_position], perps only."""
    out = []
    for x in raw or []:
        f = x.get("fill") if twap else x
        if not f:
            continue
        c = str(f.get("coin", ""))
        if ":" in c or c.startswith("@") or "/" in c or c.startswith("#"):
            continue
        try:
            out.append([c.upper(), int(f["time"]), float(f["px"]), float(f["sz"]), f["side"],
                        float(f.get("startPosition") or 0)])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fold(coins: dict, fills: list[list]) -> list[list]:
    """Positions from flat back to flat, by average cost (S3). `coins` carries each coin's open position and average
    cost from one call to the next and is updated in place; returns the positions these fills closed:
    [coin, side, t0_ms, t1_ms, size_usd, pnl]. A position open before the first fill read has no known entry and
    is not counted. Pure apart from `coins`."""
    closed = []
    for coin, t, px, sz, side, start in sorted(fills, key=lambda f: f[1]):
        st = coins.setdefault(coin, {"seg": None, "avg": 0.0, "pos": 0.0})
        seg = st["seg"]
        pos = 0.0 if abs(start) < 1e-6 else start
        delta = sz if side == "B" else -sz
        new = pos + delta
        new = 0.0 if abs(new) < 1e-6 else new
        if pos == 0.0:
            seg = {"side": "long" if delta > 0 else "short", "t0": t, "pnl": 0.0, "size": 0.0}
            st["avg"] = px
        elif seg is not None:
            if (pos > 0) == (delta > 0):
                st["avg"] = (st["avg"] * abs(pos) + px * abs(delta)) / (abs(pos) + abs(delta))
            else:
                seg["pnl"] += (px - st["avg"]) * min(abs(delta), abs(pos)) * (1 if pos > 0 else -1)
        flipped = pos != 0.0 and new != 0.0 and (pos > 0) != (new > 0)
        if seg is not None:
            # a flip's new side is the next position's size, not this one's
            seg["size"] = max(seg["size"], abs(pos) * px, 0.0 if flipped else abs(new) * px)
        if pos != 0.0 and (new == 0.0 or flipped):
            if seg is not None:
                closed.append([coin, seg["side"], seg["t0"], t, round(seg["size"], 2), round(seg["pnl"], 2)])
            seg = None
            if flipped:                     # the new side's entry is known even when the old side's was not
                seg = {"side": "long" if new > 0 else "short", "t0": t, "pnl": 0.0, "size": abs(new) * px}
                st["avg"] = px
        st["seg"], st["pos"] = seg, new
    return closed


def reconcile(coins: dict, closed: list[list], live: dict) -> list[list]:
    """S2's end check: a coin whose rebuilt position disagrees with the live one (a liquidation or a fill not served)
    loses what these fills closed and its open position; the next read starts it afresh. Returns what stays."""
    bad = set()
    for coin, st in coins.items():
        end = 0 if st.get("pos", 0.0) == 0.0 else (1 if st["pos"] > 0 else -1)
        now = 0 if coin not in live else (1 if live[coin]["side"] == "long" else -1)
        if end != now:
            bad.add(coin)
            st.update({"seg": None, "pos": float(now)})
    return [p for p in closed if p[0] not in bad]


def add_moves(moves: list[list], last: dict, fills: list[list], keep_from_ms: int) -> list[list]:
    """E1 link 3a: a wallet's fills in one coin and direction within 10 minutes of each other are one move.
    `last` carries each (coin, side)'s latest fill time across calls. Returns [[coin, side, t_ms]] kept from
    `keep_from_ms`."""
    for coin, t, px, sz, side, start in sorted(fills, key=lambda f: f[1]):
        k = f"{coin}:{side}"
        if k in last and t - last[k] <= 600_000:
            last[k] = t
            continue
        last[k] = t
        moves.append([coin, side, t])
    return [m for m in moves if m[2] >= keep_from_ms]


def bot_like(fills: list[list], since_ms: int, now_ms: int) -> bool:
    """S1 amendment 2: fills within 2 minutes in one coin and direction are one trade; 40+ trades a day, or 10+ with
    a quarter of them reversing within 15 minutes, is a bot or a market maker."""
    days = max(1.0, (now_ms - since_ms) / DAY_MS)
    trades, last = [], {}
    for coin, t, px, sz, side, start in sorted(fills, key=lambda f: f[1]):
        if t < since_ms:
            continue
        tr = last.get(coin)
        if tr and tr[0] == side and t - tr[2] <= 120_000:
            tr[2] = t
        else:
            tr = [side, t, t, coin]
            trades.append(tr)
            last[coin] = tr
    prev, flips = {}, 0
    for tr in trades:
        p = prev.get(tr[3])
        if p and p[0] != tr[0] and tr[1] - p[2] <= 900_000:
            flips += 1
        prev[tr[3]] = tr
    per_day = len(trades) / days
    return per_day >= 40 or (per_day >= 10 and flips >= 0.25 * len(trades))


# ---------------------------------------------------------------- record --
def record(ps: list[dict]) -> dict:
    sw = [p for p in ps if p["swing"]]
    tot_size = sum(p["size"] for p in ps) or 1
    wins = sum(p["pnl"] for p in sw if p["pnl"] > 0)
    losses = -sum(p["pnl"] for p in sw if p["pnl"] < 0)
    return {"n": len(ps), "swing_n": len(sw), "swing_share": sum(p["size"] for p in sw) / tot_size,
            "swing_pnl": sum(p["pnl"] for p in sw), "pf": wins / losses if losses else (math.inf if wins else 0),
            "win": sum(p["pnl"] > 0 for p in sw) / len(sw) if sw else 0,
            "median_hold": statistics.median([p["hold_d"] for p in sw]) if sw else None,
            "median_size": statistics.median([p["size"] for p in ps]) if ps else None}


def is_proven(r: dict) -> bool:
    return r["swing_n"] >= 5 and r["swing_share"] >= 0.5 and r["swing_pnl"] > 0 and r["pf"] >= 1.5


def _day(ms: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def evaluate(address: str, closed: list[list], coins: dict, live: dict, now_ms: int,
             info: dict | None = None) -> tuple[dict, dict | None]:
    """A wallet's record from its closed positions and its open ones now (held 3+ days: at what was already taken
    off them plus their unrealised PnL, so a loser cut to a small winning remnant still reads as a loser; sized at
    their largest value while open, as closed ones are). Returns (summary, list entry if proven, else None). Pure."""
    ps = [{"coin": c, "side": s, "t0": t0, "t1": t1, "size": size, "pnl": pnl, "hold_d": (t1 - t0) / DAY_MS,
           "swing": (t1 - t0) / DAY_MS >= 3} for c, s, t0, t1, size, pnl in closed]
    open_since = {}
    for coin, p in live.items():
        seg = (coins.get(coin) or {}).get("seg")
        t0 = seg["t0"] if seg and seg["side"] == p["side"] else None
        open_since[f"{coin}:{p['side']}"] = t0 / 1000 if t0 else None
        if t0 and (now_ms - t0) / DAY_MS >= 3:
            ps.append({"coin": coin, "side": p["side"], "t0": t0, "t1": now_ms,
                       "size": max(p["notional"], seg.get("size") or 0.0),
                       "pnl": (seg.get("pnl") or 0.0) + p["upnl"], "hold_d": (now_ms - t0) / DAY_MS, "swing": True, "open": True,
                       "move": move(p["side"], p.get("entry_px"), p.get("size"), p["upnl"]), "roe": p.get("roe")})
    r = record(ps)
    pf = None if r["pf"] == math.inf else round(r["pf"], 2)
    summary = {"positions": r["n"], "swing_positions": r["swing_n"], "swing_pnl": round(r["swing_pnl"]),
               "profit_factor": pf, "proven": is_proven(r)}
    if not is_proven(r):
        return summary, None
    sw = [p for p in ps if p["swing"]]
    best = max(sw, key=lambda p: p["pnl"])
    entry = {"address": address, "tier": "proven", "swing_positions": r["swing_n"],
             "realized_swing_pnl": round(sum(p["pnl"] for p in sw if not p.get("open"))),
             "open_swing_pnl": round(sum(p["pnl"] for p in sw if p.get("open"))),
             "swing_pnl": round(r["swing_pnl"]), "profit_factor": pf, "win_rate": round(r["win"], 3),
             "closed_swing": sum(1 for p in sw if not p.get("open")),
             "closed_won": sum(1 for p in sw if not p.get("open") and p["pnl"] > 0),
             "open_swing": sum(1 for p in sw if p.get("open")),
             "median_hold_days": round(r["median_hold"], 1), "median_size": round(r["median_size"]),
             "long_share": round(sum(p["size"] for p in sw if p["side"] == "long") / (sum(p["size"] for p in sw) or 1), 2),
             "best": {"coin": best["coin"], "side": best["side"], "pnl": round(best["pnl"]), "opened": _day(best["t0"]),
                      "closed": None if best.get("open") else _day(best["t1"]),
                      **({"move": round(best["move"], 4)} if best.get("move") is not None else {}),
                      **({"roe": round(best["roe"], 4)} if best.get("roe") is not None else {})},
             "alltime_pnl": (info or {}).get("alltime_pnl"), "open_since": open_since, "checked": now_ms / 1000}
    return summary, entry


def live_summary(live: dict, coins: dict, equity: float, now: float) -> dict:
    """A wallet's open positions at this read, with how long each has been open (from the rebuilt fills) and how far
    the price has moved its way. Pure."""
    pos = {}
    for c, p in live.items():
        seg = (coins.get(c) or {}).get("seg")
        m = move(p["side"], p.get("entry_px"), p.get("size"), p["upnl"])
        pos[c] = {"side": p["side"], "value": round(p["notional"]), "upnl": round(p["upnl"]), "entry": p.get("entry_px"),
                  "size": p.get("size"), "roe": round(p["roe"], 4) if p.get("roe") is not None else None,
                  "lev": p.get("lev"), "move": round(m, 4) if m is not None else None,
                  "since": seg["t0"] / 1000 if seg and seg["side"] == p["side"] else None}
    return {"equity": round(equity), "ts": now, "positions": pos}


def rides_long_winner(summary: dict, info: dict, now: float) -> dict | None:
    """Riding a long-held winner (see RIDE_HELD_DAYS): a list entry, else None. Pure."""
    if info.get("alltime_pnl") is not None and info["alltime_pnl"] <= 0:
        return None
    ps = summary.get("positions") or {}
    winners = [(c, p) for c, p in ps.items() if p.get("since") and now - p["since"] >= RIDE_HELD_DAYS * DAY
               and big_open_winner(p)]
    if not winners:
        return None
    coin, best = max(winners, key=lambda kv: kv[1]["upnl"])
    vals = [p["value"] for p in ps.values()]
    return {"tier": "riding", "rule": "long-held winner", "open_pnl": round(sum(p["upnl"] for p in ps.values())),
            "median_size": round(statistics.median(vals)),
            "long_share": round(sum(p["value"] for p in ps.values() if p["side"] == "long") / (sum(vals) or 1), 2),
            "best": {"coin": coin, "side": best["side"], "pnl": best["upnl"], "move": best["move"], "roe": best.get("roe"),
                     "opened": _day(best["since"] * 1000), "closed": None},
            "alltime_pnl": info.get("alltime_pnl"), "month_pnl": info.get("month_pnl"), "checked": now}


# ----------------------------------------------------------------- owners --
def same_minutes_pairs(moves: dict[str, list[list]]) -> list[list[str]]:
    """E1 link 3a: two wallets with at least 5 moves in the same coin and direction within 5 minutes of each other,
    covering at least half the less active wallet's moves. Pure."""
    buckets = defaultdict(set)
    for a, ms in moves.items():
        for coin, side, t in ms:
            b = int(t // 300_000)
            for k in (b - 1, b, b + 1):
                buckets[(coin, side, k)].add(a)
    co = Counter()
    for ws in buckets.values():
        if 2 <= len(ws) <= 80:
            ws = sorted(ws)
            for i in range(len(ws)):
                for j in range(i + 1, len(ws)):
                    co[(ws[i], ws[j])] += 1
    out = []
    for (a, b), n in co.items():
        if n < 5:
            continue
        by = defaultdict(list)
        for j, m in enumerate(moves[b]):
            by[(m[0], m[1])].append(j)
        cands = sorted((abs(x[2] - moves[b][j][2]), i, j) for i, x in enumerate(moves[a])
                       for j in by.get((x[0], x[1]), []) if abs(x[2] - moves[b][j][2]) <= 300_000)
        ua, ub, matched = set(), set(), 0
        for _, i, j in cands:
            if i not in ua and j not in ub:
                ua.add(i)
                ub.add(j)
                matched += 1
        if matched >= 5 and matched >= 0.5 * min(len(moves[a]), len(moves[b])):
            out.append([a, b])
    return out


# ----------------------------------------------------------------- worker --
class Refresher:
    """Keeps the proven list current in the background (see the module doc)."""

    def __init__(self, store: Store, seed_traders: dict[str, dict], seed_ts: float):
        self.store = store
        self.seed_traders = seed_traders
        self.seed_ts = seed_ts
        self.weight_per_min = WEIGHT_PER_MIN
        self._queue: list[str] = []
        self.running = False

    # pool --------------------------------------------------------------------
    def update_pool(self, roster: dict[str, dict], big: dict[str, dict], now: float,
                    winners: dict[str, dict] | None = None) -> int:
        """Daily, from the leaderboard: who to check. Wallets the daily scan saw holding a big open winner come
        first in the queue; listed traders and the seed always stay in the pool."""
        pool: dict[str, tuple[list[str], dict]] = {}
        for a, i in roster.items():
            pool[a] = (["roster"], i)
        for a, i in big.items():
            src, _ = pool.get(a, ([], i))
            pool[a] = (src + ["big"], i)
        for a, i in (winners or {}).items():
            src, _ = pool.get(a, ([], i))
            pool[a] = (src + ["winner"], i)
        for a in set(self.store.pt_list()) | set(self.seed_traders):
            src, i = pool.get(a, ([], {}))
            pool[a] = (src + ["listed"], i)
        self.store.pt_cand_pool(pool, now)
        self.store.pt_cand_prune(now - POOL_KEEP_S, keep=set(self.store.pt_list()))
        self._queue = []
        return len(pool)

    def _due(self, now: float) -> list[str]:
        listed = set(self.store.pt_list()) | set(self.seed_traders)
        rows = self.store.pt_cand_rows()
        fresh = [r for r in rows if r["checked_ts"] is None]
        prio = {"winner": 4, "listed": 3, "roster": 2, "big": 1}
        fresh.sort(key=lambda r: (-max(prio.get(s, 0) for s in (r["src"] or ["big"])), -(r["info"] or {}).get("av", 0)))
        wait = {"bot": RECHECK_SKIPPED_S, "busy": RECHECK_SKIPPED_S, "error": RECHECK_ERROR_S}
        due = [r for r in rows if r["checked_ts"] is not None and now - r["checked_ts"] >= wait.get(
            r["status"], RECHECK_LISTED_S if r["address"] in listed else RECHECK_OTHERS_S)]
        due.sort(key=lambda r: r["checked_ts"])
        return [r["address"] for r in fresh] + [r["address"] for r in due]

    def next(self, now: float) -> str | None:
        if not self._queue:
            self._queue = self._due(now)[:200]
        return self._queue.pop(0) if self._queue else None

    # one check -----------------------------------------------------------------
    async def _call(self, hl: HL, body: dict, weight: int | None = None) -> Any:
        """One read, then a pause for its weight: the refresh keeps to its own share (WEIGHT_PER_MIN) of the shared
        budget every Hyperliquid read draws on (synth/hyperliquid.py)."""
        res = await hl._post(body)
        w = weight if weight is not None else 20 + (len(res) // 20 if isinstance(res, list) else 0)
        await asyncio.sleep(w * 60 / self.weight_per_min)
        return res

    async def _fills(self, hl: HL, address: str, start_ms: int, max_pages: int,
                     end_ms: int | None = None) -> tuple[list[list], bool]:
        """Regular fills and TWAP slices from `start_ms` (to `end_ms`), oldest first. False when the page cap was hit."""
        out: list[list] = []
        for kind, twap in (("userFillsByTime", False), ("userTwapSliceFillsByTime", True)):
            t0, seen = start_ms, set()
            for _ in range(max_pages):
                body = {"type": kind, "user": address, "startTime": t0}
                if end_ms is not None:
                    body["endTime"] = end_ms
                if not twap:
                    body["aggregateByTime"] = True
                page = await self._call(hl, body) or []
                fs = [x.get("fill") or {} for x in page] if twap else page
                keys = [(f.get("time"), f.get("tid"), f.get("coin"), f.get("px"), f.get("sz")) for f in fs]
                out += compact([x for x, k in zip(page, keys) if k not in seen], twap)
                seen.update(keys)
                if len(page) < PAGE:
                    break
                nxt = int(fs[-1]["time"])
                if nxt == t0:                              # a whole page in one millisecond: step past it
                    nxt += 1
                t0 = nxt
            else:
                return out, False
        return out, True

    async def check(self, hl: HL, address: str, now: float) -> str:
        """Read one wallet, update its rebuilt positions and its place on the list. Returns its status."""
        row = self.store.pt_cand_get(address) or {"address": address, "info": {}, "src": []}
        now_ms = int(now * 1000)
        st = await self._call(hl, {"type": "clearinghouseState", "user": address}, weight=2) or {}
        live = HL.parse_positions(st)
        fresh = row.get("checked_ts") is None or row.get("status") in ("bot", "busy") or not row.get("fills_ms")
        if fresh:
            probe_from = now_ms - PROBE_DAYS * DAY_MS
            probe, ok = await self._fills(hl, address, probe_from, PROBE_PAGES)
            if not ok:
                return self._save_skip(row, "busy", now)
            if bot_like(probe, max(probe_from, now_ms - 14 * DAY_MS), now_ms):
                return self._save_skip(row, "bot", now)
            older, ok = await self._fills(hl, address, now_ms - WINDOW_DAYS * DAY_MS, MAX_PAGES, end_ms=probe_from - 1)
            if not ok:
                return self._save_skip(row, "busy", now)
            fills = older + probe
            coins, closed, moves, last = {}, [], [], {}
        else:
            fills, ok = await self._fills(hl, address, int(row["fills_ms"]) + 1, MAX_PAGES)
            if not ok or bot_like(fills, max(int(row["fills_ms"]), now_ms - 14 * DAY_MS), now_ms):
                return self._save_skip(row, "busy" if not ok else "bot", now)
            coins, closed = row.get("state") or {}, row.get("closed") or []
            moves, last = row.get("moves") or [], row.get("moves_last") or {}
        new = reconcile(coins, fold(coins, fills), live)
        closed = [p for p in closed + new if p[3] >= now_ms - WINDOW_DAYS * DAY_MS]
        big = (row.get("info") or {}).get("av", 0) >= BIG_AV or address in self.seed_traders
        moves = add_moves(moves, last, fills, now_ms - MOVES_DAYS * DAY_MS) if big else []
        summary, entry = evaluate(address, closed, coins, live, now_ms, row.get("info"))
        equity = float((st.get("marginSummary") or {}).get("accountValue") or 0)
        live_now = live_summary(live, coins, equity, now)
        rider = None if entry else rides_long_winner(live_now, row.get("info") or {}, now)
        status = "proven" if entry else "riding" if rider else "no"
        self._list(address, entry or rider, now)
        subs, subs_ts = row.get("subs"), row.get("subs_ts")
        if big and (not subs_ts or now - subs_ts >= SUBS_EVERY_S):
            got = await self._call(hl, {"type": "subAccounts", "user": address}) or []
            subs, subs_ts = [str(s.get("subAccountUser", "")).lower() for s in got if s.get("subAccountUser")], now
        self.store.pt_cand_save(address, checked_ts=now, status=status, fills_ms=max([f[1] for f in fills] or
                                [int(row.get("fills_ms") or now_ms - 1)]), state=coins, closed=closed, moves=moves,
                                moves_last=last if big else {}, record=summary, subs=subs, subs_ts=subs_ts,
                                live=live_now)
        return status

    def _save_skip(self, row: dict, status: str, now: float) -> str:
        self.store.pt_cand_save(row["address"], checked_ts=now, status=status, fills_ms=None, state={}, closed=[],
                                moves=[], moves_last={}, record=None, subs=row.get("subs"), subs_ts=row.get("subs_ts"))
        self._list(row["address"], None, now)
        return status

    def _list(self, address: str, entry: dict | None, now: float) -> None:
        """Proven or riding a long-held winner: on the list, its record refreshed. Neither: a seed trader seen for the
        first time keeps its seed record with the grace counted from the seed's date; anyone else listed keeps its
        grace running."""
        seeded = address in self.seed_traders
        if entry:
            self.store.pt_list_upsert(address, entry, now, added_ts=self.seed_ts if seeded else None)
        elif seeded and address not in self.store.pt_list():
            self.store.pt_list_upsert(address, self.seed_traders[address], now, added_ts=self.seed_ts,
                                      qualified_ts=self.seed_ts)

    def note_live(self, address: str, positions: dict, equity: float, now: float) -> None:
        """A position read from the daily scan (no fills): kept as the wallet's latest open positions, keeping how
        long each has been open from an earlier full read when the side is the same."""
        prev = (self.store.pt_cand_live_of(address) or {}).get("positions") or {}
        summary = live_summary(positions, {}, equity, now)
        for c, p in summary["positions"].items():
            old = prev.get(c) or {}
            if old.get("side") == p["side"] and old.get("since"):
                p["since"] = old["since"]
        self.store.pt_cand_save(address, live=summary)

    # owner links -----------------------------------------------------------------
    def owner_pairs(self) -> list[list[str]]:
        """Sub-accounts on record plus the latest same-minutes links."""
        pairs = [[a, s] for a, subs in self.store.pt_cand_subs().items() for s in subs]
        links = self.store.latest_brief("pt_links")
        return pairs + ((links or {}).get("pairs") or [])

    async def maybe_links(self, now: float) -> None:
        last = self.store.latest_brief("pt_links")
        if last and now - last["ts"] < LINKS_EVERY_S:
            return
        moves = {a: m for a, m in self.store.pt_cand_moves().items() if m}
        if len(moves) < 50:
            return
        pairs = await asyncio.to_thread(same_minutes_pairs, moves)
        self.store.add_brief("pt_links", {"pairs": pairs, "wallets": len(moves)})

    # loop -----------------------------------------------------------------------
    async def run_forever(self, enabled) -> None:
        """Check one wallet after another, paced; idle when nothing is due."""
        self.running = True
        async with httpx.AsyncClient(timeout=30) as http:
            hl = HL(http)
            while True:
                try:
                    if not enabled():
                        await asyncio.sleep(600)
                        continue
                    now = time.time()
                    await self.maybe_links(now)
                    a = self.next(now)
                    if a is None:
                        await asyncio.sleep(600)
                        continue
                    try:
                        await self.check(hl, a, now)
                    except (httpx.HTTPError, ValueError) as e:
                        log.warning("proven check %s failed: %s", a, e)
                        self.store.pt_cand_save(a, checked_ts=now, status="error")
                        await asyncio.sleep(30)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - keep the worker alive
                    log.exception("proven refresh failed")
                    await asyncio.sleep(300)

    def stats(self, now: float) -> dict:
        rows = self.store.pt_cand_rows()
        status = Counter(r["status"] or "waiting" for r in rows)
        listed = self.store.pt_list_rows()
        proven = [r for r in listed if r["info"].get("tier") == "proven"]
        joined = [r for r in proven if r["added_ts"] >= now - 7 * DAY and r["address"] not in self.seed_traders]
        dropped = sum(len(b["body"].get("dropped") or []) for b in self.store.briefs_since("pt_dropped", now - 7 * DAY))
        return {"pool": len(rows), "checked": len(rows) - status.get("waiting", 0), "waiting": status.get("waiting", 0),
                "bots": status.get("bot", 0) + status.get("busy", 0), "proven": len(proven),
                "joined_7d": len(joined), "dropped_7d": dropped}
