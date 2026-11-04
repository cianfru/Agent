"""/studies: every study against its declared bar, and when its verdict can come at the pace so far.

The counts come from the same code that computes each verdict (journal.s5 and first_bets for S5, s6 for S6, the
convergence groups and their picks for S1, the position picks for S3's forward record). A date is projected from what
has been logged since the study began, plus the horizon its outcome needs before it is scored; it assumes the pace
holds. A study that has logged too little reads "too early to project". A verdict, when it comes, is a pass or
"context, not a signal" (docs/studies)."""

from __future__ import annotations

import calendar
import statistics
import time

from . import journal, s6, study

DAY = 86400.0

DONE = [
    ("S2", "does the traders' lean mark tops and bottoms?",
     "done 10 Oct: context, not a signal (they buy dips and take profit into strength)"),
    ("S4", "do build-ups lead to a move their way?", "done 10 Oct: context, not a signal"),
    ("E1", "wallets grouped into entities and classes", "done: a description, no verdict by design"),
    ("S3", "are proven traders worth following, on their history?",
     "done 10 Oct: everything they open, no; conviction bets met the bar on a list that favours them: promising, "
     "not proven until the live check below"),
]


def _date(ts: float, now: float) -> str:
    fmt = "%-d %b" if time.gmtime(ts).tm_year == time.gmtime(now).tm_year else "%-d %b %Y"
    return time.strftime(fmt, time.gmtime(ts))


def project(logged: list[float], need: int, lag_days: float, now: float, since: float | None = None) -> str:
    """When the `need`-th item is logged at the pace so far, plus the days before it is scored. `logged`: the times
    items were logged; `since`: when logging began (else the first item)."""
    ts = sorted(logged)
    if len(ts) >= need:
        due = ts[need - 1] + lag_days * DAY
        return "due now" if due <= now else f"about {_date(due, now)}"
    start = since if since is not None else (ts[0] if ts else None)
    elapsed = (now - start) / DAY if start is not None else 0.0
    if len(ts) < 3 or elapsed < 2:
        return "too early to project"
    rate = len(ts) / elapsed
    return f"about {_date(now + ((need - len(ts)) / rate + lag_days) * DAY, now)}"


def project_days(day_ts: list[float], need: int, lag_days: int, now: float) -> str:
    """For a study scored once a day: the days logged so far, one more each day, and `lag_days` before the last is
    scored."""
    if not day_ts:
        return f"about {_date(now + (need + lag_days) * DAY, now)} (starts with the next daily log)"
    last = max(day_ts)
    due = last + (max(0, need - len(day_ts)) + lag_days) * DAY
    return "due now" if due <= now else f"about {_date(due, now)}"


def _day_ts(day: str) -> float:
    return float(calendar.timegm(time.strptime(day, "%Y-%m-%d")))


def _rows_by_day(store, keep) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in store.screen_log():
        if r["coin"] != "*" and not r.get("thin") and r.get("price") and keep(r):
            out[r["day"]] = out.get(r["day"], 0) + 1
    return out


def s1(store, now: float) -> dict:
    """S1 (Amendment 3): convergences the engine agreed with against the rest, scored at 3 days."""
    gs = [g for g in store.groups(limit=100_000)
          if g["venue"] == "hyperliquid" and (g["meta"].get("study") or {}).get("logged")]
    picks: dict[int, dict] = {}
    for p in store.picks(limit=1_000_000, origin="group"):
        try:
            picks[int(p["thesis"].split("#")[1])] = p
        except (IndexError, ValueError, AttributeError):
            continue
    arms = {"with": [g for g in gs if g["meta"]["study"].get("align_1d") == "with"],
            "without": [g for g in gs if g["meta"]["study"].get("align_1d") in ("against", "none")]}
    out: dict = {}
    for name, sel in arms.items():
        rets = [picks[g["id"]]["ret_3d"] for g in sel if picks.get(g["id"], {}).get("ret_3d") is not None]
        out[name] = {"logged": len(sel), "scored": len(rets), "coins": len({g["coin"] for g in sel}),
                     "median_pct": round(100 * statistics.median(rets), 2) if rets else None,
                     "positive_pct": round(100 * sum(r > 0 for r in rets) / len(rets)) if rets else None}
    w, o = out["with"], out["without"]
    need = study.MIN_EACH
    ready = (w["scored"] >= need and o["scored"] >= need and w["coins"] >= study.MIN_COINS
             and o["coins"] >= study.MIN_COINS)
    verdict = "context, not a signal"
    if ready:
        adds = (w["median_pct"] - o["median_pct"] >= 1.0 and w["median_pct"] > 0 and w["positive_pct"] >= 55)
        if adds:
            verdict = "the engine adds to convergences"
        elif w["median_pct"] <= o["median_pct"] or (w["scored"] >= 2 * need and o["scored"] >= 2 * need):
            verdict = "no difference"
        else:
            verdict = f"in between: collecting to {2 * need} each"
    slower = min(("with", "without"), key=lambda k: out[k]["logged"])
    times = [g["ts"] for g in arms[slower]]
    since = min((g["ts"] for g in gs), default=None)
    return {**out, "ready": ready, "verdict": verdict, "when": project(times, need, 3, now, since)}


def s3_forward(store, now: float) -> dict:
    """S3's live check: conviction bets logged since tracking began, scored at 30 days (30 from 10 traders)."""
    picks = [p for p in store.picks(limit=1_000_000, origin="position")
             if (p.get("conviction") or 0) >= 2 and not (p.get("state") or {}).get("upgraded_late")]

    def trader(p: dict) -> str:
        """The wallet behind a pick: kept in its state, else in its thesis ("pt <wallet> <coin> <side>")."""
        w = (p.get("state") or {}).get("wallet")
        if w:
            return w
        parts = (p.get("thesis") or "").split()
        return parts[1] if len(parts) > 1 and parts[0] == "pt" else "?"

    scored = [p for p in picks if p.get("ret_30d") is not None]
    rets = [p["ret_30d"] for p in scored]
    return {"logged": len(picks), "scored": len(scored), "traders": len({trader(p) for p in scored}),
            "traders_logged": len({trader(p) for p in picks}),
            "median_pct": round(100 * statistics.median(rets), 2) if rets else None,
            "positive_pct": round(100 * sum(r > 0 for r in rets) / len(rets)) if rets else None,
            "when": project([p["ts"] for p in picks], 30, 30, now)}


def s5(store, now: float) -> dict:
    picks = [p for p in store.picks(limit=1_000_000, origin="read") if p["ts"] >= journal.S5_SETUPS_FROM]
    v = journal.s5(picks)
    bets = journal.first_bets(picks)
    return {**v, "logged": len(bets), "when": project([p["ts"] for p in bets], journal.S5_BAR["n"], 3, now,
                                                       journal.S5_SETUPS_FROM)}


def s6_parts(store, now: float) -> dict:
    rep = s6.report(store)
    min_coins = s6.MIN_COINS
    q1 = _rows_by_day(store, lambda r: (r.get("long") or {}).get("score") is not None)
    q3 = _rows_by_day(store, lambda r: r.get("priority_1d") is not None and (r.get("long") or {}).get("score")
                      is not None)
    q1_days = [_day_ts(d) for d, n in q1.items() if n >= min_coins]
    q3_days = [_day_ts(d) for d, n in q3.items() if n >= min_coins]
    arm_times = {}
    for o in ("read", "screen", "random"):
        picks = [p for p in store.picks(limit=1_000_000, origin=o) if p["ts"] >= journal.S5_SETUPS_FROM]
        arm_times[o] = [p["ts"] for p in journal.first_bets(picks)]
    slow = min(arm_times, key=lambda k: len(arm_times[k]))
    need = s6.BAR["days"]
    return {
        "q1": {"days": max(rep["screen"]["long"].get("days", 0), rep["screen"]["short"].get("days", 0)),
               "verdict": rep["screen"]["long"].get("verdict", "context, not a signal"),
               "short_verdict": rep["screen"]["short"].get("verdict", "context, not a signal"),
               "when": project_days(q1_days, need, s6.H_DAYS, now)},
        "q2": {"bets": {o: rep["arms"][o]["bets"] for o in ("read", "screen", "random")},
               "verdict": rep["arms"]["read_vs_screen"]["verdict"],
               "when": project(arm_times[slow], s6.BAR["bets"], 3, now, journal.S5_SETUPS_FROM)},
        "q3": {"days": rep["reflex"]["days"], "verdict": rep["reflex"]["verdict"],
               "when": project_days(q3_days, need, s6.H_DAYS, now)},
    }


def report(store, now: float | None = None) -> dict:
    now = now or time.time()
    return {"s1": s1(store, now), "s3": s3_forward(store, now), "s5": s5(store, now), "s6": s6_parts(store, now),
            "done": [{"id": i, "question": q, "result": r} for i, q, r in DONE]}


def _when(w: str, reached: bool, verdict: str) -> str:
    return f"verdict: {verdict}" if reached else w


def text(rep: dict) -> str:
    """The /studies message: what each study is collecting, how far it is, when its verdict can come."""
    a, c, v, x = rep["s1"], rep["s3"], rep["s5"], rep["s6"]
    lines = ["Studies: each against its declared bar, and when its verdict can come at the pace so far. "
             "A verdict is a pass or \"context, not a signal\".", "", "Collecting"]
    lines.append(f"S1 · do convergences go better when the engine agrees? {a['with']['scored']} with and "
                 f"{a['without']['scored']} without scored at 3 days, from {a['with']['coins']} and "
                 f"{a['without']['coins']} coins (30 each from 10+ coins) · "
                 + _when(a["when"], a["ready"], a["verdict"]))
    s5_ready = v["bets"] >= v["bar"]["n"]
    lines.append(f"S5 · are the reads' setups right more often than chance? {v['bets']} separate bets of "
                 f"{v['bar']['n']} scored at 3 days ({v['logged']} logged) · " + _when(v["when"], s5_ready, v["verdict"]))
    q1, q2, q3 = x["q1"], x["q2"], x["q3"]
    need_days, need_bets = s6.BAR["days"], s6.BAR["bets"]
    lines.append(f"S6 · does the screen rank coins? {q1['days']} days of {need_days} · "
                 + _when(q1["when"], q1["days"] >= need_days, f"longs {q1['verdict']}, shorts {q1['short_verdict']}"))
    b = q2["bets"]
    lines.append(f"S6 · does the read beat the code and chance? read {b['read']}, code {b['screen']}, random "
                 f"{b['random']} bets of {need_bets} each · "
                 + _when(q2["when"], min(b.values()) >= need_bets, q2["verdict"]))
    lines.append(f"S6 · the screen against Reflex's priority (longs): {q3['days']} days of {need_days} · "
                 + _when(q3["when"], q3["days"] >= need_days, q3["verdict"]))
    s3_line = (f"S3 · conviction bets, live: {c['scored']} of 30 scored at 30 days, from {c['traders']} traders "
               f"(10 needed); {c['logged']} logged from {c['traders_logged']}")
    if c["scored"]:
        s3_line += f" · so far median {c['median_pct']:+.1f}% their way, {c['positive_pct']}% positive"
    s3_ready = c["scored"] >= 30 and c["traders"] >= 10
    lines.append(s3_line + " · " + ("minimum reached: judged against S3's bar (median +3%, 55% positive, 2 points "
                                     "over the control)" if s3_ready else c["when"]))
    lines += ["", "Done"] + [f"{d['id']} · {d['question']} · {d['result']}" for d in rep["done"]]
    lines += ["", "Dates assume the pace so far holds. The numbers behind each are in /record."]
    return "\n".join(lines)
