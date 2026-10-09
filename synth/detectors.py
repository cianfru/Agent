"""Deterministic event detection. No model calls here.

Each detector compares the previous and current normalised snapshot of a source
and returns events: {key, kind, coin, direction, score (0-100), title, data}.
`key` is used to suppress repeats of the same event within a cooldown window.
"""

from __future__ import annotations

from typing import Iterable

BULLISH_SIGNALS = {"STRONG_LONG": 3, "LIGHT_LONG": 2, "ACCUMULATE": 2, "REVIVAL_SEED": 1}
BEARISH_SIGNALS = {"RISK_OFF": 3, "TRIM_HARD": 3, "TRIM": 2, "NO_LONG": 1}
BULLISH_REGIMES = {"MARKUP", "ACCUM", "REACC"}
BEARISH_REGIMES = {"MARKDOWN", "CAP", "BLOWOFF"}


def _signal_side(sig: str | None) -> tuple[str | None, int]:
    if sig in BULLISH_SIGNALS:
        return "long", BULLISH_SIGNALS[sig]
    if sig in BEARISH_SIGNALS:
        return "short", BEARISH_SIGNALS[sig]
    return None, 0


def _usd(x: float) -> str:
    a = abs(x)
    if a >= 1e6:
        return f"${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"${a / 1e3:.0f}K"
    return f"${a:.0f}"


def _ev(kind: str, coin: str | None, direction: str | None, score: float, title: str,
        key: str, **data) -> dict:
    return {
        "kind": kind,
        "coin": coin,
        "direction": direction,
        "score": round(max(0.0, min(100.0, score)), 1),
        "title": title,
        "key": key,
        "data": data,
    }


# ---------------------------------------------------------------- engine ---


def engine_changes(source: str, prev: dict, cur: dict) -> list[dict]:
    """Signal and regime transitions from the shared RCCE engine."""
    out = []
    pc, cc = prev.get("coins") or {}, cur.get("coins") or {}
    for coin, now in cc.items():
        was = pc.get(coin)
        if not was:
            continue
        for tf in ("1d", "4h"):
            a, b = was.get(f"signal_{tf}"), now.get(f"signal_{tf}")
            side, strength = _signal_side(b)
            if a and b and a != b and side is not None:
                base = 45 if tf == "1d" else 30
                score = base + strength * 10
                out.append(_ev(
                    "signal_change", coin, side, score,
                    f"{coin} {tf} signal {a} -> {b} ({source})",
                    f"{source}:signal:{coin}:{tf}:{b}",
                    source=source, timeframe=tf, before=a, after=b,
                    regime=now.get(f"regime_{tf}"), z=now.get(f"z_{tf}"), heat=now.get(f"heat_{tf}"),
                ))
            a, b = was.get(f"regime_{tf}"), now.get(f"regime_{tf}")
            if tf == "1d" and a and b and a != b:
                side = "long" if b in BULLISH_REGIMES else "short" if b in BEARISH_REGIMES else None
                out.append(_ev(
                    "regime_change", coin, side, 50,
                    f"{coin} 1d regime {a} -> {b} ({source})",
                    f"{source}:regime:{coin}:{b}",
                    source=source, before=a, after=b,
                ))
    return out


# ---------------------------------------------------------- smart money ---


def wallet_moves(source: str, prev: dict, cur: dict, min_usd: float) -> list[dict]:
    """Per-wallet position changes among smart holders, plus convergence.

    Expects snapshot["wallets"] = {coin: [{address, smart, net_delta_usd, rank, cohort, ...}]}.
    Convergence = 2+ distinct smart wallets moving the same direction on one coin
    in the same poll window (the event the Reflex bell showed by chance).
    """
    out = []
    pw, cw = prev.get("wallets") or {}, cur.get("wallets") or {}
    for coin, holders in cw.items():
        before = {h["address"]: h for h in pw.get(coin) or []}
        if coin not in pw:
            continue  # first sighting of this coin: no baseline yet
        moves = []
        for h in holders:
            if not h.get("smart"):
                continue
            old = before.get(h["address"], {}).get("net_delta_usd", 0) or 0
            delta = (h.get("net_delta_usd") or 0) - old
            if abs(delta) < min_usd:
                continue
            moves.append((h, old, delta))
        # Smart holders that dropped out of the list entirely = closed.
        now_addrs = {h["address"] for h in holders}
        for addr, h in before.items():
            if h.get("smart") and addr not in now_addrs and abs(h.get("net_delta_usd") or 0) >= min_usd:
                moves.append(({**h, "net_delta_usd": 0}, h["net_delta_usd"], -h["net_delta_usd"]))

        for h, old, delta in moves:
            side = "long" if delta > 0 else "short"
            opened = old == 0
            closed = (h.get("net_delta_usd") or 0) == 0
            verb = "opened" if opened else "closed" if closed else ("added" if abs(h["net_delta_usd"]) > abs(old) else "cut")
            rank = h.get("rank") or 999
            score = 35 + min(25, abs(delta) / 200_000 * 5) + (15 if rank <= 10 else 8 if rank <= 50 else 0)
            out.append(_ev(
                "wallet_move", coin, side, score,
                f"{h.get('cohort') or 'Smart'} wallet #{rank} {verb} {_usd(delta)} {'long' if delta > 0 else 'short'} delta on {coin} ({source})",
                f"{source}:wallet:{coin}:{h['address']}:{verb}:{side}",
                source=source, address=h["address"], rank=rank, cohort=h.get("cohort"),
                win_rate=h.get("win_rate"), pnl_usd=h.get("pnl_usd"),
                delta_usd=round(delta), before_usd=round(old), after_usd=h.get("net_delta_usd"),
            ))

        for side in ("long", "short"):
            same = [(h, d) for h, _, d in moves if (d > 0) == (side == "long")]
            addrs = {h["address"] for h, _ in same}
            if len(addrs) >= 2:
                total = sum(d for _, d in same)
                score = 70 + min(20, 5 * (len(addrs) - 2)) + min(10, abs(total) / 1_000_000 * 2)
                out.append(_ev(
                    "smart_convergence", coin, side, score,
                    f"{len(addrs)} smart wallets moved {side} on {coin}, {_usd(total)} combined ({source})",
                    f"{source}:convergence:{coin}:{side}",
                    source=source, wallets=sorted(addrs), total_delta_usd=round(total),
                ))
    return out


def cohort_flips(source: str, prev: dict, cur: dict) -> list[dict]:
    """A smart cohort's aggregate lean on a coin flips between up / defensive."""
    out = []
    pc, cc = prev.get("smart_cohorts") or {}, cur.get("smart_cohorts") or {}
    for cohort, coins in cc.items():
        for coin, now in coins.items():
            was = (pc.get(cohort) or {}).get(coin) or {}
            a, b = was.get("state"), (now or {}).get("state")
            if a and b and a != b and "neutral" not in (a, b):
                side = "long" if b == "up" else "short"
                out.append(_ev(
                    "cohort_flip", coin, side, 65,
                    f"{cohort} cohort flipped {a} -> {b} on {coin} ({source})",
                    f"{source}:cohort:{cohort}:{coin}:{b}",
                    source=source, cohort=cohort, before=a, after=b,
                    net_delta_usd=(now or {}).get("net_delta_usd"),
                ))
    return out


# --------------------------------------------------------------- options ---


def skew_shifts(source: str, prev: dict, cur: dict, threshold: float = 0.02) -> list[dict]:
    """30d 25-delta risk reversal moved sharply (positive = calls bid)."""
    out = []
    pc, cc = prev.get("coins") or {}, cur.get("coins") or {}
    for coin, now in cc.items():
        a, b = (pc.get(coin) or {}).get("rr25_30d"), now.get("rr25_30d")
        if a is None or b is None:
            continue
        move = b - a
        if abs(move) >= threshold:
            side = "long" if move > 0 else "short"
            out.append(_ev(
                "skew_shift", coin, side, 40 + min(30, abs(move) / threshold * 10),
                f"{coin} 30d skew {'toward calls' if move > 0 else 'toward puts'}: rr25 {a:+.3f} -> {b:+.3f} ({source})",
                f"{source}:skew:{coin}:{side}",
                source=source, before=a, after=b,
            ))
    return out


# ---------------------------------------------------------------- market ---


def crowded(source: str, cur: dict) -> list[dict]:
    """Breadth + sentiment stretched: many coins trending up while Fear & Greed is high."""
    m = cur.get("market") or {}
    fg = m.get("fear_greed")
    strength = m.get("consensus_1d_strength")
    coins = cur.get("coins") or {}
    hot = [c for c, v in coins.items() if (v.get("heat_1d") or 0) >= 80]
    if fg is None or strength is None:
        return []
    if m.get("consensus_1d") == "RISK-ON" and strength >= 80 and fg >= 70:
        return [_ev(
            "crowded", None, "short", 60 + min(20, (fg - 70)) + min(10, len(hot) * 2),
            f"Crowded: {strength:.0f}% of coins RISK-ON, Fear & Greed {fg}, {len(hot)} coins heat>=80 ({source})",
            f"{source}:crowded",
            source=source, fear_greed=fg, consensus_strength=strength, hot=hot,
        )]
    return []


# ---------------------------------------------------------------- reflex ---


def reflex_feeds(cur: dict) -> list[dict]:
    """Reflex's own detections (convergence, anomalies, setups). Dedup is by key, so the
    same item reappearing on later polls is suppressed by the store's cooldown."""
    out = []
    for c in cur.get("convergences") or []:
        n = c.get("n") or 2
        out.append(_ev(
            "smart_convergence", c["coin"], c.get("side"), 80 + min(15, 5 * (n - 2)),
            f"{n} profitable Hyperliquid traders converged {c.get('side')} on {c['coin']} (reflex)",
            f"reflex:{c.get('key') or ('conv:' + c['coin'] + ':' + str(c.get('side')))}",
            source="reflex", n=n, detail=c.get("detail"), ts=c.get("ts"),
        ))
    for a in cur.get("anomalies") or []:
        sev = a.get("severity")
        d = str(a.get("direction") or "").lower()
        side = "long" if d in ("up", "long", "bullish") else "short" if d in ("down", "short", "bearish") else "watch"
        out.append(_ev(
            "anomaly", a["coin"], side, 60 if sev == "critical" else 45,
            f"{a['coin']} {a.get('type')} ({sev}, z={a.get('z')}) (reflex)",
            f"reflex:{a.get('key') or ('anom:' + a['coin'] + ':' + str(a.get('type')))}",
            source="reflex", **{k: a.get(k) for k in ("type", "severity", "value", "z", "context")},
        ))
    # Reflex's market setups and exhaustion opportunities are all long-side patterns
    # (squeeze, crowded short, OI front-run, capitulation, exhaustion floor, climax reversal).
    for s in cur.get("setups") or []:
        out.append(_ev(
            "setup", s["coin"], "long", 55 if s.get("severity") == "high" else 42,
            f"{s.get('title')} (reflex)",
            f"reflex:setup:{s['coin']}:{s.get('type')}",
            source="reflex", **{k: s.get(k) for k in ("type", "severity", "signal", "regime", "heat")},
        ))
    return out


def lean_flips(source: str, prev: dict, cur: dict) -> list[dict]:
    """Profitable traders' aggregate long/short lean on a coin flips."""
    out = []
    pc, cc = prev.get("coins") or {}, cur.get("coins") or {}
    for coin, now in cc.items():
        a, b = (pc.get(coin) or {}).get("smart_lean"), now.get("smart_lean")
        if a and b and a != b and b in ("long", "short") and a != "neutral":
            out.append(_ev(
                "cohort_flip", coin, b, 68,
                f"Profitable traders flipped {a} -> {b} on {coin} ({source})",
                f"{source}:lean:{coin}:{b}",
                source=source, before=a, after=b,
                smart_long=now.get("smart_long"), smart_short=now.get("smart_short"),
            ))
    return out


def run_all(source: str, prev: dict | None, cur: dict, wallet_min_usd: float) -> list[dict]:
    events: list[dict] = list(crowded(source, cur))
    if source == "reflex":
        events += reflex_feeds(cur)
    if prev:
        events += engine_changes(source, prev, cur)
        events += wallet_moves(source, prev, cur, wallet_min_usd)
        events += cohort_flips(source, prev, cur)
        events += lean_flips(source, prev, cur)
        events += skew_shifts(source, prev, cur)
    return events


def cross_source(events: Iterable[dict], snapshots: dict[str, dict]) -> list[dict]:
    """Same coin, same direction, confirmed by an independent source's smart money."""
    out = []
    by_coin: dict[tuple[str, str], set[str]] = {}
    for ev in events:
        if ev.get("coin") and ev.get("direction") in ("long", "short"):
            by_coin.setdefault((ev["coin"], ev["direction"]), set()).add(ev["data"].get("source"))
    for (coin, side), sources in by_coin.items():
        if len(sources) >= 2:
            out.append(_ev(
                "cross_confirm", coin, side, 85,
                f"{coin}: {side} events on both {' and '.join(sorted(sources))}",
                f"cross:{coin}:{side}", sources=sorted(sources),
            ))
            continue
        # One source fired; check the other source's standing smart-money lean.
        (src,) = sources
        for other, snap in snapshots.items():
            if other == src:
                continue
            lean = _standing_lean(snap, coin)
            if lean == side:
                out.append(_ev(
                    "cross_confirm", coin, side, 75,
                    f"{coin}: {side} event on {src} matches {other} smart-money lean",
                    f"cross:{coin}:{side}", sources=[src, other],
                ))
    return out


def _standing_lean(snap: dict, coin: str) -> str | None:
    c = (snap.get("coins") or {}).get(coin) or {}
    state = (c.get("wallets_30d") or {}).get("state") or c.get("smart_lean")
    return {"up": "long", "long": "long", "defensive": "short", "short": "short"}.get(state or "")
