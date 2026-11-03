"""Telegram bot input: Follow / Unfollow / Ignore buttons and commands.

Long-polls getUpdates (no webhook, no public endpoint). Only the configured chat is obeyed.
  /following  open groups you follow, trader by trader
  /groups     the latest groups and how they ended
  /now        the live pulse: what smart traders opened and closed in the last 1h, 6h and 24h
  /positions  what the proven position traders hold now, by coin
  /help       what each alert means and the words in them
  /draft      one of Outrider's X posts now, as a draft (off the menu while X drafts are off)
  /read       Outrider's read of the market now;  /record  how its past views did;  /mine  his positions, briefed
  /watch      the watch list, or /watch ENA long 0.09 5k to add;  /unwatch ENA  to take it off
  /reset      start the conversation afresh;  /cost  what talking and reads have cost today;  /held  held back
Any other message is a question for Outrider (synth/chat.py), answered from the agent's data.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from . import groups as G
from . import notify

log = logging.getLogger(__name__)

HELP = """Outrider watches profitable traders on Hyperliquid and Derive's options, and tells you what it makes of them.
Write to me in plain words and I answer from the data (ask about a coin, a trader, who is sitting on big winners, what I'd do with your position). Reply to any of my messages to ask about it.

What I send you
My morning read: what is happening and up to two setups worth a look, with what to see on the chart (each scored after 1, 3 and 7 days; /record shows how they did). At most two more a day when the market really changes.
Conviction: a proven trader opens a position at least 2x their usual size.
A short note when a conviction bet or a build-up goes against one of your positions. Otherwise your positions are yours: /mine when you want a briefing.
Updates on groups you follow.

What I keep without pinging (in my reads, the dashboard and /record)
Building: 3+ tracked traders add to one coin within 3 days while its price hardly moved.
Group: several profitable traders open the same coin and side together.
Smart-money events and toxic flow.

Words
Proven: 5+ trades held 3+ days, in profit, winning at least $1.5 for every $1 lost.
Riding a winner: $250K+ profit in open trades now.
Still open: profit not yet taken, in trades still running.
Usual size: the size of a typical position of theirs.
Big money: accounts of $500K+ in profit all-time, one vote per owner.
Scored after 14 and 30 days: the move since the alert, kept as a record.

/read  my read of the market now
/board  the market at a glance: the cycle, BTC and ETH, breadth, the z-score's extremes
/map  every coin by its band and its z-score
/chart SOL  a coin's daily chart with its band, 200-day, off level and z (/chart SOL short)
/mine  a briefing on your positions and watch list
/record  how my views and every other kind of pick did
/watch  your watch list; /watch ENA long 0.09 5k adds one (side, entry and size optional)
/unwatch ENA  takes it off
/now  live pulse (1h, 6h, 24h)
/positions  what tracked traders hold
/following  groups you follow
/groups  latest groups
/reset  start our conversation afresh
/cost  what talking and reads have cost today, and messages sent
/held  messages the daily cap of unprompted messages held back
/status  is everything running: sources, jobs, data age"""

MINE = ("Brief me on my positions and my watch list: for each, where it stands (price against my entry and the levels "
        "that matter), who is on which side and what changed lately. The picture, no instructions.")

COMMANDS = [{"command": "read", "description": "Outrider's read of the market now"},
            {"command": "board", "description": "The market at a glance"},
            {"command": "map", "description": "Every coin by its band and its z-score"},
            {"command": "chart", "description": "A coin's daily chart with its levels (/chart SOL)"},
            {"command": "mine", "description": "A briefing on your positions and watch list"},
            {"command": "watch", "description": "Your watch list (/watch ENA long 0.09 5k adds one)"},
            {"command": "unwatch", "description": "Take a coin off the watch list (/unwatch ENA)"},
            {"command": "record", "description": "How Outrider's views and the other picks did"},
            {"command": "now", "description": "Live pulse: who opened and closed in 1h, 6h, 24h"},
            {"command": "positions", "description": "What the proven position traders hold now"},
            {"command": "following", "description": "Groups you follow"},
            {"command": "groups", "description": "Latest groups"},
            {"command": "reset", "description": "Start the conversation afresh"},
            {"command": "cost", "description": "What talking and reads have cost today"},
            {"command": "held", "description": "Messages the daily cap held back"},
            {"command": "status", "description": "Is everything running"},
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
        if action in ("pp", "px"):
            await _post_button(agent, cq, msg, action, gid)
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
    replied = msg.get("reply_to_message") or {}
    reply_to = replied.get("message_id")
    reply_text = (msg.get("text") or msg.get("caption") or "").strip()
    if reply_to and reply_text and not reply_text.startswith("/") and (draft := store.post_by_tg(reply_to)):
        problems = agent.edit_post(draft["id"], reply_text)
        if problems:
            await notify.telegram(s.telegram_token, chat, "Not saved: " + "; ".join(problems))
        else:
            await agent.send_draft(draft["id"], reply=True)
        return
    cmd = [c.split("@")[0] for c in reply_text.split()[0:1]]
    if reply_text and not reply_text.startswith("/"):
        quoted = (replied.get("text") or replied.get("caption") or "").strip()
        if quoted:      # a reply to one of Outrider's messages: say which, so the answer knows what "this" is
            reply_text = f'He is replying to your message: "{quoted[:800]}"\n\n{reply_text}'
        await _talk(agent, chat, reply_text)
        return
    if not reply_text and not cmd:
        await notify.telegram(s.telegram_token, chat, "I read text messages (a picture with a question as its caption "
                                                      "works too).")
        return
    if cmd == ["/status"]:
        await notify.telegram(s.telegram_token, chat, status_text(agent))
        return
    if cmd == ["/held"]:
        await notify.telegram(s.telegram_token, chat, notify.held_text(store))
        return
    if cmd == ["/mine"]:                                  # his positions, on request only (owner, 3 November 2026)
        await _talk(agent, chat, MINE)
        return
    if cmd == ["/reset"]:
        store.chat_reset(chat)
        await notify.telegram(s.telegram_token, chat, "Fresh start. Notes I keep stay.")
        return
    if cmd == ["/cost"]:
        if not agent.chat:
            await notify.telegram(s.telegram_token, chat, "Talking is off: no ANTHROPIC_API_KEY on Railway.")
        else:
            text = f"Talking today: ${agent.chat.spent_today():.2f} of ${agent.chat.daily_usd:.2f} ({agent.chat.model})."
            sent = store.count_briefs("push", time.time() - 86400)
            held = store.count_briefs("push_held", time.time() - 86400)
            text += f"\nUnprompted messages in 24h: {sent} of {s.max_pushes_per_day}" + (
                f", {held} held back (/held)" if held else "") + "."
            if agent.analyst:
                text += (f"\nReads today: ${agent.analyst.spent_today():.2f} of ${agent.analyst.daily_usd:.2f} "
                         f"({agent.analyst.model}).")
            await notify.telegram(s.telegram_token, chat, text)
        return
    if cmd == ["/read"]:
        if not agent.analyst:
            await notify.telegram(s.telegram_token, chat, "Reads are off: no ANTHROPIC_API_KEY, or SYNTH_READS=off.")
            return
        if agent.analyst.busy:          # yours follows unless the one running reaches you
            agent.analyst.pending_ask = True
            await notify.telegram(s.telegram_token, chat, "A read is running now; you get it, or a fresh one right "
                                                          "after, in a moment.")
            return
        typing = asyncio.create_task(_typing(s.telegram_token, chat))
        try:
            await agent.analyst.run("asked", force_send=True)
        except Exception as e:  # noqa: BLE001 - say why instead of going quiet
            log.warning("/read failed: %s", e)
            await notify.telegram(s.telegram_token, chat, f"The read failed: {type(e).__name__}. Try again in a minute.")
        finally:
            typing.cancel()
        return
    if cmd in (["/watch"], ["/unwatch"]):
        from .analyst import parse_watch, watch_text
        w = parse_watch(reply_text.split()[1:])
        if cmd == ["/unwatch"]:
            n = store.watch_drop(w["coin"], w["side"]) if w else 0
            text = f"Took {w['coin']} off the watch list." if n else "Nothing to take off: /unwatch ENA"
        else:
            if w:
                store.watch_add(w["coin"], w["side"], w["entry"], w["size_usd"], None, time.time())
            text = await asyncio.to_thread(watch_text, agent)
        await notify.telegram(s.telegram_token, chat, text)
        return
    if cmd == ["/record"]:
        from . import journal, pictures
        text = journal.text(journal.record(store), journal.reads(store))
        png = await pictures.journal_png(store)
        await notify.send_with_pictures(s.telegram_token, chat, text, [png] if png else [])
        return
    if cmd == ["/board"]:
        from .analyst import board_text, scan
        cur = await asyncio.to_thread(scan, agent)
        await notify.telegram(s.telegram_token, chat, board_text(cur) or "The board fills once the levels are read "
                                                                         "(daily after 00:30 UTC).")
        return
    if cmd == ["/map"]:
        from . import pictures
        m = await pictures.map_png(agent)
        if m:
            await notify.telegram_photo(s.telegram_token, chat, m[0], m[1])
        else:
            await notify.telegram(s.telegram_token, chat, "No map yet: the levels are read daily after 00:30 UTC.")
        return
    if cmd == ["/chart"]:
        from . import pictures
        words = reply_text.split()[1:]
        if not words:
            await notify.telegram(s.telegram_token, chat, "Which coin? /chart SOL (or /chart SOL short)")
            return
        coin = words[0].upper().lstrip("$")
        want = words[1].lower() if len(words) > 1 and words[1].lower() in ("long", "short") else None
        side, risk = await asyncio.to_thread(pictures.chart_frame, agent, coin, want)
        png = await pictures.setup_png(agent, coin, side, "chart", risk)
        if not png:
            await notify.telegram(s.telegram_token, chat, f"No daily chart for {coin} on Hyperliquid.")
            return
        cap = f"{coin} {side}: where it would be wrong and the next level, from the screen."
        if risk and risk.get("off"):
            o = risk["off"]
            cap = (f"{coin} {side} · off {'below' if side == 'long' else 'above'} {o['level']:g} ({o['name']}, "
                   f"{o['pct']:+.1f}%)" + (f" · next {risk['next']['level']:g} ({risk['next']['name']})"
                                           if risk.get("next") else ""))
        await notify.telegram_photo(s.telegram_token, chat, png, cap)
        return
    if cmd == ["/draft"]:
        try:
            res = await agent.persona_post(publish=False, reply=True)     # he asked: not an unprompted message
        except Exception as e:  # noqa: BLE001 - say why instead of going quiet
            log.warning("/draft failed: %s", e)
            res = {"status": "failed", "problems": [str(e)]}
        if not res:
            await notify.telegram(s.telegram_token, chat, "Nothing new worth posting right now.")
        elif res["status"] != "draft":
            await notify.telegram(s.telegram_token, chat, "No draft: " + "; ".join(res.get("problems") or [res["status"]]))
        return
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


async def _talk(agent, chat: str, text: str) -> None:
    """A plain message: Outrider answers it (typing shown while it reads the data)."""
    s = agent.s
    if not getattr(agent, "chat", None):
        await notify.telegram(s.telegram_token, chat, "I can't talk yet: add ANTHROPIC_API_KEY on Railway.\n\n" + HELP)
        return
    lock = getattr(agent, "talk_lock", None) or contextlib.nullcontext()
    async with lock:
        typing = asyncio.create_task(_typing(s.telegram_token, chat))
        try:
            answer = await agent.chat.reply(chat, text)
        except Exception as e:  # noqa: BLE001 - say what went wrong instead of going quiet
            log.warning("chat failed: %s", e)
            answer = f"Something went wrong reaching the model: {type(e).__name__}. Try again in a minute."
        finally:
            typing.cancel()
    await notify.telegram(s.telegram_token, chat, answer)      # split into several messages when long


async def _typing(token: str, chat: str) -> None:
    """'typing...' while an answer is being written (Telegram shows it for 5 seconds at a time)."""
    while True:
        await notify.tg(token, "sendChatAction", chat_id=chat, action="typing")
        await asyncio.sleep(4)


async def _post_button(agent, cq: dict, msg: dict, action: str, pid: int) -> None:
    """Post publishes the draft through Orbio; Skip sets it aside. The buttons go once it is done; a failed post
    keeps them, so it can be tried again (e.g. once the X account is connected in Orbio)."""
    s = agent.s
    p = agent.store.get_post(pid)
    done = True
    if not p or p["status"] not in ("draft", "rejected", "failed"):
        text = f"Draft #{pid} is {p['status'] if p else 'gone'}."
    elif action == "px":
        agent.store.update_post(pid, status="skipped")
        text = f"Skipped #{pid}."
    else:
        try:
            res = await agent.publish_post(pid)
            done = res.get("status") in ("published", "running", "posting")
            text = (f"Posted #{pid}" + (f": {res['url']}" if res.get("url") else ".")) if done else \
                f"Not posted: {res.get('error')}"
        except ValueError as e:
            done, text = False, f"Not posted: {e}"
    await notify.tg(s.telegram_token, "answerCallbackQuery", callback_query_id=cq["id"], text=text[:190])
    if done:
        await notify.tg(s.telegram_token, "editMessageReplyMarkup", chat_id=str(s.telegram_chat_id),
                        message_id=msg.get("message_id"), reply_markup={"inline_keyboard": []})
    await notify.telegram(s.telegram_token, str(s.telegram_chat_id), text)


def status_text(agent) -> str:
    """Is everything running: each source and job, when it last worked, its last error, and Hyperliquid's budget."""
    st = agent.status()
    ok, problems = agent.healthy()
    lines = ["All running." if ok else "Problems: " + "; ".join(problems)]
    if st.get("cycle_age_min") is not None:
        lines.append(f"Last poll {st['cycle_age_min']:g} min ago ({st.get('cycle_secs')}s).")
    for name, v in sorted((st.get("sources") or {}).items()):
        line = f"{name}: " + (f"ok {v['ok_ago_min']} min ago" if v.get("ok_ago_min") is not None else "not yet ok")
        if v.get("fails"):
            line += f", failing {v['fails']}x: {v.get('error', '')[:80]}"
        lines.append(line)
    ages = {k: v for k, v in (st.get("data_age_min") or {}).items() if v is not None}
    if ages:
        lines.append("Data age (min): " + ", ".join(f"{k} {v}" for k, v in ages.items()))
    hl = st.get("hyperliquid") or {}
    lines.append(f"Hyperliquid: {hl.get('weight_last_min')} of {hl.get('budget_per_min'):g} weight in the last minute, "
                 f"{hl.get('throttled_last_hour')} throttled in the hour")
    lr = st.get("levels_refresh") or {}
    lines.append(f"Levels: {lr.get('day') or 'not done today'}" + (f", {lr.get('errors')} failed" if lr.get("errors")
                                                                    else ""))
    lr2 = st.get("last_read") or {}
    if lr2.get("ago_min") is not None:
        lines.append(f"Last read {lr2['ago_min']} min ago ({lr2.get('reason')})" +
                     (f", failed: {str(lr2['error'])[:80]}" if lr2.get("error") else ""))
    return "\n".join(lines)


async def _safe(agent, u: dict) -> None:
    try:
        await handle(agent, u)
    except Exception:  # noqa: BLE001
        log.exception("telegram update failed")


async def run(agent) -> None:
    """Long-poll Telegram for button taps and commands until cancelled."""
    if not (agent.s.telegram_token and agent.s.telegram_chat_id):
        return
    await notify.tg(agent.s.telegram_token, "setMyCommands", commands=COMMANDS)  # the "/" menu in Telegram
    agent.talk_lock = asyncio.Lock()           # answers one at a time, in the order asked
    running: set = set()
    offset = None
    while True:
        try:
            params = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                params["offset"] = offset
            updates = await notify.tg(agent.s.telegram_token, "getUpdates", http_timeout=35, **params)
            for u in updates or []:
                offset = u["update_id"] + 1
                t = asyncio.create_task(_safe(agent, u))   # a long answer never holds up a tap or a command
                running.add(t)
                t.add_done_callback(running.discard)
            if updates is None:
                await asyncio.sleep(5)  # error (e.g. another instance polling during a deploy)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("telegram loop error")
            await asyncio.sleep(5)
