"""Runtime settings for the brnrd backend."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Telegram bot usernames: `[A-Za-z0-9_]{5,32}` — no hyphens, no other
# punctuation (https://core.telegram.org/method/account.checkUsername).
# `BRNRD_TELEGRAM_BOT_USERNAME=brnrd-bot` (the GitHub bot login spelling,
# hyphenated) fails this shape; `t.me/brnrd-bot` resolves to no Telegram
# entity, so a deep link built on it is a link to nowhere (#1242).
_TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def telegram_username_is_valid(username: str) -> bool:
    """Shape-check a Telegram bot username, leading ``@`` already stripped.

    Empty is deliberately *not* "invalid" here — an unset username is the
    legitimate "Telegram integration disabled" state, distinct from a
    configured-but-malformed one. Callers that care about the difference
    check emptiness themselves.
    """
    return bool(_TELEGRAM_USERNAME_RE.match(username))


# #1463 — the token defines the bot, and Telegram's own `getMe` returns its
# username, so a hand-typed `BRNRD_TELEGRAM_BOT_USERNAME` is at best a
# duplicate of a fact the token already carries and at worst a stale one
# (#1242: the ops act to fix a bad env value never happened, and the same
# class of bug resurfaced nine days later against a different consumer).
# `app._maybe_derive_telegram_bot_username` populates this once at startup
# (short-timeout, side-effect-free `getMe` call, same posture as
# `_maybe_register_telegram_webhook` beside it); keyed by token so a token
# rotation can't serve a stale derived username, and so the module-level
# cache stays a process-lifetime memo rather than something a per-request
# read has to fetch itself.
_derived_telegram_usernames: dict[str, str] = {}


def record_telegram_derived_bot_username(token: str, username: str) -> None:
    """Cache a `getMe`-derived bot username, keyed by the token that produced it."""
    _derived_telegram_usernames[token] = username


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

def _env_csv_lower(name: str) -> tuple[str, ...]:
    return tuple(p.strip().lower() for p in os.environ.get(name, "").split(",") if p.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get("BRNRD_DATABASE_URL", "sqlite:///./brnrd.db")
    public_base_url: str = os.environ.get("BRNRD_PUBLIC_BASE_URL", "http://localhost:8000")
    inbox_long_poll_max_s: float = _env_float("BRNRD_INBOX_LONGPOLL_MAX_S", 25.0)
    inbox_poll_interval_s: float = _env_float("BRNRD_INBOX_POLL_INTERVAL_S", 0.5)
    # Ceiling on one inbox poll's response. Until 2026-08-14 there was none,
    # and a fresh account home polling `since = 0` was handed 1,226 events in
    # a single body. A page bounds the blast radius of any cursor fault: the
    # cursor advances per page, so the backlog still drains, one poll at a
    # time, and a daemon that dies mid-drain resumes instead of restarting.
    inbox_page_limit: int = _env_int("BRNRD_INBOX_PAGE_LIMIT", 200)
    # brnrd#1388 — a queued row past this age closes `expired` on the
    # server's own clock rather than waiting on a daemon `note:` that may
    # never come; see `inbox.gc_events` / `RESPONSE_STATUS_EXPIRED`. Same
    # 48h default as the daemon-side ingestion horizon (`daemon.py`'s
    # `dispatch.stale_event_horizon_hours`) — the two enforce the same
    # "resend to rehandle" invariant from opposite ends of the wire and are
    # tuned independently on purpose (a daemon that changed its own horizon
    # should not silently move the floor every other daemon on the account
    # relies on).
    inbox_stale_event_horizon_hours: float = _env_float(
        "BRNRD_INBOX_STALE_EVENT_HORIZON_HOURS", 48.0,
    )
    pair_ttl_s: int = _env_int("BRNRD_PAIR_TTL_S", 600)
    # The messenger-door mint's own, shorter TTL (brr/every-door-on-the-page):
    # a `TgPairCode` is a bearer link rendered on a page and tapped from a
    # phone picked up mid-read, not a CLI polling loop — `pair_ttl_s` above
    # is the device-connect flow's number and stays untouched. 3 minutes
    # (not the 30s first floated) survives the realistic "read on a laptop,
    # walk to the phone, unlock, find the app" path with a visible countdown
    # and one-tap remint covering the rest; still short enough that a leaked
    # link is dead well before anyone finds it in a screenshot or a log line.
    messenger_pair_ttl_s: int = _env_int("BRNRD_MESSENGER_PAIR_TTL_S", 180)
    pack_relay_ttl_s: int = _env_int("BRNRD_PACK_RELAY_TTL_S", 3600)
    enable_dev_endpoints: bool = os.environ.get("BRNRD_ENABLE_DEV", "1") != "0"
    # #847 — where the built SvelteKit SPA lives. Empty means "the source
    # checkout layout" (src/frontend/build, resolved relative to this
    # package); a container image that puts the build elsewhere names it here.
    # No directory at all is a legal backend-only deployment.
    frontend_dir: str = os.environ.get("BRNRD_FRONTEND_DIR", "")

    # `Strict-Transport-Security: max-age=<n>` on HTTPS responses; `0` disables
    # the header for an operator whose own edge already sets it. Default one
    # year. See `security_headers.py` for why the app owns this rather than the
    # host's route table.
    hsts_max_age: int = _env_int("BRNRD_HSTS_MAX_AGE", 31536000)

    telegram_bot_token: str = os.environ.get("BRNRD_TELEGRAM_BOT_TOKEN", "")
    telegram_webhook_secret: str = os.environ.get("BRNRD_TELEGRAM_WEBHOOK_SECRET", "")
    telegram_bot_username: str = os.environ.get("BRNRD_TELEGRAM_BOT_USERNAME", "")
    telegram_auto_webhook: bool = _env_bool("BRNRD_TELEGRAM_AUTO_WEBHOOK", True)
    # #409 — default-closed Telegram authorization: the pairing sender
    # (ChannelRoute.paired_user_id) is always trusted; this allowlist adds
    # extra trusted user ids (e.g. teammates) on top of that principal.
    telegram_authz_allowlist: tuple[int, ...] = _env_int_tuple("BRNRD_TELEGRAM_AUTHZ_ALLOWLIST")
    # w-52 pre-alpha teams — the room-membership grant: when enabled, any
    # identifiable sender in a *paired group/supergroup* chat is authorized
    # (the room's admins control membership, so the room is the grant).
    # Default-closed like everything else on this gate (#409): private
    # chats stay principal+allowlist-only regardless of this flag, and a
    # deployment that never opts in behaves exactly as before.
    telegram_open_rooms: bool = _env_bool("BRNRD_TELEGRAM_OPEN_ROOMS", False)
    # #525 — per-file size cap for the attachment read-through proxy
    # (GET /v1/daemons/events/{id}/attachments/{i}); bytes stream through
    # memory only, so the cap bounds transient buffering, not storage.
    telegram_media_max_mb: int = _env_int("BRNRD_TELEGRAM_MEDIA_MAX_MB", 10)

    # WhatsApp (Meta Business Cloud API) — the second cloud lane after
    # Telegram. ``whatsapp_access_token`` is the permanent/system-user
    # bearer token for the configured phone number; ``whatsapp_verify_token``
    # is the arbitrary string Meta echoes back on the GET hub-challenge
    # subscription handshake (never a secret in transit — it rides a query
    # string — so it only proves the *subscriber* configured it, not that
    # the request came from Meta); ``whatsapp_app_secret`` is what actually
    # authenticates each POST via ``X-Hub-Signature-256`` (the same scheme
    # GitHub's webhook HMAC uses, see ``_hub_signature_ok``).
    whatsapp_access_token: str = os.environ.get("BRNRD_WHATSAPP_ACCESS_TOKEN", "")
    whatsapp_phone_number_id: str = os.environ.get("BRNRD_WHATSAPP_PHONE_NUMBER_ID", "")
    whatsapp_verify_token: str = os.environ.get("BRNRD_WHATSAPP_VERIFY_TOKEN", "")
    whatsapp_app_secret: str = os.environ.get("BRNRD_WHATSAPP_APP_SECRET", "")
    whatsapp_api_base_url: str = os.environ.get("BRNRD_WHATSAPP_API_BASE_URL", "https://graph.facebook.com")
    whatsapp_api_version: str = os.environ.get("BRNRD_WHATSAPP_API_VERSION", "v22.0")

    session_cookie: str = os.environ.get("BRNRD_SESSION_COOKIE", "brnrd_session")

    github_oauth_client_id: str = _env_first("BRNRD_GITHUB_OAUTH_CLIENT_ID", "GITHUB_CLIENT_ID")
    github_oauth_client_secret: str = _env_first("BRNRD_GITHUB_OAUTH_CLIENT_SECRET", "GITHUB_CLIENT_SECRET")
    # w-57 (2026-08-16): dropped `user:email` — brnrd no longer collects a
    # login email at all (see oauth.py, kb decision). Empty means "no scope
    # requested", which is enough: `/user` already returns the token
    # owner's own id/login with no scope, and nothing here reads email
    # anymore.
    github_oauth_scope: str = os.environ.get("BRNRD_GITHUB_OAUTH_SCOPE", "")
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
    github_trigger_label: str = os.environ.get(
        "BRNRD_GITHUB_TRIGGER_LABEL", "brnrd"
    )
    github_trigger_aliases: str = os.environ.get("BRNRD_GITHUB_TRIGGER_ALIASES", "brnrd,brr")
    github_bot_token: str = os.environ.get("BRNRD_GITHUB_BOT_TOKEN", "")
    # #408 — default-closed authorization gate: logins here bypass the
    # author_association check (OWNER/MEMBER/COLLABORATOR) for the
    # managed GitHub webhook. Comma-split, lowercased.
    github_authz_allowlist: tuple[str, ...] = _env_csv_lower("BRNRD_GITHUB_AUTHZ_ALLOWLIST")

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

    # Free-tier headroom limits + abuse ceilings (#501 repo-cap half; account
    # decision ledger 2026-07-21). ``limit_free_*`` rows bind only accounts
    # whose ``billing.entitlements`` has no live subscription — the supporter
    # tier's one entitlement is lifting exactly these. ``limit_abuse_*`` and
    # ``limit_max_*`` rows bind every tier and sit far above real use:
    # protection, not product. Enforcement lives in ``limits.py``; nothing
    # outside this block carries a limit numeral.
    limit_free_repos: int = _env_int("BRNRD_LIMIT_FREE_REPOS", 1)
    limit_free_events_per_minute: int = _env_int("BRNRD_LIMIT_FREE_EVENTS_PER_MINUTE", 6)
    limit_free_events_per_day: int = _env_int("BRNRD_LIMIT_FREE_EVENTS_PER_DAY", 200)
    limit_abuse_repos: int = _env_int("BRNRD_LIMIT_ABUSE_REPOS", 100)
    limit_abuse_events_per_minute: int = _env_int("BRNRD_LIMIT_ABUSE_EVENTS_PER_MINUTE", 60)
    limit_abuse_events_per_day: int = _env_int("BRNRD_LIMIT_ABUSE_EVENTS_PER_DAY", 5000)
    limit_max_event_body_bytes: int = _env_int("BRNRD_LIMIT_MAX_EVENT_BODY_BYTES", 100_000)
    limit_max_event_attachments: int = _env_int("BRNRD_LIMIT_MAX_EVENT_ATTACHMENTS", 10)

    oauth_state_cookie: str = os.environ.get("BRNRD_OAUTH_STATE_COOKIE", "brnrd_oauth_state")
    oauth_pkce_cookie: str = os.environ.get("BRNRD_OAUTH_PKCE_COOKIE", "brnrd_oauth_pkce")
    oauth_next_cookie: str = os.environ.get("BRNRD_OAUTH_NEXT_COOKIE", "brnrd_oauth_next")
    oauth_state_ttl_s: int = _env_int("BRNRD_OAUTH_STATE_TTL_S", 600)

    def __post_init__(self) -> None:
        # #1242 — loud at construction (app startup calls `get_settings()`
        # exactly once; every `Settings(...)` call in tests re-checks its
        # own fixture value) rather than only at the pairing mint, so a
        # misconfigured deploy is visible in the boot log before the first
        # user ever hits the broken deep link.
        # This runs at Settings() construction, before app startup has had a
        # chance to derive a username from the token (#1463) — every real
        # boot constructs Settings exactly once, then derives afterwards, so
        # there is nothing yet to prefer over the raw env read here. The
        # warning text below reflects that: an invalid env value is now only
        # ever a *fallback* problem, not necessarily a fatal one.
        username = self.telegram_bot_username.lstrip("@")
        if username and not telegram_username_is_valid(username):
            logger.warning(
                "BRNRD_TELEGRAM_BOT_USERNAME=%r is not a valid Telegram "
                "username (must match [A-Za-z0-9_]{5,32}, no hyphens) — "
                "unusable as a fallback. A deep link still mints if the bot "
                "token's own getMe call resolves a username at startup; "
                "otherwise pairing falls back to manual /start instructions.",
                self.telegram_bot_username,
            )


def get_settings() -> Settings:
    return Settings()


def telegram_effective_bot_username(settings: Settings) -> str:
    """The bot's username: token-derived (`getMe`) when available, env as fallback.

    `getMe` is ground truth — the token defines the bot — so a cached
    derived value always wins over `BRNRD_TELEGRAM_BOT_USERNAME` once one
    exists. Falls back to a shape-valid env value when nothing has been
    derived yet (token unset, or the startup `getMe` call hasn't run or
    failed), and to `""` when neither source yields a usable shape (#1463).
    Every consumer reads this instead of `settings.telegram_bot_username`
    directly.
    """
    derived = _derived_telegram_usernames.get(settings.telegram_bot_token, "")
    if derived:
        return derived
    env_username = settings.telegram_bot_username.lstrip("@")
    return env_username if telegram_username_is_valid(env_username) else ""
