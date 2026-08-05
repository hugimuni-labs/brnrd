"""The shells-and-doors support matrix — one place both landings answer from.

brnrd.dev's own landing (``Landing.svelte``) and the docs site
(``docs/src/content/docs/index.md``) each render a "what does brnrd
support" shelf. #1070 shipped the docs shelf with hand-typed ``soon`` tags
justified by a one-time grep of ``src/brr/gates/`` — and it went stale in a
single day (#1072 landed the Signal gate, #1074 the WhatsApp cloud lane,
neither commit touched the shelf). A hand-typed claim with no test behind
it rots the moment the code it described moves.

This module is the fix: **a status is only as true as the last time
someone checked it**, so make the check itself the source instead of a
memory of having done it once.

Two different questions, both answered from the same six-door roster:

- **"Can I get this running if I self-host?"** — :func:`shipped_status`.
  True the moment the gate's code exists on ``main``, because every
  self-hosted gate needs *some* operator setup (a bot token, a linked
  device, a PAT) regardless of live/soon — that per-operator cost is not
  what the tag is answering. This is the criterion #1070 already used by
  hand for the docs page; :data:`DOORS` plus this function just make it
  mechanical instead of a one-time grep.
- **"Is brnrd.dev's own hosted convenience layer wired up today?"** —
  :func:`hosted_status`. A door only has a hosted axis when brnrd.dev's
  backend actually mediates it (Telegram's managed bot, the WhatsApp cloud
  lane, the managed GitHub App) — those read live ``Settings`` fields.
  Slack and Signal never touch brnrd.dev's backend at all (self-hosted
  gates, full stop — see their own module docstrings), so they have no
  ``cloud_settings``; the web dashboard *is* brnrd.dev, so it is
  unconditionally live (``is_brnrd_dev=True``). This is deliberately not
  one global switch: the maintainer's "future things which we enable as I
  finalise the set up and testing" is true of brnrd.dev's own WhatsApp
  deployment specifically (Meta business verification, not yet done as of
  this writing) and does not make WhatsApp any less real for someone who
  runs their own backend with their own Meta credentials — the docs page
  is talking to that person, the app landing is talking to a brnrd.dev
  visitor, and they get different, both-honest answers to the same door.

**A third state, ``"ready"``, for "shipped but nobody has vouched for an
identity yet".** #1072 and #1074 shipped Signal and WhatsApp's gate code a
day after the docs shelf was hand-tagged ``soon`` for both — and the fix
for *that* staleness (making ``shipped_status`` mechanical) quietly
created a second, opposite failure: the instant a door's code lands,
``hosted_status`` used to jump straight to ``"live"`` for Slack and Signal
(no ``cloud_settings`` to fail) while still correctly saying ``"soon"``
for a shipped-but-unconfigured WhatsApp — two different reasons for "no
confirmed identity on brnrd.dev today", rendered as two different, both
wrong, answers (one over-promising, one indistinguishable from
"not shipped"). A reader who sees a live Signal tile reasonably assumes
there is a Signal address to message; there is not one, and getting one is
on the maintainer, not the code. ``"ready"`` names that state once,
uniformly, for both causes: code shipped, no confirmed brnrd.dev identity
yet. It reads as neither an apology nor a promise with a date attached.

What ``hosted_status`` still cannot check: whether a door's *credentials*
actually work end to end (a bot token can be present and revoked) —
``hosted_status`` answers "configured", not "healthy". A configured-but-
broken hosted gate still reads ``live`` here. And for Slack and Signal
specifically, ``"ready"`` is a static ceiling under the current
architecture, not a value in flight toward ``"live"``: there is no
``cloud_settings`` for either because brnrd.dev's backend does not mediate
them at all (see their gate module docstrings) — the same mechanism that
promotes WhatsApp from ``"ready"`` to ``"live"`` the moment its Meta
credentials land has nothing to read for Slack or Signal until a future
change decides what a brnrd.dev-hosted lane for them would even mean, and
gives them ``cloud_settings`` of their own. That is a product decision,
not a status computation, and this module does not make it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class _SettingsLike(Protocol):
    def __getattr__(self, name: str) -> Any: ...


def _module_shipped(dotted: str) -> bool:
    """Best-effort "does this gate module exist and import cleanly" check.

    ``importlib`` rather than a filesystem path so this stays correct if
    gates are ever repackaged; a missing/broken module answers ``False``
    rather than raising, since a broken import is itself "not shipped".
    Broadly caught: a module can fail import with more than
    ``ImportError`` (a bad top-level statement raises ``SyntaxError``, a
    broken dependency can raise anything at import time), and this feeds
    the live ``/v1/stats/support`` endpoint — one door's broken import
    must read as "not shipped", not 500 the whole matrix.
    """
    import importlib

    try:
        importlib.import_module(dotted)
    except Exception:  # noqa: BLE001 - a broken import is "not shipped", not a crash
        return False
    return True


def _whatsapp_shipped() -> bool:
    """WhatsApp has no standalone ``gates/whatsapp.py`` — it is a platform
    branch inside ``gates/cloud.py`` (the brnrd-inbox-draining gate shared
    with Telegram's hosted mode). Presence is therefore "does cloud.py's own
    response-limit table know the platform" rather than a module import —
    the same signal #1074 introduced, mechanized instead of grepped."""
    if not _module_shipped("brr.gates.cloud"):
        return False
    from brr.gates import cloud

    return "whatsapp" in getattr(cloud, "_RESPONSE_LIMITS", {})


@dataclass(frozen=True)
class Door:
    slug: str
    label: str
    tag: str | None = None
    shipped: Callable[[], bool] = lambda: True  # noqa: E731 - trivial default, no lambda gymnastics needed
    # Settings attribute names that must ALL be truthy for brnrd.dev's own
    # backend to consider this door hosted-and-configured. Empty = no
    # hosted axis exists for this door — either it *is* brnrd.dev
    # (`is_brnrd_dev=True`, see below) or it is self-hosted-only and
    # hosted_status has nothing to read for it, so it reads "ready" once
    # shipped rather than a fabricated "live".
    cloud_settings: tuple[str, ...] = ()
    # True only for the door that *is* brnrd.dev's own backend (the web
    # dashboard) — the one legitimate case where "no cloud_settings" means
    # "trivially always live" rather than "no confirmed identity yet".
    is_brnrd_dev: bool = False


DOORS: tuple[Door, ...] = (
    Door(
        "telegram",
        "Telegram",
        shipped=lambda: _module_shipped("brr.gates.telegram"),
        cloud_settings=("telegram_bot_token", "telegram_bot_username"),
    ),
    Door(
        "slack",
        "Slack",
        shipped=lambda: _module_shipped("brr.gates.slack"),
    ),
    Door(
        "github",
        "GitHub",
        tag="notifications",
        shipped=lambda: _module_shipped("brr.gates.github"),
        cloud_settings=("github_app_id", "github_app_private_key_b64"),
    ),
    Door(
        "dashboard",
        "Web dashboard",
        is_brnrd_dev=True,
    ),
    Door(
        "whatsapp",
        "WhatsApp",
        shipped=_whatsapp_shipped,
        cloud_settings=(
            "whatsapp_access_token",
            "whatsapp_phone_number_id",
        ),
    ),
    Door(
        "signal",
        "Signal",
        shipped=lambda: _module_shipped("brr.gates.signal"),
    ),
)

_DOORS_BY_SLUG: dict[str, Door] = {door.slug: door for door in DOORS}


def door(slug: str) -> Door:
    return _DOORS_BY_SLUG[slug]


def shipped_status(target: Door | str) -> str:
    """"Can a self-hoster get this running today?" — ``"live"`` once the
    gate's code exists on ``main``, ``"soon"`` otherwise. This is what the
    docs page's shelf renders."""
    d = target if isinstance(target, Door) else door(target)
    return "live" if d.shipped() else "soon"


def hosted_status(target: Door | str, settings: _SettingsLike) -> str:
    """"Is brnrd.dev's own backend wired up for this today?" — reads live
    ``Settings`` for doors brnrd.dev's backend actually mediates. This is
    what the app landing's shelf renders.

    Three outcomes, not two: ``"soon"`` means the gate's code does not
    exist on ``main`` yet — nothing to configure regardless. ``"live"``
    means brnrd.dev itself is reachable through this door today. Anything
    shipped short of that — no hosted axis at all (Slack, Signal), or an
    axis that exists but is not fully configured (WhatsApp pre-Meta-
    verification) — reads ``"ready"``: the code is done, brnrd.dev just
    has not vouched for an identity on it yet. Collapsing that case into
    ``"soon"`` would misreport finished code as unwritten; collapsing it
    into ``"live"`` (the old behaviour for doors with no ``cloud_settings``)
    would misreport a door nobody can message yet as one that works today.
    """
    d = target if isinstance(target, Door) else door(target)
    if not d.shipped():
        return "soon"
    if not d.cloud_settings:
        return "live" if d.is_brnrd_dev else "ready"
    configured = all(bool(getattr(settings, key, "")) for key in d.cloud_settings)
    return "live" if configured else "ready"


# ---------------------------------------------------------------------------
# Shells
# ---------------------------------------------------------------------------

# Not exposed over an endpoint the way doors are: both bundled shells are
# always live (no live/soon axis, unlike doors — every Shell that ships in
# the registry is immediately runnable by anyone with that CLI on PATH), and
# they are already asserted as static prose two lines above the shelf in
# ``Landing.svelte`` ("runs on Claude Code and Codex"). Re-deriving that at
# request time would add a fetch + loading state for two names that do not
# drift the way door status does. What *can* drift is the roster itself —
# a third bundled shell provider landing with no shelf entry — so this map
# is what a test pins against instead: ``test_support_matrix.py`` asserts
# it covers every shell id ``runner_cores`` actually bundles, and fails
# loudly (not silently) the day that stops being true.
SHELL_LABELS: dict[str, str] = {
    "claude": "Claude Code",
    "codex": "Codex",
}


def bundled_shells() -> tuple[str, ...]:
    """Distinct Shell ids from the bundled Core registry (``_BUNDLED_CORES``)
    — reachable from the backend (which already imports ``brr`` elsewhere,
    e.g. ``brnrd/app.py``), not from the frontend build (no Python there).
    """
    from . import runner_cores

    return tuple(sorted({entry["shell"] for entry in runner_cores.all_cores().values()}))
