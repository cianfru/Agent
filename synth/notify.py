"""Telegram push (optional). Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


async def telegram(token: str, chat_id: str, text: str) -> bool:
    if not (token and chat_id):
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
            )
        if r.status_code != 200:
            log.warning("telegram send failed: HTTP %s", r.status_code)
        return r.status_code == 200
    except httpx.HTTPError as e:
        log.warning("telegram send failed: %s", e)
        return False


def format_alert(alert: dict, event: dict) -> str:
    lines = [alert["headline"]]
    if alert.get("why"):
        lines.append(alert["why"])
    if alert.get("against"):
        lines.append(f"Against: {alert['against']}")
    return "\n".join(lines)
