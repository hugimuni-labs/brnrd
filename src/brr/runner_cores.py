"""Task 2B — dynamic Core registry for brr's cost-aware Shell/Core selection.

When a new model ships, it should be available to the selector as soon as
this file is updated — no new runner profile entry required. This module is
the "live source" the selector reads; static ``runners.md`` profile metadata
becomes *defaults/overrides* on top of it
(``kb/plan-repo-gardening.md`` §2B).

**Design:** neither ``claude`` nor ``codex`` expose a model-list CLI subcommand
today. The probe is therefore: if the Shell binary is on PATH, all Cores
declared for that Shell in the bundled registry are available. The registry
ships as a typed Python dict (no separate data file needed at this scale; split
if it grows past ~100 entries). Each Core entry carries the same metadata shape
as a ``RunnerProfile`` so :func:`available_cores` can hand the result directly
to :func:`runner_select.select_runner`. The daemon resolver uses
``generated_profile_entries()`` to turn the same registry rows into concrete
profiles with model flags inserted into the Shell command.

Capability metadata is layered on top from the packaged
``runner-capabilities.json`` cache. Hand-set ``class`` on a Core entry wins;
when it is absent, the capability cache may derive economy/balanced/strong from
benchmark scores. Empty benchmark scores stay empty rather than inventing a
capability claim.

**TTL / staleness:** the registry is static within a brr release. Operators
who need to add a model before the next brr release add an entry to their
project ``runners.md`` — those profile records override and extend this
registry. A ``freshness_date`` field in each entry records when it was last
verified so a future tooling pass can flag stale entries.
"""

from __future__ import annotations

from functools import lru_cache
import re
import shlex
import shutil
import subprocess
from typing import Any

from . import runner_capabilities, runner_select

_PROBE_TIMEOUT_S = 2.0
_MODEL_TOKEN_RE = re.compile(
    r"\b(?:claude|gpt|o\d|gemini|llama|mistral|qwen|deepseek|devstral|grok)"
    r"[A-Za-z0-9_.:/+-]*\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Bundled Core registry
# ---------------------------------------------------------------------------
# Format per entry:
#   "profile_name": {         # becomes RunnerProfile.name / .profile
#     "shell": "<cli_name>",  # binary that must be on PATH
#     "model": "<model_id>",  # the Core (exact CLI flag value)
#     "provider": "...",
#     "class": "economy"|"balanced"|"strong",
#     "cost_rank": <int>,     # lower = cheaper (tune freely)
#     "freshness_date": "YYYY-MM-DD",
#   }
#
# Entries where "class" is omitted stay unclassed (the selector treats them
# as unknown-cost, sorted after all classed profiles).

_BUNDLED_CORES: dict[str, dict[str, Any]] = {
    # ── Claude (Anthropic) ──────────────────────────────────────────────
    # Claude Code's --model flag accepts model IDs directly; aliases like
    # "haiku", "sonnet" also work but IDs are more stable across releases.
    "claude-haiku": {
        "shell": "claude",
        "model": "claude-haiku-4-5-20251001",
        "provider": "anthropic",
        "class": "economy",
        "cost_rank": 10,
        "freshness_date": "2026-06-29",
    },
    "claude-sonnet": {
        "shell": "claude",
        "model": "claude-sonnet-4-6",
        "provider": "anthropic",
        "class": "balanced",
        "cost_rank": 30,
        "freshness_date": "2026-06-29",
    },
    "claude-opus": {
        "shell": "claude",
        "model": "claude-opus-4-8",
        "provider": "anthropic",
        "class": "strong",
        "cost_rank": 50,
        "freshness_date": "2026-06-29",
    },
    "claude-fable": {
        "shell": "claude",
        "model": "claude-fable-5",
        "provider": "anthropic",
        # Priciest core in the rack (maintainer-confirmed 2026-07-11);
        # was mislabeled economy/rank-15 in the 2026-06-29 seed.
        "class": "strong",
        "cost_rank": 55,
        "freshness_date": "2026-07-11",
    },
    # ── Codex (OpenAI) ──────────────────────────────────────────────────
    # codex exec -m <model> selects the Core. Mini is the economy tier;
    # gpt-5-codex is the balanced/strong tier.
    "codex-mini": {
        "shell": "codex",
        "model": "gpt-5.4-mini",
        "provider": "openai",
        "class": "economy",
        "cost_rank": 20,
        "freshness_date": "2026-06-29",
    },
    "codex-full": {
        "shell": "codex",
        "model": "gpt-5-codex",
        "provider": "openai",
        "class": "balanced",
        "cost_rank": 35,
        "freshness_date": "2026-06-29",
    },
    # ── Gemini (Google) ─────────────────────────────────────────────────
    # gemini CLI model selection not yet probed; placeholder for when we
    # have a live Gemini runner test.
    "gemini-flash": {
        "shell": "gemini",
        "model": "gemini-2.0-flash",
        "provider": "google",
        "class": "economy",
        "cost_rank": 12,
        "freshness_date": "2026-06-29",
    },
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def all_cores() -> dict[str, dict[str, Any]]:
    """The full bundled Core registry, keyed by profile name."""
    return dict(_BUNDLED_CORES)


@lru_cache(maxsize=16)
def probe_shell_models(
    shell_name: str,
    *,
    timeout: float = _PROBE_TIMEOUT_S,
) -> tuple[str, ...]:
    """Best-effort model discovery from the Shell itself.

    Some CLIs expose model choices in help output while offering no stable
    ``list models`` subcommand. This probe is deliberately small and bounded:
    run the Shell's local help path with a short timeout, parse only model-ish
    tokens on model-related lines, and fall back to the bundled registry when
    nothing is exposed. It never touches the network intentionally.
    """
    shell = shell_name.strip()
    if not shell:
        return ()
    binary = shutil.which(shell)
    if not binary:
        return ()
    models: list[str] = []
    for cmd in _probe_commands(shell, binary):
        try:
            proc = subprocess.run(
                cmd,
                input="",
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        models.extend(
            _models_from_text((proc.stdout or "") + "\n" + (proc.stderr or ""))
        )
    return tuple(dict.fromkeys(models))


def available_cores(
    *,
    extra: dict[str, dict[str, Any]] | None = None,
) -> list[runner_select.RunnerProfile]:
    """Cores whose Shell binary is on PATH, as :class:`~runner_select.RunnerProfile` records.

    *extra* lets the caller inject project-level override entries (from
    ``runners.md`` frontmatter) that extend or supersede the bundled
    registry. Entries in *extra* win over bundled entries with the same name.

    The result is sorted cheapest-first (by cost_rank, then name), matching
    the convention :func:`runner_select.select_runner` expects. Only
    local-Shell profiles (no relay) are included — relay Cores are never
    auto-available here.
    """
    registry = dict(_BUNDLED_CORES)
    registry.update(_probed_core_entries(set(_registry_shells(registry))))
    if extra:
        registry.update(extra)

    out: list[runner_select.RunnerProfile] = []
    for name, entry in registry.items():
        shell = str(entry.get("shell") or name).strip()
        if not shell or shutil.which(shell) is None:
            continue
        cap_meta = runner_capabilities.metadata_for_model(entry.get("model"))
        profile = runner_select.RunnerProfile(
            name=name,
            profile=shell,  # invoke the base Shell; Core is in cmd/model
            model=_str(entry.get("model")),
            provider=_str(entry.get("provider")),
            owner="user",
            cost_class=_class_for_entry(entry),
            cost_rank=_int(entry.get("cost_rank")),
            capability_score=_float(cap_meta.get("capability_score")),
            capability_source=_str(cap_meta.get("capability_source")),
            capability_freshness=_str(cap_meta.get("capability_freshness")),
        )
        if profile.is_relay:
            continue
        out.append(profile)

    out.sort(key=lambda p: (p.rank, p.name))
    return out


def cores_for_shell(shell_name: str) -> list[runner_select.RunnerProfile]:
    """All bundled Cores declared for *shell_name*, regardless of PATH."""
    shell_lower = shell_name.strip().lower()
    out: list[runner_select.RunnerProfile] = []
    for name, entry in _BUNDLED_CORES.items():
        declared_shell = str(entry.get("shell") or "").strip().lower()
        if declared_shell != shell_lower:
            continue
        cap_meta = runner_capabilities.metadata_for_model(entry.get("model"))
        out.append(
            runner_select.RunnerProfile(
                name=name,
                profile=declared_shell,
                model=_str(entry.get("model")),
                provider=_str(entry.get("provider")),
                owner="user",
                cost_class=_class_for_entry(entry),
                cost_rank=_int(entry.get("cost_rank")),
                capability_score=_float(cap_meta.get("capability_score")),
                capability_source=_str(cap_meta.get("capability_source")),
                capability_freshness=_str(cap_meta.get("capability_freshness")),
            )
        )
    out.sort(key=lambda p: (p.rank, p.name))
    return out


def generated_profile_entries(
    declared_profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Invokable profile entries generated from the bundled Core registry.

    ``available_cores()`` exposes registry entries as selector records. The
    daemon also needs a concrete profile name it can pass to ``_build_cmd``.
    This helper derives those profiles from the Shell's declared base profile:
    copy hook/quota metadata from the base Shell, insert the Core's model flag
    into the base command, and keep project-declared profiles authoritative
    *per field*: the caller (``runner._selection_profiles``) overlays declared
    fields on top of the generated twin, so a declaration that only pins
    ``cmd`` still inherits the registry's model/class/cost metadata instead
    of silently shedding it.
    If a declared base profile carries an ``auth_variant`` flag (for example
    Claude's ``--bare`` / ``ANTHROPIC_API_KEY`` path), generate the same Core
    names under that base profile too. The Core metadata still comes from this
    registry; the profile supplies only the authentication/command variant.

    A registry Core is generated only when its Shell has a declared base profile
    in the active ``runners.md`` source. That keeps a project-owned
    ``.brr/runners.md`` from unexpectedly reintroducing bundled Shells it chose
    not to declare.
    """
    declared = declared_profiles or {}
    registry = dict(_BUNDLED_CORES)
    registry.update(_probed_core_entries(_declared_shells(declared), registry))
    out: dict[str, dict[str, Any]] = {}
    for core_name, entry in registry.items():
        shell = _str(entry.get("shell"))
        model = _str(entry.get("model"))
        if not shell or not model:
            continue
        bases = _base_profiles_for_shell(declared, shell)
        if not bases:
            continue
        for base_name, base in bases:
            name = _generated_profile_name(core_name, shell, base_name)
            if name in out:
                continue
            cmd = _cmd_with_model(shell, _str(base.get("cmd")) or shell, model)
            generated: dict[str, Any] = {
                "binary": _str(base.get("binary")) or shell,
                "cmd": cmd,
                "shell": shell,
                "model": model,
                "provider": _str(entry.get("provider")) or _str(base.get("provider")),
                "owner": _str(entry.get("owner")) or _str(base.get("owner")) or "user",
                "class": _class_for_entry(entry),
                "cost_rank": _int(entry.get("cost_rank")),
                "freshness_date": _str(entry.get("freshness_date")),
                "freshness_source": _str(entry.get("freshness_source")),
                "generated_core": True,
            }
            generated.update(runner_capabilities.metadata_for_model(model))
            hooks = _str(entry.get("hooks")) or _str(base.get("hooks"))
            if hooks:
                generated["hooks"] = hooks
            quota_source = _str(entry.get("quota_source")) or _str(
                base.get("quota_source")
            )
            if quota_source:
                generated["quota_source"] = quota_source
            auth_variant = _str(base.get("auth_variant"))
            if auth_variant:
                generated["auth_variant"] = auth_variant
            auth_env = _str(base.get("auth_env"))
            if auth_env:
                generated["auth_env"] = auth_env
            out[name] = generated
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _registry_shells(registry: dict[str, dict[str, Any]]) -> list[str]:
    shells: list[str] = []
    for entry in registry.values():
        shell = _str(entry.get("shell"))
        if shell:
            shells.append(shell)
    return list(dict.fromkeys(shells))


def _declared_shells(declared_profiles: dict[str, dict[str, Any]]) -> set[str]:
    shells: set[str] = set()
    for name, profile in declared_profiles.items():
        profile = profile or {}
        shell = _str(profile.get("shell")) or _str(profile.get("binary")) or name
        if shell:
            shells.add(shell)
    return shells


def _probe_commands(shell: str, binary: str) -> list[list[str]]:
    if shell == "codex":
        return [[binary, "exec", "--help"], [binary, "--help"]]
    return [[binary, "--help"]]


def _models_from_text(text: str) -> list[str]:
    models: list[str] = []
    for line in text.splitlines():
        lower = line.lower()
        if "model" not in lower and "core" not in lower:
            continue
        for match in _MODEL_TOKEN_RE.findall(line):
            token = match.strip("`'\".,;:()[]{}<>")
            if _valid_model_token(token):
                models.append(token)
    return list(dict.fromkeys(models))


def _valid_model_token(token: str) -> bool:
    if len(token) < 4:
        return False
    lower = token.lower()
    if lower in {"model", "models", "core", "cores"}:
        return False
    return any(ch.isdigit() for ch in token) or "-" in token


def _probed_core_entries(
    shells: set[str],
    registry: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    if not shells:
        return {}
    registry = registry or _BUNDLED_CORES
    known = {
        (
            str(entry.get("shell") or "").strip().lower(),
            str(entry.get("model") or "").strip().lower(),
        )
        for entry in registry.values()
    }
    out: dict[str, dict[str, Any]] = {}
    for shell in sorted(shells):
        for model in probe_shell_models(shell):
            key = (shell.lower(), model.lower())
            if key in known:
                continue
            name = _unique_name(f"{shell}-{_slug_model(model)}", registry, out)
            out[name] = {
                "shell": shell,
                "model": model,
                "provider": _provider_for_shell(shell),
                "class": runner_capabilities.derived_cost_class(model),
                "cost_rank": None,
                "freshness_source": "cli-help",
            }
            known.add(key)
    return out


def _provider_for_shell(shell: str) -> str | None:
    return {
        "claude": "anthropic",
        "codex": "openai",
        "gemini": "google",
    }.get(shell)


def _slug_model(model: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-").lower()
    return slug or "model"


def _unique_name(
    candidate: str,
    registry: dict[str, dict[str, Any]],
    out: dict[str, dict[str, Any]],
) -> str:
    name = candidate
    idx = 2
    while name in registry or name in out:
        name = f"{candidate}-{idx}"
        idx += 1
    return name


def _class_for_entry(entry: dict[str, Any]) -> str | None:
    return _str(entry.get("class")) or runner_capabilities.derived_cost_class(
        _str(entry.get("model"))
    )


def _base_profiles_for_shell(
    declared_profiles: dict[str, dict[str, Any]],
    shell: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Declared base profiles that can host generated Cores for *shell*."""
    out: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for name, profile in declared_profiles.items():
        if not isinstance(profile, dict):
            continue
        declared_shell = (
            _str(profile.get("shell")) or _str(profile.get("binary")) or name
        )
        if declared_shell != shell:
            continue
        model = _str(profile.get("model"))
        has_auth_variant = bool(_str(profile.get("auth_variant")))
        # Model-pinned profiles are generated outputs or user overrides, not
        # base commands for every Core. Auth variants are the intentional alias
        # base exception (`claude-bare-api-only`).
        if model and not has_auth_variant:
            continue
        # A same-shell profile with no auth variant is the canonical base
        # profile; arbitrary aliases are exact pins, not extra catalogs.
        if name != shell and not has_auth_variant:
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append((name, profile))
    return out


def _generated_profile_name(core_name: str, shell: str, base_name: str) -> str:
    if base_name == shell:
        return core_name
    prefix = f"{shell}-"
    suffix = core_name[len(prefix):] if core_name.startswith(prefix) else core_name
    return f"{base_name}-{suffix}"


def _cmd_with_model(shell: str, base_cmd: str, model: str) -> str:
    parts = shlex.split(base_cmd) if base_cmd else [shell]
    if not parts:
        parts = [shell]

    for flag in ("--model", "-m"):
        if flag not in parts:
            continue
        idx = parts.index(flag)
        if idx + 1 < len(parts):
            parts[idx + 1] = model
        else:
            parts.append(model)
        return shlex.join(parts)

    insert_at = 1
    if shell == "codex" and len(parts) > 1 and parts[1] == "exec":
        insert_at = 2
    return shlex.join([*parts[:insert_at], "--model", model, *parts[insert_at:]])
