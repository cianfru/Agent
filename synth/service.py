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
        self.market = MarketIndex(store)    # the market's move beside every pick's (synth/market.py)
        self.last_cycle: dict[str, Any] = {}

    # ------------------------------------------------------------ collect --
    async def collect(self) -> dict[str, dict]:
        snaps: dict[str, dict] = {}
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                snaps["cowboy"] = await cowboy.fetch(self.s.cowboy_base_url, http)
        except Exception as e:  # noqa: BLE001 - one source down must not stop the other
            log.warning("cowboy fetch failed: %s", e)
        if self.reflex:
            try:
                snaps["reflex"] = await reflex.fetch(self.reflex)
            except Exception as e:  # noqa: BLE001
                log.warning("reflex fetch failed: %s", e)
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
            self.store.prune_series(started - SERIES_KEEP_S)
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
            tracked.update(await self.positions.step(mids=self.tracker.last_mids))
        except Exception as e:  # noqa: BLE001 - position traders must not stop the poll
            log.warning("position traders failed: %s", e)
        try:
            tracked["toxic_alerts"] = await self._toxic_alerts(snaps.get("reflex"), seeding)
        except Exception as e:  # noqa: BLE001
            log.warning("toxic flow check failed: %s", e)
        for name, cur in snaps.items():
            self.store.put_snapshot(name, cur)
        scored = self.score_picks(snaps)
        try:
            if self.market.ready:
                async with httpx.AsyncClient(timeout=20) as http:     # used once a day, for the basket's coins
                    await self.market.step(self.tracker.last_mids, started, HL(http))
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
        await notify.telegram(self.s.telegram_token, self.s.telegram_chat_id, text)
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
            await notify.telegram(self.s.telegram_token, self.s.telegram_chat_id, text)
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
            await notify.telegram(self.s.telegram_token, self.s.telegram_chat_id, text)
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
        await notify.telegram(self.s.telegram_token, self.s.telegram_chat_id,
                              f"Draft desk post (approve it in the dashboard, Posts tab)\n\n{text}")
        return {"id": pid, "status": "draft", "text": text, "pick_id": pick_id}

    # ---------------------------------------------------------- Outrider on X --
    async def persona_post(self, publish: bool | None = None, now: float | None = None,
                           data: dict | None = None) -> dict | None:
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
        await self.send_draft(pid)
        return {"id": pid, "status": "draft", "text": text, "story": story["key"]}

    async def send_draft(self, pid: int) -> None:
        """A draft on Telegram with Post and Skip; replying to it with new text replaces the text."""
        p = self.store.get_post(pid)
        s = p.get("story") or {}
        head = f"Outrider draft #{pid} · {s.get('kind', '')}{' ' + s['coin'] if s.get('coin') else ''} · {len(p['text'])} chars"
        msg = await notify.telegram(self.s.telegram_token, self.s.telegram_chat_id,
                                    f"{head}\n\n{p['text']}\n\nReply with new text to change it.",
                                    buttons=notify.post_buttons(pid))
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
        await notify.telegram(self.s.telegram_token, self.s.telegram_chat_id,
                              f"Draft scorecard (approve it in the dashboard, Posts tab)\n\n{text}")
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
        if p["status"] in ("published", "running"):
            return p  # never post twice
        if p["kind"] == "persona":
            story = p.get("story") or {}
            problems = persona.lint(p["text"], story, check_numbers=not story.get("edited") and "facts" in story)
        else:
            problems = post.lint(p["text"])
        if problems:
            raise ValueError("; ".join(problems))
        try:
            res = await orbio.post_x(self.s.orbio_api_key, p["text"], self.s.orbio_max_cost)
        except orbio.OrbioError as e:
            self.store.update_post(post_id, status="failed", error=str(e))
            return {**p, "status": "failed", "error": str(e)}
        status = "running" if res.get("status") == "running" else "published"
        self.store.update_post(post_id, status=status, orbio_post_id=res.get("post_id"), url=res.get("url"),
                               error=res.get("error"), published_ts=time.time())
        return self.store.get_post(post_id)

    # ------------------------------------------------------------- journal --
    def score_picks(self, snaps: dict[str, dict]) -> int:
        """Fill 1d/3d/7d returns for logged picks once each horizon has passed (and 14d/30d for position traders')."""
        now, n = time.time(), 0
        for p in self.store.open_picks():
            hl_px = self.tracker.last_mids.get(p["coin"].upper())
            # group, position and read picks were entered at Hyperliquid's mid, so they are scored on it too
            hl_entry = p.get("origin") in ("group", "position", "read")
            px = (hl_px or price_of(p["coin"], snaps)) if hl_entry else (price_of(p["coin"], snaps) or hl_px)
            if not px or not p["entry_price"]:
                continue
            horizons = dict(HORIZONS)
            if p.get("origin") == "position":
                # scored within a day of the horizon, never late (a late price is not the 14- or 30-day price)
                horizons.update({h: s for h, s in LONG_HORIZONS.items() if now - p["ts"] < s + 86400})
            for h, secs in horizons.items():
                if p[f"ret_{h}"] is None and now - p["ts"] >= secs:
                    ret = px / p["entry_price"] - 1
                    if p["direction"] == "short":
                        ret = -ret
                    self.store.set_pick_return(p["id"], h, round(ret, 5))
                    n += 1
        return n


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
