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
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import board, bot, config, notify
from . import groups as G
from .service import Agent, build_digest, coin_context, make_agent

log = logging.getLogger(__name__)


async def _loop(agent: Agent) -> None:
    while True:
        try:
            await agent.cycle()
            await _maybe_daily_brief(agent)
            await _maybe_desk_posts(agent)
            await _maybe_pulse(agent)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("cycle failed")
        await asyncio.sleep(agent.s.poll_seconds)


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
    await notify.telegram(agent.s.telegram_token, agent.s.telegram_chat_id, read["text"])


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
def ask(q: Question):
    if not agent.synth:
        raise HTTPException(503, "no LLM configured (set ANTHROPIC_API_KEY)")
    snaps = {n: s for n in ("cowboy", "reflex") if (s := agent.store.get_snapshot(n))}
    digest = build_digest(snaps, agent.store.events(time.time() - 7 * 86400, limit=80))
    answer, usage = agent.synth.ask(q.question, digest)
    return {**answer.model_dump(), "usage": usage}


@app.get("/picks", dependencies=[Depends(auth)])
def picks(limit: int = 100):
    return {"picks": agent.store.picks(limit)}


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
