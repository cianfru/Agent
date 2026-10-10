"""Each poll: create groups from both venues, follow every trader in every open group, and message
the owner about new fast groups (chart + Follow buttons) and about groups they follow.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any

import httpx

from . import groups as G
from . import notify
from . import study
from .chart import group_chart
from .config import Settings
from .hyperliquid import HL
from .sources import cowboy
from .store import Store

log = logging.getLogger(__name__)

FRESH_S = 2 * 3600  # only groups that formed in the last 2h are announced (not a restart's backlog)
MAX_DERIVE_GROUPS_PER_POLL = 2  # more than this in one poll looks like a feed change, not trading
DERIVE_DETECTOR = 2  # groups from the full-book detector; earlier ones (top-4 legs) were false and are purged


def first_ts_of(key: str | None) -> float | None:
    """Reflex's dedup key is conv:{coin}:{side}:{int(first_ts)}:{n}."""
    try:
        return float((key or "").split(":")[3])
    except (IndexError, ValueError):
        return None


class GroupTracker:
    def __init__(self, settings: Settings, store: Store, reflex_client=None, hl: HL | None = None):
        self.s = settings
        self.store = store
        self.reflex = reflex_client
        self._hl = hl  # injected in tests; otherwise a client per step (one event loop per step is safe)
        self.hl: HL | None = None
        self.last_mids: dict[str, float] = {}  # Hyperliquid mids from the last step, to score picks on any coin
        self._purge_v1_derive_groups()

    def _purge_v1_derive_groups(self) -> None:
        """Remove Derive groups that cannot be right: the old top-4-legs detector's, and every Derive group
        while they are off (Cowboy's books update once a day, so their 'opens' arrive in daily bursts)."""
        for g in self.store.groups(limit=10_000):
            if g["venue"] != "derive":
                continue
            if not self.s.derive_groups or g["meta"].get("detector") != DERIVE_DETECTOR:
                self.store.delete_group(g["id"])
                log.info("purged Derive group #%s", g["id"])

    # ------------------------------------------------------------------ step --
    async def step(self, snaps: dict[str, dict], prevs: dict[str, dict | None], seeding: set[str],
                   now: float | None = None) -> dict:
        if self._hl is not None:
            self.hl = self._hl
            return await self._step(snaps, prevs, seeding, now)
        async with httpx.AsyncClient(timeout=20) as http:
            self.hl = HL(http)
            return await self._step(snaps, prevs, seeding, now)

    async def _step(self, snaps: dict[str, dict], prevs: dict[str, dict | None], seeding: set[str],
                    now: float | None = None) -> dict:
        now = now or time.time()
        try:
            mids = await self.hl.mids()
        except Exception as e:  # noqa: BLE001
            log.warning("hyperliquid mids failed: %s", e)
            mids = {}
        if mids:
            self.last_mids = mids
        created: list[dict] = []
        if "reflex" in snaps and self.reflex:
            created += await self._hl_groups(snaps["reflex"], mids, now)
        derive_created: list[dict] = []
        if "cowboy" in snaps and self.s.derive_groups:
            derive_created = await self._derive_groups(snaps["cowboy"], now)
            created += derive_created
        burst = len(derive_created) > MAX_DERIVE_GROUPS_PER_POLL
        changes = await self._track(snaps, mids, now)

        announced = 0
        for g in created:
            fresh = now - g["ts"] <= FRESH_S
            if fresh:
                self.store.add_pick("group", g["coin"], g["side"], None, self._price(g, snaps, mids),
                                    f"group #{g['id']}", {"group_id": g["id"], **(g["meta"].get("study") or {})})
            fast = g["venue"] == "derive" or (g["meta"].get("span_h") or 0) <= self.s.fast_convergence_hours
            source = "reflex" if g["venue"] == "hyperliquid" else "cowboy"
            held_back = burst and g["venue"] == "derive"
            if fresh and fast and source not in seeding and not held_back and self._room() > 0:
                await self._announce(g, snaps, mids)
                announced += 1
        for g, m, change in changes:
            if not g["followed"]:
                continue
            members = self.store.members(g["id"])
            if change == "group_closed":
                text = G.summary_text(g, members, self._price(g, snaps, mids))
            else:
                left = sum(1 for x in members if x["status"] in ("open", "reduced"))
                text = G.change_text(g, m, change, left, len(members))
            await notify.telegram(self.s.telegram_token, self.s.telegram_chat_id, text)
        return {"groups_created": len(created), "groups_announced": announced, "member_changes": len(changes)}

    def _room(self) -> int:
        return self.s.max_alerts_per_day - self.store.count_briefs("alert_sent", time.time() - 86400)

    def _price(self, g: dict, snaps: dict, mids: dict) -> float | None:
        if g["venue"] == "hyperliquid" and mids.get(g["coin"]):
            return mids[g["coin"]]
        for name in ("cowboy", "reflex"):
            p = ((snaps.get(name) or {}).get("coins") or {}).get(g["coin"], {}).get("price")
            if p:
                return float(p)
        return None

    # ------------------------------------------------------------ creation --
    async def _hl_groups(self, rf: dict, mids: dict, now: float) -> list[dict]:
        created = []
        for c in rf.get("convergences") or []:
            coin, side = c.get("coin"), c.get("side")
            first = first_ts_of(c.get("key"))
            if not coin or side not in ("long", "short"):
                continue
            key = f"hl:{coin}:{side}:{int(first or c.get('ts') or 0)}"
            g = self.store.group_by_key(key)
            if g and len(self.store.members(g["id"])) >= (c.get("n") or 0):
                continue
            wallets = await self._conv_wallets(coin, side, first)
            if not wallets:
                continue
            new = g is None
            if new:
                ts = c.get("ts") or now
                meta = {"span_h": c.get("span_h"), "n": c.get("n")}
                # S1 Part L: log the engine's reading when the group forms (only if seen within 2h of it).
                meta["study"] = {"logged": now - ts <= FRESH_S, "seen_ts": now, **study.reading(rf, coin, side)}
                gid = self.store.add_group("hyperliquid", coin, side, key, mids.get(coin.upper()), meta, ts=ts)
                g = self.store.get_group(gid)
            for w in wallets:
                entry = w.get("entry_px")
                self.store.add_member(g["id"], {
                    "address": w["address"], "side": side, "entry_px": entry, "size_usd": w.get("size_usd"),
                    "size": (w["size_usd"] / entry) if entry and w.get("size_usd") else None,
                    "opened_ts": w.get("ts") or now,
                })
            if new:
                created.append(self.store.get_group(g["id"]))
        return created

    async def _conv_wallets(self, coin: str, side: str, first_ts: float | None) -> list[dict]:
        try:
            body = await self.reflex.get(f"/api/hyperlens/opens/{coin}?days=2")
        except Exception as e:  # noqa: BLE001
            log.warning("reflex opens %s failed: %s", coin, e)
            return []
        best = None
        for c in body.get("convergences") or []:
            if c.get("side") != side:
                continue
            if first_ts is None or abs((c.get("first_ts") or 0) - first_ts) < 5:
                if best is None or (c.get("n") or 0) > (best.get("n") or 0):
                    best = c
        return (best or {}).get("wallets") or []

    async def _derive_groups(self, cb: dict, now: float) -> list[dict]:
        """Diff each smart wallet's full Derive book against the previous publication."""
        window = self.s.fast_convergence_hours * 3600
        state = self.store.get_snapshot("derive_books") or {}
        pending = [o for o in (self.store.get_snapshot("derive_opens") or {}).get("opens", [])
                   if now - o["opened_ts"] <= window]
        if cb.get("revision") and cb.get("revision") != state.get("revision"):
            smart = sorted({h["address"] for hs in (cb.get("wallets") or {}).values() for h in hs if h.get("smart")})
            books = await self._books(smart)
            prev_books = state.get("books") or {}
            pending += G.book_opens(prev_books, books, now)
            self.store.put_snapshot("derive_books", {"revision": cb.get("revision"),
                                                     "books": {**prev_books, **books}})
        self.store.put_snapshot("derive_opens", {"opens": pending})

        created = []
        by: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for o in pending:
            by[(o["coin"], o["side"])].append(o)
        for (coin, side), opens in by.items():
            existing = next((g for g in self.store.groups(status="open", limit=50)
                             if g["venue"] == "derive" and g["coin"] == coin and g["side"] == side
                             and now - g["ts"] <= window), None)
            if existing:
                for o in opens:
                    self.store.add_member(existing["id"], o)
                continue
            if len({o["address"] for o in opens}) < 2:
                continue
            t0 = min(o["opened_ts"] for o in opens)
            span = round((max(o["opened_ts"] for o in opens) - t0) / 3600, 1)
            gid = self.store.add_group("derive", coin, side, f"derive:{coin}:{side}:{int(t0)}",
                                       (cb.get("coins") or {}).get(coin, {}).get("price"),
                                       {"span_h": span, "detector": DERIVE_DETECTOR}, ts=now)
            for o in opens:
                self.store.add_member(gid, o)
            created.append(self.store.get_group(gid))
        return created

    async def _books(self, addresses: list[str]) -> dict[str, dict]:
        """Full published books (slimmed) for these wallets; wallets Cowboy doesn't publish are skipped."""
        out: dict[str, dict] = {}
        sem = asyncio.Semaphore(8)
        async with httpx.AsyncClient(timeout=20) as http:
            async def one(addr: str) -> None:
                async with sem:
                    try:
                        book = await cowboy.trader_book(self.s.cowboy_base_url, addr, http)
                    except Exception as e:  # noqa: BLE001
                        log.warning("derive book %s failed: %s", addr, e)
                        return
                    if book is not None:
                        out[addr] = G.slim_book(book)
            await asyncio.gather(*(one(a) for a in addresses))
        return out

    # ------------------------------------------------------------ tracking --
    async def _track(self, snaps: dict, mids: dict, now: float) -> list[tuple[dict, dict, str]]:
        changes: list[tuple[dict, dict, str]] = []
        positions: dict[str, Any] = {}
        books: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=20) as http:
            for g in self.store.groups(status="open", limit=200):
                members = self.store.members(g["id"])
                for m in members:
                    if m["status"] not in ("open", "reduced"):
                        continue
                    try:
                        if g["venue"] == "hyperliquid":
                            if m["address"] not in positions:
                                positions[m["address"]] = await self.hl.positions(m["address"])
                            pos = positions[m["address"]].get(g["coin"].upper())
                            fills = None
                            if not pos or pos["side"] != g["side"]:
                                fills = await self.hl.closing_fills(m["address"], g["coin"], int(m["opened_ts"] * 1000))
                            upd, change = G.hl_update(g, m, pos, fills, mids.get(g["coin"].upper()), now)
                        else:
                            if m["address"] not in books:
                                books[m["address"]] = await cowboy.trader_book(self.s.cowboy_base_url, m["address"], http)
                            book = books[m["address"]]
                            if book is None:
                                continue  # trader not published: leave as is
                            upd, change = G.derive_update(m, book.get(m["instrument"]), now)
                    except Exception as e:  # noqa: BLE001 - one wallet failing must not stop the rest
                        log.warning("tracking %s in #%s failed: %s", m["address"], g["id"], e)
                        continue
                    self.store.update_member(m["id"], **upd)
                    if change:
                        changes.append((g, {**m, **upd}, change))
                members = self.store.members(g["id"])
                if members and all(x["status"] in ("closed", "expired") for x in members):
                    self.store.update_group(g["id"], status="closed", closed_ts=now)
                    changes.append((g, {}, "group_closed"))
        return changes

    # ------------------------------------------------------------ announce --
    async def _announce(self, g: dict, snaps: dict, mids: dict) -> None:
        members = self.store.members(g["id"])
        now_px = self._price(g, snaps, mids)
        text = G.caption(g, members, now_px)
        buttons = notify.follow_buttons(g["id"])
        sent = None
        if g["venue"] == "hyperliquid":
            try:
                end = int(time.time() * 1000)
                candles = await self.hl.candles(g["coin"], "15m", end - 48 * 3600 * 1000, end)
                lv = G.levels(g, members)
                png = group_chart(g["coin"], g["side"], candles, members, lv.get("all_losing"),
                                  text.splitlines()[0])
                sent = await notify.telegram_photo(self.s.telegram_token, self.s.telegram_chat_id, png, text, buttons)
            except Exception as e:  # noqa: BLE001 - fall back to text
                log.warning("group chart failed: %s", e)
        if sent is None:
            sent = await notify.telegram(self.s.telegram_token, self.s.telegram_chat_id, text, buttons)
        self.store.update_group(g["id"], alerted=1)
        self.store.add_brief("alert_sent", {"group_id": g["id"]})

    # ------------------------------------------------------------ follow --
    def set_follow(self, gid: int, on: bool) -> dict | None:
        g = self.store.get_group(gid)
        if g:
            self.store.update_group(gid, followed=1 if on else 0)
            g = self.store.get_group(gid)
        return g
