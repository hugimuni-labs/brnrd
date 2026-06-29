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

**TTL / staleness:** the registry is static within a brr release. Operators
who need to add a model before the next brr release add an entry to their
project ``runners.md`` — those profile records override and extend this
registry. A ``freshness_date`` field in each entry records when it was last
verified so a future tooling pass can flag stale entries.
"""

from __future__ import annotations

import shlex
import shutil
from typing import Any

from . import runner_select

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
        "class": "economy",
        "cost_rank": 15,
        "freshness_date": "2026-06-29",
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
    if extra:
        registry.update(extra)

    out: list[runner_select.RunnerProfile] = []
    for name, entry in registry.items():
        shell = str(entry.get("shell") or name).strip()
        if not shell or shutil.which(shell) is None:
            continue
        profile = runner_select.RunnerProfile(
            name=name,
            profile=shell,  # invoke the base Shell; Core is in cmd/model
            model=_str(entry.get("model")),
            provider=_str(entry.get("provider")),
            owner="user",
            cost_class=_str(entry.get("class")),
            cost_rank=_int(entry.get("cost_rank")),
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
        out.append(
            runner_select.RunnerProfile(
                name=name,
                profile=declared_shell,
                model=_str(entry.get("model")),
                provider=_str(entry.get("provider")),
                owner="user",
                cost_class=_str(entry.get("class")),
                cost_rank=_int(entry.get("cost_rank")),
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
    into the base command, and keep project-declared profiles authoritative.

    A registry Core is generated only when its Shell has a declared base profile
    in the active ``runners.md`` source. That keeps a project-owned
    ``.brr/runners.md`` from unexpectedly reintroducing bundled Shells it chose
    not to declare.
    """
    declared = declared_profiles or {}
    out: dict[str, dict[str, Any]] = {}
    for name, entry in _BUNDLED_CORES.items():
        if name in declared:
            continue
        shell = _str(entry.get("shell"))
        model = _str(entry.get("model"))
        if not shell or not model:
            continue
        base = _base_profile_for_shell(declared, shell)
        if base is None:
            continue
        cmd = _cmd_with_model(shell, _str(base.get("cmd")) or shell, model)
        generated: dict[str, Any] = {
            "binary": _str(base.get("binary")) or shell,
            "cmd": cmd,
            "shell": shell,
            "model": model,
            "provider": _str(entry.get("provider")) or _str(base.get("provider")),
            "owner": _str(entry.get("owner")) or _str(base.get("owner")) or "user",
            "class": _str(entry.get("class")),
            "cost_rank": _int(entry.get("cost_rank")),
            "freshness_date": _str(entry.get("freshness_date")),
            "generated_core": True,
        }
        hooks = _str(entry.get("hooks")) or _str(base.get("hooks"))
        if hooks:
            generated["hooks"] = hooks
        quota_source = _str(entry.get("quota_source")) or _str(base.get("quota_source"))
        if quota_source:
            generated["quota_source"] = quota_source
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


def _base_profile_for_shell(
    declared_profiles: dict[str, dict[str, Any]],
    shell: str,
) -> dict[str, Any] | None:
    base = declared_profiles.get(shell)
    if isinstance(base, dict):
        return base
    return None


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
