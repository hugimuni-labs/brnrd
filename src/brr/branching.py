"""Branch plan resolution for daemon tasks.

The plan is a thin pre-run record: which ref to seed the
``brr/<task-id>`` worktree branch from, and which branch (if any)
finalization may fast-forward when the agent stays on the task branch.

Branch *intent* — "should this work continue a prior thread branch?",
"does the task body name a branch?" — belongs to the worker agent. The
agent already sees the recent conversation and the task body in its
prompt and can ``git switch`` inside the worktree. The daemon only owns
the mechanical safety contract around that runtime choice.

Resolution order:

1. Structured event branch field (``branch_target``, ``target_branch``,
   ``base_branch``, or the legacy ``branch``). When the event names a
   target, the plan seeds from the **remote** tracking ref
   (``<remote>/<target>``) if present, so the worker sprouts from the
   forge-visible state even when the daemon's local copy of that branch
   diverged and the pre-task ff was refused.
2. Fallback policy from config (``branch.fallback``):
   * ``preserve`` (default) — no auto-land target; agent commits live on
     ``brr/<task-id>`` until a human routes them.
   * ``current`` — seed from and auto-land to the host current branch;
     opt-in self-development behaviour. The remote preference is **not**
     applied here: ``current`` is the self-development knob and the host
     is the source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import gitops


STRUCTURED_BRANCH_KEYS = (
    "branch_target",
    "target_branch",
    "base_branch",
    "branch",
)
# Backwards-compat alias for in-tree callers that still read the
# private name; exposed publicly so daemon-side helpers (e.g. the
# sync hook) can reuse the same key list without duplication.
_STRUCTURED_BRANCH_KEYS = STRUCTURED_BRANCH_KEYS


@dataclass(frozen=True)
class BranchPlan:
    """Pre-run branch plan resolved without asking the worker model."""

    seed_ref: str
    auto_land_branch: str | None
    source: str
    host_context_branch: str | None
    expected_old_oid: str | None = None

    def meta_items(self) -> dict[str, str]:
        """Return non-empty task metadata fields for this plan."""
        out: dict[str, str] = {
            "seed_ref": self.seed_ref,
            "branch_source": self.source,
        }
        if self.auto_land_branch:
            out["auto_land_branch"] = self.auto_land_branch
        if self.host_context_branch:
            out["host_context_branch"] = self.host_context_branch
        if self.expected_old_oid:
            out["auto_land_old_oid"] = self.expected_old_oid
        return out


def resolve_branch_plan(
    repo_root: Path,
    event: dict[str, Any],
    cfg: dict[str, Any],
) -> BranchPlan:
    """Resolve a branch plan from structured state and explicit policy.

    Deliberately does not look at conversation history, parse free-text
    instructions, or run an LLM. Anything beyond a structured event
    field belongs to the worker agent.
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
                source=f"event:{key}",
                host_context_branch=host_context,
                default_seed=default_seed,
                prefer_remote=True,
            )

    mode = _fallback_mode(cfg)
    if mode == "current" and host_context:
        return _plan_for_target(
            repo_root,
            host_context,
            source="fallback:current",
            host_context_branch=host_context,
            default_seed=default_seed,
        )

    return BranchPlan(
        seed_ref=default_seed,
        auto_land_branch=None,
        source="fallback:preserve",
        host_context_branch=host_context,
    )


def _plan_for_target(
    repo_root: Path,
    target: str,
    *,
    source: str,
    host_context_branch: str | None,
    default_seed: str,
    prefer_remote: bool = False,
) -> BranchPlan:
    """Resolve seed + ff anchor for *target*.

    With ``prefer_remote=True`` (set on the event-branch path), the seed
    ref becomes ``<remote>/<target>`` when that tracking ref exists. This
    matters when the host's local copy of *target* has diverged from the
    remote: the daemon's pre-task sync ff is refused on diverged history,
    and without this preference the worker would seed from a stale local
    branch and produce a divergent, unpushable history. Anchoring to the
    remote ref guarantees the worker sprouts from the GitHub-visible
    state regardless of how stale the host's local branch is.

    The ff anchor (``expected_old_oid``) follows the seed: when we seed
    from the remote, finalize's auto-land ff is checked against the
    remote oid we actually built on, not the diverged local one.
    """
    local_oid = gitops.branch_head(repo_root, target)
    seed_ref = target if local_oid else default_seed
    old_oid = local_oid

    if prefer_remote:
        remote = gitops.default_remote(repo_root)
        if remote:
            remote_ref = f"{remote}/{target}"
            remote_oid = gitops.rev_parse(repo_root, remote_ref)
            if remote_oid:
                seed_ref = remote_ref
                old_oid = remote_oid

    return BranchPlan(
        seed_ref=seed_ref,
        auto_land_branch=target,
        source=source,
        host_context_branch=host_context_branch,
        expected_old_oid=old_oid,
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
    if mode in {"preserve", "current"}:
        return mode
    return "preserve"


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
    if not isinstance(value, str):
        return None
    branch = value.strip()
    if not branch:
        return None
    if not gitops.valid_branch_name(repo_root, branch):
        return None
    return branch
