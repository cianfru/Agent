"""LLM synthesis: turn digests + detected events into structured reads.

Uses the Anthropic SDK with structured outputs so callers always get validated JSON.
The default model is Claude Haiku 5.5 (cheap); set SYNTH_MODEL to change it.
"""

from __future__ import annotations

import json
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

SYSTEM = """You are a crypto market-structure analyst. You receive machine data from two in-house scanners:

- Reflex (RCCE engine): per-coin regime (ACCUM, MARKUP, BLOWOFF, REACC, MARKDOWN, CAP, ...), z-score, heat (0-100, >80 is overheated), signals (STRONG_LONG, LIGHT_LONG, ACCUMULATE, WAIT, TRIM, RISK_OFF, ...), 4h/1d confluence, and Hyperliquid smart-wallet positioning.
- Cowboy (Derive options scanner): the same RCCE engine on Derive perps, plus options structure (ATM IV, 25-delta risk reversal rr25: negative = puts bid), 24h taker flow, and positioning of ranked Derive option traders. Smart cohorts are "Money Printer" > "Smart Money" > "Grinder" by realised PnL. net_delta_usd > 0 means net long exposure.

The `events` list holds what changed since the last poll, already scored 0-100 by deterministic rules. Treat it as the primary input; use the snapshot data to confirm or contradict each event.

Rules:
- Ground every claim in a number from the input. Never invent prices, levels or wallets.
- Prefer agreement across independent evidence (engine regime + options skew/flow + smart wallets). One source alone is low conviction.
- Say plainly when evidence conflicts or data is stale/missing.
- Conviction 1-5: 5 only when engine, options and smart money all agree and nothing is overheated.
- "Overheated" means heat above 80 only; below that, do not call a coin hot or overheated.
- Only propose a setup for a coin the snapshot covers (a price and an engine reading); coins mentioned only in events are context, not setups.
- This is decision support, not financial advice; never size positions."""


class Setup(BaseModel):
    coin: str
    direction: Literal["long", "short", "watch"]
    conviction: int = Field(ge=1, le=5)
    thesis: str = Field(description="One or two sentences.")
    evidence: list[str] = Field(description="Concrete data points, each with numbers and source.")
    invalidation: str = Field(description="What in the data would kill the idea.")


class Brief(BaseModel):
    regime: Literal["risk_on", "neutral", "risk_off"]
    market_read: str = Field(description="3-5 sentence read of overall market structure.")
    setups: list[Setup] = Field(description="Best opportunities, strongest first; max 5.")
    risks: list[str] = Field(description="Things that could go wrong; max 4.")


class Alert(BaseModel):
    headline: str = Field(description="Under 90 characters, e.g. '2 smart wallets added $1.2M long HYPE'.")
    coin: str | None
    direction: Literal["long", "short", "watch"]
    conviction: int = Field(ge=1, le=5)
    why_it_matters: str = Field(description="2-3 sentences tying the event to the wider structure.")
    confirms: list[str]
    contradicts: list[str]


class Answer(BaseModel):
    answer: str
    coins: list[str]


class Synthesizer:
    def __init__(self, model: str, effort: str = "low", client: anthropic.Anthropic | None = None):
        self.model = model
        self.effort = effort
        self.client = client or anthropic.Anthropic()

    def _parse(self, schema: type[BaseModel], task: str, payload: dict, max_tokens: int = 4000):
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": self.effort},
            messages=[
                {
                    "role": "user",
                    "content": f"{task}\n\n<data>\n{json.dumps(payload, separators=(',', ':'))}\n</data>",
                }
            ],
            output_format=schema,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise RuntimeError(f"model returned no usable output (stop_reason={response.stop_reason})")
        usage = {
            "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read_input_tokens": response.usage.cache_read_input_tokens or 0,
        }
        return response.parsed_output, usage

    def brief(self, digest: dict) -> tuple[Brief, dict]:
        return self._parse(
            Brief,
            "Write the market brief: overall regime, then the best setups ranked by conviction.",
            digest,
        )

    def alert(self, event: dict, context: dict) -> tuple[Alert, dict]:
        return self._parse(
            Alert,
            "Explain this event for a trader who will read it on their phone. "
            "Check it against the coin context and say whether the rest of the data confirms it.",
            {"event": event, "context": context},
            max_tokens=1500,
        )

    def desk_post(self, digest: dict, feedback: str = ""):
        from .post import DeskPost

        task = (
            "Fill the fields of today's public post for a market-structure desk account on X. Readers are "
            "crypto traders on a phone. Pick at most ONE setup where the RCCE engine, options (skew/flow) and "
            "smart money on Hyperliquid and Derive agree; if they do not agree anywhere, set setup_direction "
            "'none'. Plain words a retail trader understands, numbers from the data, no forecasts: describe "
            "what the data shows now. Never write internal codes (STRONG_LONG, MARKUP, REACC, rr25, z-score, "
            "taker, delta): say 'trending up', 'cooling off', 'puts priced above calls', 'call buying', "
            "'net long'. "
            "Never use: will, expect, likely, predict, target, probability, odds, buy, moon, gem, alpha, "
            "guaranteed. No links or domains. Tickers without $."
        )
        if feedback:
            task += f"\nThe previous draft was rejected: {feedback}. Fix that."
        return self._parse(DeskPost, task, digest, max_tokens=2000)

    def ask(self, question: str, digest: dict) -> tuple[Answer, dict]:
        return self._parse(Answer, f"Answer this question from the data only: {question}", digest)
