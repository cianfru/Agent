"""Telegram bot input: Follow / Unfollow / Ignore buttons and commands.

Long-polls getUpdates (no webhook, no public endpoint). Only the configured chat is obeyed.
  /following  open groups you follow, trader by trader
  /groups     the latest groups and how they ended
  /now        the live pulse: what smart traders opened and closed in the last 1h, 6h and 24h
  /positions  what the proven position traders hold now, by coin
  /help       what each alert means and the words in them
"""

from __future__ import annotations

import asyncio
import logging

from . import groups as G
from . import notify

log = logging.getLogger(__name__)

HELP = """Outrider watches profitable traders on Hyperliquid. Context, not signals.

Alerts
Conviction: a tracked trader opens a position at least 2x their usual size.
Building: 3+ tracked traders add to one coin within 3 days while its price hardly moved.
Group: several profitable traders open the same coin and side together. Tap Follow to hear as each one closes.

Words
Proven: 5+ trades held 3+ days, in profit, winning at least $1.5 for every $1 lost.
Riding a winner: $250K+ profit in open trades now.
Still open: profit not yet taken, in trades still running.
Usual size: the size of a typical position of theirs.
Big money: accounts of $500K+ in profit all-time, one vote per owner.
Scored after 14 and 30 days: the move since the alert, kept as a record.

/now  live pulse (1h, 6h, 24h)
/positions  what tracked traders hold
/following  groups you follow
/groups  latest groups"""

COMMANDS = [{"command": "now", "description": "Live pulse: who opened and closed in 1h, 6h, 24h"},
            {"command": "positions", "description": "What the proven position traders hold now"},
            {"command": "following", "description": "Groups you follow"},
            {"command": "groups", "description": "Latest groups"},
            {"command": "help", "description": "What the alerts and their words mean"}]


def group_lines(store, g: dict) -> list[str]:
    members = store.members(g["id"])
    lines = [f"#{g['id']} {g['coin']} {g['side']} · {g['status']}" + (" · following" if g["followed"] else "")]
    for m in members:
        state = m["status"]
        if state in ("closed", "expired"):
            state += f" @ {G.px(m.get('exit_px'))} {G.pct(m.get('pnl_pct'))}" + (" ≈" if m.get("approx") else "")
        elif m.get("last_px") and m.get("entry_px"):
            now = G.SIGN[g["side"]] * (m["last_px"] / m["entry_px"] - 1)
            state += f" · {G.pct(now)} now"
        inst = f" {m['instrument']}" if m.get("instrument") else ""
        lines.append(f"  {G.short_addr(m['address'])}{inst} @ {G.px(m.get('entry_px'))} · {state}")
    return lines


async def handle(agent, update: dict) -> None:
    s, store = agent.s, agent.store
    chat = str(s.telegram_chat_id)
    cq = update.get("callback_query")
    if cq:
        msg = cq.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != chat:
            return
        action, _, gid_s = (cq.get("data") or "").partition(":")
        try:
            gid = int(gid_s)
        except ValueError:
            return
        g = store.get_group(gid)
        if not g:
            await notify.tg(s.telegram_token, "answerCallbackQuery", callback_query_id=cq["id"], text="Unknown group")
            return
        if action == "f":
            agent.tracker.set_follow(gid, True)
            text, markup = f"Following #{gid}: you'll get a message as each trader closes.", notify.follow_buttons(gid, True)
        elif action == "u":
            agent.tracker.set_follow(gid, False)
            text, markup = f"Stopped following #{gid}. Still recorded on the dashboard.", notify.follow_buttons(gid)
        else:
            text, markup = f"Ignored #{gid}. Still recorded on the dashboard.", {"inline_keyboard": []}
        await notify.tg(s.telegram_token, "answerCallbackQuery", callback_query_id=cq["id"], text=text)
        await notify.tg(s.telegram_token, "editMessageReplyMarkup", chat_id=chat,
                        message_id=msg.get("message_id"), reply_markup=markup)
        return
    msg = update.get("message") or {}
    if str((msg.get("chat") or {}).get("id")) != chat:
        return
    cmd = [c.split("@")[0] for c in (msg.get("text") or "").strip().split()[0:1]]
    if cmd in (["/now"], ["/pulse"]):
        try:
            p = await asyncio.to_thread(agent.pulse)
            await notify.telegram_photo(s.telegram_token, chat, p["png"], p["summary"])
        except Exception as e:  # noqa: BLE001
            log.warning("/now failed: %s", e)
            await notify.telegram(s.telegram_token, chat, "The pulse isn't available right now.")
        return
    if cmd == ["/positions"]:
        await notify.telegram(s.telegram_token, chat, agent.positions.coins_text())
        return
    if cmd == ["/following"]:
        gs = [g for g in store.groups(limit=200) if g["followed"] and g["status"] == "open"]
        lines = [ln for g in gs for ln in group_lines(store, g)] or ["You're not following any open group."]
    elif cmd == ["/groups"]:
        lines = [ln for g in store.groups(limit=5) for ln in group_lines(store, g)] or ["No groups yet."]
    else:
        lines = [HELP]
    await notify.telegram(s.telegram_token, chat, "\n".join(lines))


async def run(agent) -> None:
    """Long-poll Telegram for button taps and commands until cancelled."""
    if not (agent.s.telegram_token and agent.s.telegram_chat_id):
        return
    await notify.tg(agent.s.telegram_token, "setMyCommands", commands=COMMANDS)  # the "/" menu in Telegram
    offset = None
    while True:
        try:
            params = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                params["offset"] = offset
            updates = await notify.tg(agent.s.telegram_token, "getUpdates", http_timeout=35, **params)
            for u in updates or []:
                offset = u["update_id"] + 1
                try:
                    await handle(agent, u)
                except Exception:  # noqa: BLE001
                    log.exception("telegram update failed")
            if updates is None:
                await asyncio.sleep(5)  # error (e.g. another instance polling during a deploy)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("telegram loop error")
            await asyncio.sleep(5)
