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
    stars = "*" * int(alert.get("conviction") or 1)
    lines = [
        f"{alert['headline']}",
        f"{(alert.get('direction') or '').upper()}  conviction {stars}  score {event['score']:.0f}",
        "",
        alert.get("why_it_matters", ""),
    ]
    if alert.get("confirms"):
        lines += ["", "Confirms: " + "; ".join(alert["confirms"])]
    if alert.get("contradicts"):
        lines += ["Against: " + "; ".join(alert["contradicts"])]
    return "\n".join(lines)


def format_brief(brief: dict) -> str:
    lines = [f"Market: {brief['regime'].replace('_', ' ')}", brief["market_read"], ""]
    for s in brief.get("setups", []):
        lines.append(f"{s['coin']} {s['direction'].upper()} ({s['conviction']}/5): {s['thesis']}")
        lines.append(f"  invalidation: {s['invalidation']}")
    if brief.get("risks"):
        lines += ["", "Risks: " + "; ".join(brief["risks"])]
    return "\n".join(lines)
