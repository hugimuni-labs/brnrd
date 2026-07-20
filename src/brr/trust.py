"""Source-trust tiering — pick the execution environment by ingress trust.

The environment a run executes in used to be chosen by static config
alone (``resolve_env``), so an untrusted GitHub commenter's run got the
same authority — same env, same credentials, same network — as the
owner's. This module adds a **trust tier**, resolved *per event at
manifest-build time* (deterministic, no LLM in the loop, the same shape
as env resolution today), that routes hostile-adjacent ingress onto an
isolated environment or refuses it outright. Issue #517.

Three tiers:

- ``owner`` — the operator / bound account / paired chat (and every
  owner-only path: schedule wakes, resident self-wakes, CLI, respawn,
  spawn). Gets the configured default env — today's behaviour, unchanged.
- ``collaborator`` — repo write+ access, a room member the operator
  allowlisted. Gets the configured default too, overridable down to a
  tighter env with ``trust.collaborator_env``.
- ``untrusted`` — anything else that still reaches enqueue. Routed to
  ``trust.untrusted_env`` (default ``solitary``), or **refused** when
  solitary isn't available (no ``docker.image``) or ``trust.untrusted``
  is ``refuse``.

Who is which tier is a fact only the gate knows — it holds the sender
identity (a GitHub collaborator-permission lookup, a Telegram paired /
allowlisted principal), authorized at ingress (#408, #409). So the gate
**stamps** ``trust_tier`` onto the event meta, and this module reads that
stamp, falling back to a source-based default that **fails closed**:
owner-only sources resolve to ``owner``; any ingress path that can carry
strangers resolves to ``untrusted`` when the stamp is missing.

The tier rides the run meta (``trust_tier``, visible like ``environment``)
so surfaces can render it, and — critically — an event-supplied
``environment`` / ``env`` key can never escalate an untrusted event out of
its tier: for untrusted, the event's env policy is ignored entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


OWNER = "owner"
COLLABORATOR = "collaborator"
UNTRUSTED = "untrusted"
TIERS = (OWNER, COLLABORATOR, UNTRUSTED)

# Sources that can only originate from the operator/daemon itself — no
# stranger can forge one, so they resolve to ``owner`` without a stamp.
# ``cloud`` is here because a cloud event only exists when the operator
# paired the account and the brnrd server authorized the sender into a
# bound room; the relay is the account's own channel (a finer server-side
# stamp can still override this default per event).
#
# Deliberately *not* here: the empty source. ``protocol.create_event``
# requires a source, so a sourceless event is malformed/unattributed —
# and an allowlist entry for "we don't know where this came from" would
# be fail-open in the one place this module promises fail-closed.
_OWNER_SOURCES = frozenset({"schedule", "cli", "respawn", "spawn", "cloud"})


@dataclass(frozen=True)
class TrustDecision:
    """The tier + env routing decided for one event at manifest-build time.

    ``env_policy`` is the *policy* string (``auto``/``worktree``/``solitary``
    /…) to feed the normal resolver; ``env`` is the concrete backend name.
    On a refusal both are ``None`` and ``refused`` is set with a ``reason``
    the daemon logs — the run must not execute.
    """

    tier: str
    env_policy: str | None
    env: str | None
    refused: bool = False
    reason: str = ""


def _cfg_str(cfg: dict[str, Any], key: str, default: str = "") -> str:
    """Read a ``trust.<key>`` config value, accepting the ``trust_<key>`` form."""
    dotted = f"trust.{key}"
    under = f"trust_{key}"
    value = cfg.get(dotted, cfg.get(under, default))
    return str(value).strip() if value is not None else default


def resolve_tier(event: dict[str, Any], cfg: dict[str, Any] | None = None) -> str:
    """Resolve the trust tier for *event*. Deterministic, fail-closed.

    Prefers the gate-stamped ``trust_tier`` (the gate is the only place
    that holds the sender's authorization facts). Absent a valid stamp,
    falls back on the event ``source``: owner-only sources → ``owner``;
    every other (ingress) source, or an unknown one → ``untrusted``.
    """
    stamped = str(event.get("trust_tier") or "").strip().casefold()
    if stamped in TIERS:
        return stamped
    source = str(event.get("source") or "").strip().casefold()
    if source in _OWNER_SOURCES:
        return OWNER
    # Ingress path that can carry strangers, with no authorization stamp:
    # fail closed. A local gate that authorized the sender always stamps a
    # tier; a missing stamp here means an unattributed / legacy event.
    return UNTRUSTED


def resolve_decision(
    event: dict[str, Any], cfg: dict[str, Any] | None = None
) -> TrustDecision:
    """Resolve tier → env routing for *event*, honouring the trust config.

    - ``owner``        → the event's own env policy (today's behaviour).
    - ``collaborator`` → ``trust.collaborator_env`` when set, else the
      event's own env policy (today's behaviour at zero config).
    - ``untrusted``    → ``trust.untrusted_env`` (default ``solitary``),
      or a refusal when ``trust.untrusted=refuse`` or the resolved env is
      ``solitary`` without a ``docker.image`` to back it. The event's own
      env policy is **never** consulted — a stranger cannot name their env.
    """
    from .run import _event_environment_policy, resolve_env, _docker_configured

    cfg = cfg or {}
    tier = resolve_tier(event, cfg)

    if tier == OWNER:
        policy = _event_environment_policy(event, cfg)
        return TrustDecision(tier, policy, resolve_env(policy, cfg))

    if tier == COLLABORATOR:
        override = _cfg_str(cfg, "collaborator_env")
        policy = override or _event_environment_policy(event, cfg)
        return TrustDecision(tier, policy, resolve_env(policy, cfg))

    # untrusted — the event's own env policy is deliberately ignored.
    mode = _cfg_str(cfg, "untrusted", "solitary").casefold() or "solitary"
    if mode not in ("solitary", "refuse"):
        mode = "solitary"
    if mode == "refuse":
        return TrustDecision(
            tier, None, None, refused=True,
            reason="trust.untrusted=refuse",
        )
    untrusted_env = _cfg_str(cfg, "untrusted_env", "solitary") or "solitary"
    resolved = resolve_env(untrusted_env, cfg)
    if resolved == "solitary" and not _docker_configured(cfg):
        return TrustDecision(
            tier, None, None, refused=True,
            reason=(
                "untrusted source and solitary is unavailable "
                "(no docker.image configured)"
            ),
        )
    return TrustDecision(tier, untrusted_env, resolved)
