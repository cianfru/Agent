"""The agent's pictures, from its own data: a setup on its daily chart, the market map, the record against its bar.

Built at no cost: one Hyperliquid candle request for a setup's coin (about 40 weight of the 1,200 a minute), the
screen and the journal the agent already keeps, rendering in a worker thread (synth/chart.py). Every function returns
None rather than raising when there is nothing to draw, so a picture never holds a message up.
"""

from __future__ import annotations

import asyncio
import logging
import time

from . import chart, journal, screen
from .hyperliquid import HL

log = logging.getLogger(__name__)

DAY = 86400
STRENGTH = {1: "light", 2: "firm", 3: "strong"}


def z_history(store, coin: str, now: float) -> list[tuple[float, float]]:
    """The coin's daily z-score as the screen logged it each day (from 3 November 2026), then the hourly reading now."""
    out = [(time.mktime(time.strptime(r["day"], "%Y-%m-%d")) - time.timezone, r["z_1d"])
           for r in store.screen_log(coin.upper()) if r.get("z_1d") is not None]
    z = (((store.get_snapshot("engine_all") or {}).get("c") or {}).get(coin.upper()) or {}).get("z_1d")
    if z is not None:
        out.append((now, z))
    return out


def entries(agent, coin: str, now: float) -> list[tuple[float, float, str]]:
    """Tracked traders' open positions on the coin with a known open time: (when, entry, side)."""
    out = []
    for t in agent.positions.board(now).get("traders") or []:
        for p in t.get("positions") or []:
            if p["coin"] == coin.upper() and p.get("since") and p.get("entry"):
                out.append((p["since"], p["entry"], p["side"]))
    return out


async def setup_png(agent, coin: str, side: str, strength: int | str = 1, risk: dict | None = None,
                    now: float | None = None, hl: HL | None = None) -> bytes | None:
    """One setup on its daily chart (400 days read, the last 120 shown)."""
    now = now or time.time()
    own = hl is None
    hl = hl or HL()
    try:
        await hl.ensure_names()
        end = int(now * 1000)
        bars = await hl.candles(coin, "1d", end - 400 * DAY * 1000, end)
    except Exception as e:  # noqa: BLE001 - no picture, the text still goes
        log.warning("candles for the %s chart failed: %s", coin, e)
        return None
    finally:
        if own:
            await hl.aclose()
    if len(bars) < 30:
        return None
    word = STRENGTH.get(strength, strength) if isinstance(strength, int) else strength
    try:
        return await asyncio.to_thread(chart.setup_chart, coin.upper(), side, word, bars, risk,
                                       z_history(agent.store, coin, now), entries(agent, coin, now))
    except Exception as e:  # noqa: BLE001
        log.warning("the %s setup chart failed: %s", coin, e)
        return None


def map_caption(table: dict) -> str:
    b = table["breadth"]
    return (f"Market map · {table['coins']} coins. Over the band {b['above'] + b['just above'] + b['extended']} · "
            f"inside {b['inside']} · under {b['below']} · over the 200-day {b['over_200d']} of {b['with_200d']} · "
            f"stretched (z 2.5+) {b.get('stretched', 0)} · washed out (z -1 or lower) {b.get('washed_out', 0)}. "
            "Top right: stretched over the band; bottom left: under it and washed out. Context, not a signal.")


async def map_png(agent, now: float | None = None, label: list[str] = ()) -> tuple[bytes, str] | None:
    """The market map and its caption, from the screen."""
    now = now or time.time()
    try:
        table = await asyncio.to_thread(screen.build, agent, now)
        if not table["rows"]:
            return None
        png = await asyncio.to_thread(chart.market_map, table["rows"], screen.candidates(table), list(label))
        return png, map_caption(table)
    except Exception as e:  # noqa: BLE001
        log.warning("market map failed: %s", e)
        return None


async def journal_png(store) -> bytes | None:
    """S5's setups against the declared bar."""
    try:
        picks = store.picks(limit=100_000, origin="read")
        return await asyncio.to_thread(chart.journal_chart, picks, journal.S5_BAR, journal.S5_SETUPS_FROM)
    except Exception as e:  # noqa: BLE001
        log.warning("journal chart failed: %s", e)
        return None


def chart_frame(agent, coin: str, side: str | None = None, now: float | None = None) -> tuple[str, dict | None]:
    """For /chart COIN: the side asked, or else the one the screen scores higher, with its risk frame."""
    t = screen.build(agent, now or time.time())
    row = next((r for r in t["rows"] if r["coin"] == coin.upper()), None)
    if not row:
        return side or "long", None
    side = side or ("long" if row["score_long"] >= row["score_short"] else "short")
    return side, {**row[f"levels_{side}"], "price": row["price"]}
