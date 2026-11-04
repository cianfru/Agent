"""One poll cycle: collect -> detect -> dedupe -> alert -> journal. Plus the daily brief."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import time
from typing import Any

import httpx

from . import brief as brief_mod
from . import chart, detectors, flow, notify, orbio, persona, post, stories
from .config import Settings
from .llm import PersonaWriter, Synthesizer
from .hyperliquid import HL
from .levels import Levels
from .market import MarketIndex
from .sources import block0, cowboy, reflex
from .store import Store
from .position_traders import PositionTraders
from .tracking import GroupTracker

log = logging.getLogger(__name__)

COOLDOWN_S = 12 * 3600  # the same event key is not re-alerted within this window
MAX_ALERTS_PER_CYCLE = 2  # bounds noise and model spend if a feed glitch fires everything at once
HORIZONS = {"1d": 86400, "3d": 3 * 86400, "7d": 7 * 86400}
LONG_HORIZONS = {"14d": 14 * 86400, "30d": 30 * 86400}  # position traders' picks (study S3)
SERIES_KEEP_S = 8 * 86400  # per-poll positioning rows for the live flow
TAKER_FEE = 0.00045        # Hyperliquid's base taker fee a side: what a round trip costs before spread
PATH_ORIGINS = ("read", "screen", "random")   # picks whose path to each horizon is recorded (S5 Amendment 3, S6)


class Agent:
    def __init__(self, settings: Settings, store: Store, synth: Synthesizer | None = None,
                 brief_synth: Synthesizer | None = None, persona_writer: PersonaWriter | None = None):
        self.s = settings
        self.store = store
        self.synth = synth
        self.persona = persona_writer
        self.chat = None          # synth/chat.py, set by make_agent when there is an API key
        self.analyst = None       # synth/analyst.py, the same
        self.brief_synth = brief_synth or synth
        self.reflex = (
            reflex.ReflexClient(
                settings.reflex_base_url,
                access_code=settings.reflex_access_code,
                token=settings.reflex_token,
            )
            if settings.reflex_base_url
            else None
        )
        self.tracker = GroupTracker(settings, store, self.reflex)
        self.positions = PositionTraders(settings, store)
        try:
            self.positions.mark_late_upgrades()   # once: S3's amendment, earlier late upgrades reported apart
        except Exception as e:  # noqa: BLE001 - tried again at the next start
            log.warning("marking late conviction upgrades failed: %s", e)
        self.market = MarketIndex(store)    # the market's move beside every pick's (synth/market.py)
        self.levels = Levels(store)         # open interest, funding, 200-day average, support band (synth/levels.py)
        self.last_cycle: dict[str, Any] = {}
        self._pruned = 0.0
        self._refine: list[tuple[int, str, float]] = []    # scored picks whose path awaits hourly highs and lows
        self.sources: dict[str, dict] = {}   # per source: last success, last error, failures in a row (status)

    def mark(self, name: str, ok: bool, err: Exception | str | None = None, now: float | None = None) -> None:
        """Remember how a source or job last went, for /status and /health."""
        now = now or time.time()
        st = self.sources.setdefault(name, {"fails": 0})
        if ok:
            st.update({"ok_ts": now, "fails": 0, "down_since": None})
        else:
            st["fails"] = st.get("fails", 0) + 1
            st["error"] = str(err)[:200]
            st["error_ts"] = now
            st["down_since"] = st.get("down_since") or now

    def status(self, now: float | None = None) -> dict:
        """Every source's state and every job's, with how old each reading is: what /status and /health show."""
        from .hyperliquid import BUDGET
        now = now or time.time()
        snaps = {}
        for name in ("reflex", "cowboy", "hl_ctx", "engine_all", "owner_positions", "trend_levels"):
            row = self.store._db.execute("SELECT ts FROM snapshots WHERE source=?", (name,)).fetchone()
            snaps[name] = round((now - row["ts"]) / 60) if row else None
        last_read = self.store.latest_brief("read") or {}
        lr = self.store.get_snapshot("levels_refresh") or {}
        mlast = self.store.market_last()
        return {"ts": now, "cycle_age_min": round((now - self.last_cycle["ts"]) / 60, 1) if self.last_cycle else None,
                "cycle_secs": self.last_cycle.get("secs"), "data_age_min": snaps,
                "sources": {k: {**v, "ok_ago_min": round((now - v["ok_ts"]) / 60) if v.get("ok_ts") else None}
                            for k, v in self.sources.items()},
                "hyperliquid": BUDGET.stats(),
                "levels_refresh": {k: lr.get(k) for k in ("day", "read", "tried", "errors", "attempt")},
                "market_index": {"ready": self.market.ready,
                                 "last_age_min": round((now - mlast["ts"]) / 60) if mlast else None},
                "last_read": {"ago_min": round((now - last_read["ts"]) / 60) if last_read.get("ts") else None,
                              **{k: last_read.get(k) for k in ("reason", "sent", "error", "cost")}}}

    def healthy(self, now: float | None = None) -> tuple[bool, list[str]]:
        """Down when the poll has not run for three polls, or Reflex has not answered for 30 minutes."""
        now = now or time.time()
        bad = []
        if self.last_cycle and now - self.last_cycle["ts"] > 3 * self.s.poll_seconds + 120:
            bad.append(f"no poll for {round((now - self.last_cycle['ts']) / 60)} minutes")
        r = self.sources.get("reflex") or {}
        if self.reflex and r.get("down_since") and now - r["down_since"] > 1800:
            bad.append(f"Reflex failing since {time.strftime('%H:%M UTC', time.gmtime(r['down_since']))}: "
                       f"{r.get('error', '')[:80]}")
        return not bad, bad

    async def incidents(self, now: float | None = None) -> None:
        """One message per outage that lasts (a source down two hours), counted against the daily cap; none when it
        recovers, the next read says so if it matters."""
        now = now or time.time()
        for name, st in self.sources.items():
            down = st.get("down_since")
            if down and now - down >= 2 * 3600 and not st.get("told"):
                st["told"] = True
                await notify.push(self.s, self.store, f"{name.capitalize()} has not answered since "
                                  f"{time.strftime('%H:%M UTC', time.gmtime(down))} ({st.get('error', '')[:120]}). "
                                  "Reads and alerts go on without it; /status shows the rest.", "status")
            elif not down:
                st["told"] = False

    # ------------------------------------------------------------ collect --
    async def collect(self) -> dict[str, dict]:
        snaps: dict[str, dict] = {}
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                snaps["cowboy"] = await cowboy.fetch(self.s.cowboy_base_url, http)
            self.mark("cowboy", True)
        except Exception as e:  # noqa: BLE001 - one source down must not stop the other
            log.warning("cowboy fetch failed: %s", e)
            self.mark("cowboy", False, e)
        if self.reflex:
            try:
                snaps["reflex"] = await reflex.fetch(self.reflex)
                self.mark("reflex", True)
            except Exception as e:  # noqa: BLE001
                log.warning("reflex fetch failed: %s", e)
                self.mark("reflex", False, e)
        if self.s.coins:
            keep = set(self.s.coins)
            for snap in snaps.values():
                snap["coins"] = {k: v for k, v in snap.get("coins", {}).items() if k in keep}
        return snaps

    # -------------------------------------------------------------- cycle --
    async def cycle(self) -> dict:
        started = time.time()
        snaps = await self.collect()
        fresh: list[dict] = []
        seeding: set[str] = set()
        prevs = {name: self.store.get_snapshot(name) for name in snaps}
        self._record_sentiment(snaps.get("reflex"))
        rec = flow.record(snaps.get("reflex"))
        if rec:
            self.store.add_series("reflex", rec, started)
        for name, cur in snaps.items():
            prev = prevs[name]
            if prev is None:
                seeding.add(name)
            for ev in detectors.run_all(name, prev, cur, self.s.wallet_move_min_usd,
                                            self.s.fast_convergence_hours):
                fresh.append(ev)
        fresh += detectors.cross_source(fresh, snaps)

        new_events = []
        for ev in fresh:
            if self.store.seen_recently(ev["key"], COOLDOWN_S):
                continue
            ev["id"] = self.store.add_event(ev)
            new_events.append(ev)

        alerts = []
        candidates = [
            e for e in sorted(new_events, key=lambda e: -e["score"])
            if e["score"] >= self.s.alert_min_score and e["kind"] in detectors.ALERT_KINDS
            and not ({e["data"].get("source"), *e["data"].get("sources", [])} & seeding)
        ]
        room = self.s.max_alerts_per_day - self.store.count_briefs("alert_sent", time.time() - 86400)
        for ev in candidates[:max(0, min(room, MAX_ALERTS_PER_CYCLE))]:
            alerts.append(await self._alert(ev, snaps))
            self.store.add_brief("alert_sent", {"event_id": ev["id"]})

        try:
            tracked = await self.tracker.step(snaps, prevs, seeding)
        except Exception as e:  # noqa: BLE001 - tracking must not stop the poll
            log.exception("group tracking failed: %s", e)
            tracked = {}
        try:
            tracked.update(await self.positions.step(mids=self.tracker.fresh_mids(started)))
        except Exception as e:  # noqa: BLE001 - position traders must not stop the poll
            log.warning("position traders failed: %s", e)
        try:
            tracked["toxic_alerts"] = await self._toxic_alerts(snaps.get("reflex"), seeding)
        except Exception as e:  # noqa: BLE001
            log.warning("toxic flow check failed: %s", e)
        for name, cur in snaps.items():
            self.store.put_snapshot(name, cur)
        self.store.prune_series(started - SERIES_KEEP_S)          # every poll, whether or not Reflex answered
        if started - self._pruned > 86400:
            self.store.prune_old(started)
            self._pruned = started
        scored = self.score_picks(snaps)
        if self.reflex:                                           # every coin's z-score, once an hour
            try:
                snap = self.store.get_snapshot("engine_all") or {}
                if started - (snap.get("ts") or 0) >= 3600:
                    self.store.put_snapshot("engine_all", {"ts": started, "c": await reflex.engine_all(self.reflex)})
            except Exception as e:  # noqa: BLE001 - the last reading stands
                log.warning("engine readings for every coin failed: %s", e)
        async with httpx.AsyncClient(timeout=20) as http:
            hl = HL(http)
            try:
                await self.levels.step(hl, started)              # every coin's open interest and funding
                self.mark("hyperliquid", True)
            except Exception as e:  # noqa: BLE001 - the last reading stands until the next poll
                log.warning("open interest and funding read failed: %s", e)
                self.mark("hyperliquid", False, e)
            try:
                await self.refine_paths(hl)
            except Exception as e:  # noqa: BLE001 - the hourly closes stand
                log.warning("path refine failed: %s", e)
            try:
                mids = self.tracker.fresh_mids(started)
                if self.market.ready and mids:
                    await self.market.step(mids, started, hl)
                    self.market.fill()
            except Exception as e:  # noqa: BLE001 - the market move waits for the next poll
                log.warning("market index step failed: %s", e)

        self.last_cycle = {
            "ts": started,
            "secs": round(time.time() - started, 2),
            "sources": {k: {"coins": len(v.get("coins", {})), "seeded": k in seeding} for k, v in snaps.items()},
            "new_events": len(new_events),
            "alerts": len(alerts),
            "picks_scored": scored,
            **tracked,
        }
        log.info("cycle %s", self.last_cycle)
        try:
            self.store.put_snapshot("status", self.status())
            await self.incidents()
        except Exception as e:  # noqa: BLE001 - the status never stops the poll
            log.warning("status failed: %s", e)
        return {**self.last_cycle, "events": new_events, "alert_bodies": alerts}

    async def _alert(self, ev: dict, snaps: dict[str, dict]) -> dict:
        context = coin_context(ev.get("coin"), snaps)
        body: dict
        if self.synth and self.s.llm_alerts:
            try:
                alert, usage = self.synth.alert(_public(ev), context)
                body = alert.model_dump()
                self.store.add_brief("alert", {"event_id": ev["id"], **body}, usage)
            except Exception as e:  # noqa: BLE001 - fall back to the plain event
                log.warning("alert synthesis failed: %s", e)
                body = _plain_alert(ev)
        else:
            body = _plain_alert(ev)
        text = notify.format_alert(body, ev)
        if "smart" in self.s.pushes:
            await notify.push(self.s, self.store, text, "smart")
        if ev.get("coin") and body.get("direction") in ("long", "short") and (body.get("conviction") or 0) >= 3:
            self.store.add_pick("alert", ev["coin"], body["direction"], body.get("conviction"),
                                price_of(ev["coin"], snaps), body.get("headline", ""),
                                {"event": _public(ev), "context": context})
        return {"event_id": ev["id"], **body, "text": text}

    # --------------------------------------------------------------- brief --
    async def brief(self, snaps: dict[str, dict] | None = None, push: bool = True) -> dict:
        """Built in code: Derive lean, Hyperliquid today, and the open groups with their levels."""
        snaps = snaps or await self.collect()
        open_groups = [g for g in self.store.groups(status="open", limit=50) if time.time() - g["ts"] <= 3 * 86400]
        rows = sorted(((g, self.store.members(g["id"])) for g in open_groups), key=lambda gm: -len(gm[1]))[:4]
        body: dict[str, Any] = {
            "derive": brief_mod.derive_lines(snaps.get("cowboy")),
            "hyperliquid": brief_mod.hl_today(snaps.get("reflex"), self.s.fast_convergence_hours),
            "groups": [{"id": g["id"], "text": brief_mod.group_line(g, ms)} for g, ms in rows],
        }
        self.store.add_brief("daily", body)
        text = brief_mod.format_brief(body)
        if push:
            await notify.push(self.s, self.store, text, "brief")
        return {**body, "text": text}

    # ------------------------------------------------------------ market read --
    def _record_sentiment(self, reflex: dict | None) -> None:
        """Keep 48h of market-wide smart-money totals, so a read can say what changed."""
        sent = stories.sentiment(reflex)
        if not sent:
            return
        hist = (self.store.get_snapshot("sentiment_history") or {}).get("rows", [])
        now = time.time()
        hist = [h for h in hist if now - h["ts"] <= 48 * 3600]
        hist.append({"ts": now, "long_usd": sent["long_usd"], "short_usd": sent["short_usd"]})
        self.store.put_snapshot("sentiment_history", {"rows": hist})

    # ---------------------------------------------------------- live pulse --
    def flow_rows(self, hours: float, now: float | None = None) -> list[tuple[float, dict]]:
        now = now or time.time()
        return self.store.series("reflex", now - hours * 3600 - flow.MAX_GAP_S)

    def flow(self, hours: float) -> dict:
        """What profitable traders opened and closed over the last `hours` (synth/flow.py)."""
        now = time.time()
        rows = self.flow_rows(max(hours, 6), now)
        return {"window": flow.window(rows, hours, now), "series": flow.series(rows, max(hours, 6), now)}

    def pulse(self) -> dict:
        """The pulse map (1h / 6h / 24h), the 24h lean line, a two-line summary and the picture."""
        now = time.time()
        rows = self.flow_rows(24, now)
        g = flow.grid(rows, now)
        points = flow.series(rows, 24, now)
        return {"grid": g, "points": points, "summary": flow.summary(g["windows"]),
                "png": chart.pulse_chart(points, g, now)}

    def market_read(self, since_ts: float | None = None) -> dict:
        """Overall lean, change since `since_ts`, Derive lean, and coins where smart money and data agree."""
        now = time.time()
        since_ts = since_ts or now - 4 * 3600
        snaps = {n: s for n in ("cowboy", "reflex") if (s := self.store.get_snapshot(n))}
        sent = stories.sentiment(snaps.get("reflex"))
        hist = (self.store.get_snapshot("sentiment_history") or {}).get("rows", [])
        recent = [g for g in self.store.groups(limit=100)
                  if g["status"] == "open" or (g.get("closed_ts") or 0) >= since_ts]
        recent = [(g, self.store.members(g["id"])) for g in recent if now - g["ts"] <= 24 * 3600]
        events = self.store.events(since=since_ts, limit=300)
        found = stories.coin_stories(snaps.get("reflex"), recent, events)
        in_story = {s["coin"] for s in found}
        toxic_only = [t for t in stories.toxic_flows(snaps.get("reflex"), recent, events) if t["coin"] not in in_story]
        text = stories.format_read(sent, stories.change(hist, since_ts), brief_mod.derive_lines(snaps.get("cowboy")),
                                   found, dt.datetime.fromtimestamp(now, dt.timezone.utc),
                                   dt.datetime.fromtimestamp(since_ts, dt.timezone.utc), toxic_only)
        return {"text": text, "sentiment": sent, "stories": found, "toxic": toxic_only}

    async def _toxic_alerts(self, reflex: dict | None, seeding: set[str]) -> int:
        """Ping at once when flow is critically one-sided (VPIN >= 70%) and smart money is on that side."""
        if not reflex or "reflex" in seeding:
            return 0
        now = time.time()
        recent = [(g, self.store.members(g["id"])) for g in self.store.groups(status="open", limit=100)
                  if now - g["ts"] <= 24 * 3600]
        events = self.store.events(since=now - 24 * 3600, limit=300)
        sent = 0
        for t in stories.toxic_flows(reflex, recent, events):
            if not t["aligned"] or t["vpin"] < stories.VPIN_CRITICAL:
                continue
            key = f"toxic:{t['coin']}:{t['flow']}"
            if self.store.seen_recently(key, COOLDOWN_S):
                continue
            text = stories.toxic_alert_text(t)
            self.store.add_event({"key": key, "kind": "toxic_flow", "coin": t["coin"], "direction": t["flow"],
                                  "score": 90, "title": text.splitlines()[0], "data": {"source": "reflex", **t}})
            if self.s.max_alerts_per_day - self.store.count_briefs("alert_sent", now - 86400) <= 0:
                continue
            if "smart" in self.s.pushes:
                await notify.push(self.s, self.store, text, "smart")
            self.store.add_brief("alert_sent", {"toxic": t["coin"]})
            sent += 1
        return sent

    # --------------------------------------------------------- public posts --
    async def desk_post(self, publish: bool | None = None) -> dict:
        """Write today's public post, log its setup as a numbered pick, then draft or publish it."""
        if not self.synth:
            raise RuntimeError("no LLM configured (set ANTHROPIC_API_KEY)")
        snaps = await self.collect()
        digest = build_digest(snaps, self.store.events(since=time.time() - 86400, limit=40))
        try:
            digest["block0"] = await block0.fetch(self.s.block0_stats_url)
        except Exception as e:  # noqa: BLE001 - the chain line is optional
            log.warning("block0 stats unavailable: %s", e)

        today = dt.datetime.now(dt.timezone.utc).date()
        feedback, text, problems, pick_id = "", "", ["not generated"], None
        for _ in range(2):  # one retry with the lint problems fed back
            fields, usage = self.synth.desk_post(digest, feedback)
            self.store.add_brief("desk_post", fields.model_dump(), usage)
            has_setup = bool(fields.setup_coin) and fields.setup_direction in ("long", "short")
            if has_setup and pick_id is None:
                coin = fields.setup_coin.upper().lstrip("$")
                pick_id = self.store.add_pick(
                    "post", coin, fields.setup_direction, None, price_of(coin, snaps),
                    f"{fields.setup_line} | off if: {fields.invalidation_line}",
                    {"post": fields.model_dump(), "coin": coin_context(coin, snaps)},
                )
            elif not has_setup and pick_id is not None:
                self.store.delete_pick(pick_id)
                pick_id = None
            text = post.assemble(fields, self._public_no(pick_id), today)
            problems = post.lint(text)
            if not problems:
                break
            feedback = "; ".join(problems)

        if problems:
            if pick_id is not None:
                self.store.delete_pick(pick_id)
            pid = self.store.add_post("daily", text, "rejected", None, "; ".join(problems))
            return {"id": pid, "status": "rejected", "text": text, "problems": problems}

        pid = self.store.add_post("daily", text, "draft", pick_id)
        live = self.s.post_mode == "live" if publish is None else publish
        if live:
            return await self.publish_post(pid)
        await notify.push(self.s, self.store, f"Draft desk post (approve it in the dashboard, Posts tab)\n\n{text}",
                          "desk_draft")
        return {"id": pid, "status": "draft", "text": text, "pick_id": pick_id}

    # ---------------------------------------------------------- Outrider on X --
    async def persona_post(self, publish: bool | None = None, now: float | None = None,
                           data: dict | None = None, reply: bool = False) -> dict | None:
        """One of Outrider's posts: the best story not told lately, written in the voice, checked, then sent to
        Telegram as a draft (Post or Skip) or published when SYNTH_PERSONA_MODE=live. None when there is no story."""
        if not self.persona:
            raise RuntimeError("no LLM configured (set ANTHROPIC_API_KEY)")
        now = now or time.time()
        if data is None:
            async with httpx.AsyncClient(timeout=30) as http:
                data = await persona.gather(self.s.cowboy_base_url, http)
        found = persona.find_stories(data, self.store.briefs_since("pt_alert", now - 8 * 3600),
                                     self.store.briefs_since("pt_build", now - 86400), now,
                                     winners=self.positions.open_winners(now))
        recent = self.store.posts(limit=60, kind="persona", since=now - 21 * 86400)
        story = persona.choose(found, [p for p in recent if p["status"] != "rejected"], now)
        if not story:
            return None
        texts = [p["text"] for p in recent if p["status"] in ("draft", "published", "running")]
        feedback, text, problems = "", "", ["not written"]
        for _ in range(2):   # one retry with the problems fed back
            task = persona.task(story, texts)
            if feedback:
                task += f"\n\nYour previous draft was rejected: {feedback}. Fix that."
            text, usage = await asyncio.to_thread(self.persona.write, persona.VOICE, task)
            self.store.add_brief("persona_draft", {"key": story["key"], "text": text}, usage)
            problems = persona.lint(text, story)
            if not problems:
                break
            feedback = "; ".join(problems)
        meta = {k: story[k] for k in ("kind", "coin", "key", "cowboy", "facts")}
        if problems:
            pid = self.store.add_post("persona", text, "rejected", None, "; ".join(problems), story=meta)
            return {"id": pid, "status": "rejected", "text": text, "problems": problems}
        pid = self.store.add_post("persona", text, "draft", story=meta)
        live = self.s.persona_mode == "live" if publish is None else publish
        if live:
            return await self.publish_post(pid)
        await self.send_draft(pid, reply=reply)
        return {"id": pid, "status": "draft", "text": text, "story": story["key"]}

    async def send_draft(self, pid: int, reply: bool = False) -> None:
        """A draft on Telegram with Post and Skip; replying to it with new text replaces the text."""
        p = self.store.get_post(pid)
        s = p.get("story") or {}
        head = f"Outrider draft #{pid} · {s.get('kind', '')}{' ' + s['coin'] if s.get('coin') else ''} · {len(p['text'])} chars"
        msg = await notify.push(self.s, self.store, f"{head}\n\n{p['text']}\n\nReply with new text to change it.",
                                "persona_draft", buttons=notify.post_buttons(pid), reply=reply)
        if msg and msg.get("message_id"):
            self.store.update_post(pid, tg_msg=msg["message_id"])

    def edit_post(self, pid: int, text: str) -> list[str]:
        """The owner's own wording for a draft. Checked like any post, except the facts check (the owner may add
        what they know). Returns the problems; an empty list means it was saved."""
        p = self.store.get_post(pid)
        if not p or p["status"] not in ("draft", "rejected"):
            return ["that draft is no longer open"]
        story = {**(p.get("story") or {}), "edited": True}
        problems = persona.lint(text.strip(), story, check_numbers=False)
        if not problems:
            self.store.update_post(pid, text=text.strip(), story=story, status="draft", error=None)
        return problems

    async def weekly_scorecard(self, publish: bool | None = None) -> dict | None:
        nos = self._public_numbers()
        picks = [{**p, "id": nos.get(p["id"], p["id"])}
                 for p in self.store.picks(limit=500, origin="post", since=time.time() - 30 * 86400)]
        text = post.scorecard(picks, dt.datetime.now(dt.timezone.utc).date())
        if not text:
            return None
        problems = post.lint(text)
        if problems:
            pid = self.store.add_post("scorecard", text, "rejected", None, "; ".join(problems))
            return {"id": pid, "status": "rejected", "problems": problems}
        pid = self.store.add_post("scorecard", text, "draft")
        live = self.s.post_mode == "live" if publish is None else publish
        if live:
            return await self.publish_post(pid)
        await notify.push(self.s, self.store, f"Draft scorecard (approve it in the dashboard, Posts tab)\n\n{text}",
                          "scorecard_draft")
        return {"id": pid, "status": "draft", "text": text}

    def _public_numbers(self) -> dict[int, int]:
        """Desk setups are numbered 1, 2, 3... in the order they were logged (alert/brief picks excluded)."""
        ids = sorted(p["id"] for p in self.store.picks(limit=1_000_000, origin="post"))
        return {pid: i + 1 for i, pid in enumerate(ids)}

    def _public_no(self, pick_id: int | None) -> int | None:
        return self._public_numbers().get(pick_id) if pick_id else None

    async def publish_post(self, post_id: int) -> dict:
        p = self.store.get_post(post_id)
        if not p:
            raise KeyError(post_id)
        if p["status"] in ("published", "running", "posting"):
            return p  # never post twice
        if p["kind"] == "persona":
            story = p.get("story") or {}
            problems = persona.lint(p["text"], story, check_numbers=not story.get("edited") and "facts" in story)
        else:
            problems = post.lint(p["text"])
        if problems:
            raise ValueError("; ".join(problems))
        if not self.store.claim_post(post_id):          # a second tap while the first is sending: posted once
            return self.store.get_post(post_id)
        try:
            res = await orbio.post_x(self.s.orbio_api_key, p["text"], self.s.orbio_max_cost)
        except orbio.OrbioError as e:
            self.store.update_post(post_id, status="failed", error=str(e))
            return {**p, "status": "failed", "error": str(e)}
        except Exception as e:
            self.store.update_post(post_id, status="failed", error=str(e)[:300])
            raise
        status = "running" if res.get("status") == "running" else "published"
        self.store.update_post(post_id, status=status, orbio_post_id=res.get("post_id"), url=res.get("url"),
                               error=res.get("error"), published_ts=time.time())
        return self.store.get_post(post_id)

    # ------------------------------------------------------------- journal --
    def price_now(self, coin: str, now: float | None = None) -> float | None:
        """Hyperliquid's price for a coin now: the poll's mid when fresh, else the context read this poll (mark)."""
        now = now or time.time()
        c = coin.upper()
        px = self.tracker.fresh_mids(now).get(c)
        if px:
            return px
        snap = self.store.get_snapshot("hl_ctx") or {}
        if now - (snap.get("ts") or 0) <= max(2 * self.s.poll_seconds, 900):
            return ((snap.get("c") or {}).get(c) or {}).get("px")
        return None

    def score_picks(self, snaps: dict[str, dict]) -> int:
        """Fill 1d/3d/7d returns for logged picks once each horizon has passed (and 14d/30d for position traders').

        Each horizon is priced at its own time: from the hourly copy of every coin's price (levels.py, kept 8 days)
        nearest the horizon, or from the price now while the horizon is under two hours old. A pick whose horizon
        price is in neither (an outage longer than the copy's 8 days, a coin Hyperliquid does not list) stays
        unscored and is counted as such; it is never scored at a later price as if it were the horizon's."""
        now, n = time.time(), 0
        for p in self.store.open_picks():
            if not p["entry_price"]:
                continue
            horizons = dict(HORIZONS)
            if p.get("origin") == "position":
                horizons.update(LONG_HORIZONS)
            hl_entry = p.get("origin") in ("group", "position", "read", "screen", "random")
            for h, secs in horizons.items():
                at = p["ts"] + secs
                if p[f"ret_{h}"] is not None or now < at:
                    continue
                def live() -> float | None:
                    return (self.price_now(p["coin"], now) or price_of(p["coin"], snaps)) if hl_entry else \
                        (price_of(p["coin"], snaps) or self.price_now(p["coin"], now))
                late = now - at
                px = live() if late <= max(2 * self.s.poll_seconds, 900) else None   # on time: the price now
                if px is None:
                    px = self.levels.price_at(p["coin"].upper(), at, within=90 * 60, now=now)
                if px is None and late <= 2 * 3600:          # no copy near it yet: the price now, under 2 hours late
                    px = live()
                if not px:
                    continue
                ret = px / p["entry_price"] - 1
                if p["direction"] == "short":
                    ret = -ret
                self.store.set_pick_return(p["id"], h, round(ret, 5))
                if p.get("origin") in PATH_ORIGINS and h in HORIZONS:
                    st = self.path_stats(p, at, ret, now)
                    if st:
                        self.store.update_pick_state(p["id"], {f"path_{h}": st})
                        self._refine.append((p["id"], h, at))   # exact highs and lows next, from hourly candles
                n += 1
        return n

    async def refine_paths(self, hl, limit: int = 8) -> int:
        """The worst and best move on the way to a horizon from Hyperliquid's hourly highs and lows (the hourly copies
        only hold closes, which understate them), and whether the setup's off level was touched intrabar. A few picks a
        poll, one candle request each."""
        done = 0
        while self._refine and done < limit:
            pid, h, at = self._refine.pop(0)
            p = self.store.pick(pid)
            if not p or not p.get("entry_price"):
                continue
            await hl.ensure_names()
            bars = await hl.candles(p["coin"], "1h", int((p["ts"] - 3600) * 1000), int((at + 3600) * 1000))
            bars = [b for b in bars if b[0] / 1000 + 3600 > p["ts"] and b[0] / 1000 < at]
            if not bars:
                continue
            sign, e = (1, p["entry_price"]) if p["direction"] == "long" else (-1, p["entry_price"])
            worst = min(b[3] for b in bars) if sign > 0 else max(b[2] for b in bars)
            best = max(b[2] for b in bars) if sign > 0 else min(b[3] for b in bars)
            st = dict(((p.get("state") or {}).get(f"path_{h}")) or {})
            st.update({"mae_pct": round(100 * min(0.0, sign * (worst / e - 1)), 2),
                       "mfe_pct": round(100 * max(0.0, sign * (best / e - 1)), 2), "exact": True})
            off = (((p.get("state") or {}).get("risk") or {}).get("off") or {}).get("level")
            if off:
                hit = next((b for b in bars if (b[3] <= off if sign > 0 else b[2] >= off)), None)
                st["off_hit"] = hit is not None
                st.pop("off_hit_h", None)
                if hit is not None:
                    st["off_hit_h"] = round(max(0.0, hit[0] / 1000 - p["ts"]) / 3600, 1)
            self.store.update_pick_state(pid, {f"path_{h}": st})
            done += 1
        return done

    def path_stats(self, p: dict, at: float, ret: float, now: float) -> dict | None:
        """What happened on the way to a horizon, from the hourly copies: the worst and best move the pick's way, whether
        it touched the level its setup named as wrong (and after how many hours), the funding it paid, its return
        after a taker round trip and that funding, and BTC's move over the same hours (for the beta-adjusted record).
        Hourly closes understate the extremes a little."""
        sign = 1 if p["direction"] == "long" else -1
        path = self.levels.path(p["coin"].upper(), p["ts"], at, now)
        if len(path) < 2 or not p["entry_price"]:
            return None
        moves = [sign * (px / p["entry_price"] - 1) for _, px, _ in path]
        funding = sum(f for _, _, f in path) * sign               # a long pays positive funding, a short receives it
        out = {"n": len(path), "mae_pct": round(100 * min(0.0, min(moves)), 2),
               "mfe_pct": round(100 * max(0.0, max(moves)), 2), "funding_pct": round(100 * funding, 3),
               "net_pct": round(100 * (ret - 2 * TAKER_FEE - funding), 2)}
        off = (((p.get("state") or {}).get("risk") or {}).get("off") or {}).get("level")
        if off:
            hit = next((t for t, px, _ in path if (px <= off if sign > 0 else px >= off)), None)
            out["off_hit"] = hit is not None
            if hit is not None:
                out["off_hit_h"] = round((hit - p["ts"]) / 3600, 1)
        b0, b1 = self.levels.price_at("BTC", p["ts"], now=now), self.levels.price_at("BTC", at, now=now)
        if b0 and b1:
            out["btc_pct"] = round(100 * (b1 / b0 - 1), 2)
        return out


# ------------------------------------------------------------------ helpers --


def make_agent(settings: Settings) -> Agent:
    synth = brief_synth = None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        log.warning("no ANTHROPIC_API_KEY: running detection-only (plain alerts, no briefs)")
        return Agent(settings, Store(settings.db_path))
    try:
        synth = Synthesizer(settings.model, settings.effort)
        if settings.brief_model and settings.brief_model != settings.model:
            brief_synth = Synthesizer(settings.brief_model, settings.effort)
        writer = PersonaWriter(settings.persona_model)
    except Exception as e:  # noqa: BLE001 - no key: run detection-only
        log.warning("LLM disabled: %s", e)
        writer = None
    agent = Agent(settings, Store(settings.db_path), synth, brief_synth, writer)
    if writer:
        from .analyst import Analyst
        from .chat import Chat
        agent.chat = Chat(agent, settings.chat_model, settings.chat_effort, settings.chat_daily_usd)
        if settings.reads:
            agent.analyst = Analyst(agent, settings.read_model, settings.read_effort, settings.read_daily_usd,
                                    settings.reads_per_day, settings.read_quiet_utc)
    return agent


def _public(ev: dict) -> dict:
    return {k: ev[k] for k in ("kind", "coin", "direction", "score", "title", "data") if k in ev}


def _plain_alert(ev: dict) -> dict:
    return {"headline": ev["title"], "coin": ev.get("coin"), "direction": ev.get("direction") or "watch",
            "conviction": 1 + int(ev["score"] // 25), "why": "", "against": None}


def price_of(coin: str | None, snaps: dict[str, dict]) -> float | None:
    if not coin:
        return None
    for name in ("cowboy", "reflex"):
        px = ((snaps.get(name) or {}).get("coins") or {}).get(coin.upper(), {}).get("price")
        if px:
            return float(px)
    return None


def coin_context(coin: str | None, snaps: dict[str, dict]) -> dict:
    """Everything both sources say about one coin, plus the market backdrop."""
    out: dict[str, Any] = {"market": {k: v.get("market") for k, v in snaps.items()}}
    if not coin:
        return out
    coin = coin.upper()
    for name, snap in snaps.items():
        c = (snap.get("coins") or {}).get(coin)
        if c:
            out[name] = c
        holders = (snap.get("wallets") or {}).get(coin)
        if holders:
            out[f"{name}_smart_holders"] = [h for h in holders if h.get("smart")][:8]
        cohorts = {k: v.get(coin) for k, v in (snap.get("smart_cohorts") or {}).items() if v.get(coin)}
        if cohorts:
            out[f"{name}_cohorts"] = cohorts
        for key in ("convergences", "anomalies", "setups"):
            items = [i for i in snap.get(key) or [] if i.get("coin") == coin]
            if items:
                out[f"{name}_{key}"] = items
    return out


def build_digest(snaps: dict[str, dict], events: list[dict]) -> dict:
    """Compact, model-ready view of both sources (wallet holder lists are trimmed)."""
    digest: dict[str, Any] = {"generated_at": int(time.time()), "events_24h": [
        {k: e[k] for k in ("kind", "coin", "direction", "score", "title")} for e in events
    ]}
    for name, snap in snaps.items():
        slim = {k: v for k, v in snap.items() if k not in ("wallets", "errors")}
        if snap.get("wallets"):
            slim["smart_holders"] = {
                coin: [{k: h[k] for k in ("rank", "cohort", "net_delta_usd") if k in h}
                       for h in hs if h.get("smart")][:5]
                for coin, hs in snap["wallets"].items()
            }
        digest[name] = slim
    return digest
