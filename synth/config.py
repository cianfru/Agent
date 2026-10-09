"""Runtime settings, all from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


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

    # Service.
    db_path: str = field(default_factory=lambda: _env("SYNTH_DB", "synth.db"))
    poll_seconds: float = field(default_factory=lambda: _float("SYNTH_POLL_SECONDS", 300))
    brief_hour_utc: int = field(default_factory=lambda: int(_float("SYNTH_BRIEF_HOUR_UTC", 8)))
    api_key: str = field(default_factory=lambda: _env("SYNTH_API_KEY"))
    # Only events at or above this score are pushed/summarised as alerts (0-100).
    alert_min_score: float = field(default_factory=lambda: _float("SYNTH_ALERT_MIN_SCORE", 60))
    # Minimum USD delta change for a single wallet move to count as an event.
    wallet_move_min_usd: float = field(
        default_factory=lambda: _float("SYNTH_WALLET_MOVE_MIN_USD", 100_000)
    )

    # Optional Telegram push.
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))

    coins: list[str] = field(default_factory=lambda: _list("SYNTH_COINS"))


def load() -> Settings:
    return Settings()
