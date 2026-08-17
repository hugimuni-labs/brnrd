"""The messenger-door registry (#1465) — one owning place, per connector:
can it mint a deep link, from what derived config, what fallback shape.
`GET /v1/dashboard/repos` renders the answer as `messenger_doors`;
`telegram_bot_username` stays on the wire one release for the just-shipped
(#1457) consumer, deprecated in its own comment (`routers/dashboard.py`).

**No user-facing copy owned by the registry rows themselves** — same
house rule `capabilities.py` states for its own catalog: the renderer
(frontend, this module's own `pair_instructions`) owns every sentence,
`MessengerDoor` is a flat capability flag.

Telegram and WhatsApp identities are derived from the token/credentials
already configured (Telegram's own `getMe`, the WhatsApp Cloud API's own
phone-number lookup) rather than a second hand-typed env var — the
#1242 -> #1463 lesson: a config knob duplicating a fact the token already
carries is fixed once and drifts forever. Derivation is attempted exactly
once, at app startup (`app.py`'s lifespan, next to the existing Telegram
webhook registration — same seam, same failure posture: short timeout,
warn and fall back on failure, never block boot), and the result is
cached on `app.state.messenger_identities`.

**This module never makes a network call from inside a request.** Every
`derive_*` function below is a startup-only call — `app.py`'s lifespan is
the only caller. A request that cannot see a populated
`app.state.messenger_identities` (chiefly: this test suite's bare
`TestClient(app)`, which never runs the ASGI lifespan at all — see
`test_startup_registers_hosted_telegram_webhook` for the proof) reads
`env_only_identities` instead: whatever is already hand-configured,
shape-checked, zero network. Getting this wrong once already cost a
flaky/slow test suite; the split is deliberate and structural, not a
convention to remember.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from .config import Settings, telegram_username_is_valid

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MessengerIdentities:
    """Ground truth for deep-link construction — either startup-derived
    (`derive_messenger_identities`) or the env-only fallback
    (`env_only_identities`) a request reads when startup hasn't run."""

    telegram_bot_username: str = ""
    whatsapp_e164: str = ""


@dataclass(frozen=True)
class MessengerDoor:
    """One registry row — `GET /v1/dashboard/repos`'s `messenger_doors`
    entry, verbatim (`to_wire`)."""

    platform: str
    deep_link_available: bool

    def to_wire(self) -> dict:
        return {"platform": self.platform, "deep_link_available": self.deep_link_available}


def env_only_identities(settings: Settings) -> MessengerIdentities:
    """The pre-derivation fallback: whatever is already hand-configured,
    shape-checked, no network — what a request sees whenever startup's
    derivation hasn't populated `app.state` (see module docstring)."""
    username = settings.telegram_bot_username.lstrip("@")
    return MessengerIdentities(
        telegram_bot_username=username if telegram_username_is_valid(username) else "",
        # No hand-typed WhatsApp number env var exists to fall back to —
        # that omission is #1465's own point (#1242 -> #1463's lesson,
        # applied from the start instead of retrofitted): the Cloud API
        # phone lookup is the *only* source, so with no derivation run yet
        # there is nothing here but empty.
        whatsapp_e164="",
    )


_DIGITS_RE = re.compile(r"\d")


def _digits_only(raw: str) -> str:
    """`wa.me/<E.164>` wants bare digits, no `+` — Meta's own
    `display_phone_number` comes back formatted (`"+1 555-123-4567"`)."""
    return "".join(_DIGITS_RE.findall(raw))


def derive_telegram_bot_username(settings: Settings, *, timeout: float = 5.0) -> str:
    """`getMe`-derived wins when reachable — ground truth; a shape-valid
    env value is the fallback when `getMe` is unreachable, unset, or the
    token is empty. `""` only when neither source yields a valid shape
    (#1242's invariant, unchanged). **Network call — startup only** (see
    module docstring)."""
    fallback = env_only_identities(settings).telegram_bot_username
    if not settings.telegram_bot_token:
        return fallback
    from .platforms import telegram

    try:
        derived = telegram.fetch_bot_username(settings.telegram_bot_token, timeout=timeout)
    except Exception:  # noqa: BLE001 - startup must never crash on this
        derived = None
    if not derived or not telegram_username_is_valid(derived):
        if not derived:
            logger.warning(
                "telegram getMe did not return a usable bot username — "
                "falling back to BRNRD_TELEGRAM_BOT_USERNAME"
            )
        return fallback
    if fallback and derived != fallback:
        logger.warning(
            "telegram getMe reports username %r, disagreeing with "
            "BRNRD_TELEGRAM_BOT_USERNAME=%r — the getMe reading wins",
            derived,
            settings.telegram_bot_username,
        )
    return derived


def derive_whatsapp_number(settings: Settings, *, timeout: float = 5.0) -> str:
    """The WhatsApp Cloud API's own phone-number lookup — no env var to
    duplicate (#1465, the #1242 lesson applied up front): the number lives
    on Meta's side, keyed by `whatsapp_phone_number_id`, which config
    already carries. `""` when unconfigured or the lookup fails; unlike
    Telegram there is no second source to fall back to. **Network call —
    startup only** (see module docstring)."""
    if not (settings.whatsapp_access_token and settings.whatsapp_phone_number_id):
        return ""
    from .platforms import whatsapp

    try:
        raw = whatsapp.fetch_display_phone_number(
            settings.whatsapp_api_base_url,
            settings.whatsapp_api_version,
            settings.whatsapp_phone_number_id,
            settings.whatsapp_access_token,
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 - startup must never crash on this
        raw = None
    if not raw:
        logger.warning("whatsapp phone-number lookup failed — no WhatsApp deep-link door until this succeeds")
        return ""
    digits = _digits_only(raw)
    if not digits:
        logger.warning("whatsapp phone-number lookup returned an unusable value %r", raw)
        return ""
    return digits


def derive_messenger_identities(settings: Settings, *, timeout: float = 5.0) -> MessengerIdentities:
    """Called once, at startup (`app.py`'s lifespan). **Never call this
    from inside a request handler** — see module docstring."""
    return MessengerIdentities(
        telegram_bot_username=derive_telegram_bot_username(settings, timeout=timeout),
        whatsapp_e164=derive_whatsapp_number(settings, timeout=timeout),
    )


# ---------------------------------------------------------------------------
# The registry — the connector set itself. #1465's own discipline: a caller
# that wants "every connector" asks `PLATFORMS` / `messenger_doors`, never
# hand-copies a list — see `test_messenger_doors.py`'s registry test.
# ---------------------------------------------------------------------------


def _telegram_deep_link(identity_value: str, code: str) -> str:
    return f"https://t.me/{identity_value}?start={code}"


def _whatsapp_deep_link(identity_value: str, code: str) -> str:
    return f"https://wa.me/{identity_value}?text={code}"


@dataclass(frozen=True)
class _DoorDef:
    platform: str
    # `None` for a platform with no mint lane at all (Slack, Signal): the
    # entry still exists so the connector set stays complete (#1465) — the
    # UI honestly renders "no deep-link door" instead of the platform
    # vanishing from the set entirely.
    identity_attr: str | None
    mint: Callable[[str, str], str] | None = None


_REGISTRY: tuple[_DoorDef, ...] = (
    _DoorDef("telegram", "telegram_bot_username", _telegram_deep_link),
    _DoorDef("whatsapp", "whatsapp_e164", _whatsapp_deep_link),
    # Slack's door is OAuth-install, not a deep link; Signal has no
    # equivalent lane at all. Both declared `deep_link_available=False`
    # rather than omitted — the maintainer's brief: "the set complete, the
    # UI honest."
    _DoorDef("slack", None),
    _DoorDef("signal", None),
)

PLATFORMS: tuple[str, ...] = tuple(d.platform for d in _REGISTRY)


def messenger_doors(identities: MessengerIdentities) -> list[MessengerDoor]:
    """Every connector, in registry order — the wire array
    `GET /v1/dashboard/repos` ships as `messenger_doors`."""
    doors = []
    for d in _REGISTRY:
        value = getattr(identities, d.identity_attr) if d.identity_attr else ""
        doors.append(MessengerDoor(d.platform, bool(value)))
    return doors


def mint_deep_link(platform: str, identities: MessengerIdentities, code: str) -> str | None:
    """`None` when the platform is unknown, has no mint lane, or its
    identity isn't available right now — the caller's job to render the
    honest fallback, never this module's (no user-facing copy here)."""
    for d in _REGISTRY:
        if d.platform != platform:
            continue
        if d.identity_attr is None or d.mint is None:
            return None
        value = getattr(identities, d.identity_attr)
        return d.mint(value, code) if value else None
    return None


def pair_instructions(platform: str, code: str, deep_link: str | None) -> str:
    """Account-level pairing instructions — mirrors `routers/pairing.py`'s
    `_telegram_pair_response` phrasing for Telegram, new for WhatsApp.
    Repo-agnostic on purpose: the one caller of this
    (`POST /v1/dashboard/pair`) mints account-level codes only, same as
    `dashboard_telegram_pair_api` already does.

    Raises `ValueError` for a platform with no instructions to give
    (unknown, or no mint lane) — callers check `deep_link_available`
    before reaching here, so this is a programming error, not a user path.
    """
    if platform == "telegram":
        if deep_link:
            return (
                f"Open {deep_link}, then press Start if Telegram prompts. "
                f"If Telegram only opens the chat, send `/start {code}` to "
                "bind this chat to your account."
            )
        return f"Send `/start {code}` to your brnrd Telegram bot to bind this chat to your account."
    if platform == "whatsapp":
        if deep_link:
            return (
                f"Open {deep_link} and send the pre-filled message. If it opens empty, "
                f"text `{code}` by itself — no other words — to bind this chat to your account."
            )
        return (
            f"Text `{code}` by itself — no other words — to your brnrd WhatsApp number "
            "to bind this chat to your account."
        )
    raise ValueError(f"no pairing instructions for platform {platform!r}")
