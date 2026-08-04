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
  ``cloud_settings`` and this function falls back to the shipped-status
  answer for them; the web dashboard *is* brnrd.dev, so it is always live
  by the same fallback. This is deliberately not one global switch: the
  maintainer's "future things which we enable as I finalise the set up
  and testing" is true of brnrd.dev's own WhatsApp deployment specifically
  (Meta business verification, not yet done as of this writing) and does
  not make WhatsApp any less real for someone who runs their own backend
  with their own Meta credentials — the docs page is talking to that
  person, the app landing is talking to a brnrd.dev visitor, and they get
  different, both-honest answers to the same door.

What this still cannot check: whether a door's *credentials* actually work
end to end (a bot token can be present and revoked) — ``hosted_status``
answers "configured", not "healthy". A configured-but-broken hosted gate
still reads ``live`` here.
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
    """
    import importlib

    try:
        importlib.import_module(dotted)
    except ImportError:
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
    # hosted axis exists for this door (self-hosted-only, or the door *is*
    # brnrd.dev itself) — hosted_status then mirrors shipped_status.
    cloud_settings: tuple[str, ...] = ()


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
    ``Settings`` for doors brnrd.dev's backend actually mediates; falls
    back to :func:`shipped_status` for doors with no hosted axis at all.
    This is what the app landing's shelf renders."""
    d = target if isinstance(target, Door) else door(target)
    if not d.cloud_settings:
        return shipped_status(d)
    if not d.shipped():
        return "soon"
    configured = all(bool(getattr(settings, key, "")) for key in d.cloud_settings)
    return "live" if configured else "soon"


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
