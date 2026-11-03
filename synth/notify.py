"""Telegram push (optional). Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."""

from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger(__name__)


API = "https://api.telegram.org/bot{token}/{method}"


def follow_buttons(gid: int, followed: bool = False) -> dict:
    if followed:
        return {"inline_keyboard": [[{"text": f"Following #{gid} · Unfollow", "callback_data": f"u:{gid}"}]]}
    return {"inline_keyboard": [[{"text": f"Follow #{gid}", "callback_data": f"f:{gid}"},
                                 {"text": "Ignore", "callback_data": f"i:{gid}"}]]}


def post_buttons(pid: int) -> dict:
    """Post or Skip under one of Outrider's drafts."""
    return {"inline_keyboard": [[{"text": "Post", "callback_data": f"pp:{pid}"},
                                 {"text": "Skip", "callback_data": f"px:{pid}"}]]}


async def tg(token: str, method: str, http_timeout: float = 15, files: dict | None = None,
             **params) -> dict | list | None:
    """Call a Bot API method; returns `result` or None (never raises)."""
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=http_timeout) as c:
            url = API.format(token=token, method=method)
            if files:
                data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in params.items()}
                r = await c.post(url, data=data, files=files)
            else:
                r = await c.post(url, json=params)
        body = r.json()
        if not body.get("ok"):
            log.warning("telegram %s failed: %s", method, str(body.get("description"))[:200])
            return None
        return body.get("result")
    except (httpx.HTTPError, ValueError) as e:
        log.warning("telegram %s failed: %s", method, e)
        return None


def split_text(text: str, limit: int = 3900) -> list[str]:
    """Text in pieces Telegram takes (4,096 characters a message), cut at line ends where it can."""
    out, rest = [], text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        out.append(rest[:cut].rstrip("\n"))
        rest = rest[cut:].lstrip("\n")
    return out + ([rest] if rest or not out else [])


async def telegram(token: str, chat_id: str, text: str, buttons: dict | None = None,
                   silent: bool = False) -> dict | None:
    """Send text, in several messages when it is long (nothing cut off); the buttons go on the last piece. Returns
    the first message."""
    if not (token and chat_id):
        return None
    first = None
    parts = split_text(text)
    for i, part in enumerate(parts):
        params = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
        if buttons and i == len(parts) - 1:
            params["reply_markup"] = buttons
        if silent or i:
            params["disable_notification"] = True
        msg = await tg(token, "sendMessage", **params)
        first = first or msg
    return first


async def push(s, store, text: str, kind: str, buttons: dict | None = None, png: bytes | None = None,
               must: bool = False, reply: bool = False) -> dict | None:
    """An unprompted message (anything but a reply to him): at most SYNTH_MAX_PUSHES_PER_DAY in 24 hours. Past that
    it is kept as held (briefs 'push_held', listed by /held and counted in the morning read), not sent. `must` (the
    morning read, a note against his position) always goes and counts; `reply` (a read he asked for) goes and does
    not count, since it answers him. A send that fails (Telegram down) does not use up the allowance."""
    import time
    if not reply and not must and store.count_briefs("push", time.time() - 86400) >= s.max_pushes_per_day:
        store.add_brief("push_held", {"kind": kind, "text": text[:1500]})
        return None
    if png is not None:
        msg = await telegram_photo(s.telegram_token, s.telegram_chat_id, png, text, buttons)
    else:
        msg = await telegram(s.telegram_token, s.telegram_chat_id, text, buttons)
    configured = bool(s.telegram_token and s.telegram_chat_id)
    if not reply and (msg is not None or not configured):
        store.add_brief("push", {"kind": kind})
    return msg


def held_text(store, now: float | None = None, hours: float = 24) -> str:
    """What the daily cap held back in the last `hours`, newest first."""
    import time
    now = now or time.time()
    held = store.briefs_since("push_held", now - hours * 3600)
    if not held:
        return f"Nothing held back in the last {hours:g} hours."
    lines = [f"Held back by the daily cap, last {hours:g} hours ({len(held)}):"]
    for b in reversed(held):
        when = time.strftime("%d %b %H:%M", time.gmtime(b["ts"]))
        lines.append(f"\n{when} UTC · {b['body'].get('kind', '')}\n{b['body'].get('text', '')}")
    return "\n".join(lines)


async def telegram_photo(token: str, chat_id: str, png: bytes, caption: str,
                         buttons: dict | None = None) -> dict | None:
    """A picture with its caption; a caption over Telegram's 1,024 characters goes as a message under the picture,
    whole, instead of being cut."""
    if not (token and chat_id):
        return None
    long = len(caption) > 1024
    params = {"chat_id": chat_id, "caption": caption.split("\n")[0][:1024] if long else caption}
    if buttons and not long:
        params["reply_markup"] = buttons
    msg = await tg(token, "sendPhoto", http_timeout=30, files={"photo": ("chart.png", png, "image/png")}, **params)
    if long:
        await telegram(token, chat_id, caption, buttons, silent=True)
    return msg


def format_alert(alert: dict, event: dict) -> str:
    lines = [alert["headline"]]
    if alert.get("why"):
        lines.append(alert["why"])
    if alert.get("against"):
        lines.append(f"Against: {alert['against']}")
    return "\n".join(lines)
