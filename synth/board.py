"""The dashboard's "smart money now" board. Pure functions over stored snapshots.

One row per coin with what each layer says, and a vote:
  engine     1d signal (Cowboy's unified signal, else Reflex's) -> long / short / none
  hl_smart   Reflex: profitable Hyperliquid traders' net lean (long/short by dollars)
  derive     Cowboy: Derive smart-wallet delta over 30 days (up / defensive)
  options    Cowboy: options tone over 7 days (skew + taker flow)
`agree` = longs - shorts over the layers that have a reading.
"""

from __future__ import annotations

from collections import defaultdict

from .detectors import _signal_side

LAYERS = ("engine", "hl_smart", "derive", "options")
STATE = {"up": "long", "long": "long", "defensive": "short", "short": "short"}


def _engine(cb: dict, rf: dict) -> str | None:
    sig = cb.get("unified") or cb.get("signal_1d") or rf.get("signal_1d")
    return _signal_side(sig)[0]


def smart_board(snaps: dict[str, dict], events: list[dict]) -> list[dict]:
    cbc = (snaps.get("cowboy") or {}).get("coins") or {}
    rfc = (snaps.get("reflex") or {}).get("coins") or {}
    ev_by = defaultdict(list)
    for e in events:
        if e.get("coin"):
            ev_by[e["coin"]].append(e)
    rows = []
    for coin in sorted(set(cbc) | set(rfc)):
        cb, rf = cbc.get(coin) or {}, rfc.get(coin) or {}
        votes = {
            "engine": _engine(cb, rf),
            "hl_smart": rf.get("smart_lean") if rf.get("smart_lean") in ("long", "short") else None,
            "derive": STATE.get((cb.get("wallets_30d") or {}).get("state") or ""),
            "options": STATE.get((cb.get("options_7d") or {}).get("state") or ""),
        }
        longs = sum(v == "long" for v in votes.values())
        shorts = sum(v == "short" for v in votes.values())
        evs = sorted(ev_by.get(coin, []), key=lambda e: -e["score"])
        sl, ss = rf.get("smart_long") or {}, rf.get("smart_short") or {}
        rows.append({
            "coin": coin,
            "price": cb.get("price") or rf.get("price"),
            "chg_24h": cb.get("chg_24h"),
            "votes": votes,
            "agree": longs - shorts,
            "longs": longs,
            "shorts": shorts,
            "layers": sum(v is not None for v in votes.values()),
            "signal_1d": cb.get("unified") or cb.get("signal_1d") or rf.get("signal_1d"),
            "regime_1d": cb.get("regime_1d") or rf.get("regime_1d"),
            "hl": {"long_n": sl.get("n"), "long_usd": sl.get("usd"), "short_n": ss.get("n"), "short_usd": ss.get("usd"),
                   "opens_24h": rf.get("opens_24h"), "convergence": rf.get("convergence")} if rf else None,
            "derive": {"net_30d": (cb.get("wallets_30d") or {}).get("net_delta_usd"),
                       "net_7d": (cb.get("wallets_7d") or {}).get("net_delta_usd"),
                       "rr25_30d": cb.get("rr25_30d"), "taker_net_24h": cb.get("taker_net_24h_usd")} if cb else None,
            "events": len(evs),
            "top_event": evs[0]["title"] if evs else None,
            "top_score": evs[0]["score"] if evs else 0,
        })
    rows.sort(key=lambda r: (-abs(r["agree"]), -r["top_score"], r["coin"]))
    return rows
