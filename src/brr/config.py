"""Config — flat key=value reader for ``.brr/config``, split by trust domain.

brr-specific settings live in ``.brr/config`` (gitignored), not in
AGENTS.md.  AGENTS.md is a pure markdown playbook — universal across
tools.  This module reads the flat config file only.

Issue #533: every docker/solitary container bind-mounts the repo
read-write, so a run — including an *untrusted*-tier run routed into
solitary by #524 — can write ``.brr/config`` from inside its own
containment, and the daemon reads that file host-side for the next
run. A handful of keys are load-bearing for containment itself
(``runner_cmd`` is arbitrary argv executed on the host; ``trust.*`` picks
where the *next* stranger's event runs; ``docker.*`` / ``solitary.*``
size the isolation the trust decision leans on; ``environment`` / ``env``
/ ``default_env`` pick the env policy trust routing folds through) — a
config surface that can rewrite its own constraints from inside them is
no constraint at all.

So this module splits config into two trust domains:

- **repo config** (``.brr/config``) — every ordinary per-repo preference
  (``shell``, ``core``, ``runner.timeout_seconds``, ...), read from the
  repo and therefore writable by anything that can write the repo,
  including an untrusted-tier run's own bind mount.
- **security config** (``<home root>/security.config``) — the handful of
  keys above, read *only* from the daemon-owned home directory, which
  sits outside every run's mount. ``brnrd config promote`` (``cli.py``)
  moves recognised keys from the former into the latter, once, operator
  -run.

``load_config`` returns the merged view most of this codebase already
expects (repo config, minus any security key, plus the security config
override) so its 50+ call sites are unaffected. A security key set in
``.brr/config`` is **ignored, never honoured** — and that must never be
silent: ``load_config_report`` returns the ignored key names alongside
the merged config so a caller (today: the daemon, at run-dispatch time)
can log a warning and surface a portal notice. See ``daemon.py``'s
``_run_worker`` for where that happens.

Deliberately *not* security keys: ``home.path`` / ``home.kind`` /
``account.id`` / ``account_id`` / ``forge.identity`` and friends. Those
are *locating* keys — ``account.resolve_context`` needs to read them
from the repo config to find the home in the first place, so keeping
them repo-side is circular by necessity, not an oversight. A poisoned
repo config can still redirect which home (and therefore which
``security.config``) a run resolves to; that residual hole is named,
not closed, on issue #533 and in ``account.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SECURITY_CONFIG_FILENAME = "security.config"

# Resolved ``security.config`` paths, keyed by (repo root, raw repo-config
# contents). Review fixup on the first #533 draft, which resolved the home
# on *every* ``load_config`` call. Measured on an otherwise-idle machine,
# 200 warm calls each:
#
#     main                     0.037 ms/call
#     draft (no cache)         4.047 ms/call   ~109x
#     with this cache          0.094 ms/call
#
# The draft's cost is ``account.resolve_context`` → ``repo_label`` →
# ``gitops.default_remote``/``remote_url`` — two `git` subprocess spawns
# per call. ``load_config`` was a pure file read and is called 13 times by
# ``prompts.py`` alone while assembling one wake, so the draft put ~26
# subprocess spawns on the hottest path in the product; it showed up as
# the +7% full-suite wall time the worker reported and correctly flagged.
#
# Keying on the raw repo config means any edit to a locating key
# (``home.path``, ``account.id``, ...) misses the cache and re-resolves,
# which is the only input a caller can change deliberately. The residual
# staleness is a *git remote* change mid-process — `repo_label` reads it —
# which in practice is followed by a daemon restart anyway. Existence of
# the file is never cached: ``_read_flat`` stats it on every call, so
# ``config promote`` takes effect immediately.
_SECURITY_PATH_CACHE: dict[tuple[str, tuple[tuple[str, str], ...]], Path | None] = {}

# Exact key names that are security-defining regardless of any prefix
# match below. ``runner_cmd`` has no dotted/underscore twin (it's already
# flat); the env-policy keys are read from either the repo config or an
# event, so all three spellings the resolvers accept are listed here (see
# ``run.py::_cfg_environment_policy``).
_SECURITY_EXACT_KEYS = frozenset({"runner_cmd", "environment", "env", "default_env"})

# Prefix families that are security-defining. Both the dotted (``trust.``)
# and underscore (``trust_``) spellings are listed because the readers
# accept both (``trust.py::_cfg_str``, ``envs/__init__.py``'s docker/
# solitary config lookups) — a repo author writing the underscore form
# must not slip past a dotted-only filter.
_SECURITY_PREFIXES = (
    "trust.", "trust_",
    "docker.", "docker_",
    "solitary.", "solitary_",
)


def is_security_key(key: str) -> bool:
    """Return whether *key* is security-defining (issue #533).

    Security-defining keys decide *where and with what authority* a run
    executes — the trust tier, the isolation backend, the argv a runner
    invokes. They may load only from the daemon-owned security config,
    never from a repo-writable file. Everything else (timeouts, shell/
    core preference, spawn ceilings, ...) is a benign per-repo
    preference and stays in ``.brr/config``.
    """
    if key in _SECURITY_EXACT_KEYS:
        return True
    return key.startswith(_SECURITY_PREFIXES)


def _parse_value(val: str) -> Any:
    """Coerce a string value to bool / int / str."""
    if val in ("true", "True"):
        return True
    if val in ("false", "False"):
        return False
    try:
        return int(val)
    except ValueError:
        return val


def _read_flat(path: Path) -> dict[str, Any]:
    """Parse one flat ``key=value`` file. A missing file reads as ``{}``."""
    if not path.exists():
        return {}
    result: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, val = line.partition("=")
        if not sep:
            continue
        result[key.strip()] = _parse_value(val.strip())
    return result


def _write_flat(path: Path, cfg: dict[str, Any], *, mode: int | None = None) -> None:
    """Write *cfg* as flat ``key=value`` lines, atomically.

    ``mode`` is applied to the file after the atomic replace (not just the
    temp file) so an existing file's looser permissions don't survive the
    rewrite — used for ``security.config``, which may carry no secrets
    today but is exactly the kind of file that grows one.
    """
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in cfg.items()]
    text = "\n".join(lines) + ("\n" if lines else "")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    if mode is not None:
        os.chmod(path, mode)


def repo_config_path(repo_root: Path) -> Path:
    """Return the repo-side ``.brr/config`` path."""
    from . import gitops

    return gitops.shared_brr_dir(repo_root) / "config"


def _canonical_repo_root(repo_root: Path) -> Path:
    """The repo's *main* worktree root, given any of its worktrees.

    Review fixup on the first #533 draft, and the one defect that would
    have shipped dark. ``account._connected_account_id`` falls back to
    matching the account repo registry by **exact path**
    (``registered.resolve() == resolved_repo``). A linked worktree lives
    at a different path, so it never matches, ``resolve_context`` falls
    through to ``kind="project"``, and the home resolves to
    ``…/state/brnrd/projects/<slug>-<hash>/home`` instead of the
    account's. Measured on this account:

        host checkout  → …/accounts/acc_bdda…/home     (kind=account)
        linked worktree→ …/projects/hugimuni-labs__brnrd-2521ecb6a9/home

    Both resolve the *same* ``shared_brr_dir``, so the repo identity was
    never in doubt — only the path used to look it up. Left unfixed,
    every run in a ``worktree`` environment would look for
    ``security.config`` in a home nobody writes, find nothing, and run
    with every security key silently unset — the split failing open in
    exactly the environment it matters most. It passes tests and passes a
    live check on a ``host``-environment account, which is this one.

    ``shared_brr_dir`` already resolves a worktree to the main checkout's
    ``.brr``, so its parent is the canonical root. Falls back to
    *repo_root* unchanged if that lookup fails or doesn't look like a
    repo root.
    """
    from . import gitops

    try:
        candidate = gitops.shared_brr_dir(repo_root).parent
    except Exception:
        return repo_root
    return candidate if candidate.is_dir() else repo_root


def security_config_path(
    repo_root: Path, repo_cfg: dict[str, Any] | None = None
) -> Path | None:
    """Return the daemon-owned ``security.config`` path, or ``None``.

    Resolved via ``account.resolve_context`` against the *raw repo
    config* (``repo_cfg`` — read straight off disk, not through
    ``load_config``/``load_config_report``): resolution only consults the
    locating keys (``home.path``, ``account.id``, ...), which stay
    repo-readable by design (see the module docstring), so this cannot
    re-enter ``load_config``. ``create=False`` — locating the security
    domain must never have the side effect of provisioning a home; that
    stays the daemon's own startup path and ``brnrd config promote``'s
    job. Returns ``None`` when the home can't be resolved at all (a
    reserved label, mid-init odd states, ...); callers treat that as "no
    security overrides available", which is the fail-closed direction —
    a repo-side security key is still ignored either way.
    """
    from . import account

    repo_root = _canonical_repo_root(repo_root)
    cache_key = (
        str(repo_root),
        tuple(sorted((str(k), str(v)) for k, v in (repo_cfg or {}).items())),
    )
    if cache_key in _SECURITY_PATH_CACHE:
        return _SECURITY_PATH_CACHE[cache_key]

    try:
        ctx = account.resolve_context(repo_root, repo_cfg or {}, create=False)
    except Exception:
        resolved = None
    else:
        resolved = account.context_home_root(ctx) / SECURITY_CONFIG_FILENAME
    _SECURITY_PATH_CACHE[cache_key] = resolved
    return resolved


def load_config_report(repo_root: Path) -> tuple[dict[str, Any], list[str]]:
    """Load the merged config view, and report ignored repo-side security keys.

    Returns ``(cfg, ignored_keys)``:

    - ``cfg`` — repo config with any security key stripped, overlaid with
      ``security.config`` (the security config wins on overlap, though in
      practice the two domains are disjoint by construction).
    - ``ignored_keys`` — sorted repo-config key names that matched
      ``is_security_key`` and were therefore dropped, not merged. Empty
      when the repo config set none. This is the visibility half of
      #533: a security key in ``.brr/config`` must never be silently
      honoured *or* silently dropped — callers that care (today:
      ``daemon.py`` at run-dispatch time) log a WARNING and surface a
      portal notice from this list.
    """
    repo_cfg = _read_flat(repo_config_path(repo_root))
    ignored = sorted(key for key in repo_cfg if is_security_key(key))
    ignored_set = set(ignored)
    merged = {k: v for k, v in repo_cfg.items() if k not in ignored_set}

    sec_path = security_config_path(repo_root, repo_cfg)
    if sec_path is not None:
        merged.update(_read_flat(sec_path))

    return merged, ignored


def load_config(repo_root: Path) -> dict[str, Any]:
    """Load brnrd config from ``.brr/config`` in the given repo root.

    The merged view: repo config minus security-defining keys, plus the
    daemon-owned security config override (issue #533). Plain ``dict``
    return type on purpose — see ``load_config_report`` for the ignored-
    keys visibility this repo's 50+ ``load_config`` call sites don't need
    to know about.
    """
    return load_config_report(repo_root)[0]


def write_config(repo_root: Path, cfg: dict[str, Any]) -> None:
    """Write config to ``.brr/config``.

    Repo-side only — this never writes ``security.config``. A caller
    that hands this a dict containing a security key is writing it to
    the domain ``load_config`` ignores; that's exactly the shape #533
    closes, so it is allowed to happen (config-change proposals already
    allowlist which keys they'll write here — ``daemon.py``'s
    ``_CONFIG_CHANGE_ALLOWED_KEYS``) and simply has no effect once
    written.
    """
    _write_flat(repo_config_path(repo_root), cfg)


# ── ``brnrd config promote`` — the one-time repo→security migration ────


class ConfigPromoteError(RuntimeError):
    """A promote plan that must not be applied without operator intervention."""


@dataclass(frozen=True)
class PromotePlan:
    """What ``brnrd config promote`` would do, computed without touching disk.

    ``security_path`` is ``None`` when the daemon-owned home can't be
    resolved at all — nothing to promote into, distinct from "nothing to
    move" (``moves`` empty, the ordinary idempotent-rerun case).
    ``conflicts`` names every key that's already in ``security.config``
    with a *different* value than ``.brr/config`` holds; applying over a
    conflict requires ``force=True``.
    """

    security_path: Path | None
    moves: dict[str, Any] = field(default_factory=dict)
    conflicts: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    remaining_repo_cfg: dict[str, Any] = field(default_factory=dict)


def plan_promote(repo_root: Path) -> PromotePlan:
    """Compute the ``config promote`` plan for *repo_root*. Pure — touches nothing."""
    repo_cfg = _read_flat(repo_config_path(repo_root))
    to_move = {k: v for k, v in repo_cfg.items() if is_security_key(k)}
    sec_path = security_config_path(repo_root, repo_cfg)
    existing = _read_flat(sec_path) if sec_path is not None else {}
    conflicts = {
        k: (existing[k], v)
        for k, v in to_move.items()
        if k in existing and existing[k] != v
    }
    remaining = {k: v for k, v in repo_cfg.items() if k not in to_move}
    return PromotePlan(
        security_path=sec_path,
        moves=to_move,
        conflicts=conflicts,
        remaining_repo_cfg=remaining,
    )


def apply_promote(repo_root: Path, plan: PromotePlan, *, force: bool = False) -> None:
    """Apply *plan* (from :func:`plan_promote`).

    Idempotent: rerunning after a successful promote recomputes an empty
    ``plan.moves`` (the keys are gone from ``.brr/config``), so applying
    it is a no-op. Raises :class:`ConfigPromoteError` rather than
    clobbering an existing differing ``security.config`` value — the CLI
    surfaces this as the ``--force``-required refusal.
    """
    if not plan.moves:
        # Nothing to move: correct even if `security_path` is also
        # unresolved — there's no destination to need in that case.
        return
    if plan.security_path is None:
        raise ConfigPromoteError(
            "could not resolve the daemon-owned home for this repo — "
            "nothing to promote into"
        )
    if plan.conflicts and not force:
        raise ConfigPromoteError(
            "security.config already has differing value(s) for: "
            + ", ".join(sorted(plan.conflicts))
        )
    existing = _read_flat(plan.security_path)
    merged_security = dict(existing)
    merged_security.update(plan.moves)
    _write_flat(plan.security_path, merged_security, mode=0o600)
    write_config(repo_root, plan.remaining_repo_cfg)
