"""Groups: smart traders who converged on one coin and side, tracked trader by trader until they close.

Hyperliquid groups come from Reflex's convergences (wallet, entry, size, time per trader); each
trader's position is then read straight from Hyperliquid's public API, and a close is priced from
the trader's own closing fills (exact) or, if none are found, the mid at the time we saw it gone.

Derive groups: 2+ smart option wallets opening legs with the same direction on one coin within the
fast window. Each leg is followed in the trader's published book (cowboy-feed traders/{address}.json):
a leg gone before expiry was closed (priced at its last mark, marked approximate), after expiry it
expired.

Every group is recorded; only followed groups push updates to Telegram.
"""

from __future__ import annotations

import time
from typing import Any

from .brief import usd

SIGN = {"long": 1, "short": -1}


def short_addr(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}" if a and len(a) > 12 else a


def px(x: float | None) -> str:
    if x is None:
        return "-"
    if x >= 1000:
        return f"{x:,.0f}"
    if x >= 1:
        return f"{x:.2f}"
    return f"{x:.4g}"


def pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:+.1f}%"


def hours(a: float, b: float) -> str:
    h = max(0.0, (b - a) / 3600)
    return f"{h * 60:.0f}m" if h < 1 else f"{h:.0f}h" if h < 48 else f"{h / 24:.0f}d"


# ------------------------------------------------------------------ levels --


def levels(group: dict, members: list[dict]) -> dict:
    """Entry range and the line beyond which every trader in the group is losing (Hyperliquid)."""
    entries = [m["entry_px"] for m in members if m.get("entry_px")]
    if not entries:
        return {}
    lo, hi = min(entries), max(entries)
    w = sum(m.get("size_usd") or 0 for m in members if m.get("entry_px"))
    avg = (sum(m["entry_px"] * (m.get("size_usd") or 0) for m in members if m.get("entry_px")) / w
           if w else sum(entries) / len(entries))
    # shorts all lose above the highest entry; longs all lose below the lowest
    all_losing = hi if group["side"] == "short" else lo
    return {"lo": lo, "hi": hi, "avg": avg, "all_losing": all_losing}


# ----------------------------------------------------------- text, for TG --


def caption(group: dict, members: list[dict], now_px: float | None) -> str:
    g, n = group, len(members)
    if g["venue"] == "hyperliquid":
        span = g["meta"].get("span_h")
        head = f"#{g['id']} {g['coin']} {g['side']} · {n} profitable traders" + (f" in {span:g}h" if span else "")
        lines = [head]
        lines += [f"{short_addr(m['address'])}  {usd(m.get('size_usd')).lstrip('+')} @ {px(m['entry_px'])}"
                  for m in members[:6]]
        lv = levels(g, members)
        if lv:
            lines.append(f"Entries {px(lv['lo'])}–{px(lv['hi'])} · now {px(now_px)}")
            word = "above" if g["side"] == "short" else "below"
            lines.append(f"{word.capitalize()} {px(lv['all_losing'])} every {g['side']} here is losing.")
        return "\n".join(lines)
    head = f"#{g['id']} {g['coin']} {g['side']} · {len({m['address'] for m in members})} smart option wallets"
    if g["meta"].get("span_h") is not None:
        head += f" in {g['meta']['span_h']:g}h"
    lines = [head]
    for m in members[:6]:
        lines.append(f"{short_addr(m['address'])}  {m['instrument']} {m['size']:+g} @ {px(m['entry_px'])}"
                     + (f" · delta {usd(m['size_usd'])}" if m.get("size_usd") else ""))
    if now_px:
        lines.append(f"Index now {px(now_px)}")
    return "\n".join(lines)


def change_text(group: dict, member: dict, change: str, open_left: int, total: int) -> str:
    who = short_addr(member["address"])
    tag = f"#{group['id']} {group['coin']} {group['side']}"
    if change == "reduced":
        return f"{tag}: {who} cut size by half or more. {open_left} of {total} still in."
    verb = {"closed": "closed", "expired": "expired"}[change]
    approx = " (≈ last mark)" if member.get("approx") else ""
    held = hours(member["opened_ts"], member.get("exit_ts") or time.time())
    return (f"{tag}: {who} {verb} @ {px(member.get('exit_px'))}{approx}, {pct(member.get('pnl_pct'))} "
            f"in {held}. {open_left} of {total} still in.")


def summary_text(group: dict, members: list[dict], now_px: float | None) -> str:
    done = [m for m in members if m.get("pnl_pct") is not None]
    wins = sum(1 for m in done if m["pnl_pct"] > 0)
    avg = sum(m["pnl_pct"] for m in done) / len(done) if done else None
    move = ""
    if group.get("ref_px") and now_px:
        move = f" Price {px(group['ref_px'])} → {px(now_px)}."
    return (f"#{group['id']} {group['coin']} {group['side']}: all out. {wins} of {len(done)} closed in profit, "
            f"average {pct(avg)}.{move}")


# ----------------------------------------------------------------- stats --


def side_move(group: dict, now_px: float | None) -> float | None:
    if not group.get("ref_px") or not now_px:
        return None
    return SIGN[group["side"]] * (now_px / group["ref_px"] - 1)


def scoreboard(groups: list[dict], members_by_group: dict[int, list[dict]]) -> dict:
    closed = [g for g in groups if g["status"] == "closed"]
    results = []
    for g in closed:
        done = [m["pnl_pct"] for m in members_by_group.get(g["id"], []) if m.get("pnl_pct") is not None]
        if done:
            results.append(sum(done) / len(done))
    return {
        "groups": len(groups),
        "open": sum(1 for g in groups if g["status"] == "open"),
        "closed": len(closed),
        "closed_in_profit": sum(1 for r in results if r > 0),
        "avg_closed_pct": (sum(results) / len(results)) if results else None,
    }


# ---------------------------------------------------------------- tracking --


def hl_update(group: dict, m: dict, pos: dict | None, fills: list[dict] | None, mid: float | None,
              now: float) -> tuple[dict, str | None]:
    """New member fields and the change ('reduced' / 'closed' / None). Pure."""
    upd: dict[str, Any] = {"last_seen_ts": now}
    if mid:
        upd["last_px"] = mid
    if pos and pos["side"] == group["side"]:
        if m["status"] == "open" and m.get("size") and pos["size"] < 0.5 * m["size"]:
            return {**upd, "status": "reduced"}, "reduced"
        return upd, None
    # gone (or flipped): closed. Price it from the trader's own closing fills when we have them.
    if fills:
        sz = sum(f["sz"] for f in fills) or 1
        exit_px = sum(f["px"] * f["sz"] for f in fills) / sz
        upd.update(exit_px=exit_px, exit_ts=fills[-1]["time"] / 1000, pnl_usd=sum(f["pnl"] for f in fills),
                   approx=0)
    else:
        upd.update(exit_px=mid, exit_ts=now, approx=1)
    if upd.get("exit_px") and m.get("entry_px"):
        upd["pnl_pct"] = SIGN[group["side"]] * (upd["exit_px"] / m["entry_px"] - 1)
    upd["status"] = "closed"
    return upd, "closed"


def derive_update(m: dict, leg: dict | None, now: float) -> tuple[dict, str | None]:
    """A Derive leg against the trader's current book. Pure."""
    upd: dict[str, Any] = {"last_seen_ts": now}
    if leg is not None:
        upd["last_px"] = leg.get("mark")
        if m["status"] == "open" and m.get("size") and abs(leg.get("net") or 0) < 0.5 * abs(m["size"]):
            return {**upd, "status": "reduced"}, "reduced"
        return upd, None
    status = "expired" if m.get("expiry") and now >= m["expiry"] else "closed"
    exit_px = m.get("last_px")
    upd.update(status=status, exit_px=exit_px, exit_ts=now, approx=1)
    if exit_px is not None and m.get("entry_px"):
        sign = 1 if (m.get("size") or 0) > 0 else -1
        upd["pnl_pct"] = sign * (exit_px / m["entry_px"] - 1)
        upd["pnl_usd"] = (exit_px - m["entry_px"]) * (m.get("size") or 0)
    return upd, status


# ------------------------------------------------------- derive detection --


def derive_opens(prev: dict | None, cur: dict, now: float, min_delta_usd: float = 25_000) -> list[dict]:
    """New legs held by smart Derive wallets since the previous snapshot. Pure."""
    if not prev:
        return []
    out = []
    pw, cw = prev.get("wallets") or {}, cur.get("wallets") or {}
    for coin, holders in cw.items():
        if coin not in pw:
            continue
        before = {h["address"]: {lg["instrument"] for lg in h.get("legs") or []} for h in pw[coin]}
        for h in holders:
            if not h.get("smart") or h["address"] not in before:
                continue
            for lg in h.get("legs") or []:
                if lg["instrument"] in before[h["address"]] or abs(lg.get("delta_usd") or 0) < min_delta_usd:
                    continue
                out.append({"coin": coin, "address": h["address"], "instrument": lg["instrument"],
                            "side": "long" if lg["delta_usd"] > 0 else "short", "size": lg.get("net"),
                            "entry_px": lg.get("entry"), "size_usd": lg.get("delta_usd"),
                            "expiry": lg.get("expiry"), "opened_ts": now, "rank": h.get("rank")})
    return out
