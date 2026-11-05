"""Runtime settings, all from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


def _secret(name: str) -> str:
    """A credential as pasted into Railway: spaces and one pair of surrounding quotes taken off."""
    v = _env(name)
    return v[1:-1].strip() if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" else v


def _list(name: str, default: str = "") -> list[str]:
    return [p.strip() for p in _env(name, default).split(",") if p.strip()]


@dataclass(frozen=True)
class Settings:
    # Cowboy (derive_scan) publishes static JSON to the cowboy-feed `site-data` branch.
    cowboy_base_url: str = field(
        default_factory=lambda: _env(
            "COWBOY_BASE_URL",
            "https://raw.githubusercontent.com/cianfru/cowboy-feed/site-data",
        ).rstrip("/")
    )
    # Reflex (RCCE_Scanner) backend.
    reflex_base_url: str = field(default_factory=lambda: _env("REFLEX_BASE_URL").rstrip("/"))
    reflex_access_code: str = field(default_factory=lambda: _env("REFLEX_ACCESS_CODE"))
    reflex_token: str = field(default_factory=lambda: _env("REFLEX_TOKEN"))

    # LLM. Haiku is the cheap default; override with SYNTH_MODEL.
    model: str = field(default_factory=lambda: _env("SYNTH_MODEL", "claude-haiku-5-5"))
    # The daily brief can use a stronger model (e.g. claude-opus-5-5); defaults to SYNTH_MODEL.
    brief_model: str = field(default_factory=lambda: _env("SYNTH_BRIEF_MODEL"))
    effort: str = field(default_factory=lambda: _env("SYNTH_EFFORT", "low"))
    # Talking to Outrider (synth/chat.py): the model, its effort, and the most it may spend a day in dollars.
    # Sonnet 5.5 by default (owner, 20 October 2026: "it has to be efficient"); claude-haiku-5-5 is cheaper still.
    chat_model: str = field(default_factory=lambda: _env("SYNTH_CHAT_MODEL", "claude-sonnet-5-5"))
    chat_effort: str = field(default_factory=lambda: _env("SYNTH_CHAT_EFFORT", "medium"))
    chat_daily_usd: float = field(default_factory=lambda: _float("SYNTH_CHAT_DAILY_USD", 1.0))
    # Outrider's read (synth/analyst.py): the market reasoned over by the model, sent when it is worth reading; it
    # replaces the code-built pulse and daily brief. SYNTH_READS=off brings those back.
    reads: bool = field(default_factory=lambda: _env("SYNTH_READS", "on").lower() not in ("off", "0", "no"))
    read_model: str = field(default_factory=lambda: _env("SYNTH_READ_MODEL", "claude-sonnet-5-5"))
    read_effort: str = field(default_factory=lambda: _env("SYNTH_READ_EFFORT", "medium"))
    read_daily_usd: float = field(default_factory=lambda: _float("SYNTH_READ_DAILY_USD", 0.5))
    reads_per_day: int = field(default_factory=lambda: int(_float("SYNTH_READS_PER_DAY", 3)))
    # UTC hours without reads on market changes ("22-6"); a group turning against his position still comes through.
    read_quiet_utc: str = field(default_factory=lambda: _env("SYNTH_READ_QUIET_UTC", "22-6"))
    # His own Hyperliquid wallets (comma-separated addresses), read each poll so the reads can speak to his positions.
    owner_wallets: list[str] = field(default_factory=lambda: _list("SYNTH_OWNER_WALLETS"))
    # His KuCoin futures account, read each poll beside his wallets (synth/kucoin.py): a key with the General
    # permission only. The key version is found by trying 2 then 3 unless set.
    kucoin_key: str = field(default_factory=lambda: _secret("KUCOIN_API_KEY"))
    kucoin_secret: str = field(default_factory=lambda: _secret("KUCOIN_API_SECRET"))
    kucoin_passphrase: str = field(default_factory=lambda: _secret("KUCOIN_API_PASSPHRASE"))
    kucoin_key_version: str = field(default_factory=lambda: _env("KUCOIN_API_KEY_VERSION"))
    # Outrider's posts on X (synth/persona.py): the model that writes them, the UTC hours a draft is made (empty
    # disables), and "draft" (to your Telegram, Post or Skip) or "live" (published as written).
    persona_model: str = field(default_factory=lambda: _env("SYNTH_PERSONA_MODEL", "claude-opus-5-5"))
    persona_hours_utc: list[int] = field(default_factory=lambda: [
        int(h) for h in _env("SYNTH_PERSONA_HOURS_UTC", "9,13,17,21").split(",") if h.strip()])
    persona_mode: str = field(default_factory=lambda: _env("SYNTH_PERSONA_MODE", "draft"))
    # Alerts are built in code; set SYNTH_LLM_ALERTS=1 to have the model word them instead.
    llm_alerts: bool = field(default_factory=lambda: _env("SYNTH_LLM_ALERTS") == "1")

    # Service.
    db_path: str = field(default_factory=lambda: _env("SYNTH_DB", "synth.db"))
    poll_seconds: float = field(default_factory=lambda: _float("SYNTH_POLL_SECONDS", 300))
    brief_hour_utc: int = field(default_factory=lambda: int(_float("SYNTH_BRIEF_HOUR_UTC", 8)))
    api_key: str = field(default_factory=lambda: _env("SYNTH_API_KEY"))
    # Only events at or above this score are pushed/summarised as alerts (0-100).
    alert_min_score: float = field(default_factory=lambda: _float("SYNTH_ALERT_MIN_SCORE", 60))
    # At most this many Telegram alerts per rolling 24h (strongest first); the rest stay on the dashboard.
    max_alerts_per_day: int = field(default_factory=lambda: int(_float("SYNTH_MAX_ALERTS_PER_DAY", 6)))
    # What pings Telegram besides the reads (owner, 3 November 2026: only essential and important updates):
    # conviction (a proven trader's 2x-size bet), build (several tracked traders adding to one coin), smart
    # (smart-money events and toxic flow), group (profitable traders opening together). A muted kind is still logged,
    # scored and read by Outrider; updates on a group you follow always come.
    # A hard cap on unprompted messages (everything but replies to him), over any 24 hours; past it they are kept as
    # held, not sent. The morning read and a read he asks for always go.
    max_pushes_per_day: int = field(default_factory=lambda: int(_float("SYNTH_MAX_PUSHES_PER_DAY", 6)))
    pushes: frozenset = field(default_factory=lambda: frozenset(
        p.lower() for p in _list("SYNTH_PUSH", "conviction")))
    # Outrider's X drafts (persona posts, the daily desk post, the Monday scorecard): off until wanted (owner,
    # 3 November 2026). SYNTH_X_DRAFTS=on brings them back; /draft still makes one on request.
    x_drafts: bool = field(default_factory=lambda: _env("SYNTH_X_DRAFTS").lower() in ("on", "1", "yes"))
    # Minimum USD delta change for a single wallet move to count as an event.
    wallet_move_min_usd: float = field(
        default_factory=lambda: _float("SYNTH_WALLET_MOVE_MIN_USD", 100_000)
    )

    # Derive groups need live positions; Cowboy's trader books update once a day (from its daily history),
    # so they are off until Cowboy publishes positions as they trade. 1 turns them back on.
    derive_groups: bool = field(default_factory=lambda: _env("SYNTH_DERIVE_GROUPS") == "1")
    # Reflex convergences faster than this (first to last open) alert; slower ones are logged only.
    fast_convergence_hours: float = field(default_factory=lambda: _float("SYNTH_FAST_CONVERGENCE_HOURS", 6))

    # Public desk posts (X via Orbio). "draft" stores posts and sends them to you; "live" publishes.
    post_mode: str = field(default_factory=lambda: _env("SYNTH_POST_MODE", "draft"))
    post_hour_utc: int = field(default_factory=lambda: int(_float("SYNTH_POST_HOUR_UTC", 14)))
    orbio_api_key: str = field(default_factory=lambda: _env("ORBIO_API_KEY"))
    orbio_max_cost: float = field(default_factory=lambda: _float("ORBIO_MAX_COST", 0.03))
    block0_stats_url: str = field(
        default_factory=lambda: _env(
            "BLOCK0_STATS_URL", "https://raw.githubusercontent.com/cianfru/Block0/agent-data/stats.json"
        )
    )

    # Market read to Telegram (code-built, no model) at these UTC hours; empty disables it.
    pulse_hours_utc: list[int] = field(
        default_factory=lambda: [int(h) for h in _list("SYNTH_PULSE_HOURS_UTC", "6,10,14,18")]
    )
    pulse_min_score: float = field(default_factory=lambda: _float("SYNTH_PULSE_MIN_SCORE", 40))

    # Optional Telegram push.
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))

    coins: list[str] = field(default_factory=lambda: _list("SYNTH_COINS"))


def load() -> Settings:
    return Settings()
