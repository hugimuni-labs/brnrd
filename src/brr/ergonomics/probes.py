"""Deterministic, daemon-side ergonomics probes (the ``probe`` layer).

Each probe is a pure-ish function over a ``ProbeContext`` returning a
list of ``Finding`` (issue + severity + structured detail). The
orchestrator wraps findings into full ``Record`` envelopes and hands
them to the configured proxy.

Three contracts, from the design:

- **Never gate the run.** A probe raising is swallowed; an
  ``error``-severity finding is a signal, not a refusal to run.
- **Cheap.** O(ms) each; the heaviest is a single ``docker image
  inspect``. No probe spawns a container or hits the network.
- **The vantage rule bounds the set.** Every probe observes a
  host/operator-vantage fact — something the sandboxed agent can't see
  for itself (a host file, cross-run state, pre-run resolution,
  installed-version drift). Anything the agent *can* check itself is
  reflection's job, not a probe's.

Routing is owner-aware: ``probe_run_prep`` resolves the proxy from
``RunContext.owner`` plus the ``ergonomics`` knob. The user-owned
default (``LogErgoProxy``) means probes run for everyone and surface
``warn``+ to the daemon log; ``ergonomics=off`` resolves to
``NullErgoProxy``, which the orchestrator short-circuits so that path
pays nothing.

Probes run host-side on the daemon, so env-sensitive checks are scoped
by ``ctx.name``. In-container probing for docker runs is deferred — it
needs a probe container (breaks the O(ms) contract) and most
in-container facts are agent-vantage anyway.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..run import Run
from ..envs import RunContext
from .record import Record
from .proxy import ErgoProxy, NullErgoProxy, resolve_proxy

# brr's installed package root (``src/brr``): probes.py lives at
# ``src/brr/ergonomics/probes.py``, so two parents up is the package dir
# that carries the bundled Dockerfile and the adopter template.
_PKG_ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_DOCKERFILE = _PKG_ROOT / "Dockerfile"
# The adopter template (Layer 0). An installed ``AGENTS.md`` is a tailored
# render of this; drift is measured block-by-block (Layer 1), never by
# whole-file equality — see ``probe_doc_drift``.
_BUNDLED_AGENTS_MD = _PKG_ROOT / "templates" / "constitution.md"


@dataclass
class Finding:
    """A single probe result before the common envelope is attached."""

    issue: str
    severity: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProbeContext:
    task: Run
    repo_root: Path
    brr_dir: Path
    cfg: dict[str, Any]
    ctx: RunContext


# ── individual probes ───────────────────────────────────────────────


def probe_github_auth(p: ProbeContext) -> list[Finding]:
    """Docker runs need an injected token; the container can't reach
    the host keyring. If github is in play and ``DockerEnv.prepare``
    resolved no token, gh CLI and HTTPS push will silently be
    unauthenticated — the exact confusion the design was born from.
    """
    if p.ctx.name != "docker" or not _github_in_play(p.task, p.brr_dir):
        return []
    if p.ctx.env_state.get("github_token"):
        return []
    return [
        Finding(
            "auth_unresolvable",
            "warn",
            {
                "what": "github_token",
                "hint": (
                    "no GitHub token resolved for the container; gh CLI "
                    "and HTTPS git push will run unauthenticated. Set "
                    "GITHUB_TOKEN/GH_TOKEN on the daemon, authenticate the "
                    "github gate, or make `gh auth token` resolvable."
                ),
            },
        )
    ]


def probe_stale_image(p: ProbeContext) -> list[Finding]:
    """Warn when the runner image predates brr's bundled Dockerfile.

    The bundled Dockerfile's mtime tracks the installed brr version, so
    "image older than the Dockerfile" approximates "image built before
    your current brr expected these tools" — the stale-image / missing-
    pytest failure mode. Skips silently when the image isn't local or
    docker isn't reachable (no inspect → no signal, not a false alarm).
    """
    if p.ctx.name != "docker":
        return []
    image = p.ctx.env_state.get("docker_image")
    if not image:
        return []
    created = _docker_image_created_epoch(str(image))
    if created is None or not _BUNDLED_DOCKERFILE.exists():
        return []
    df_mtime = _BUNDLED_DOCKERFILE.stat().st_mtime
    if created >= df_mtime:
        return []
    return [
        Finding(
            "stale_image",
            "warn",
            {
                "image": image,
                "image_built": _iso(created),
                "bundled_dockerfile_modified": _iso(df_mtime),
                "hint": (
                    "the runner image predates brr's current bundled "
                    "Dockerfile; rebuild it (`brnrd init -i`, or docker build "
                    "from the bundled Dockerfile) so the container carries "
                    "the brnrd CLI/runtime deps and tooling this brr version "
                    "expects."
                ),
            },
        )
    ]


def probe_worktree_buildup(p: ProbeContext) -> list[Finding]:
    """Finalize keeps worktrees on failure/dirty exit; they accumulate.

    A growing pile burns disk and makes ``git worktree list`` noisy.
    Warn past a threshold so the operator prunes.
    """
    from .. import worktree

    try:
        worktrees = worktree.list_worktrees(p.repo_root)
    except Exception:
        return []
    threshold = _int_cfg(p.cfg, "ergonomics.worktree_warn", 5)
    if len(worktrees) < threshold:
        return []
    return [
        Finding(
            "worktree_buildup",
            "warn",
            {
                "count": len(worktrees),
                "threshold": threshold,
                "paths": [str(w.path) for w in worktrees[:20]],
                "hint": (
                    "leftover run worktrees are piling up (kept on "
                    "failure/dirty exit). Inspect and remove stale ones to "
                    "reclaim space and cut noise."
                ),
            },
        )
    ]


def probe_disk(p: ProbeContext) -> list[Finding]:
    """Low free space on the repo filesystem breaks docker builds,
    worktree creation, and trace writes. Cheap to check, easy to miss.
    """
    try:
        usage = shutil.disk_usage(p.repo_root)
    except OSError:
        return []
    free_gb = usage.free / (1024 ** 3)
    warn_gb = _float_cfg(p.cfg, "ergonomics.disk_warn_gb", 2.0)
    if free_gb >= warn_gb:
        return []
    severity = "error" if free_gb < warn_gb / 2 else "warn"
    return [
        Finding(
            "low_disk",
            severity,
            {
                "free_gb": round(free_gb, 2),
                "threshold_gb": warn_gb,
                "path": str(p.repo_root),
                "hint": (
                    "low free disk on the repo filesystem; docker builds, "
                    "worktrees, and traces can fail. Free space or prune "
                    "images/worktrees."
                ),
            },
        )
    ]


def probe_doc_drift(p: ProbeContext) -> list[Finding]:
    """The adopter's ``AGENTS.md`` is a tailored render of the bundled
    adopter template. After ``pip install -U brr`` the shipped universals
    can move ahead of the installed copy, leaving agents on stale guidance.

    When both files carry versioned blocks (Layer 1) the comparison is
    **block-by-block**: only universal blocks whose version or content hash
    lags the template count as drift, so per-repo tailoring never
    false-fires and a stale universal can no longer hide inside a
    legitimately-diverged file. When neither side has blocks (a pre-L1
    install), it falls back to a whole-file compare. In brr's own repo the
    repo file is a symlink to the bundled one, so this never fires there.
    """
    if not _BUNDLED_AGENTS_MD.exists():
        return []
    repo_doc = p.repo_root / "AGENTS.md"
    if not repo_doc.exists():
        return []
    try:
        if repo_doc.resolve() == _BUNDLED_AGENTS_MD.resolve():
            return []
        bundled_text = _BUNDLED_AGENTS_MD.read_text(encoding="utf-8")
        repo_text = repo_doc.read_text(encoding="utf-8")
    except OSError:
        return []

    from .. import constitution

    drift = constitution.block_drift(repo_text, bundled_text)
    if drift:
        stale = ", ".join(
            f"{d.id} (v{d.installed_version}→v{d.template_version})" for d in drift
        )
        return [
            Finding(
                "drifted_bundled_docs",
                "info",
                {
                    "doc": "AGENTS.md",
                    "stale_blocks": stale,
                    "block_count": len(drift),
                    "hint": (
                        f"{len(drift)} universal block(s) in this repo's "
                        f"AGENTS.md lag the template bundled with the installed "
                        f"brr ({stale}). Re-sync those blocks so agents read "
                        "current guidance; per-repo sections are untouched."
                    ),
                },
            )
        ]

    # Pre-L1 fallback: no blocks on either side ⇒ whole-file compare.
    if constitution.block_map(bundled_text) or constitution.block_map(repo_text):
        return []
    if bundled_text == repo_text:
        return []
    return [
        Finding(
            "drifted_bundled_docs",
            "info",
            {
                "doc": "AGENTS.md",
                "bundled_revision": _revision_line(bundled_text),
                "repo_revision": _revision_line(repo_text),
                "hint": (
                    "this repo's AGENTS.md differs from the playbook bundled "
                    "with the installed brr. If brr was upgraded, re-sync the "
                    "playbook so agents read current guidance."
                ),
            },
        )
    ]


# Order is presentation-only; all probes run every run-prep. Every
# probe here observes a host/operator-vantage fact (the vantage rule in
# kb/design-agent-ergonomics.md): something the agent in its sandbox
# structurally can't see for itself. Checks the agent *can* run itself
# (e.g. a tool on its own PATH) belong to reflection, not here — that's
# why ``missing_tool`` was retired.
_PROBES: tuple[Callable[[ProbeContext], list[Finding]], ...] = (
    probe_stale_image,
    probe_github_auth,
    probe_worktree_buildup,
    probe_disk,
    probe_doc_drift,
)


# ── orchestration ───────────────────────────────────────────────────


def probe_run_prep(
    *,
    task: Run,
    repo_root: Path,
    brr_dir: Path,
    cfg: dict[str, Any],
    ctx: RunContext,
) -> list[Record]:
    """Resolve the proxy (owner-aware) and run the run-prep probe set.

    Short-circuits to an empty result on ``NullErgoProxy`` — i.e.
    ``ergonomics=off`` or an operator-owned run — so those paths run no
    probes at all. Otherwise the proxy is ``LogErgoProxy`` (the
    user-owned default) or ``LocalErgoProxy``. Safe to call from the
    daemon hot path: every failure mode is swallowed and returns ``[]``.
    """
    try:
        proxy = resolve_proxy(cfg, brr_dir, owner=getattr(ctx, "owner", "user"))
    except Exception:
        return []
    if isinstance(proxy, NullErgoProxy):
        return []
    return run_probes(
        task=task,
        repo_root=repo_root,
        brr_dir=brr_dir,
        cfg=cfg,
        ctx=ctx,
        proxy=proxy,
    )


def run_probes(
    *,
    task: Run,
    repo_root: Path,
    brr_dir: Path,
    cfg: dict[str, Any],
    ctx: RunContext,
    proxy: ErgoProxy,
) -> list[Record]:
    """Run every probe, emit findings to *proxy*, return the records.

    Exposed separately from ``probe_run_prep`` so tests can drive it
    with an explicit (non-null) proxy without touching config.
    """
    from .. import __version__

    pctx = ProbeContext(
        task=task, repo_root=repo_root, brr_dir=brr_dir, cfg=cfg, ctx=ctx
    )
    envelope = {
        "project_id": _project_id(repo_root),
        "run_id": task.id,
        "env": ctx.name,
        "image": ctx.env_state.get("docker_image"),
        "source": task.source or None,
        "daemon_version": __version__,
    }
    records: list[Record] = []
    for probe in _PROBES:
        try:
            findings = probe(pctx) or []
        except Exception:
            continue
        for finding in findings:
            record = Record(
                kind="probe",
                issue=finding.issue,
                severity=finding.severity,
                detail=finding.detail,
                **envelope,
            )
            try:
                proxy.emit(record)
            except Exception:
                continue
            records.append(record)
    return records


# ── helpers ─────────────────────────────────────────────────────────


def _github_in_play(task: Run, brr_dir: Path) -> bool:
    if (task.source or "") == "github":
        return True
    return (brr_dir / "gates" / "github.json").exists()


def _docker_image_created_epoch(image: str) -> float | None:
    if shutil.which("docker") is None:
        return None
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Created}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _parse_docker_time(result.stdout.strip())


_DOCKER_TIME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$"
)


def _parse_docker_time(value: str) -> float | None:
    """Parse docker's RFC3339 ``.Created`` into an epoch float.

    Docker emits up to nanosecond precision and a trailing ``Z`` that
    older ``datetime.fromisoformat`` can't ingest; clamp the fraction to
    microseconds and normalise the zone before parsing.
    """
    if not value:
        return None
    match = _DOCKER_TIME_RE.match(value.strip())
    if not match:
        return None
    base, frac, zone = match.group(1), match.group(2), match.group(3)
    text = base
    if frac:
        text += "." + frac[:6]
    if zone in (None, "Z", "z"):
        text += "+00:00"
    elif ":" not in zone:
        text += zone[:3] + ":" + zone[3:]
    else:
        text += zone
    from datetime import datetime

    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _revision_line(text: str) -> str:
    for line in text.splitlines():
        if "Revision:" in line:
            return line.strip().lstrip("> ").strip()
    return ""


def _project_id(repo_root: Path) -> str:
    """Best-effort ``owner/repo`` from the origin remote, else dir name.

    Local-store records don't strictly need a stable project id (the
    store is per-repo already); brnrd rollups will. Cheap to compute
    now so the field is populated consistently.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return repo_root.name
    url = result.stdout.strip()
    if url:
        from .. import forges

        parsed = forges.parse_remote(url)
        if parsed:
            _host, owner, repo = parsed
            return f"{owner}/{repo}"
    return repo_root.name


def _int_cfg(cfg: dict[str, Any], key: str, default: int) -> int:
    raw = cfg.get(key, cfg.get(key.replace(".", "_"), default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float_cfg(cfg: dict[str, Any], key: str, default: float) -> float:
    raw = cfg.get(key, cfg.get(key.replace(".", "_"), default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default
