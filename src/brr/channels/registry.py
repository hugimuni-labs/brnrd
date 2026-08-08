"""The channel registry — one table of per-channel identity facts.

Rung 1 of the limbs build ladder (``design-the-limbs-and-the-face.md``):
facts that were previously restated as ``if source == ...`` chains become
rows. A new channel joins by adding a row; consumers iterate the table and
never name a member — the class is defined by a structural property, not
by whoever remembered to extend an if-chain.

Two rule families live here today:

- ``ThreadRule`` — how an event's frontmatter yields the gate-thread key
  ("which thread should receive the reply").
- ``IdentityRule`` — how it yields the correspondent key ("who is
  talking"), which deliberately sits *above* thread keys so a native gate
  and its cloud-relayed twin resolve to one person without merging their
  delivery channels.

The relay ("cloud") is a carrier, not a platform: its thread rule carries
the origin platform as a key prefix, and its identity resolution defers to
``CLOUD_IDENTITY`` per origin platform, with ``CLOUD_GENERIC_FIELDS`` as
the any-platform fallback (whatsapp rides this today).

The rendering engine lives in ``brr.conversations``; this module owns the
*members*. Guard tests derive the member list from here — never from a
literal restated in a test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Part:
    """One colon-joined component of a thread key."""

    fields: tuple[str, ...]  # first present, non-empty value wins
    required: bool = True  # a missing required part voids the key
    as_int: bool = False  # accept int or digit-string, render as int


@dataclass(frozen=True)
class ThreadRule:
    """How a channel's event meta becomes its gate-thread key."""

    channel: str  # leading key component == the event `source`
    parts: tuple[Part, ...]
    fallback: str | None = None  # key when a required part is missing


@dataclass(frozen=True)
class IdentityField:
    """One candidate frontmatter field for the correspondent identity."""

    field: str
    label: str  # rendered kind component: "user-id" | "username" | "user" | "login"
    fold: bool = True  # case-fold the value component


@dataclass(frozen=True)
class IdentityRule:
    """How a channel's event meta becomes its correspondent key."""

    platform: str  # rendered platform component
    fields: tuple[IdentityField, ...]  # priority order; first present wins


# ── Thread rules ─────────────────────────────────────────────────────

THREAD_RULES: dict[str, ThreadRule] = {
    "telegram": ThreadRule(
        channel="telegram",
        parts=(
            Part(("telegram_chat_id",)),
            Part(("telegram_topic_id",), required=False),
        ),
    ),
    "slack": ThreadRule(
        channel="slack",
        parts=(
            Part(("slack_channel",)),
            Part(("slack_thread_ts", "slack_ts"), required=False),
        ),
    ),
    "github": ThreadRule(
        channel="github",
        parts=(
            Part(("github_repo",)),
            Part(("github_issue_number",), as_int=True),
        ),
    ),
    "signal": ThreadRule(
        # Previously unlisted: every signal DM fell to the shared
        # "signal:default" thread — one paired sender masked it, a second
        # allowlisted sender would have collapsed into the same
        # conversation. The gate has shipped dark (no on-disk threads), so
        # listing it is a fix with no migration.
        channel="signal",
        parts=(
            Part(("signal_sender",)),
            Part((), required=False),  # no topic concept; keep the 3-part shape
        ),
        fallback="signal:default",
    ),
    "cloud": ThreadRule(
        channel="cloud",
        parts=(
            Part(("cloud_platform",)),
            Part(("cloud_chat_id",)),
            Part(("cloud_topic_id",), required=False),
        ),
        fallback="cloud:default",
    ),
}


# ── Identity rules ───────────────────────────────────────────────────

IDENTITY_RULES: dict[str, IdentityRule] = {
    "telegram": IdentityRule(
        platform="telegram",
        fields=(
            IdentityField("telegram_user_id", "user-id", fold=False),
            IdentityField("telegram_username", "username"),
            IdentityField("telegram_user", "user"),
        ),
    ),
    "slack": IdentityRule(
        platform="slack",
        fields=(IdentityField("slack_user", "user"),),
    ),
    "github": IdentityRule(
        platform="github",
        fields=(IdentityField("github_author", "login"),),
    ),
    "signal": IdentityRule(
        # New with the registry: a Signal sender (E.164 number) is an
        # identity like any other; previously the chain just fell through
        # to None.
        platform="signal",
        fields=(IdentityField("signal_sender", "user"),),
    ),
}

# The relay's per-origin-platform identity resolution. An origin platform
# not listed here resolves through CLOUD_GENERIC_FIELDS with the folded
# platform name itself (whatsapp's current path).
CLOUD_IDENTITY: dict[str, IdentityRule] = {
    "telegram": IdentityRule(
        platform="telegram",
        fields=(
            IdentityField("cloud_user_id", "user-id", fold=False),
            IdentityField("cloud_username", "username"),
            IdentityField("cloud_user", "user"),
        ),
    ),
    "github": IdentityRule(
        platform="github",
        fields=(
            IdentityField("github_author", "login"),
            IdentityField("cloud_user", "login"),
        ),
    ),
}

CLOUD_GENERIC_FIELDS: tuple[IdentityField, ...] = (
    IdentityField("cloud_user_id", "user"),
    IdentityField("cloud_username", "user"),
    IdentityField("cloud_user", "user"),
)
