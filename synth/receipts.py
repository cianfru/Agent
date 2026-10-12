"""The public record (receipts): every call the agent logged and how it went, misses and voided calls included,
shown RECEIPTS_DELAY_H after it was logged (holders see calls live on the desk).

Built from the ledger alone (synth/store.py), so it shows exactly what the hash chain holds. The chain is published
whole: entries about calls still inside the delay keep their hashes but not their bodies, so anyone can check that no
entry was changed, dropped or slipped in later, and, once a body is shown, that it is the one hashed back then."""

from __future__ import annotations

import json
import statistics
import time

from .store import Store

HORIZONS = ("1d", "3d", "7d", "14d", "30d")


def _calls(rows: list[dict]) -> dict[int, dict]:
    calls: dict[int, dict] = {}
    for r in rows:
        b = json.loads(r["body"])
        pid = b.get("pick")
        if r["kind"] == "call":
            calls[pid] = {**b, "seq": r["seq"], "returns": {}, "void": None, "posted": None}
            continue
        c = calls.get(pid)
        if c is None:
            continue
        if r["kind"] == "score":
            c["returns"][b["horizon"]] = b["ret"]
        elif r["kind"] == "void":
            c["void"] = b["reason"]
        elif r["kind"] == "posted":
            c["posted"] = {"ts": b["ts"], "px": b.get("px")}
        elif r["kind"] == "unposted":
            c["posted"] = None
    return calls


def _stats(calls: list[dict]) -> dict:
    live = [c for c in calls if not c["void"]]
    out = {"calls": len(live), "void": len(calls) - len(live), "horizons": {}}
    for h in HORIZONS:
        v = [c["returns"][h] for c in live if h in c["returns"]]
        if v:
            out["horizons"][h] = {"n": len(v), "right_way": sum(1 for x in v if x > 0),
                                  "median_pct": round(100 * statistics.median(v), 2),
                                  "mean_pct": round(100 * sum(v) / len(v), 2)}
    return out


def public(store: Store, delay_s: float, now: float | None = None, limit: int = 500) -> dict:
    now = now or time.time()
    calls = _calls(store.ledger())
    cutoff = now - delay_s
    shown = sorted((c for c in calls.values() if c["ts"] <= cutoff), key=lambda c: -c["ts"])
    by_source: dict[str, list[dict]] = {}
    for c in shown:
        by_source.setdefault(c["source"], []).append(c)
    chain = store.ledger_verify()
    return {"generated": now, "delay_hours": round(delay_s / 3600, 2),
            "chain": {"ok": chain["ok"], "length": chain["length"], "head": chain["head"]},
            "withheld": sum(1 for c in calls.values() if c["ts"] > cutoff),
            "summary": {"all": _stats(shown), **{s: _stats(cs) for s, cs in sorted(by_source.items())}},
            "calls": [{k: c[k] for k in ("pick", "ts", "source", "coin", "side", "conviction", "entry", "returns",
                                         "void", "posted", "seq")} | ({"backfilled": True} if c.get("backfilled") else {})
                      for c in shown[:limit]]}


def public_ledger(store: Store, delay_s: float, now: float | None = None) -> dict:
    """The whole chain, bodies about calls still inside the delay withheld (their hashes kept)."""
    now = now or time.time()
    rows = store.ledger()
    call_ts = {json.loads(r["body"])["pick"]: json.loads(r["body"])["ts"] for r in rows if r["kind"] == "call"}
    cutoff = now - delay_s
    out = []
    for r in rows:
        hidden = r["pick_id"] is not None and call_ts.get(r["pick_id"], 0) > cutoff
        out.append({"seq": r["seq"], "kind": r["kind"], "prev": r["prev"], "body_hash": r["body_hash"],
                    "hash": r["hash"], "body": None if hidden else r["body"]})
    return {"generated": now, "delay_hours": round(delay_s / 3600, 2),
            "how_to_check": "for each entry: hash = sha256(prev + ':' + body_hash); where the body is shown, "
                            "body_hash = sha256(body); the first prev is 64 zeros",
            "entries": out}
