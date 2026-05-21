"""Publish plan resolution for daemon tasks.

The plan is a thin pre-run record naming the ref the
``brr/<task-id>`` worktree branch sprouts from, the branch (if any)
the event expects work to land under on the remote, and the
remote-tracking oid captured at task start so a leased rebase push can
refuse to clobber a concurrent writer.

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
2. Fallback policy: seed from the repo default branch (falling back to
   host ``HEAD``) and set no expected publish target — committed task
   branches are preserved for human routing and published under their
   own name. ``branch.fallback=current`` (the old self-development knob
   that also fast-forwarded the host checkout) was removed when the
   publish kernel collapsed; legacy config values warn and downgrade to
   the preserve default.
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
class PublishPlan:
    """Pre-run publish plan resolved without asking the worker model."""

    seed_ref: str
    expected_publish_branch: str | None
    source: str
    host_context_branch: str | None
    expected_remote_oid: str | None = None

    def meta_items(self) -> dict[str, str]:
        """Return non-empty task metadata fields for this plan."""
        out: dict[str, str] = {
            "seed_ref": self.seed_ref,
            "branch_source": self.source,
        }
        if self.expected_publish_branch:
            out["expected_publish_branch"] = self.expected_publish_branch
        if self.host_context_branch:
            out["host_context_branch"] = self.host_context_branch
        if self.expected_remote_oid:
            out["expected_remote_oid"] = self.expected_remote_oid
        return out


def resolve_publish_plan(
    repo_root: Path,
    event: dict[str, Any],
    cfg: dict[str, Any],
) -> PublishPlan:
    """Resolve a publish plan from structured event state and policy.

    Deliberately does not look at conversation history, parse free-text
    instructions, or run an LLM. Anything beyond a structured event
    field belongs to the worker agent.
    """
    host_branch = gitops.current_branch(repo_root)
    host_context = host_branch if host_branch != "HEAD" else None
    default_seed = _default_seed(repo_root, host_branch)

    for key in _STRUCTURED_BRANCH_KEYS:
        candidate = _event_branch_candidate(
            repo_root, key, event.get(key),
        )
        if candidate:
            return _plan_for_event_target(
                repo_root,
                candidate,
                source=f"event:{key}",
                host_context_branch=host_context,
                default_seed=default_seed,
            )

    _warn_on_legacy_fallback(cfg)
    return PublishPlan(
        seed_ref=default_seed,
        expected_publish_branch=None,
        source="fallback:preserve",
        host_context_branch=host_context,
    )


def _plan_for_event_target(
    repo_root: Path,
    target: str,
    *,
    source: str,
    host_context_branch: str | None,
    default_seed: str,
) -> PublishPlan:
    """Resolve seed + remote lease anchor for an event-named *target*.

    The seed prefers ``<remote>/<target>`` when that tracking ref
    exists: the daemon's pre-task sync is ff-only and refuses on
    diverged history, so without this preference the worker would seed
    from a stale local branch and produce a divergent, unpushable
    history. Anchoring to the remote ref guarantees the worker sprouts
    from the forge-visible state regardless of how stale the host's
    local branch is.

    The expected remote oid is captured from the remote-tracking ref at
    task start. It only ever feeds a force-with-lease push when the
    agent rewrote that branch locally (the PR-rebase case); plain
    pushes never use it.
    """
    seed_ref = target if gitops.branch_head(repo_root, target) else default_seed
    expected_remote_oid: str | None = None

    remote = gitops.default_remote(repo_root)
    if remote:
        remote_ref = f"{remote}/{target}"
        remote_oid = gitops.rev_parse(repo_root, remote_ref)
        if remote_oid:
            seed_ref = remote_ref
            expected_remote_oid = remote_oid

    return PublishPlan(
        seed_ref=seed_ref,
        expected_publish_branch=target,
        source=source,
        host_context_branch=host_context_branch,
        expected_remote_oid=expected_remote_oid,
    )


def _default_seed(repo_root: Path, host_branch: str) -> str:
    default_branch = gitops.default_branch(repo_root)
    if default_branch:
        return default_branch
    if host_branch and host_branch != "HEAD":
        return host_branch
    return "HEAD"


_LEGACY_FALLBACK_WARNED = False


def _warn_on_legacy_fallback(cfg: dict[str, Any]) -> None:
    """One-shot warning when ``branch.fallback`` carries a non-preserve value.

    The publish kernel only supports ``preserve``; older configs may
    still set ``current`` / ``inbox`` / ``default``. Silently
    downgrading would hide the change from operators inheriting an old
    file, so we surface it once per process. The warning is a print to
    stderr-equivalent (the daemon prints to stdout for operator-visible
    notices); promoting to ``logging`` would require a logger setup
    this module deliberately avoids.
    """
    global _LEGACY_FALLBACK_WARNED
    if _LEGACY_FALLBACK_WARNED:
        return
    raw = cfg.get("branch.fallback", cfg.get("branch_fallback", "preserve"))
    mode = str(raw).strip().lower()
    if mode and mode != "preserve":
        print(
            f"[brr] warning: branch.fallback={raw!r} is no longer "
            "supported (only 'preserve' remains); ignoring."
        )
        _LEGACY_FALLBACK_WARNED = True


def _event_branch_candidate(
    repo_root: Path,
    key: str,
    value: Any,
) -> str | None:
    if key == "branch" and isinstance(value, str):
        legacy = value.strip().lower()
        if legacy in {"", "auto", "task", "none", "current"}:
            return None
    if not isinstance(value, str):
        return None
    branch = value.strip()
    if not branch:
        return None
    if not gitops.valid_branch_name(repo_root, branch):
        return None
    return branch
