"""Talk to Outrider: the owner's market agent as a conversation, on Telegram and on the desk's Ask tab.

The model answers from the agent's own data through tools (the market now, one coin, tracked traders, one trader,
open winners, where money is moving, recent alerts, the studies) and keeps short notes the owner asks it to remember.
Context, never advice: it cannot place trades and never makes a call. Each conversation keeps its last exchanges as
plain text (no reasoning carried between turns), and a daily budget in dollars stops it before the credits run out.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from typing import Any

import anthropic

from . import desk, flow
from .llm import FALLBACK_MODELS
from .wallet_names import codename

log = logging.getLogger(__name__)

HISTORY = 12                 # messages kept per conversation (6 exchanges)
MAX_ROUNDS = 6               # tool rounds per answer
RESULT_CHARS = 12_000        # a tool result is cut here so one answer cannot burn the budget
# $ per million tokens: input, output, cache read, cache write (5 minutes). Prices of 6 October 2026.
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20, 5.0), "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
          "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125)}

SYSTEM = """You are Outrider, the owner's private market agent. You watch crypto markets for him and answer his \
questions in a Telegram chat. He built you and the products you read; talk to him like a sharp trading partner who \
knows the data cold: direct, concise, no filler.

What you can see (call the tools; never answer about the market from memory):
- Reflex: profitable Hyperliquid traders (its roster: best monthly returns), how they are positioned per coin, and \
its RCCE engine's regime and signals per coin (daily and 4-hour).
- Cowboy: Derive's options (implied volatility, skew, where the big call and put bets sit), Derive's best option \
wallets, and the Saloon (higher-or-lower questions priced from options).
- Tracked traders: "proven" (5+ trades held 3+ days, open ones counted at their unrealised profit, in profit with \
wins at least 1.5x losses) and "riding a winner" (big open profit, or a position open 14+ days up 30%+ on price or \
200%+ on margin). The list is rerun daily on every profitable Hyperliquid account of $10K+.
- Big money: accounts of $500K+ in profit all-time, one vote per owner.
- Open winners: the biggest unrealised profits among everyone the agent reads, with time held, move on price and \
return on margin.
- The studies (rules fixed before results): S1 convergences of profitable traders did no better than single opens \
(context); S2 the crowd leans against the move rather than leading it (context); S3 proven traders' conviction bets \
(2x their usual size) met the bar in the backtest, confirmed only by the live record (promising, not proven); S4 \
build-ups pick coins that hold up better than the market but do not run more often (context).

How you answer:
- Every number comes from a tool result. If the data is missing or stale, say so plainly.
- Context, not advice. Never tell him to buy or sell, never predict a price. When he asks what you think, lay out \
what the data shows for and against, and what would change the picture.
- Unrealised profit counts: a trader up big on an open position matters as much as one who closed.
- Plain text for a phone: short paragraphs or a few lines, no markdown headers or tables, no emojis. Name traders by \
codename with the short address.
- If he asks you to remember something (a position he holds, a preference), use remember; if he asks you to drop it, \
use forget. Notes you hold are listed below.
- You cannot trade, post or change settings; say so if asked."""

TOOLS = [
    {"name": "market_overview", "description": "The market now: profitable traders' share long and its 24h change, "
     "big money and tracked traders' share long, the market cycle (share of coins in an uptrend, run length), BTC, "
     "BTC and ETH direction by each group, the largest coins with how the three groups lean, alerts in 24h, and the "
     "latest market read.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "coin", "description": "Everything on one coin: price, Reflex engine signals and regime, profitable "
     "traders long/short, tracked traders holding it, big money owners, Derive options read, its open winners, what "
     "tracked traders added in 7 days, and recent alerts on it.",
     "input_schema": {"type": "object", "properties": {"coin": {"type": "string", "description": "Ticker, e.g. BTC, HYPE, ENA"}},
                      "required": ["coin"]}},
    {"name": "tracked_traders", "description": "Tracked traders with their record in plain words, account, leverage "
     "and open positions (value, open profit, move on price, return on margin, days held). Sorted by record.",
     "input_schema": {"type": "object", "properties": {
         "tier": {"type": "string", "enum": ["all", "proven", "riding"]},
         "coin": {"type": "string", "description": "Only traders holding this coin"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 20}}}},
    {"name": "trader", "description": "One trader by address (full or its first characters) or codename: record, "
     "open positions, adds and cuts in the last 14 days, or what the agent knows if not tracked.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "open_winners", "description": "The biggest open (unrealised) winners among everyone the agent reads.",
     "input_schema": {"type": "object", "properties": {
         "coin": {"type": "string"}, "sort": {"type": "string", "enum": ["profit", "move", "held"]},
         "limit": {"type": "integer", "minimum": 1, "maximum": 25}}}},
    {"name": "money_moving", "description": "Where money moved: tracked traders' net adds per coin and side, and "
     "profitable traders' opens and closes per coin, over the last hours.",
     "input_schema": {"type": "object", "properties": {"hours": {"type": "integer", "enum": [24, 72, 168]}}}},
    {"name": "recent_alerts", "description": "Alerts the agent sent (build-ups, conviction bets, convergences, smart "
     "money events) in the last hours.",
     "input_schema": {"type": "object", "properties": {"hours": {"type": "integer", "minimum": 1, "maximum": 168}}}},
    {"name": "studies", "description": "The studies with their findings and verdicts, and the live forward record "
     "of tracked traders' new positions (14 and 30 days).", "input_schema": {"type": "object", "properties": {}}},
    {"name": "remember", "description": "Keep a short note about the owner for later conversations.",
     "input_schema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}},
    {"name": "forget", "description": "Drop a note by its number.",
     "input_schema": {"type": "object", "properties": {"note_id": {"type": "integer"}}, "required": ["note_id"]}},
]


def cost_of(model: str, usage: Any) -> float:
    """Dollars for one response (about: the published prices above)."""
    p_in, p_out, p_read, p_write = PRICES.get(model, PRICES["claude-opus-5-5"])
    get = (lambda k: getattr(usage, k, None) or 0) if not isinstance(usage, dict) else (lambda k: usage.get(k) or 0)
    return (get("input_tokens") * p_in + get("output_tokens") * p_out + get("cache_read_input_tokens") * p_read
            + get("cache_creation_input_tokens") * p_write) / 1e6


def _day_start(now: float) -> float:
    d = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}" if a and len(a) > 12 else a


class Chat:
    def __init__(self, agent, model: str, effort: str = "medium", daily_usd: float = 2.0,
                 client: anthropic.Anthropic | None = None):
        self.agent = agent
        self.store = agent.store
        self.model = model
        self.effort = effort
        self.daily_usd = daily_usd
        self.client = client or anthropic.Anthropic()

    # ---------------------------------------------------------------- talking --
    async def reply(self, chat_id: str, text: str, now: float | None = None) -> str:
        return await asyncio.to_thread(self.run, chat_id, text, now or time.time())

    def spent_today(self, now: float | None = None) -> float:
        return self.store.chat_spent(_day_start(now or time.time()))

    def run(self, chat_id: str, text: str, now: float) -> str:
        if self.spent_today(now) >= self.daily_usd:
            return (f"Today's budget of ${self.daily_usd:.2f} is spent. It resets at 00:00 UTC "
                    "(SYNTH_CHAT_DAILY_USD on Railway changes it).")
        history = self.store.chat_history(chat_id, HISTORY)
        while history and history[0]["role"] != "user":
            history.pop(0)
        messages: list[dict] = [{"role": h["role"], "content": h["text"]} for h in history]
        messages.append({"role": "user", "content": text})
        system = self._system()
        cost, answer = 0.0, ""
        for rnd in range(MAX_ROUNDS):
            resp = self._create(system, messages)
            cost += cost_of(resp.model if resp.model in PRICES else self.model, resp.usage)
            if resp.stop_reason == "refusal":
                answer = "I can't answer that one."
                break
            if resp.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": resp.content})
                results: list[dict] = [{"type": "tool_result", "tool_use_id": b.id, "content": self.execute(b.name, b.input, now)}
                                       for b in resp.content if b.type == "tool_use"]
                if rnd == MAX_ROUNDS - 2:
                    results.append({"type": "text", "text": "Answer now with what you have; no more tools."})
                messages.append({"role": "user", "content": results})
                continue
            answer = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            if resp.stop_reason == "max_tokens":
                answer += "\n(cut short)"
            break
        else:
            answer = answer or "That took too many steps; ask it more narrowly."
        answer = answer or "No answer came back; try again."
        self.store.chat_add(chat_id, "user", text, now, 0.0)
        self.store.chat_add(chat_id, "assistant", answer, now, round(cost, 5))
        return answer

    def _system(self) -> str:
        notes = self.store.chat_notes()
        if not notes:
            return SYSTEM + "\n\nNotes you hold: none yet."
        return SYSTEM + "\n\nNotes you hold:\n" + "\n".join(f"{n['id']}. {n['text']}" for n in notes)

    def _create(self, system: str, messages: list[dict]):
        params = {"model": self.model, "max_tokens": 16000, "tools": TOOLS, "messages": messages,
                  "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                  "output_config": {"effort": self.effort}}
        if self.model in FALLBACK_MODELS:
            return self.client.beta.messages.create(**params, betas=["server-side-fallback-2026-07-01"],
                                                    fallbacks="default")
        return self.client.messages.create(**params)

    # ------------------------------------------------------------------ tools --
    def execute(self, name: str, args: dict, now: float) -> str:
        try:
            fn = getattr(self, f"t_{name}")
            out = fn(now=now, **(args or {}))
        except Exception as e:  # noqa: BLE001 - the model reads the error and carries on
            log.warning("chat tool %s failed: %s", name, e)
            return json.dumps({"error": f"{name} failed: {e}"})
        text = json.dumps(out, default=str, separators=(",", ":"))
        return text if len(text) <= RESULT_CHARS else text[:RESULT_CHARS] + '..."(cut)"'

    def t_market_overview(self, now: float) -> dict:
        h = desk.home(self.agent, now)
        k = h["kpi"]
        k.get("crowd", {}).pop("spark", None)
        pulse = self.store.latest_brief("pulse")
        return {"kpi": k, "btc_eth_direction": [{x: d[x] for x in d if x != "series"} for d in h.get("direction") or []],
                "largest_coins": [{x: r.get(x) for x in ("coin", "price", "leans", "agree", "crowd", "tracked", "big_money")}
                                  for r in h["lenses"][:12]],
                "latest_market_read": (pulse or {}).get("text"), "read_at": (pulse or {}).get("ts")}

    def t_coin(self, now: float, coin: str) -> dict:
        from .service import coin_context    # service builds the chat: imported here, not at the top
        coin = coin.upper().lstrip("$")
        snaps = {n: s for n in ("cowboy", "reflex") if (s := self.store.get_snapshot(n))}
        ctx = coin_context(coin, snaps)
        ctx.pop("market", None)
        pos = (snaps.get("reflex") or {}).get("positions", {}).get(coin)
        pt = self.agent.positions
        holders = [{"trader": f"{codename(t['address'])} {_short(t['address'])}", "tier": t["tier"],
                    **{x: p.get(x) for x in ("side", "value", "upnl", "move", "roe", "held_days")}}
                   for t in pt.board(now)["traders"] for p in t["positions"] if p["coin"] == coin]
        week_ago = now - 7 * 86400
        alerts = [b["body"].get("text") or f"{b['body'].get('coin')} {b['body'].get('side')}"
                  for kind in ("pt_build", "pt_alert") for b in self.store.briefs_since(kind, week_ago)
                  if b["body"].get("coin") == coin]
        return {"coin": coin, "sources": ctx,
                "profitable_traders": {"long_n": pos[0], "long_usd": pos[1], "short_n": pos[2], "short_usd": pos[3],
                                       "price": pos[4]} if pos else None,
                "tracked_traders_holding": sorted(holders, key=lambda x: -(x["value"] or 0))[:12],
                "big_money_owners": pt.big_money().get(coin),
                "open_winners": [w for w in pt.open_winners(now, limit=500) if w["coin"] == coin][:8],
                "tracked_added_7d": [r for r in pt.adding(168, now) if r["coin"] == coin],
                "alerts_7d": alerts[-8:]}

    def t_tracked_traders(self, now: float, tier: str = "all", coin: str | None = None, limit: int = 10) -> list[dict]:
        out = []
        for t in self.agent.positions.board(now)["traders"]:
            if tier != "all" and t["tier"] != tier:
                continue
            if coin and not any(p["coin"] == coin.upper() for p in t["positions"]):
                continue
            out.append(self._trader_row(t))
            if len(out) >= min(limit, 20):
                break
        return out

    @staticmethod
    def _trader_row(t: dict) -> dict:
        return {"trader": f"{codename(t['address'])} {_short(t['address'])}", "address": t["address"],
                "tier": t["tier"], "record": t["record_text"], "account": t["equity"], "leverage": t["leverage"],
                "open_profit_now": t["open_pnl_now"],
                "positions": [{x: p.get(x) for x in ("coin", "side", "value", "upnl", "move", "roe", "held_days")}
                              for p in t["positions"][:8]]}

    def t_trader(self, now: float, query: str) -> dict:
        q = query.strip().lower()
        board = self.agent.positions.board(now)["traders"]
        hit = next((t for t in board if t["address"].lower().startswith(q) or codename(t["address"]).lower() == q), None)
        if hit:
            a = hit["address"].lower()
            flows = [f for f in self.store.pt_flow_since(now - 14 * 86400) if f["address"] == a]
            return {"tracked": True, **self._trader_row(hit),
                    "adds_and_cuts_14d": [{"when": time.strftime("%d %b %H:%M", time.gmtime(f["ts"])), "coin": f["coin"],
                                           "side": f["side"], "usd": round(f["usd"])} for f in flows[-15:]]}
        for a, lv in self.store.pt_cand_live().items():
            if a.startswith(q) or codename(a).lower() == q:
                row = self.store.pt_cand_get(a) or {}
                return {"tracked": False, "trader": f"{codename(a)} {_short(a)}", "address": a,
                        "status": row.get("status"), "record": row.get("record"), "open_positions": lv}
        return {"found": False, "note": "Not among tracked traders or the wallets the agent has read."}

    def t_open_winners(self, now: float, coin: str | None = None, sort: str = "profit", limit: int = 10) -> list[dict]:
        rows = self.agent.positions.open_winners(now, limit=500)
        if coin:
            rows = [r for r in rows if r["coin"] == coin.upper()]
        key = {"profit": lambda r: r["upnl"], "move": lambda r: r["move"] or 0,
               "held": lambda r: r["held_days"] or 0}[sort]
        return [{**{x: r[x] for x in ("coin", "side", "upnl", "move", "roe", "lev", "held_days", "since",
                                      "share_of_account", "listed")}, "trader": f"{r['codename']} {_short(r['address'])}"}
                for r in sorted(rows, key=key, reverse=True)[:min(limit, 25)]]

    def t_money_moving(self, now: float, hours: int = 24) -> dict:
        rows = self.store.series("reflex", now - hours * 3600 - 1800)
        win = flow.window(rows, hours, now) if rows else {"coins": [], "totals": {}}
        return {"tracked_traders_net_adds": self.agent.positions.adding(hours, now)[:15],
                "profitable_traders_opens_closes": win["coins"][:15], "totals": win.get("totals")}

    def t_recent_alerts(self, now: float, hours: int = 24) -> list[dict]:
        since, out = now - hours * 3600, []
        for kind, label in (("pt_build", "build-up"), ("pt_alert", "conviction")):
            for b in self.store.briefs_since(kind, since):
                out.append({"when": time.strftime("%d %b %H:%M", time.gmtime(b["ts"])), "kind": label,
                            "text": b["body"].get("text") or f"{b['body'].get('coin')} {b['body'].get('side')} "
                                                            f"{b['body'].get('record') or ''}".strip()})
        for b in self.store.briefs_since("alert_sent", since):
            ev = self.store.event(b["body"].get("event_id"))
            if ev:
                out.append({"when": time.strftime("%d %b %H:%M", time.gmtime(b["ts"])), "kind": "smart money",
                            "text": ev["title"]})
        for g in self.store.groups(limit=20):
            if g["ts"] >= since:
                out.append({"when": time.strftime("%d %b %H:%M", time.gmtime(g["ts"])), "kind": "convergence",
                            "text": f"{g['coin']} {g['side']}, {(g['meta'] or {}).get('n') or ''} traders, {g['status']}"})
        return sorted(out, key=lambda x: x["when"])[-30:]

    def t_studies(self, now: float) -> dict:
        r = desk.research(self.agent)
        return {"studies": [{x: s.get(x) for x in ("id", "title", "finding", "verdict")} for s in r.get("studies") or []],
                "forward_record": r.get("forward")}

    def t_remember(self, now: float, note: str) -> dict:
        return {"saved": self.store.chat_note_add(note.strip()[:300], now)}

    def t_forget(self, now: float, note_id: int) -> dict:
        return {"dropped": self.store.chat_note_drop(int(note_id))}
