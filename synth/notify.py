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


async def telegram(token: str, chat_id: str, text: str, buttons: dict | None = None) -> dict | None:
    if not (token and chat_id):
        return None
    params = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = buttons
    return await tg(token, "sendMessage", **params)


async def push(s, store, text: str, kind: str, buttons: dict | None = None, png: bytes | None = None,
               must: bool = False) -> dict | None:
    """An unprompted message (anything but a reply to him): at most SYNTH_MAX_PUSHES_PER_DAY in 24 hours. Past that
    it is kept as held (briefs 'push_held'), not sent; `must` (the morning read, a read he asked for) always goes."""
    import time
    if not must and store.count_briefs("push", time.time() - 86400) >= s.max_pushes_per_day:
        store.add_brief("push_held", {"kind": kind, "text": text[:500]})
        return None
    if png is not None:
        msg = await telegram_photo(s.telegram_token, s.telegram_chat_id, png, text, buttons)
    else:
        msg = await telegram(s.telegram_token, s.telegram_chat_id, text, buttons)
    store.add_brief("push", {"kind": kind})
    return msg


async def telegram_photo(token: str, chat_id: str, png: bytes, caption: str,
                         buttons: dict | None = None) -> dict | None:
    if not (token and chat_id):
        return None
    params = {"chat_id": chat_id, "caption": caption[:1024]}
    if buttons:
        params["reply_markup"] = buttons
    return await tg(token, "sendPhoto", http_timeout=30, files={"photo": ("chart.png", png, "image/png")}, **params)


def format_alert(alert: dict, event: dict) -> str:
    lines = [alert["headline"]]
    if alert.get("why"):
        lines.append(alert["why"])
    if alert.get("against"):
        lines.append(f"Against: {alert['against']}")
    return "\n".join(lines)
