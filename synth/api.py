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
import contextlib
import datetime as dt
import logging
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import config
from .service import Agent, build_digest, coin_context, make_agent

log = logging.getLogger(__name__)


async def _loop(agent: Agent) -> None:
    while True:
        try:
            await agent.cycle()
            await _maybe_daily_brief(agent)
            await _maybe_desk_posts(agent)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("cycle failed")
        await asyncio.sleep(agent.s.poll_seconds)


async def _maybe_daily_brief(agent: Agent) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    if now.hour < agent.s.brief_hour_utc or not agent.brief_synth:
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


settings = config.load()
agent = make_agent(settings)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_loop(agent)) if settings.poll_seconds > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Market synthesizer", lifespan=lifespan)


def auth(x_api_key: str | None = Header(default=None)) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(401, "bad or missing X-API-Key")


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


@app.post("/cycle", dependencies=[Depends(auth)])
async def run_cycle():
    out = await agent.cycle()
    return {k: v for k, v in out.items()}
