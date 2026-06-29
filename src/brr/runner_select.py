"""Runner selection — the cost-aware Shell/Core selection layer above runner profiles.

A runner *profile* (``runners.md``) says **how** to invoke a Shell (CLI). A
``RunnerProfile`` says **when and why** to use it: its model (the Core), provider,
owner, cost class, and relative cost rank. Profiles let the resident make an
informed, cost-aware Shell choice *without the user authoring a policy table* —
the selection policy is brr's, the available profiles are whatever the host has
installed, and the per-profile cost metadata ships on the bundled profiles
(``kb/design-runner-cores.md``).

Design stance (maintainer steer, 2026-06-28): model selection is a requirement,
not a nicety, and the user-facing shape must carry **low cognitive load** —
the runner is empowered to make the informed decision, the user is not asked to
hand-tune execution details. So profile metadata lives on the profiles brr
already ships (extra frontmatter keys, fully backward-compatible); the only
user-facing knobs are *which Shell* (``shell=``) and *which Core* (``core=``).
Everything else the resident reads and decides.

This module is the **data model + deterministic policy** for first selection and
for the narrow automatic fallback path. The daemon still invokes one Runner at a
time, but after a classified quota/auth/provider failure it may ask this module
for a conservative same-or-cheaper local fallback before surfacing the failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import runner_failures

# Cost classes, cheapest → strongest. ``relay`` is a brnrd-owned paid fallback,
# never auto-selected (it needs spend-plan consent), so it sorts outside the
# local-first ladder.
ECONOMY = "economy"
BALANCED = "balanced"
STRONG = "strong"
RELAY = "relay"

_LOCAL_CLASS_ORDER: dict[str, int] = {ECONOMY: 0, BALANCED: 1, STRONG: 2}

# Selection policies. ``cost-aware`` lets the resident/daemon pick the cheapest
# adequate local profile; ``fixed`` pins the configured runner with no escalation.
POLICY_COST_AWARE = "cost-aware"
POLICY_FIXED = "fixed"

# cost_rank for profiles that declare none. Unknown cost must never *win* a
# cheapest-first race, so it sorts last rather than as 0.
_UNKNOWN_COST_RANK = 1_000_000

AUTO_FALLBACK_FAILURES = frozenset(
    {
        runner_failures.QUOTA_EXHAUSTED,
        runner_failures.AUTH_ERROR,
        runner_failures.PROVIDER_ERROR,
    }
)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class RunnerProfile:
    """One runner profile: a Shell plus its cost/selection metadata.

    ``name`` is the profile key. ``profile`` is the profile actually invoked
    (normally ``name``; a custom ``cmd`` profile may differ). The cost metadata
    is optional — a legacy ``shell=`` string resolves to an *implicit* profile
    with unknown cost (:func:`implicit_runner`), which the selector treats
    conservatively rather than pretending to know its price.
    """

    name: str
    profile: str
    model: str | None = None
    provider: str | None = None
    owner: str = "user"
    cost_class: str | None = None
    cost_rank: int | None = None
    quota_source: str | None = None
    hooks: str | None = None
    billing: str | None = None
    consent: str | None = None
    cmd: str | None = None
    capability_score: float | None = None
    capability_source: str | None = None
    capability_freshness: str | None = None

    @property
    def is_relay(self) -> bool:
        """True for a paid, non-local Shell that needs spend-plan consent."""
        return self.owner.strip().lower() == "brnrd" or self.cost_class == RELAY

    @property
    def class_rank(self) -> int:
        """Local cost-class ordering; relay/unknown sort past every local class."""
        return _LOCAL_CLASS_ORDER.get(self.cost_class or "", len(_LOCAL_CLASS_ORDER))

    @property
    def rank(self) -> int:
        return self.cost_rank if self.cost_rank is not None else _UNKNOWN_COST_RANK

    def summary(self) -> str:
        """A compact one-line description for the status card / portal."""
        bits = [self.name]
        if self.model:
            bits.append(self.model)
        tags = [t for t in (self.cost_class, None if self.owner == "user" else self.owner) if t]
        suffix = f" ({', '.join(tags)})" if tags else ""
        return " · ".join(bits) + suffix


def runner_from_profile(name: str, profile: dict[str, Any] | None) -> RunnerProfile:
    """Build a :class:`RunnerProfile` from a parsed ``runners.md`` profile entry.

    Unknown keys are ignored; missing cost metadata stays ``None`` so the
    selector can tell "cheap" from "uncosted".
    """
    profile = profile or {}
    owner = _as_str(profile.get("owner")) or "user"
    return RunnerProfile(
        name=name,
        profile=_as_str(profile.get("profile")) or name,
        model=_as_str(profile.get("model")),
        provider=_as_str(profile.get("provider")),
        owner=owner,
        cost_class=_as_str(profile.get("class")),
        cost_rank=_as_int(profile.get("cost_rank")),
        quota_source=_as_str(profile.get("quota_source")),
        hooks=_as_str(profile.get("hooks")),
        billing=_as_str(profile.get("billing")),
        consent=_as_str(profile.get("consent")),
        cmd=_as_str(profile.get("cmd")),
        capability_score=_as_float(profile.get("capability_score")),
        capability_source=_as_str(profile.get("capability_source")),
        capability_freshness=_as_str(profile.get("capability_freshness")),
    )


def implicit_runner(runner_name: str) -> RunnerProfile:
    """The legacy shim: a bare ``shell=`` string as one uncosted local profile.

    Pre-release, brr keeps exactly one spelling of the legacy path — a runner
    name with no profile metadata becomes a single ``owner=user`` profile with
    unknown cost. The selector then has nothing to optimise and falls back to
    "use this one", which is the current behaviour, made explicit.
    """
    return RunnerProfile(name=runner_name, profile=runner_name)


def load_runners(repo_root: Path | None = None) -> dict[str, RunnerProfile]:
    """All declared profiles, keyed by name (bundled + project ``runners.md``)."""
    from . import runner

    profiles = runner._load_profiles(repo_root)
    return {name: runner_from_profile(name, prof) for name, prof in profiles.items()}


def available_runners(repo_root: Path | None = None) -> list[RunnerProfile]:
    """Declared profiles whose underlying Shell binary is on PATH.

    Mirrors :func:`runner.detect_all_runners` but yields the richer profile
    records, so the selector reasons over installed-and-costed Shells only.
    """
    from . import runner

    profiles = runner._load_profiles(repo_root)
    out: list[RunnerProfile] = []
    for name in profiles:
        if runner._runner_available(name, profiles):
            out.append(runner_from_profile(name, profiles[name]))
    return out


def _local(runners: list[RunnerProfile]) -> list[RunnerProfile]:
    return [r for r in runners if not r.is_relay]


def _by_cost(runner: RunnerProfile) -> tuple[int, str]:
    return (runner.rank, runner.name)


def select_runner(
    runners: list[RunnerProfile],
    *,
    policy: str = POLICY_COST_AWARE,
    default_class: str | None = None,
    override: str | None = None,
) -> RunnerProfile | None:
    """Pick the first profile for a run, deterministically and conservatively.

    Inputs are all cheap to know *before* the run — no LLM triage, per the
    design ("the first selector should be deterministic and conservative; the
    resident escalates after it has read the repo"). Order of decision:

    1. **Explicit override** (``shell=<name>`` or a user pick) wins outright
       when that profile is available — the user's stated choice is never
       silently overridden, even toward a cheaper Shell.
    2. **Fixed policy** returns the cheapest available local profile and never
       escalates; it exists for users who do not want cost-aware movement.
    3. **Cost-aware policy** prefers the cheapest *local* profile at or below the
       requested ``default_class`` (``economy`` when unset), falling back to the
       cheapest local profile of any class. A **relay** (paid, brnrd-owned)
       profile is *never* auto-selected here — relay needs spend-plan consent, so
       it only enters via an explicit override or a later consent flow.

    Returns ``None`` only when no profile is available at all.
    """
    if override:
        for r in runners:
            if r.name == override:
                return r
        # An override naming an unavailable profile falls through to policy
        # rather than failing here; the caller (resolve_runner) raises with a
        # clearer message for a genuinely missing pinned runner.

    local = _local(runners)
    if not local:
        # Nothing local available. Do not auto-pick relay; surface no choice so
        # the caller can run the consent/setup path.
        return None

    def cheapest(candidates: list[RunnerProfile]) -> RunnerProfile:
        return sorted(candidates, key=_by_cost)[0]

    if policy == POLICY_FIXED:
        return cheapest(local)

    target = default_class or ECONOMY
    target_rank = _LOCAL_CLASS_ORDER.get(target, len(_LOCAL_CLASS_ORDER))
    at_or_below = [r for r in local if r.class_rank <= target_rank]
    return cheapest(at_or_below or local)


def automatic_fallback_runner(
    runners: list[RunnerProfile],
    *,
    current: str,
    failure_kind: str | None,
    tried: list[str] | tuple[str, ...] = (),
) -> RunnerProfile | None:
    """Pick the next local Runner after an operational failure.

    Automatic fallback is deliberately narrower than first selection:

    - only unambiguous operational failures enter it (quota/auth/provider);
    - paid relay profiles are excluded until the spend-plan consent slice lands;
    - the next Runner must be in the same or a cheaper class than the failed one,
      so recovery does not silently escalate cost;
    - provider outages require a different provider, while quota/auth failures
      require a different failure domain (quota source first, provider second).

    Returns ``None`` when no conservative local fallback exists.
    """
    if failure_kind not in AUTO_FALLBACK_FAILURES:
        return None

    current_profile = _find_runner(runners, current)
    if current_profile is None:
        return None

    tried_names = {str(name) for name in tried if str(name).strip()}
    tried_names.add(current_profile.name)
    tried_names.add(current_profile.profile)

    candidates = [
        runner for runner in runners
        if (
            not runner.is_relay
            and runner.name not in tried_names
            and runner.profile not in tried_names
        )
    ]
    if not candidates:
        return None

    if current_profile.cost_class in _LOCAL_CLASS_ORDER:
        candidates = [
            runner for runner in candidates
            if runner.class_rank <= current_profile.class_rank
        ]
    if not candidates:
        return None

    if failure_kind == runner_failures.PROVIDER_ERROR and current_profile.provider:
        current_provider = current_profile.provider.strip().lower()
        candidates = [
            runner for runner in candidates
            if (runner.provider or "").strip().lower() != current_provider
        ]
    else:
        current_domain = _failure_domain(current_profile)
        if current_domain:
            candidates = [
                runner for runner in candidates
                if _failure_domain(runner) != current_domain
            ]

    if not candidates:
        return None
    return sorted(candidates, key=_by_cost)[0]


def quality_escalation_runner(
    runners: list[RunnerProfile],
    *,
    current: str,
    target_class: str | None = STRONG,
    tried: list[str] | tuple[str, ...] = (),
) -> RunnerProfile | None:
    """Pick a stronger local Runner for a resident-authored quality escalation.

    This is deliberately separate from automatic fallback. Operational fallback
    recovers from quota/auth/provider failures without spending more; quality
    escalation is an explicit resident-authored handoff after reading the repo
    and deciding the task wants a stronger Core. It therefore may move up the
    local cost ladder, but still excludes relay profiles until the spend-consent
    slice exists.

    The default target is ``strong`` because the design reserves strong local
    Cores for explicit asks, repeated quality failure, or resident-authored
    escalation. If no target-class candidate exists, fall back to the cheapest
    strictly stronger local candidate.
    """
    current_profile = _find_runner(runners, current)
    if current_profile is None:
        return None

    tried_names = {str(name) for name in tried if str(name).strip()}
    tried_names.add(current_profile.name)
    tried_names.add(current_profile.profile)

    local = [
        runner for runner in runners
        if (
            not runner.is_relay
            and runner.name not in tried_names
            and runner.profile not in tried_names
        )
    ]
    if not local:
        return None

    current_rank = _LOCAL_CLASS_ORDER.get(current_profile.cost_class or "", -1)
    target_rank = _LOCAL_CLASS_ORDER.get(
        str(target_class or "").strip().lower(), None
    )
    if target_rank is not None:
        target_candidates = [
            runner for runner in local
            if (
                runner.cost_class in _LOCAL_CLASS_ORDER
                and runner.class_rank >= target_rank
                and runner.class_rank > current_rank
            )
        ]
        if target_candidates:
            return sorted(target_candidates, key=_quality_key)[0]

    stronger = [
        runner for runner in local
        if runner.cost_class in _LOCAL_CLASS_ORDER and runner.class_rank > current_rank
    ]
    if not stronger:
        return None
    return sorted(stronger, key=_quality_key)[0]


def _find_runner(runners: list[RunnerProfile], name: str) -> RunnerProfile | None:
    for runner in runners:
        if runner.name == name or runner.profile == name:
            return runner
    return None


def _quality_key(runner: RunnerProfile) -> tuple[int, int, str]:
    return (runner.class_rank, runner.rank, runner.name)


def _failure_domain(runner: RunnerProfile) -> str | None:
    for value in (runner.quota_source, runner.provider, runner.profile, runner.name):
        text = (value or "").strip().lower()
        if text:
            return text
    return None


@dataclass(frozen=True)
class RespawnRequest:
    """A resident-authored ask to re-run on a stronger/different Shell or Core.

    The data shape of the parked respawn portal (``design-runner-cores.md``):
    an economy run that finds the task harder than it looked, or hits a
    classified quota/auth/provider failure, emits this rather than grinding. The
    optional ``at`` / ``defer_until`` fields compose respawn with the existing
    schedule/defer machinery ("run in half an hour on Codex") instead of adding
    a parallel time queue. The daemon's respawn loop (a later slice, gated on
    the run/event model #128) consumes it; this records the contract so the
    selector and the loop agree.
    """

    reason: str
    proposed_runner: str
    carry_forward: str | None = None
    consent: str | None = None
    at: str | None = None
    defer_until: str | None = None
