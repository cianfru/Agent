"""Outrider on X: a character that posts what the data shows, built to bring traders to Cowboy.

The owner's strategy (11 October 2026): Cowboy (options on Derive) is the product; Outrider is the funnel, a masked
scout who rides ahead of the herd and posts what he sees, mostly about Derive's options as Cowboy shows them, some
about Hyperliquid's smart money. It posts and never replies. Context, never a call.

How a post is made:
- Code finds the stories (`find_stories`): facts from Cowboy's public files and Outrider's own alerts, every number
  already formatted for the reader. Nothing is computed by the model.
- Code picks one (`choose`): Cowboy stories weigh more, fresh ones more again, and a kind or coin posted recently
  weighs less, so the feed rotates.
- The model writes it in the voice (`VOICE`), then `lint` checks it: length, no forecast or advice words, no
  emojis, hashtags, links or addresses, never "Reflex" or "Larsson", @derivexyz on Cowboy stories, and every number
  in the post must appear in the facts. A failing draft is retried once with the problems, then rejected.
- Drafts go to the owner's Telegram with Post and Skip; a reply to a draft replaces its text.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import time
from typing import Any

import httpx

from . import post
from .wallet_names import codename

DAY = 86400
VOICE = """You are Outrider: a masked scout who rides ahead of the herd and reports what you see. You watch where \
traders put real money on Derive's options and on Hyperliquid, through Cowboy (an options app built on Derive), and \
you post about it on X.

Voice
- Laconic, dry, sure of yourself: a frontier scout's economy of words, with a wink at the end.
- Numbers first. Short sentences. One idea per post.
- A western word or two where it fits (herd, tracks, trail, pit, saloon, dust, camp, ride). Never costume: no \
"yeehaw", "partner" or "howdy", no stacked cowboy cliches.
- Talk like a sharp trader to other traders on the timeline, not like a brand or a newsletter.
- Find what others miss: the gap, the contrast, the number that does not fit. Lead with that hook, then the why, \
with a comparison.

What you never do
- Never say what happens next, never call a top or a bottom, never tell anyone to trade. You show where the money \
sits and what happened; the reader decides.
- Never use: will, 'll, going to, gonna, about to, soon, expect, likely, predict, target, probability, odds, buy, \
sell, ape, send it, moon, gem, alpha, NFA, guaranteed.
- No emojis, no hashtags, no links, no wallet addresses, no @mentions other than @derivexyz.
- Name no source other than Derive, Hyperliquid and Cowboy.
- Every number comes from the facts you are given, written exactly as given. Do not round, convert or compute new \
numbers, and do not spell numbers out in words.

Form
- One post, at most 260 characters, plain text: no quotation marks around it, no title, no sign-off.
- Cashtags like $BTC are fine, at most two."""

MAX_PERSONA_LEN = 270
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️⬀-⯿]")
EXTRA_FORBIDDEN = re.compile(r"\b(\w+'ll|gonna|going to|about to|soon|incoming|next leg|ape[sd]?|send it|nfa)\b", re.I)
PRIVATE = re.compile(r"\b(reflex|larsson|rcce)\b", re.I)
ADDRESS = re.compile(r"\b0x[0-9a-fA-F]{6,}")
MENTION = re.compile(r"@(\w+)")
HASHTAG = re.compile(r"(?<![\w&])#[A-Za-z_]\w*")     # "#1" is a rank, not a hashtag
WEIGHT = {"settled": 1.3, "lean": 1.1, "saloon": 1.0, "walls": 0.9, "wallet": 0.9, "conviction": 0.85,
          "open_winner": 0.85, "build": 0.8, "herd": 0.6}
ONCE_IN_3_WEEKS = ("wallet", "open_winner")      # the same trader is not featured twice in 3 weeks


# ---------------------------------------------------------------- formatting --
def money(x: float) -> str:
    a = abs(x)
    if a >= 1e9:
        return f"${a / 1e9:.1f}B"
    if a >= 1e6:
        return f"${a / 1e6:.1f}M"
    if a >= 1e4:
        return f"${a / 1e3:.0f}K"
    return f"${a:,.0f}"


def price(x: float) -> str:
    if x >= 1000:
        return f"${x:,.0f}"
    if x >= 1:
        return f"${x:,.2f}"
    return f"${x:.4g}"


def strike(x: float) -> str:
    """$85K, $85.5K, $2,600, $84"""
    if x >= 10_000:
        k = x / 1000
        return f"${k:.0f}K" if k == int(k) else f"${k:.1f}K"
    return price(x) if x < 1000 else f"${x:,.0f}"


def cents(p: float) -> str:
    return f"{round(100 * p)}¢"


def pct(x: float) -> str:
    return f"{100 * x:+.1f}%"


def _when(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%-d %b")


# ------------------------------------------------------------------- stories --
def _story(kind: str, coin: str | None, key: str, facts: dict, angles: list[str], weight: float | None = None,
           cowboy: bool = True) -> dict:
    return {"kind": kind, "coin": coin, "key": key, "facts": facts, "angles": angles,
            "weight": WEIGHT.get(kind, 0.5) if weight is None else weight, "cowboy": cowboy}


def saloon_stories(questions: dict[str, dict]) -> list[dict]:
    """One per coin: the nearest date's headline question, what Higher and Lower cost and how far price must move."""
    out = []
    for und, q in questions.items():
        idx = q.get("index")
        dates = [d for d in q.get("dates") or [] if d.get("board")]
        if not idx or not dates:
            continue
        d = min(dates, key=lambda d: d.get("days") or 99)
        lv = next((x for x in d.get("levels") or [] if x.get("id") == d.get("headline")), None)
        if not lv:
            continue
        yes, no = lv.get("yes") or {}, lv.get("no") or {}
        facts: dict[str, Any] = {"coin": und, "date": d["label"], "settles": "08:00 UTC", "price_now": price(idx),
                                 "pays_in_part_between": f"{strike(lv['lo'])} and {strike(lv['hi'])}",
                                 "see_it": "the Saloon on Cowboy", "priced_from": "Derive options"}
        if yes.get("state") == "open" and yes.get("buy"):
            facts["higher"] = {"costs": f"{cents(yes['buy'])} per $1", "pays_full_from": strike(lv["hi"]),
                               "move_needed": pct(lv["hi"] / idx - 1)}
        if no.get("state") == "open" and no.get("buy"):
            facts["lower"] = {"costs": f"{cents(no['buy'])} per $1", "pays_full_at_or_below": strike(lv["lo"]),
                              "move_needed": pct(lv["lo"] / idx - 1)}
        if "higher" not in facts and "lower" not in facts:
            continue
        if q.get("thin"):
            facts["note"] = "thin order book"
        out.append(_story("saloon", und, f"saloon:{und}:{d['expiry']}", facts,
                          ["what each side costs against how far price has to move",
                           "a full dollar for a few cents less than a dollar: what has to happen by 08:00 UTC"],
                          weight=WEIGHT["saloon"] * (1.0 if und in ("BTC", "ETH") else 0.8)))
    return out


def lean_stories(questions: dict[str, dict]) -> list[dict]:
    """Per settlement date, Derive's directional option wallets long against short, and where their money points."""
    out = []
    for und, q in questions.items():
        for d in q.get("dates") or []:
            t = d.get("traders") or {}
            if t.get("status") != "ready" or not t.get("gross_delta_usd"):
                continue
            net = t.get("net_delta_usd") or 0
            money_side = {"up": "long", "down": "short"}.get(t.get("state"), "balanced")   # Cowboy's +/-0.25 rule
            heads = "long" if t["long"] > t["short"] else "short" if t["short"] > t["long"] else "even"
            split = money_side != "balanced" and heads not in (money_side, "even")
            facts = {"coin": und, "date": d["label"], "wallets_long": t["long"], "wallets_short": t["short"],
                     "money_points": money_side, "net": f"{money(net)} {'long' if net > 0 else 'short'}",
                     "of_gross": money(t["gross_delta_usd"]),
                     "who": "every directional option wallet on Derive (market makers and hedgers left out)",
                     "see_it": "the Saloon on Cowboy"}
            angles = ["more wallets on one side, more money on the other"] if split else \
                ["the herd and its money pointing the same way"]
            crowd = 1.0 if t["long"] + t["short"] >= 8 else 0.6
            out.append(_story("lean", und, f"lean:{und}:{d['expiry']}", facts, angles,
                              weight=WEIGHT["lean"] * (1.0 if split else 0.6) * crowd))
    return out


def walls_stories(markets: dict) -> list[dict]:
    """Where the biggest call and put bets sit over the next 30 days, against the price."""
    out = []
    for c in markets.get("coins") or []:
        lv = ((c.get("options") or {}).get("levels") or {})
        px, cw, pw = c.get("price"), lv.get("call_wall"), lv.get("put_wall")
        if not (px and cw and pw):
            continue
        und = c["und"]
        facts = {"coin": und, "price_now": price(px), "window": "next 30 days",
                 "biggest_call_pile": strike(cw), "call_pile_distance": pct(cw / px - 1),
                 "call_pile_size": f"{lv.get('call_wall_oi', 0):,.0f} {und} of open calls",
                 "biggest_put_pile": strike(pw), "put_pile_distance": pct(pw / px - 1),
                 "put_pile_size": f"{lv.get('put_wall_oi', 0):,.0f} {und} of open puts",
                 "where": "Derive options", "see_it": "the coin's options page on Cowboy"}
        out.append(_story("walls", und, f"walls:{und}", facts,
                          ["how close the upside bets sit against how far the crash cover sits"],
                          weight=WEIGHT["walls"] * (1.0 if und in ("BTC", "ETH") else 0.6)))
    return out


def wallet_stories(traders: dict, limit: int = 20) -> list[dict]:
    """A spotlight on one of Derive's best option traders, by codename (the name Cowboy shows), never the address."""
    out = []
    total = traders.get("ranked_total")
    for t in (traders.get("traders") or [])[:limit]:
        if not t.get("option_pnl") or t.get("class") != "directional":
            continue
        coins = sorted((t.get("coins") or {}).items(), key=lambda kv: -kv[1])[:2]
        lean = (t.get("lean") or {}).get("state")
        first = t.get("first")
        facts = {"codename": codename(t["address"]), "rank": f"#{t['rank']}", "of_ranked": f"{total:,}" if total else None,
                 "options_profit": money(t["option_pnl"]), "trading_since": dt.date.fromisoformat(first).strftime("%b %Y")
                 if first else None, "settled_bets": f"{t.get('expired') or 0:,}",
                 "won_share": f"{round(100 * (t.get('win_rate') or 0))}%", "open_positions": t.get("open_count"),
                 "cohort": " · ".join(x for x in (t.get("pnl_cohort"), t.get("size_cohort")) if x),
                 "main_coins": [k for k, _ in coins], "leans_now": {"up": "long", "down": "short"}.get(lean, "balanced"),
                 "market_maker": "no: under 5% of its fills rest on the book" if (t.get("maker_share") or 0) < 0.05
                 else None, "see_it": "the Traders page on Cowboy, by this codename"}
        out.append(_story("wallet", None, f"wallet:{t['address'].lower()}", {k: v for k, v in facts.items() if v},
                          ["what this record says about how they trade", "the codename and the receipts"]))
    return out


def settled_stories(questions: dict[str, dict], histories: dict[str, dict], now: float) -> list[dict]:
    """A date that settled in the last 36 hours: the cheapest answer, a day before, that then paid in full."""
    out = []
    for und, q in questions.items():
        for s in q.get("settled") or []:
            sp, ts = s.get("settle_price"), s.get("settle_ts") or 0
            if sp is None or now - ts > 36 * 3600:
                continue
            h = histories.get(f"{und}-{s['expiry']}") or {}
            times = h.get("ts") or []
            i = max((k for k, t in enumerate(times) if t <= ts - DAY), default=None)
            best = None
            for lv in s.get("levels") or []:
                paid = lv.get("paid")
                row = (h.get("rows") or {}).get(lv["id"]) or {}
                if paid is None or i is None:
                    continue
                for side, full, series in (("Higher", paid >= 0.999, row.get("yb")), ("Lower", paid <= 0.001, row.get("nb"))):
                    cost = series[i] if series and i < len(series) else None
                    if full and cost and 0.02 <= cost <= 0.9 and (best is None or cost < best[1]):
                        best = (side, cost, lv)
            if not best:
                continue
            side, cost, lv = best
            facts = {"coin": und, "date": s["label"], "settled_at": price(sp),
                     "answer": f"{side} on {strike(lv['lo'])} to {strike(lv['hi'])}",
                     "cost_a_day_before": f"{cents(cost)} per $1", "paid": "$1 per $1",
                     "times_the_money": f"{1 / cost:.1f}x", "see_it": "the Saloon on Cowboy"}
            out.append(_story("settled", und, f"settled:{und}:{s['expiry']}", facts,
                              ["what it cost a day out against what it paid"]))
    return out


def herd_story(markets: dict) -> list[dict]:
    d = (markets.get("consensus_detail") or {}).get("1d") or {}
    n = d.get("counts") or {}
    fg = ((markets.get("context") or {}).get("sentiment") or {})
    if not n.get("total"):
        return []
    facts = {"coins_trending_up": n.get("markup", 0), "of_coins": n["total"], "timeframe": "daily",
             "fear_and_greed": fg.get("fear_greed_value"), "fear_and_greed_word": fg.get("fear_greed_label"),
             "where": "the perps on Derive", "see_it": "Markets on Cowboy"}
    return [_story("herd", None, f"herd:{time.strftime('%Y-%m-%d')}", {k: v for k, v in facts.items() if v is not None},
                   ["how one-sided the herd is"])]


def hyperliquid_stories(alerts: list[dict], builds: list[dict]) -> list[dict]:
    """Outrider's own fresh alerts: a proven trader's conviction bet, or several traders building one coin."""
    out = []
    for a in alerts:
        b = a["body"]
        if not b.get("value"):
            continue
        facts = {"venue": "Hyperliquid", "coin": b["coin"], "side": b["side"], "size": money(b["value"]),
                 "times_their_usual_size": f"{b['x']:.1f}x" if b.get("x") else None,
                 "leverage": f"{b['lev']:.1f}x" if b.get("lev") else None,
                 "their_record": b.get("record"), "scored_at": "14 and 30 days after the open"}
        out.append(_story("conviction", b["coin"], f"conviction:{b['coin']}:{b['side']}:{int(a['ts'])}",
                          {k: v for k, v in facts.items() if v}, ["a trader with receipts sizing up"], cowboy=False))
    for a in builds:
        b = a["body"]
        if not b.get("traders"):
            continue
        facts = {"venue": "Hyperliquid", "coin": b["coin"], "side": b["side"], "traders_adding": b["traders"],
                 "added": money(b["added_usd"]), "over": "3 days",
                 "price_moved": pct(b["move"]) if b.get("move") is not None else None,
                 "who": "traders with a record of holding winners for weeks"}
        out.append(_story("build", b["coin"], f"build:{b['coin']}:{b['side']}:{int(a['ts'])}",
                          {k: v for k, v in facts.items() if v is not None},
                          ["money moving in while the price sits still"], cowboy=False))
    return out


WINNER_FRESH_S = 6 * 3600   # "still open" is said only of a position read in the last 6 hours


def open_winner_stories(winners: list[dict], now: float | None = None) -> list[dict]:
    """A Hyperliquid wallet sitting on a big winner for a month or more: held, up on price, on margin, open profit.
    Only from a recent read, since the story says it is still open."""
    now = now or time.time()
    out = []
    for w in winners:
        if not w.get("as_of") or now - w["as_of"] > WINNER_FRESH_S:
            continue
        if (w.get("held_days") or 0) < 30 or (w.get("move") or 0) < 0.5 or w["upnl"] < 50_000:
            continue
        since = dt.date.fromisoformat(w["since"]).strftime("%-d %b") if w.get("since") else None
        facts = {"venue": "Hyperliquid", "codename": w["codename"], "coin": w["coin"], "side": w["side"],
                 "held": f"{int(w['held_days'])} days", "since": since, "up_on_price": pct(w["move"]),
                 "on_margin": pct(w["roe"]) if w.get("roe") is not None else None, "open_profit": money(w["upnl"]),
                 "of_the_account": f"{round(100 * w['share_of_account'])}%" if w.get("share_of_account") else None,
                 "still_open": "yes, not sold"}
        out.append(_story("open_winner", w["coin"], f"open:{w['address']}:{w['coin']}",
                          {k: v for k, v in facts.items() if v is not None},
                          ["the patience: how long they have sat in it and what it is worth now"], cowboy=False))
    return out


# ---------------------------------------------------------------- gathering --
async def gather(base_url: str, client: httpx.AsyncClient | None = None, coins: int = 6) -> dict:
    """Cowboy's public files the stories need: markets, traders, the Saloon per coin and fresh settlement history."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)

    async def get(path: str) -> Any:
        try:
            r = await client.get(f"{base_url}/{path}")
            return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    try:
        markets, traders, index = await asyncio.gather(get("markets.json"), get("traders.json"),
                                                       get("questions/index.json"))
        unds = [c["und"] for c in (index or {}).get("coins") or []][:coins]
        docs = await asyncio.gather(*(get(f"questions/{u}.json") for u in unds))
        questions = {u: d for u, d in zip(unds, docs) if d}
        want = [f"{u}-{s['expiry']}" for u, q in questions.items() for s in q.get("settled") or []
                if s.get("settle_price") is not None]
        hist = await asyncio.gather(*(get(f"questions/history/{k}.json") for k in want))
    finally:
        if own:
            await client.aclose()
    if traders:
        traders = {"ranked_total": traders.get("ranked_total"), "traders": (traders.get("traders") or [])[:20]}
    return {"markets": markets or {}, "traders": traders or {}, "questions": questions,
            "histories": {k: h for k, h in zip(want, hist) if h}}


def find_stories(data: dict, alerts: list[dict], builds: list[dict], now: float,
                 winners: list[dict] | None = None) -> list[dict]:
    q, m = data.get("questions") or {}, data.get("markets") or {}
    return (settled_stories(q, data.get("histories") or {}, now) + lean_stories(q) + saloon_stories(q)
            + walls_stories(m) + wallet_stories(data.get("traders") or {}) + herd_story(m)
            + hyperliquid_stories(alerts, builds) + open_winner_stories(winners or [], now))


def choose(stories: list[dict], recent: list[dict], now: float) -> dict | None:
    """The best story not told lately: a key posted in the last 3 weeks (wallets) or 2 days (others) is out; the
    same kind in the last 12 hours or the same coin in the last 6 weighs less; Cowboy stories outweigh the rest."""
    told: dict[str, float] = {}
    for r in recent:
        k = (r.get("story") or {}).get("key")
        told[k] = max(told.get(k, 0), r["ts"])
    best, best_score = None, 0.0
    for s in stories:
        last = told.get(s["key"])
        if last and now - last < (21 if s["kind"] in ONCE_IN_3_WEEKS else 2) * DAY:
            continue
        score = s["weight"]
        for r in recent:
            rs = r.get("story") or {}
            age = now - r["ts"]
            if rs.get("kind") == s["kind"] and age < 12 * 3600:
                score *= 0.4
            if s.get("coin") and rs.get("coin") == s["coin"] and age < 6 * 3600:
                score *= 0.7
        if score > best_score:
            best, best_score = s, score
    return best


# ------------------------------------------------------------------- checks --
def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(text)}


def lint(text: str, story: dict, check_numbers: bool = True) -> list[str]:
    problems = [p for p in post.lint(text, footer=False) if not p.startswith("length")]
    if len(text) > MAX_PERSONA_LEN:
        problems.append(f"length {len(text)} > {MAX_PERSONA_LEN}")
    for rx, what in ((EXTRA_FORBIDDEN, "forecast or advice word"), (PRIVATE, "private name"),
                     (ADDRESS, "wallet address"), (HASHTAG, "hashtag")):
        if m := rx.search(text):
            problems.append(f'{what} "{m.group(0)}"')
    if EMOJI.search(text):
        problems.append("emoji")
    others = [m for m in MENTION.findall(text) if m.lower() != "derivexyz"]
    if others:
        problems.append(f"mention @{others[0]}: only @derivexyz")
    tags = len(re.findall(r"@derivexyz\b", text, re.I))
    if story.get("cowboy") and tags != 1:
        problems.append("tag @derivexyz exactly once")
    allowed = _numbers(json.dumps(story.get("facts") or {}, ensure_ascii=False))
    stray = sorted(_numbers(text) - allowed) if check_numbers else []
    if stray:
        problems.append(f"numbers not in the facts: {', '.join(stray)} (use the numbers exactly as given)")
    return problems


def task(story: dict, recent_texts: list[str]) -> str:
    tag = ("Tag @derivexyz once, where it reads naturally." if story["cowboy"]
           else "This one is about Hyperliquid: no @derivexyz tag.")
    recent = "\n".join(f"- {t}" for t in recent_texts[:6]) or "- none yet"
    return (f"Write one post from these facts.\n\nFacts (use numbers exactly as written; no other numbers):\n"
            f"{json.dumps(story['facts'], ensure_ascii=False, indent=1)}\n\n"
            f"Angles you could take (or find a better one in the facts): {'; '.join(story['angles'])}\n\n"
            f"{tag}\n\nYour latest posts (do not repeat their openings or turns of phrase):\n{recent}\n\n"
            f"Return only the post text.")
