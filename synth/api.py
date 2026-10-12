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
import base64
import hashlib
import hmac
import re
import os
import contextlib
import datetime as dt
import logging
import time

from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from . import backup, board, bot, config, desk, notify, receipts, study
from . import gate as gate_mod
from .gate import GateError
from . import groups as G
from .service import Agent, build_digest, coin_context, make_agent

log = logging.getLogger(__name__)


_background: set[asyncio.Task] = set()   # a reference to each background task, so none is collected mid-run
LOOP_ERRORS: dict[str, dict] = {}        # the last failure of each stage, for /health


async def _loop(agent: Agent) -> None:
    stages = (("cycle", agent.cycle), ("brief", lambda: _maybe_daily_brief(agent)),
              ("desk posts", lambda: _maybe_desk_posts(agent)), ("persona", lambda: _maybe_persona(agent)),
              ("pulse", lambda: _maybe_pulse(agent)), ("reflex history", lambda: _maybe_reflex_history(agent)),
              ("backup", lambda: _maybe_backup(agent)))
    while True:
        for name, stage in stages:       # one failing stage does not skip the others
            try:
                await stage()
                LOOP_ERRORS.pop(name, None)
            except Exception as e:  # noqa: BLE001 - keep the loop alive
                log.exception("%s failed", name)
                LOOP_ERRORS[name] = {"ts": time.time(), "error": f"{type(e).__name__}: {e}"[:300]}
        if agent.positions.enabled and agent.positions.discover_due():
            t = asyncio.create_task(_discover(agent))   # daily, in the background (about 15 minutes)
            _background.add(t)
            t.add_done_callback(_background.discard)
        await asyncio.sleep(agent.s.poll_seconds)


async def _maybe_backup(agent: Agent) -> None:
    """The journal's daily export (synth/backup.py), when SYNTH_BACKUP_DIR is set."""
    if agent.s.backup_dir:
        await asyncio.to_thread(backup.daily, agent.store, agent.s.backup_dir)


async def _maybe_reflex_history(agent: Agent) -> None:
    """Reflex's market history (uptrend share by day, the current risk-on or risk-off run): once a day, kept small."""
    if not agent.reflex:
        return
    snap = agent.store.get_snapshot("reflex_history")
    if snap and time.time() - (snap.get("fetched") or 0) < 20 * 3600:
        return
    try:
        body = await agent.reflex.get("/api/market-history")
    except Exception as e:  # noqa: BLE001 - context only; tried again next poll
        log.warning("reflex market history failed: %s", e)
        return
    agent.store.put_snapshot("reflex_history", {**desk.compact_history(body), "fetched": time.time()})


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
STARTED = time.time()
gate = gate_mod.from_settings(settings)
SESSION_COOKIE = "desk_session"


def _page_csp(name: str) -> str:
    """A page runs only its own inline script (by hash): text from a feed that reached the page as HTML could not
    run code, or send the API key the dashboard keeps in the browser anywhere."""
    html = (STATIC / name).read_text()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    hashes = " ".join(f"'sha256-{base64.b64encode(hashlib.sha256(x.encode()).digest()).decode()}'" for x in scripts)
    return (f"default-src 'self'; script-src {hashes}; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


DASHBOARD_HEADERS = {"content-security-policy": _page_csp("dashboard.html"), "x-content-type-options": "nosniff",
                     "referrer-policy": "no-referrer"}
RECEIPTS_HEADERS = {"content-security-policy": _page_csp("receipts.html"), "x-content-type-options": "nosniff",
                    "referrer-policy": "strict-origin-when-cross-origin", "cache-control": "public, max-age=300"}


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = [asyncio.create_task(_loop(agent))] if settings.poll_seconds > 0 else []
    if settings.poll_seconds > 0:
        tasks.append(asyncio.create_task(bot.run(agent)))
        tasks.append(asyncio.create_task(agent.positions.refresh.run_forever(lambda: agent.positions.enabled)))
    yield
    for t in tasks:
        t.cancel()


# no /docs, /redoc or /openapi.json: they would list every route without a key
app = FastAPI(title="Market synthesizer", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def auth(x_api_key: str | None = Header(default=None)) -> None:
    """The owner: everything that posts, runs, writes or costs money.
    Fail closed: with no SYNTH_API_KEY the data routes stay shut unless SYNTH_ALLOW_OPEN=1 (local use)."""
    if not settings.api_key:
        if os.environ.get("SYNTH_ALLOW_OPEN") == "1":
            return
        raise HTTPException(503, "set SYNTH_API_KEY (or SYNTH_ALLOW_OPEN=1 for local use)")
    if not hmac.compare_digest((x_api_key or "").encode(), settings.api_key.encode()):   # bytes: any header
        raise HTTPException(401, "bad or missing X-API-Key")


def _owner_key(x_api_key: str | None) -> bool:
    return bool(settings.api_key and x_api_key
                and hmac.compare_digest(x_api_key.encode(), settings.api_key.encode()))


async def reader(request: Request, x_api_key: str | None = Header(default=None)) -> str:
    """Who may read the desk: the owner (API key), or a token holder signed in with their wallet (synth/gate.py)."""
    if _owner_key(x_api_key):
        return "owner"
    who = await gate.session(request.cookies.get(SESSION_COOKIE))
    if who:
        return who
    auth(x_api_key)                                  # the owner's errors: 503 with no key set, else 401
    return "owner"


class Challenge(BaseModel):
    address: str = Field(min_length=42, max_length=42)


class Signed(BaseModel):
    nonce: str = Field(min_length=8, max_length=64)
    signature: str = Field(min_length=10, max_length=2000)


_auth_hits: dict[str, list[float]] = {}


def _throttle(request: Request, per_min: int = 10) -> None:
    ip = request.client.host if request.client else "?"
    now = time.time()
    hits = [t for t in _auth_hits.get(ip, []) if now - t < 60]
    if len(hits) >= per_min:
        raise HTTPException(429, "too many sign-in attempts; wait a minute")
    _auth_hits[ip] = hits + [now]
    if len(_auth_hits) > 10_000:
        _auth_hits.clear()


@app.get("/auth/me")
async def auth_me(request: Request, x_api_key: str | None = Header(default=None)):
    """Who this browser is (owner, holder or nobody) and what the gate asks for."""
    gate_info = {"on": gate.on, "chain_id": gate.chain_id, "token": gate.token or None,
                 "threshold": gate.threshold or None}
    if _owner_key(x_api_key) or (not settings.api_key and os.environ.get("SYNTH_ALLOW_OPEN") == "1"):
        return {"role": "owner", "gate": gate_info}
    who = await gate.session(request.cookies.get(SESSION_COOKIE))
    return {"role": "holder" if who else None, "address": who, "gate": gate_info}


@app.post("/auth/challenge")
def auth_challenge(body: Challenge, request: Request):
    _throttle(request)
    domain = settings.gate_domain or (request.url.hostname or "localhost")
    uri = f"https://{settings.gate_domain}" if settings.gate_domain else str(request.base_url).rstrip("/")
    try:
        return gate.challenge(body.address, domain, uri)
    except GateError as e:
        raise HTTPException(e.status, str(e))


@app.post("/auth/verify")
async def auth_verify(body: Signed, request: Request):
    _throttle(request)
    try:
        res = await gate.verify(body.nonce, body.signature)
    except GateError as e:
        raise HTTPException(e.status, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"could not read the chain: {type(e).__name__}")
    out = JSONResponse({"role": "holder", "address": res["address"], "balance": res["balance"]})
    out.set_cookie(SESSION_COOKIE, res["cookie"], max_age=res["max_age"], httponly=True, secure=True,
                   samesite="strict", path="/")
    return out


@app.post("/auth/logout")
def auth_logout():
    out = JSONResponse({"role": None})
    out.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return out


_public_cache: dict[str, tuple[float, dict]] = {}


def _cached(key: str, build, ttl: float = 60) -> dict:
    """Public routes are read by anyone: built at most once a minute, whoever asks."""
    hit = _public_cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = build()
    _public_cache[key] = (time.time(), value)
    return value


PUBLIC_HEADERS = {"cache-control": "public, max-age=60", "access-control-allow-origin": "*"}


@app.get("/r", include_in_schema=False)
def receipts_page():
    """The public record's page (no key): it reads /r/receipts.json and checks /r/ledger.json in the browser."""
    return FileResponse(STATIC / "receipts.html", headers=RECEIPTS_HEADERS)


@app.get("/r/receipts.json")
def receipts_json():
    """The public record: every call logged at least RECEIPTS_DELAY_H ago, how it went, misses and voids included."""
    body = _cached("receipts", lambda: receipts.public(agent.store, settings.receipts_delay_h * 3600))
    return JSONResponse(body, headers=PUBLIC_HEADERS)


@app.get("/r/ledger.json")
def ledger_json():
    """The whole hash chain behind the record, with calls inside the delay withheld (hashes only)."""
    body = _cached("ledger", lambda: receipts.public_ledger(agent.store, settings.receipts_delay_h * 3600))
    return JSONResponse(body, headers=PUBLIC_HEADERS)


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "dashboard.html", headers=DASHBOARD_HEADERS)


@app.get("/board", dependencies=[Depends(reader)])
def smart_money_board(hours: float = 24):
    """Where the layers line up per coin, plus recent events."""
    events = agent.store.events(time.time() - hours * 3600, limit=500)
    return {"rows": board.smart_board(_snaps(), events),
            "market": {n: s.get("market") for n, s in _snaps().items()},
            "as_of": {n: s.get("generated_at") for n, s in _snaps().items()}}


@app.get("/pulse", dependencies=[Depends(reader)])
def pulse(hours: float = 4, min_score: float = 0):
    """Events of the last `hours`, and the market read for that window."""
    events = agent.store.events(time.time() - hours * 3600, min_score=min_score, limit=500)
    read = agent.market_read(time.time() - hours * 3600)
    return {"hours": hours, "events": events, "text": read["text"], "stories": read["stories"],
            "sentiment": read["sentiment"]}


@app.get("/health")
def health():
    """503 when the poller has not finished a cycle for three intervals (or 15 minutes, whichever is longer)."""
    now = time.time()
    last = (agent.last_cycle or {}).get("ts") or STARTED
    stale = settings.poll_seconds > 0 and now - last > max(3 * settings.poll_seconds, 900)
    body = {"ok": not stale, "llm": bool(agent.synth), "reflex": bool(agent.reflex), "last_cycle": agent.last_cycle,
            "since_last_cycle_s": round(now - last), "errors": LOOP_ERRORS}
    return JSONResponse(body, status_code=503 if stale else 200)


@app.get("/events", dependencies=[Depends(reader)])
def events(hours: float = 24, coin: str | None = None, min_score: float = 0, limit: int = 100):
    return {"events": agent.store.events(time.time() - hours * 3600, coin, min_score, limit)}


@app.get("/coin/{coin}", dependencies=[Depends(reader)])
def coin(coin: str):
    snaps = {n: s for n in ("cowboy", "reflex") if (s := agent.store.get_snapshot(n))}
    return {
        "coin": coin.upper(),
        "context": coin_context(coin, snaps),
        "events": agent.store.events(time.time() - 7 * 86400, coin, 0, 50),
    }


@app.get("/brief", dependencies=[Depends(reader)])
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
    question: str = Field(min_length=1, max_length=500)


@app.post("/ask", dependencies=[Depends(auth)])
def ask(q: Question):
    if not agent.synth:
        raise HTTPException(503, "no LLM configured (set ANTHROPIC_API_KEY)")
    snaps = {n: s for n in ("cowboy", "reflex") if (s := agent.store.get_snapshot(n))}
    digest = build_digest(snaps, agent.store.events(time.time() - 7 * 86400, limit=80))
    answer, usage = agent.synth.ask(q.question, digest)
    return {**answer.model_dump(), "usage": usage}


@app.get("/picks", dependencies=[Depends(reader)])
def picks(limit: int = 100):
    # every pick, voided ones with their reason: the journal is shown whole
    return {"picks": agent.store.picks(min(max(limit, 1), 1000), include_void=True)}


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


@app.get("/groups", dependencies=[Depends(reader)])
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


@app.get("/flow", dependencies=[Depends(reader)])
def get_flow(hours: float = 1):
    """What profitable Hyperliquid traders opened and closed in the last `hours`, and the lean over time."""
    return agent.flow(max(0.25, min(hours, 192)))


@app.get("/flow.png", dependencies=[Depends(reader)])
def flow_png():
    """The pulse picture sent to Telegram."""
    return Response(agent.pulse()["png"], media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/desk", dependencies=[Depends(reader)])
def desk_home():
    """The desk's home: headline readings, the crowd against price, where money is moving, three lenses, alerts."""
    return desk.home(agent)


@app.get("/research", dependencies=[Depends(reader)])
def desk_research():
    """The studies as charts, the forward record and Reflex's market cycle."""
    return desk.research(agent)


@app.get("/position-traders", dependencies=[Depends(reader)])
def position_traders():
    """Proven position traders (study S3): records, what they hold now and since when, and the forward record."""
    return agent.positions.board()


@app.get("/study", dependencies=[Depends(reader)])
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


@app.get("/backup", dependencies=[Depends(auth)])
async def whole_backup():
    """A consistent copy of the whole database, for keeping off this machine (deleted here once sent)."""
    folder = settings.backup_dir or os.path.dirname(os.path.abspath(settings.db_path))
    try:
        path = await asyncio.to_thread(backup.snapshot, agent.store, folder)
    except OSError as e:
        raise HTTPException(507, str(e))
    name = f"synth-{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M}.db"
    return FileResponse(path, filename=name, media_type="application/vnd.sqlite3",
                        background=BackgroundTask(os.remove, path))


@app.post("/cycle", dependencies=[Depends(auth)])
async def run_cycle():
    out = await agent.cycle()
    return {k: v for k, v in out.items()}
