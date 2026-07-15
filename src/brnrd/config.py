"""Runtime settings for the brnrd backend."""

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


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _env_int_tuple(name: str) -> tuple[int, ...]:
    raw = os.environ.get(name, "")
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return tuple(out)


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get("BRNRD_DATABASE_URL", "sqlite:///./brnrd.db")
    public_base_url: str = os.environ.get("BRNRD_PUBLIC_BASE_URL", "http://localhost:8000")
    inbox_long_poll_max_s: float = _env_float("BRNRD_INBOX_LONGPOLL_MAX_S", 25.0)
    inbox_poll_interval_s: float = _env_float("BRNRD_INBOX_POLL_INTERVAL_S", 0.5)
    pair_ttl_s: int = _env_int("BRNRD_PAIR_TTL_S", 600)
    pack_relay_ttl_s: int = _env_int("BRNRD_PACK_RELAY_TTL_S", 3600)
    enable_dev_endpoints: bool = os.environ.get("BRNRD_ENABLE_DEV", "1") != "0"

    telegram_bot_token: str = os.environ.get("BRNRD_TELEGRAM_BOT_TOKEN", "")
    telegram_webhook_secret: str = os.environ.get("BRNRD_TELEGRAM_WEBHOOK_SECRET", "")
    telegram_bot_username: str = os.environ.get("BRNRD_TELEGRAM_BOT_USERNAME", "")
    telegram_auto_webhook: bool = _env_bool("BRNRD_TELEGRAM_AUTO_WEBHOOK", True)
    # #409 — default-closed Telegram authorization: the pairing sender
    # (ChannelRoute.paired_user_id) is always trusted; this allowlist adds
    # extra trusted user ids (e.g. teammates) on top of that principal.
    telegram_authz_allowlist: tuple[int, ...] = _env_int_tuple("BRNRD_TELEGRAM_AUTHZ_ALLOWLIST")

    session_cookie: str = os.environ.get("BRNRD_SESSION_COOKIE", "brnrd_session")

    github_oauth_client_id: str = _env_first("BRNRD_GITHUB_OAUTH_CLIENT_ID", "GITHUB_CLIENT_ID")
    github_oauth_client_secret: str = _env_first("BRNRD_GITHUB_OAUTH_CLIENT_SECRET", "GITHUB_CLIENT_SECRET")
    github_oauth_scope: str = os.environ.get("BRNRD_GITHUB_OAUTH_SCOPE", "user:email")
    github_oauth_authorize_url: str = os.environ.get("BRNRD_GITHUB_OAUTH_AUTHORIZE_URL", "https://github.com/login/oauth/authorize")
    github_oauth_token_url: str = os.environ.get("BRNRD_GITHUB_OAUTH_TOKEN_URL", "https://github.com/login/oauth/access_token")
    github_api_base_url: str = os.environ.get("BRNRD_GITHUB_API_BASE_URL", "https://api.github.com")
    github_api_version: str = os.environ.get("BRNRD_GITHUB_API_VERSION", "2026-03-10")

    github_app_id: str = _env_first("BRNRD_GITHUB_APP_ID", "GITHUB_APP_ID")
    github_app_private_key_b64: str = _env_first("BRNRD_GITHUB_APP_PRIVATE_KEY_B64", "GITHUB_APP_PRIVATE_KEY_B64")
    github_app_slug: str = _env_first("BRNRD_GITHUB_APP_SLUG", "GITHUB_APP_SLUG", default="brnrd-dev")
    github_install_url: str = _env_first(
        "BRNRD_GITHUB_INSTALL_URL",
        "GITHUB_INSTALL_URL",
        default="https://github.com/apps/brnrd-dev/installations/new",
    )
    github_webhook_secret: str = _env_first("BRNRD_GITHUB_WEBHOOK_SECRET", "GITHUB_WEBHOOK_SECRET")
    github_bot_login: str = os.environ.get("BRNRD_GITHUB_BOT_LOGIN", "brnrd-bot")
    github_bot_user_login: str = _env_first(
        "BRNRD_GITHUB_BOT_USER_LOGIN",
        default=os.environ.get("BRNRD_GITHUB_BOT_LOGIN", "brnrd-bot"),
    )
    github_bot_collaborator_permission: str = os.environ.get("BRNRD_GITHUB_BOT_COLLABORATOR_PERMISSION", "triage")
    github_trigger_aliases: str = os.environ.get("BRNRD_GITHUB_TRIGGER_ALIASES", "brnrd,brr")
    github_bot_token: str = os.environ.get("BRNRD_GITHUB_BOT_TOKEN", "")

    # Billing (#53, kb design-billing.md §"Launch defaults + tunable knobs").
    # Test mode until #52 (Stripe France KYB) flips the keys to live.
    stripe_api_key: str = os.environ.get("BRNRD_STRIPE_API_KEY", "")
    stripe_webhook_secret: str = os.environ.get("BRNRD_STRIPE_WEBHOOK_SECRET", "")
    stripe_api_base_url: str = os.environ.get("BRNRD_STRIPE_API_BASE_URL", "https://api.stripe.com")
    stripe_price_supporter_monthly: str = os.environ.get("BRNRD_STRIPE_PRICE_SUPPORTER_MONTHLY", "")
    stripe_price_supporter_annual: str = os.environ.get("BRNRD_STRIPE_PRICE_SUPPORTER_ANNUAL", "")
    stripe_price_public_monthly: str = os.environ.get("BRNRD_STRIPE_PRICE_PUBLIC_MONTHLY", "")
    stripe_price_public_annual: str = os.environ.get("BRNRD_STRIPE_PRICE_PUBLIC_ANNUAL", "")
    subscriber_monthly_credits: int = _env_int("BRNRD_SUBSCRIBER_MONTHLY_CREDITS", 300)
    supporter_cohort_size: int = _env_int("BRNRD_SUPPORTER_COHORT_SIZE", 200)
    # Optional hard cutoff (ISO date) for the supporter cohort — the
    # "12 months from public launch, whichever comes first" clause. Empty =
    # cohort closes on count alone.
    supporter_cohort_deadline: str = os.environ.get("BRNRD_SUPPORTER_COHORT_DEADLINE", "")
    topup_min_usd: int = _env_int("BRNRD_TOPUP_MIN_USD", 5)
    topup_max_usd: int = _env_int("BRNRD_TOPUP_MAX_USD", 500)

    oauth_state_cookie: str = os.environ.get("BRNRD_OAUTH_STATE_COOKIE", "brnrd_oauth_state")
    oauth_pkce_cookie: str = os.environ.get("BRNRD_OAUTH_PKCE_COOKIE", "brnrd_oauth_pkce")
    oauth_next_cookie: str = os.environ.get("BRNRD_OAUTH_NEXT_COOKIE", "brnrd_oauth_next")
    oauth_state_ttl_s: int = _env_int("BRNRD_OAUTH_STATE_TTL_S", 600)


def get_settings() -> Settings:
    return Settings()
