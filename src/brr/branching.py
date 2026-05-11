"""Branch intent resolution for daemon tasks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import gitops


_STRUCTURED_BRANCH_KEYS = (
    "branch_target",
    "target_branch",
    "base_branch",
    "branch",
)

_CONVERSATION_BRANCH_KEYS = (
    "branch_target",
    "landed_branch",
    "preserved_branch",
    "changed_branch",
)


@dataclass(frozen=True)
class BranchPlan:
    """Pre-run branch plan resolved without asking the worker model."""

    seed_ref: str
    auto_land_branch: str | None
    authority: str
    host_context_branch: str | None
    expected_old_oid: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def display_base(self) -> str:
        """Human display base for existing branch-aware renderers."""
        return self.auto_land_branch or self.seed_ref

    def meta_items(self) -> dict[str, str]:
        """Return non-empty task metadata fields for this plan."""
        out = {
            "seed_ref": self.seed_ref,
            "branch_authority": self.authority,
        }
        if self.auto_land_branch:
            out["auto_land_branch"] = self.auto_land_branch
            # Compatibility for older status views that know "base_branch".
            out["base_branch"] = self.auto_land_branch
        if self.host_context_branch:
            out["host_context_branch"] = self.host_context_branch
        if self.expected_old_oid:
            out["auto_land_old_oid"] = self.expected_old_oid
        if self.notes:
            out["branch_notes"] = "; ".join(self.notes)
        return out


def resolve_branch_plan(
    repo_root: Path,
    event: dict[str, Any],
    cfg: dict[str, Any],
    *,
    conversation_records: list[dict[str, Any]] | None = None,
) -> BranchPlan:
    """Resolve a branch plan from structured state and explicit policy.

    The resolver deliberately avoids parsing free-text instructions from
    the event body. Free text is for the worker agent, which can switch
    branches after reading the actual request.
    """
    host_branch = gitops.current_branch(repo_root)
    host_context = host_branch if host_branch != "HEAD" else None
    default_seed = _default_seed(repo_root, host_branch)

    for key in _STRUCTURED_BRANCH_KEYS:
        candidate = _event_branch_candidate(
            repo_root, key, event.get(key), host_context,
        )
        if candidate:
            return _plan_for_target(
                repo_root,
                candidate,
                authority=f"event:{key}",
                host_context_branch=host_context,
                default_seed=default_seed,
            )

    conversation_target = _conversation_branch(repo_root, conversation_records or [])
    if conversation_target:
        return _plan_for_target(
            repo_root,
            conversation_target,
            authority="conversation",
            host_context_branch=host_context,
            default_seed=default_seed,
        )

    mode = _fallback_mode(cfg)
    if mode == "current":
        if host_context:
            return _plan_for_target(
                repo_root,
                host_context,
                authority="fallback:current",
                host_context_branch=host_context,
                default_seed=default_seed,
            )
        return BranchPlan(
            seed_ref=default_seed,
            auto_land_branch=None,
            authority="fallback:current",
            host_context_branch=None,
            notes=("host checkout is detached; preserving task branch",),
        )

    if mode == "default":
        target = gitops.default_branch(repo_root)
        if target and gitops.branch_exists(repo_root, target):
            return _plan_for_target(
                repo_root,
                target,
                authority="fallback:default",
                host_context_branch=host_context,
                default_seed=default_seed,
            )
        return BranchPlan(
            seed_ref=default_seed,
            auto_land_branch=None,
            authority="fallback:default",
            host_context_branch=host_context,
            notes=("default branch has no local branch to fast-forward",),
        )

    if mode == "inbox":
        return _plan_for_target(
            repo_root,
            "brr/inbox",
            authority="fallback:inbox",
            host_context_branch=host_context,
            default_seed=default_seed,
        )

    return BranchPlan(
        seed_ref=default_seed,
        auto_land_branch=None,
        authority="fallback:preserve",
        host_context_branch=host_context,
        notes=("no structured branch authority; preserving task branch",),
    )


def _plan_for_target(
    repo_root: Path,
    target: str,
    *,
    authority: str,
    host_context_branch: str | None,
    default_seed: str,
) -> BranchPlan:
    old_oid = gitops.branch_head(repo_root, target)
    seed_ref = target if old_oid else default_seed
    notes: tuple[str, ...] = ()
    if old_oid is None:
        notes = (f"{target} does not exist locally; it will be created on land",)
    return BranchPlan(
        seed_ref=seed_ref,
        auto_land_branch=target,
        authority=authority,
        host_context_branch=host_context_branch,
        expected_old_oid=old_oid,
        notes=notes,
    )


def _default_seed(repo_root: Path, host_branch: str) -> str:
    default_branch = gitops.default_branch(repo_root)
    if default_branch:
        return default_branch
    if host_branch and host_branch != "HEAD":
        return host_branch
    return "HEAD"


def _fallback_mode(cfg: dict[str, Any]) -> str:
    raw = cfg.get("branch.fallback", cfg.get("branch_fallback", "preserve"))
    mode = str(raw).strip().lower()
    if mode in {"preserve", "current", "default", "inbox"}:
        return mode
    return "preserve"


def _clean_branch(repo_root: Path, value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    branch = value.strip()
    if not branch:
        return None
    if not gitops.valid_branch_name(repo_root, branch):
        return None
    return branch


def _event_branch_candidate(
    repo_root: Path,
    key: str,
    value: Any,
    host_context_branch: str | None,
) -> str | None:
    if key == "branch" and isinstance(value, str):
        legacy = value.strip().lower()
        if legacy in {"", "auto", "task", "none"}:
            return None
        if legacy == "current":
            return host_context_branch
    return _clean_branch(repo_root, value)


def _conversation_branch(
    repo_root: Path,
    records: list[dict[str, Any]],
) -> str | None:
    candidates: list[str] = []
    for record in reversed(records):
        for key in _CONVERSATION_BRANCH_KEYS:
            candidate = _clean_branch(repo_root, record.get(key))
            if not candidate or candidate in candidates:
                continue
            candidates.append(candidate)
            if len(candidates) > 1:
                return None
    return candidates[0] if candidates else None
