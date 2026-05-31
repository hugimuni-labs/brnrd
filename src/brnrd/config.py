"""Runtime settings for the brnrd backend.

Sourced from the environment with prototype-friendly defaults
(SQLite, dev endpoints on). Production overrides ``BRNRD_*``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable settings bundle. Pass a custom one to ``create_app``."""

    database_url: str = os.environ.get("BRNRD_DATABASE_URL", "sqlite:///./brnrd.db")
    # Public base URL, used to build the pair_url the CLI prints during
    # the device-flow connect handshake.
    public_base_url: str = os.environ.get(
        "BRNRD_PUBLIC_BASE_URL", "http://localhost:8000"
    )
    # Long-poll: the inbox GET blocks up to this many seconds waiting
    # for a queued event before returning empty. A client may request
    # less via ``?wait=`` but never more than this cap.
    inbox_long_poll_max_s: float = _env_float("BRNRD_INBOX_LONGPOLL_MAX_S", 25.0)
    inbox_poll_interval_s: float = _env_float("BRNRD_INBOX_POLL_INTERVAL_S", 0.5)
    # Pair requests (device-flow connect codes) expire after this long.
    pair_ttl_s: int = _env_int("BRNRD_PAIR_TTL_S", 600)
    # The dev enqueue ingress stands in for real platform webhooks.
    # Off in production; on by default for the prototype.
    enable_dev_endpoints: bool = os.environ.get("BRNRD_ENABLE_DEV", "1") != "0"


def get_settings() -> Settings:
    return Settings()
