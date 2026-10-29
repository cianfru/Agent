"""HTTP API. Run: uvicorn synth.api:app

GET  /health                      liveness + last cycle stats (no auth)
GET  /events?hours=24&coin=&min_score=
GET  /coin/{coin}                 both sources' current view of one coin + its recent events
GET  /brief                       latest daily brief
POST /brief                       generate a brief now
POST /ask   {"question": "..."}   free-form question answered from current data
GET  /picks                       journal of logged picks with 1d/3d/7d returns
POST /cycle                       run one poll cycle now

If SYNTH_API_KEY is set, every route except /health needs header `X-API-Key`.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import contextlib
import datetime as dt
import logging
import time

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import board, bot, config, desk, notify, study, turns
from . import groups as G
from .service import Agent, build_digest, coin_context, make_agent

log = logging.getLogger(__name__)


async def _loop(agent: Agent) -> None:
    while True:
        try:
            await agent.cycle()
            if agent.analyst:
                await _maybe_read(agent)          # the reasoned read replaces the code-built brief and pulse
            else:
                await _maybe_daily_brief(agent)
                await _maybe_pulse(agent)
            await _maybe_desk_posts(agent)
            await _maybe_persona(agent)
            await _maybe_reflex_history(agent)
            if agent.positions.enabled and agent.positions.discover_due():
                asyncio.create_task(_discover(agent))   # daily, in the background (about 15 minutes)
            if agent.market.due(time.time()):
                asyncio.create_task(_rebuild_market(agent))   # once: the market's hours back to the first pick
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("cycle failed")
        await asyncio.sleep(agent.s.poll_seconds)


async def _maybe_read(agent: Agent) -> None:
    """Outrider's read when one is due: the morning read, enough change, or a group turning against his position."""
    try:
        await agent.analyst.step()
    except Exception:  # noqa: BLE001 - the next poll tries again
        log.exception("read failed")


async def _maybe_reflex_history(agent: Agent) -> None:
    """Reflex's market history (uptrend share by day, the current risk-on or risk-off run): once a day, kept small."""
    if not agent.reflex:
        return
    snap = agent.store.get_snapshot("reflex_history")
    if snap and "turns" in snap and time.time() - (snap.get("fetched") or 0) < 20 * 3600:
        return
    try:
        body = await agent.reflex.get("/api/market-history")
    except Exception as e:  # noqa: BLE001 - context only; tried again next poll
        log.warning("reflex market history failed: %s", e)
        return
    agent.store.put_snapshot("reflex_history", {**desk.compact_history(body), "turns": turns.turns(body),
                                                "fetched": time.time()})


async def _rebuild_market(agent: Agent) -> None:
    from .hyperliquid import HL
    hl = HL()
    try:
        added = await agent.market.ensure(hl)
        if added:
            log.info("market index rebuilt: %d hours", added)
    finally:
        await hl.aclose()


async def _discover(agent: Agent) -> None:
    try:
        await agent.positions.discover()
    except Exception:  # noqa: BLE001 - tried again the next day
        log.exception("trader discovery failed")
        agent.positions.discovering = False


async def _maybe_daily_brief(agent: Agent) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    if now.hour < agent.s.brief_hour_utc:
        return
    last = agent.store.latest_brief("daily")
    today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if last and last["ts"] >= today:
        return
    await agent.brief()


async def _maybe_desk_posts(agent: Agent) -> None:
    """One daily post after SYNTH_POST_HOUR_UTC; the scorecard on Mondays at the same hour."""
    now = dt.datetime.now(dt.timezone.utc)
    if now.hour < agent.s.post_hour_utc or not agent.synth:
        return
    today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if not agent.store.posts(limit=1, kind="daily", since=today):
        await agent.desk_post()
    if now.weekday() == 0 and not agent.store.posts(limit=1, kind="scorecard", since=today):
        await agent.weekly_scorecard()


async def _maybe_persona(agent: Agent) -> None:
    """One Outrider post per configured UTC hour (default 09, 13, 17, 21): a draft to Telegram unless live."""
    now = dt.datetime.now(dt.timezone.utc)
    if not agent.persona or now.hour not in agent.s.persona_hours_utc:
        return
    slot = now.replace(minute=0, second=0, microsecond=0).timestamp()
    if agent.store.posts(limit=1, kind="persona", since=slot) or (
            (last := agent.store.latest_brief("persona_slot")) and last["ts"] >= slot):
        return
    agent.store.add_brief("persona_slot", {"hour": now.hour})     # once per slot, even when nothing is posted
    try:
        await agent.persona_post()
    except Exception:  # noqa: BLE001 - the next slot tries again
        log.exception("persona post failed")


async def _maybe_pulse(agent: Agent) -> None:
    """The market read, once per configured UTC hour (default 06, 10, 14, 18): none at night."""
    now = dt.datetime.now(dt.timezone.utc)
    if now.hour not in agent.s.pulse_hours_utc:
        return
    slot = now.replace(minute=0, second=0, microsecond=0).timestamp()
    last = agent.store.latest_brief("pulse")
    if last and last["ts"] >= slot:
        return
    since = max(last["ts"] if last else 0, time.time() - 12 * 3600)
    read = agent.market_read(since)
    agent.store.add_brief("pulse", {"text": read["text"], "stories": len(read["stories"])})
    await send_pulse(agent, read["text"])


async def send_pulse(agent: Agent, text: str | None = None) -> None:
    """The pulse picture with the read as its caption (or the picture, then the read, if it is too long)."""
    tok, chat = agent.s.telegram_token, agent.s.telegram_chat_id
    try:
        p = await asyncio.to_thread(agent.pulse)
    except Exception as e:  # noqa: BLE001 - the text still goes out without the picture
        log.warning("pulse picture failed: %s", e)
        if text:
            await notify.telegram(tok, chat, text)
        return
    if text and len(text) <= 1024:
        await notify.telegram_photo(tok, chat, p["png"], text)
        return
    await notify.telegram_photo(tok, chat, p["png"], p["summary"])
    if text:
        await notify.telegram(tok, chat, text)


def _snaps() -> dict:
    return {n: s for n in ("cowboy", "reflex") if (s := agent.store.get_snapshot(n))}


STATIC = Path(__file__).parent / "static"
settings = config.load()
agent = make_agent(settings)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = [asyncio.create_task(_loop(agent))] if settings.poll_seconds > 0 else []
    if settings.poll_seconds > 0:
        tasks.append(asyncio.create_task(bot.run(agent)))
        tasks.append(asyncio.create_task(agent.positions.refresh.run_forever(lambda: agent.positions.enabled)))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Market synthesizer", lifespan=lifespan)


def auth(x_api_key: str | None = Header(default=None)) -> None:
    # Fail closed: with no SYNTH_API_KEY the data routes stay shut unless SYNTH_ALLOW_OPEN=1 (local use).
    if not settings.api_key:
        if os.environ.get("SYNTH_ALLOW_OPEN") == "1":
            return
        raise HTTPException(503, "set SYNTH_API_KEY (or SYNTH_ALLOW_OPEN=1 for local use)")
    if not hmac.compare_digest(x_api_key or "", settings.api_key):
        raise HTTPException(401, "bad or missing X-API-Key")


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "dashboard.html")


@app.get("/board", dependencies=[Depends(auth)])
def smart_money_board(hours: float = 24):
    """Where the layers line up per coin, plus recent events."""
    events = agent.store.events(time.time() - hours * 3600, limit=500)
    return {"rows": board.smart_board(_snaps(), events),
            "market": {n: s.get("market") for n, s in _snaps().items()},
            "as_of": {n: s.get("generated_at") for n, s in _snaps().items()}}


@app.get("/pulse", dependencies=[Depends(auth)])
def pulse(hours: float = 4, min_score: float = 0):
    """Events of the last `hours`, and the market read for that window."""
    events = agent.store.events(time.time() - hours * 3600, min_score=min_score, limit=500)
    read = agent.market_read(time.time() - hours * 3600)
    return {"hours": hours, "events": events, "text": read["text"], "stories": read["stories"],
            "sentiment": read["sentiment"]}


@app.get("/health")
def health():
    return {"ok": True, "llm": bool(agent.synth), "reflex": bool(agent.reflex), "last_cycle": agent.last_cycle}


@app.get("/events", dependencies=[Depends(auth)])
def events(hours: float = 24, coin: str | None = None, min_score: float = 0, limit: int = 100):
    return {"events": agent.store.events(time.time() - hours * 3600, coin, min_score, limit)}


@app.get("/coin/{coin}", dependencies=[Depends(auth)])
def coin(coin: str):
    snaps = {n: s for n in ("cowboy", "reflex") if (s := agent.store.get_snapshot(n))}
    return {
        "coin": coin.upper(),
        "context": coin_context(coin, snaps),
        "events": agent.store.events(time.time() - 7 * 86400, coin, 0, 50),
    }


@app.get("/brief", dependencies=[Depends(auth)])
def latest_brief():
    b = agent.store.latest_brief("daily")
    if not b:
        raise HTTPException(404, "no brief yet; POST /brief to make one")
    return b


@app.post("/brief", dependencies=[Depends(auth)])
async def make_brief(push: bool = False):
    try:
        return await agent.brief(push=push)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


class Question(BaseModel):
    question: str


@app.post("/ask", dependencies=[Depends(auth)])
async def ask(q: Question):
    if not agent.synth:
        raise HTTPException(503, "no LLM configured (set ANTHROPIC_API_KEY)")
    if agent.chat:                          # the same conversation as on Telegram, kept apart as "desk"
        answer = await agent.chat.reply("desk", q.question)
        return {"answer": answer, "coins": [], "usage": {"spent_today_usd": round(agent.chat.spent_today(), 2)}}
    snaps = {n: s for n in ("cowboy", "reflex") if (s := agent.store.get_snapshot(n))}
    digest = build_digest(snaps, agent.store.events(time.time() - 7 * 86400, limit=80))
    answer, usage = await asyncio.to_thread(agent.synth.ask, q.question, digest)
    return {**answer.model_dump(), "usage": usage}


@app.post("/read", dependencies=[Depends(auth)])
async def make_read(send: bool = False):
    """Outrider's read now. send=true pushes it to Telegram whatever the model decides."""
    if not agent.analyst:
        raise HTTPException(503, "reads are off (ANTHROPIC_API_KEY missing or SYNTH_READS=off)")
    return await agent.analyst.run("asked", force_send=send)


@app.get("/reads", dependencies=[Depends(auth)])
def list_reads(days: float = 3):
    """Outrider's recent reads, sent or held back, and the record of its views."""
    from .analyst import record
    return {"reads": agent.store.briefs_since("read", time.time() - days * 86400), "record": record(agent.store)}


@app.get("/picks", dependencies=[Depends(auth)])
def picks(limit: int = 100, origin: str | None = None):
    return {"picks": agent.store.picks(min(limit, 2000), origin or None)}


@app.get("/record", dependencies=[Depends(auth)])
def pick_record():
    """How each kind of pick did over every pick logged: per horizon, share right and median, longs and shorts apart."""
    from . import journal
    return {"kinds": journal.record(agent.store), "reads": journal.reads(agent.store)}


@app.get("/posts", dependencies=[Depends(auth)])
def list_posts(limit: int = 50, kind: str | None = None):
    return {"mode": settings.post_mode, "posts": agent.store.posts(limit, kind)}


@app.post("/posts/daily", dependencies=[Depends(auth)])
async def make_desk_post(publish: bool = False):
    """Write today's post now. publish=false (default) keeps it as a draft whatever the mode."""
    try:
        return await agent.desk_post(publish=publish)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


@app.post("/posts/scorecard", dependencies=[Depends(auth)])
async def make_scorecard(publish: bool = False):
    out = await agent.weekly_scorecard(publish=publish)
    if out is None:
        raise HTTPException(404, "no setups with a 7-day result yet")
    return out


@app.post("/posts/{post_id}/publish", dependencies=[Depends(auth)])
async def publish(post_id: int):
    """Approve a draft: publish it to X through Orbio."""
    try:
        return await agent.publish_post(post_id)
    except KeyError as e:
        raise HTTPException(404, "no such post") from e
    except ValueError as e:
        raise HTTPException(422, f"post fails the checks: {e}") from e


@app.get("/groups", dependencies=[Depends(auth)])
def list_groups(limit: int = 100):
    """Every convergence group, trader by trader, plus the running scoreboard."""
    gs = agent.store.groups(limit=limit)
    members = {g["id"]: agent.store.members(g["id"]) for g in gs}
    out = []
    coins = {n: (snap or {}).get("coins") or {} for n, snap in _snaps().items()}
    for g in gs:
        ms = members[g["id"]]
        if g["venue"] == "hyperliquid":  # member last_px is Hyperliquid's mid for the coin
            last = next((m.get("last_px") for m in ms if m.get("last_px")), None)
        else:  # member last_px is an option mark: compare the coin's index instead
            last = (coins.get("cowboy", {}).get(g["coin"]) or {}).get("price")
        out.append({**g, "members": ms, "levels": G.levels(g, ms) if g["venue"] == "hyperliquid" else {},
                    "now_px": last, "move_pct": G.side_move(g, last)})
    return {"groups": out, "scoreboard": G.scoreboard(gs, members)}


@app.get("/flow", dependencies=[Depends(auth)])
def get_flow(hours: float = 1):
    """What profitable Hyperliquid traders opened and closed in the last `hours`, and the lean over time."""
    return agent.flow(max(0.25, min(hours, 192)))


@app.get("/flow.png", dependencies=[Depends(auth)])
def flow_png():
    """The pulse picture sent to Telegram."""
    return Response(agent.pulse()["png"], media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/desk", dependencies=[Depends(auth)])
def desk_home():
    """The desk's home: headline readings, the crowd against price, where money is moving, three lenses, alerts."""
    return desk.home(agent)


@app.get("/research", dependencies=[Depends(auth)])
def desk_research():
    """The studies as charts, the forward record and Reflex's market cycle."""
    return desk.research(agent)


@app.get("/position-traders", dependencies=[Depends(auth)])
def position_traders():
    """Proven position traders (study S3): records, what they hold now and since when, and the forward record."""
    return agent.positions.board()


@app.get("/study", dependencies=[Depends(auth)])
def study_report():
    """S1 Part L: convergences by whether the engine agreed when they formed, and how they went."""
    gs = agent.store.groups(limit=10_000)
    members = {g["id"]: agent.store.members(g["id"]) for g in gs}
    picks = {}
    for p in agent.store.picks(limit=1_000_000, origin="group"):
        try:
            picks[int(p["thesis"].split("#")[1])] = p
        except (IndexError, ValueError, AttributeError):
            continue
    return study.report(gs, members, picks, agent.s.fast_convergence_hours)


@app.post("/groups/{gid}/follow", dependencies=[Depends(auth)])
def follow_group(gid: int, on: bool = True):
    g = agent.tracker.set_follow(gid, on)
    if not g:
        raise HTTPException(404, "no such group")
    return g


@app.post("/cycle", dependencies=[Depends(auth)])
async def run_cycle():
    out = await agent.cycle()
    return {k: v for k, v in out.items()}
