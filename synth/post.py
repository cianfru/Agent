"""The public daily post and the weekly scorecard.

Rules (shared with Block0's agent and the products' standing rules):
- at most 280 characters, no links (Orbio refuses them; X turns bare domains into links)
- no forecast or hype words (FORBIDDEN): say what the data shows, not what happens next
- every setup is logged as a numbered pick before the outcome; the Monday scorecard counts misses too
"""

from __future__ import annotations

import datetime as dt
import re

from pydantic import BaseModel, Field

MAX_LEN = 280
# Block0's list (agent/format.mjs) plus the products' banned forecast words.
FORBIDDEN = re.compile(
    r"\b(will|expect(?:ed|s|ing)?|likely|predict(?:s|ed|ion)?|targets?|probabilit(?:y|ies)|odds|buy|sell now"
    r"|moon|gem|rug(?:ged|pull)?|scam|guaranteed?|alpha|easy money|can't lose|financial advice)\b",
    re.I,
)
LINK = re.compile(
    r"https?://\S*|\bwww\.\S*|\b[a-z0-9-]+\.(?:app|com|xyz|io|so|co|net|org|fun|ai|gg|me|to|ly|dev|finance"
    r"|trade|trading|exchange|money|meme|cc|tv|info|link|site|online|club|pro|vip|top|lol)\b",
    re.I,
)


FOOTER = "Context, not advice. Scorecard Mondays."
SHORT_FOOTER = "Not advice."
# Engine and options codes that mean nothing to a reader on X.
JARGON = re.compile(r"\b(STRONG_LONG|LIGHT_LONG|RISK_OFF|TRIM_HARD|NO_LONG|REVIVAL_SEED|MARKUP|MARKDOWN|REACC|ACCUM|"
                    r"BLOWOFF|rr25|z_?1d|z_?4h|taker|delta)\b")


def lint(text: str, footer: bool = True) -> list[str]:
    problems = []
    if not text or not text.strip():
        problems.append("empty")
    if len(text) > MAX_LEN:
        problems.append(f"length {len(text)} > {MAX_LEN}")
    if m := FORBIDDEN.search(text):
        problems.append(f'forbidden word "{m.group(0)}"')
    if m := LINK.search(text):
        problems.append(f'link "{m.group(0)}"')
    if m := JARGON.search(text):
        problems.append(f'internal jargon "{m.group(0)}": use plain words')
    if footer and FOOTER not in text and SHORT_FOOTER not in text and "scorecard" not in text.lower():
        problems.append("missing the not-advice footer")
    return problems


class DeskPost(BaseModel):
    """Fields the model fills; code assembles and length-checks the post."""

    regime_line: str = Field(description="Market backdrop in <=55 chars, plain words, e.g. '80% of coins trending up, Fear & Greed 59'.")
    smart_money_line: str = Field(
        description="Where profitable Hyperliquid traders and Derive's top option wallets line up, <=75 chars, with numbers, e.g. 'Top traders: $129M long vs $39M short; Derive whales long BTC'."
    )
    setup_coin: str | None = Field(description="Ticker of the one setup, or null when nothing lines up.")
    setup_direction: str = Field(description="'long', 'short' or 'none'.")
    setup_line: str = Field(description="Why the data lines up for that coin, <=60 chars, plain words with numbers from the input, e.g. 'trend up on daily, call buying $6M'.")
    invalidation_line: str = Field(description="What in the data would undo it, <=40 chars, plain words, e.g. 'daily trend turns down'.")
    chain_line: str | None = Field(
        default=None, description="Optional Robinhood Chain launch fact from block0, <=60 chars, or null."
    )


def assemble(p: DeskPost, pick_no: int | None, day: dt.date) -> str:
    """Build the post. The optional chain line goes first; the "not advice" footer always stays."""
    head = f"Desk read {day.strftime('%-d %b')}"
    setup = []
    if p.setup_coin and p.setup_direction in ("long", "short"):
        tag = f" #{pick_no}" if pick_no else ""
        setup = [f"Setup{tag}: ${p.setup_coin.upper()} {p.setup_direction} bias. {p.setup_line}",
                 f"Off if: {p.invalidation_line}"]
    else:
        setup = ["No setup today: sources disagree."]
    optional = [p.chain_line] if p.chain_line else []
    for extra, foot in ((optional, FOOTER), ([], FOOTER), ([], SHORT_FOOTER)):
        lines = [head, p.regime_line, p.smart_money_line, *setup, *extra, foot]
        text = "\n".join(line.strip() for line in lines if line and line.strip())
        if len(text) <= MAX_LEN:
            return text
    return text  # still too long: lint() reports it and the caller regenerates or rejects


def scorecard(picks: list[dict], day: dt.date) -> str | None:
    """Weekly public scorecard over every logged desk setup that has a 7-day result: posted or not, so a setup left
    unposted cannot drop out of the count (how many were not posted is said). Pure."""
    done = [p for p in picks if p.get("ret_7d") is not None]
    if not done:
        return None
    wins = [p for p in done if p["ret_7d"] > 0]
    avg = sum(p["ret_7d"] for p in done) / len(done)
    best = max(done, key=lambda p: p["ret_7d"])
    worst = min(done, key=lambda p: p["ret_7d"])

    def fmt(p: dict) -> str:
        return f"#{p['id']} ${p['coin']} {p['direction']} {p['ret_7d'] * 100:+.1f}%"

    lines = [
        f"Desk scorecard {day.strftime('%-d %b')}",
        f"Setups scored at 7 days: {len(done)}",
        f"Right direction: {len(wins)}/{len(done)}",
        f"Average move their way: {avg * 100:+.1f}%",
        f"Best: {fmt(best)}",
        f"Worst: {fmt(worst)}",
    ]
    unposted = sum(1 for p in done if not p.get("published_ts"))
    if unposted:
        lines.append(f"Logged but not posted: {unposted}")
    lines.append("Every setup counted, misses included.")
    return "\n".join(lines)
