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
    # Relayed diffense review packs live in RAM behind a capability token
    # for this long, then drop. Never persisted (see pack_relay.py).
    pack_relay_ttl_s: int = _env_int("BRNRD_PACK_RELAY_TTL_S", 3600)
    # The dev enqueue ingress stands in for real platform webhooks.
    # Off in production; on by default for the prototype.
    enable_dev_endpoints: bool = os.environ.get("BRNRD_ENABLE_DEV", "1") != "0"
    # Telegram: a single managed bot, multiplexed by chat_id. The
    # webhook is authenticated by the secret-token header Telegram
    # echoes back from setWebhook (not a bearer).
    telegram_bot_token: str = os.environ.get("BRNRD_TELEGRAM_BOT_TOKEN", "")
    telegram_webhook_secret: str = os.environ.get("BRNRD_TELEGRAM_WEBHOOK_SECRET", "")
    # Bot @username (without the leading @), used to build t.me deep-links
    # so a user can tap to open the bot with the pair code prefilled
    # instead of copy-pasting ``/start <code>``. Empty → no deep-link.
    telegram_bot_username: str = os.environ.get("BRNRD_TELEGRAM_BOT_USERNAME", "")
    # Web dashboard session cookie name.
    session_cookie: str = os.environ.get("BRNRD_SESSION_COOKIE", "brnrd_session")
    # GitHub OAuth / GitHub App user-authorization settings. The same
    # managed GitHub App that receives webhooks can serve as brnrd's
    # identity provider; self-hosters can point these at their own app.
    github_oauth_client_id: str = os.environ.get("BRNRD_GITHUB_OAUTH_CLIENT_ID", "")
    github_oauth_client_secret: str = os.environ.get(
        "BRNRD_GITHUB_OAUTH_CLIENT_SECRET", ""
    )
    # Minimal scope: identity (`/user` id + login) needs none, but the
    # verified-email endpoint requires `user:email`. GitHub Apps ignore
    # scope and gate email via the app's "Email addresses" permission,
    # where this param is harmless. Self-hosters can override or clear it.
    github_oauth_scope: str = os.environ.get(
        "BRNRD_GITHUB_OAUTH_SCOPE", "user:email"
    )
    github_oauth_authorize_url: str = os.environ.get(
        "BRNRD_GITHUB_OAUTH_AUTHORIZE_URL",
        "https://github.com/login/oauth/authorize",
    )
    github_oauth_token_url: str = os.environ.get(
        "BRNRD_GITHUB_OAUTH_TOKEN_URL",
        "https://github.com/login/oauth/access_token",
    )
    github_api_base_url: str = os.environ.get(
        "BRNRD_GITHUB_API_BASE_URL", "https://api.github.com"
    )
    github_api_version: str = os.environ.get(
        "BRNRD_GITHUB_API_VERSION", "2026-03-10"
    )
    # Managed GitHub App webhook / posting settings. ``github_bot_token``
    # is the narrow transport seam for this prototype slice; production
    # replaces it with an installation token minted from the App private key.
    github_webhook_secret: str = os.environ.get("BRNRD_GITHUB_WEBHOOK_SECRET", "")
    github_bot_login: str = os.environ.get("BRNRD_GITHUB_BOT_LOGIN", "brr-bot")
    github_bot_token: str = os.environ.get("BRNRD_GITHUB_BOT_TOKEN", "")
    oauth_state_cookie: str = os.environ.get(
        "BRNRD_OAUTH_STATE_COOKIE", "brnrd_oauth_state"
    )
    oauth_pkce_cookie: str = os.environ.get(
        "BRNRD_OAUTH_PKCE_COOKIE", "brnrd_oauth_pkce"
    )
    oauth_next_cookie: str = os.environ.get(
        "BRNRD_OAUTH_NEXT_COOKIE", "brnrd_oauth_next"
    )
    oauth_state_ttl_s: int = _env_int("BRNRD_OAUTH_STATE_TTL_S", 600)


def get_settings() -> Settings:
    return Settings()
