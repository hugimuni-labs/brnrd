"""Runner — shell out to AI CLIs, one run at a time.

brr doesn't do AI work itself. It delegates to whatever runner CLI the
user has installed (claude, codex, gemini, or any command on PATH).
Profiles are project-owned data (``.brr/runners.md``), with bundled
defaults kept for first-run convenience. Prompt assembly lives in
:mod:`brr.prompts`. This module is the plumbing: runner detection,
``RunnerInvocation`` and ``RunnerResult`` types, subprocess execution,
trace persistence, and the ``TaskRunner`` class for serial execution in
a background thread.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import random
import string
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_profiles_cache: dict[str, dict[str, Any]] | None = None
_profiles_cache_key: str | None = None

# Live runner subprocesses, keyed by invocation label. The daemon runs the
# resident's thought *and* up to ``spawn.max_concurrent`` worker children in
# one process, each invoking its own runner subprocess concurrently — a
# single module-global handle (the pre-2026-07-18 shape) meant a budget kill
# for one run could terminate a *different* run's process, and a finishing
# spawn nulled the handle out from under a still-live sibling. Labels are
# ``{event-id}-attempt-{n}`` for daemon runs, so a prefix match on
# ``{event-id}-`` addresses "whatever process this run is currently driving".
_active_procs: dict[str, subprocess.Popen] = {}
_proc_lock = threading.Lock()


def _register_active_proc(label: str, proc: subprocess.Popen) -> None:
    with _proc_lock:
        _active_procs[label] = proc


def _clear_active_proc(label: str) -> None:
    with _proc_lock:
        _active_procs.pop(label, None)


def _kill_procs(procs: list[subprocess.Popen]) -> bool:
    killed = False
    for proc in procs:
        if proc.poll() is not None:
            continue
        try:
            proc.kill()
        except OSError:
            continue
        killed = True
    return killed


def kill_matching(label_prefix: str) -> bool:
    """Terminate live runner subprocess(es) whose label starts with *prefix*.

    The targeted sibling of :func:`kill_active`: the daemon's budget
    enforcement kills exactly its own invocation (exact label), and the
    ``stop:`` dispatch verb kills a spawned child's current attempt by its
    event-id prefix. Returns ``True`` when at least one live process was
    signalled. Safe from any thread.
    """
    if not label_prefix:
        return False
    with _proc_lock:
        procs = [
            proc for label, proc in _active_procs.items()
            if label.startswith(label_prefix)
        ]
    return _kill_procs(procs)


# Environment variables an agent CLI sets for *its own* session that must not
# leak into a runner subprocess brr spawns. The killer is
# ``CLAUDE_CODE_SAFE_MODE``: when a Claude session spawns a child ``claude``
# (e.g. the daemon was launched from inside Claude Code, or the runner itself
# probes the CLI), the child inherits safe mode, which *silently drops
# settings-file hooks* while logging a reassuring "managed settings-file hooks
# still run". That single inherited var is what made earlier firing tests
# conclude "Claude hooks don't fire under --print" — a false negative from a
# contaminated env (verified 2026-06-27; see kb/design-runner-back-channel.md).
# The session-identity vars are stripped too so a spawned runner starts as a
# fresh top-level session rather than a confused nested child.
_RUNNER_ENV_CONTAMINANTS: frozenset[str] = frozenset(
    {
        "CLAUDE_CODE_SAFE_MODE",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
        "CLAUDE_EFFORT",
        "AI_AGENT",
    }
)


def _ensure_publishing_token_fresh() -> None:
    """Top up the managed GitHub token before a runner snapshots it.

    The runner env is a *copy* of the daemon's environment taken once, at
    dispatch, and held for the life of the run. The cloud gate's own renewal
    is paced for the daemon — it renews only when the credential is nearly
    expired — which is correct for a long-lived process that can always ask
    again, and wrong for a runner that cannot. Without this, a dispatched run
    inherits between 10 and 60 minutes of token life at random and finds out
    which by failing to push.

    Deliberately silent and never fatal: no cloud gate, no network, or a
    brnrd mid-deploy all leave the run with exactly the credential it would
    have had anyway. A run that cannot push is a bad outcome; a run that
    cannot *start* is a worse one.
    """
    try:
        from .gates import cloud

        cloud.ensure_publishing_credential_fresh()
    except Exception:
        return


def clean_runner_environ() -> dict[str, str]:
    """A copy of ``os.environ`` with parent-agent-session leakage removed.

    The base env every runner subprocess starts from. Stripping the
    contaminants above keeps a spawned agent CLI from inheriting the *parent*
    agent's session identity and, critically, its safe-mode flag — so hooks,
    skills, and plugins behave as they would for a normal top-level run.
    """
    _ensure_publishing_token_fresh()
    cleaned = {
        k: v for k, v in os.environ.items() if k not in _RUNNER_ENV_CONTAMINANTS
    }
    # GitHub CLI defines GH_TOKEN as the automation-specific override for
    # GITHUB_TOKEN.  Keep that choice unambiguous for every tool in the runner,
    # including tools that happen to inspect GITHUB_TOKEN first: when the
    # operator supplies GH_TOKEN, inherited human credentials must not leak
    # into the child alongside it.
    managed_github_token = cleaned.pop("BRNRD_MANAGED_GITHUB_TOKEN", "")
    if cleaned.get("GH_TOKEN"):
        # Operator-supplied publishing identity: brnrd never minted it and
        # cannot date it, so the frozen snapshot is correct — there is no
        # daemon-side renewal for the runner to chase. Keep today's behaviour.
        # An ambient GH_CONFIG_DIR (the daemon exports one for its own gh
        # wiring) must not ride along: it would point gh at the managed
        # pointer while the operator's token owns this run's identity.
        cleaned.pop("GITHUB_TOKEN", None)
        cleaned.pop("GH_CONFIG_DIR", None)
        _inject_github_git_config(cleaned)
    elif managed_github_token:
        pointer_dir = _github_credential_pointer_dir()
        if pointer_dir is not None:
            # Hand the runner a *pointer*, not a value (issue #477): gh reads
            # the current token from the pointer dir's hosts.yml and git's
            # helper cats the token file at each push, so a daemon-side
            # renewal reaches a run already in flight. A frozen GH_TOKEN /
            # GITHUB_TOKEN would *override* the pointer at gh's precedence, so
            # neither is exported on this path.
            cleaned.pop("GITHUB_TOKEN", None)
            _inject_github_credential_pointer(cleaned, pointer_dir)
        else:
            # No pointer available in this process (no cloud gate minting it)
            # — fall back to the legacy frozen snapshot so a run still
            # authenticates, exactly as before the pointer existed. A stale
            # inherited GH_CONFIG_DIR would contradict the frozen-token
            # choice this branch just made, so it is stripped too.
            cleaned["GH_TOKEN"] = managed_github_token
            cleaned.pop("GITHUB_TOKEN", None)
            cleaned.pop("GH_CONFIG_DIR", None)
            _inject_github_git_config(cleaned)
    return cleaned


def _github_credential_pointer_dir() -> Path | None:
    """The daemon-refreshed GitHub credential pointer dir, if usable here.

    ``None`` unless a cloud gate in this process publishes the pointer *and*
    its ``token`` file already exists — otherwise there is nothing to point at
    and callers must fall back to the frozen-token path.
    """
    try:
        from .gates import cloud

        pointer_dir = cloud.github_credentials_dir()
    except Exception:
        return None
    if pointer_dir is None:
        return None
    return pointer_dir if (pointer_dir / "token").exists() else None


def _inject_github_credential_pointer(env: dict[str, str], pointer_dir: Path) -> None:
    """Point ``gh`` and Git at the daemon-refreshed credential dir (issue #477).

    ``GH_CONFIG_DIR`` makes ``gh`` read the current token from the pointer's
    ``hosts.yml`` at each invocation; the git credential helper cats the
    ``token`` file on each transport call. Both resolve the token at *use*
    time, so a mid-run renewal is picked up without re-exec.
    """
    token_path = pointer_dir / "token"
    env["GH_CONFIG_DIR"] = str(pointer_dir)
    try:
        offset = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        offset = 0
    pairs = (
        ("url.https://github.com/.insteadOf", "git@github.com:"),
        ("url.https://github.com/.insteadOf", "ssh://git@github.com/"),
        (
            "credential.helper",
            "!f() { test \"$1\" = get || exit 0; "
            "echo username=x-access-token; "
            f"echo \"password=$(cat {shlex.quote(str(token_path))})\"; }}; f",
        ),
    )
    env["GIT_CONFIG_COUNT"] = str(offset + len(pairs))
    for index, (key, value) in enumerate(pairs, start=offset):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value


def _inject_github_git_config(env: dict[str, str]) -> None:
    """Make GH_TOKEN authoritative for both ``gh`` and Git transport."""
    try:
        offset = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        offset = 0
    pairs = (
        ("url.https://github.com/.insteadOf", "git@github.com:"),
        ("url.https://github.com/.insteadOf", "ssh://git@github.com/"),
        (
            "credential.helper",
            "!f() { test \"$1\" = get || exit 0; "
            "echo username=x-access-token; echo \"password=$GH_TOKEN\"; }; f",
        ),
    )
    env["GIT_CONFIG_COUNT"] = str(offset + len(pairs))
    for index, (key, value) in enumerate(pairs, start=offset):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value


def kill_active() -> bool:
    """Terminate every in-flight runner subprocess.

    Shutdown semantics: daemon teardown uses this to reclaim the resident
    slot *and* any live concurrent spawns promptly instead of waiting out
    their (long, possibly extended) budgets. Per-run enforcement (budget
    kill, the ``stop:`` verb) goes through :func:`kill_matching` so one
    run's deadline can never terminate a sibling's process. Returns
    ``True`` when at least one live process was signalled. Safe from any
    thread — handles are read under ``_proc_lock`` and killed outside it.
    """
    with _proc_lock:
        procs = list(_active_procs.values())
    return _kill_procs(procs)


DEFAULT_RUNNER_TIMEOUT = 7200


def runner_timeout(cfg: dict[str, Any] | None) -> int:
    """Return the runner subprocess timeout in seconds.

    Reads ``runner.timeout_seconds`` (or legacy ``runner_timeout_seconds``)
    from *cfg*; falls back to :data:`DEFAULT_RUNNER_TIMEOUT`. xhigh-reasoning
    models like gpt-5.5 routinely need 10+ minutes on a complex task, and the
    old 600s default was killing live work mid-run; a 1h follow-up default
    still cut long implementation/research sessions short without a human
    knowing to extend `.keepalive`, so this is now a 2h soft ceiling rather
    than a target (2026-07-06; the daemon's hard cap auto-scales off this —
    `max(budget*4, budget+3600)` — so 7200s here still yields an 8h backstop
    for runaway-process reclamation).
    """
    if not cfg:
        return DEFAULT_RUNNER_TIMEOUT
    raw = cfg.get("runner.timeout_seconds", cfg.get("runner_timeout_seconds"))
    if raw is None:
        return DEFAULT_RUNNER_TIMEOUT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RUNNER_TIMEOUT
    return value if value > 0 else DEFAULT_RUNNER_TIMEOUT


def _trace_id() -> str:
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{ts}-{rand}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return slug or "invocation"


@dataclass(frozen=True)
class RunnerArtifactSpec:
    """A file the runner invocation is expected to produce."""

    path: Path
    label: str | None = None
    copy_to_trace: bool = True


@dataclass(frozen=True)
class RunnerArtifactRecord:
    """Observed status for one expected runner artifact."""

    path: Path
    label: str
    exists: bool
    trace_copy: Path | None = None


@dataclass(frozen=True)
class RunnerInvocation:
    """Single runner invocation plus its validation contract."""

    kind: str
    label: str
    prompt: str
    repo_root: Path
    cwd: Path | None = None
    response_path: str | None = None
    required_artifacts: list[RunnerArtifactSpec] = field(default_factory=list)
    # Wall-clock backstop for ``proc.communicate``. ``None`` falls back to
    # ``runner_timeout(cfg)``. The daemon passes a generous hard cap here
    # and enforces the real (extensible) budget from its heartbeat — see
    # ``daemon._invoke_with_heartbeat`` and ``kill_matching``.
    timeout_seconds: int | None = None
    # Extra environment variables for the runner subprocess. Daemon runs
    # use this to expose live portal paths (BRR_PORTAL_STATE,
    # BRR_OUTBOX_DIR, etc.) without making the resident copy them out of
    # prose.
    env: dict[str, str] = field(default_factory=dict)
    # Extra argv tokens injected before the prompt on the profile path. The
    # daemon uses this for codex's argv-installed hooks (``-c hooks.*=…``);
    # ignored on the ``runner_cmd`` override path (a pinned command is honoured
    # verbatim).
    extra_runner_args: list[str] = field(default_factory=list)
    # The Core selected by policy. ``None``/``default`` means unpinned and
    # therefore cannot be attested. A concrete id must match the Shell result.
    expected_core: str | None = None
    # The immutable selection result. Daemon dispatch carries this through to
    # command construction instead of throwing it away and reloading a dict by
    # profile-name string in another module.
    selected_runner: "RunnerProfile | None" = None

    @property
    def trace_root(self) -> Path:
        from . import gitops

        return gitops.shared_brr_dir(self.repo_root) / "traces" / _slugify(self.kind)


@dataclass
class RunnerResult:
    """Runner subprocess result plus trace and artifact validation."""

    invocation: RunnerInvocation
    runner_name: str
    command: list[str]
    stdout: str
    stderr: str
    returncode: int
    trace_dir: Path | None
    artifacts: list[RunnerArtifactRecord]
    observed_core: str | None = None
    core_mismatch: bool | None = None
    # Child stderr before any display-safe redaction. Trace files are local
    # diagnostics, so they retain this exact capture while ``stderr`` is safe
    # to put in daemon packets and terminal replies.
    trace_stderr: str | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        """Whether the runner subprocess completed its work.

        Deliberately *not* a provenance verdict. ``core_mismatch`` answers a
        different question — did the Core we pinned produce this — and folding
        the two into one bit is what made a completed run report "I couldn't
        complete this run" beside its own ``done`` note (2026-07-20). A wrong
        Core makes an answer *suspect*, never *absent*: the work exists, it
        cost real quota, and the user judges it better holding it than holding
        nothing. Provenance rides as an alarm bit on the ledger, the run node,
        and a footer on the delivered reply — it no longer destroys output.
        """
        return self.returncode == 0

    @property
    def output(self) -> str:
        return self.stdout

    @property
    def missing_artifacts(self) -> list[RunnerArtifactRecord]:
        return [artifact for artifact in self.artifacts if not artifact.exists]

    @property
    def has_response(self) -> bool:
        """True iff the runner emitted a non-empty final reply on stdout.

        Only meaningful for invocations that ask brr to capture a
        response file (``invocation.response_path is not None``).
        """
        return bool(self.stdout and self.stdout.strip())

    @property
    def validation_ok(self) -> bool:
        """Structural validity of the invocation's contract — required
        artifacts only.

        An empty stdout is deliberately *not* a validation failure: the
        terminal stream is one delivery channel among several (outbox
        replies, commits, respawns), and whether a silent run succeeded is
        the daemon's success-signal call (``_result_satisfied_delivery``),
        not a per-invocation validity check. Requiring stdout here is what
        used to trigger a full re-run of an already-successful wake just to
        manufacture a terminal sentence (the request/response ceremony cut
        2026-07-16).
        """
        if not self.ok:
            return False
        if self.missing_artifacts:
            return False
        return True

    def retry_reason(self) -> str | None:
        """Return a retryable reason, or None.

        Only clean exits are retryable: when the runner subprocess exits 0
        but didn't produce the artifacts we expected, the next attempt may
        succeed (a stochastic "ran past the deliverable" case). Hard
        failures — non-zero exit, timeout — are not retryable here; the
        daemon's give-up path surfaces them with the captured error
        instead of paying for a duplicate expensive attempt.

        Empty stdout alone is *not* a retry reason: re-running a whole wake
        to extract a terminal message pays a full runner invocation for a
        sentence, and the daemon's Stop-hook boundary already warned the
        resident once when nothing had been communicated. A genuinely
        silent addressed run takes the give-up path and gets the daemon's
        terminal failure note instead.
        """
        if not self.ok:
            return None
        if self.missing_artifacts:
            labels = ", ".join(artifact.label for artifact in self.missing_artifacts)
            return f"missing required output(s): {labels}"
        return None

    def error_detail(self, *, limit: int = 500) -> str | None:
        """Truncated error text suitable for gate display, or None.

        Used by the daemon to bubble the runner's actual failure
        (stderr/stdout tail) up into the failed update packet so chat
        gates can show the operator something more useful than
        ``stage=run``.
        """
        if self.ok and self.validation_ok:
            return None
        source = self.stderr or self.stdout or ""
        prompt_lines = {
            line.strip() for line in self.invocation.prompt.splitlines()
            if line.strip()
        }
        lines = [
            line.strip() for line in source.splitlines()
            if line.strip() and line.strip() not in prompt_lines
        ]
        if not lines:
            return None
        detail = "\n".join(lines[-8:])
        if len(detail) <= limit:
            return detail
        tail = detail[-limit:]
        return f"…[truncated]{tail}" if len(detail) > limit else tail

    def raise_for_error(self) -> None:
        if self.ok:
            return
        detail = self.stderr.strip() or self.stdout.strip()
        if len(detail) > 500:
            detail = detail[:500] + " …[truncated]"
        raise RuntimeError(
            f"{self.command[0]} failed (exit {self.returncode}): "
            + (detail or "(no output)")
        )


def _profiles_source(repo_root: Path | None = None) -> tuple[str, str]:
    """Return ``(cache_key, frontmatter)``, preferring project-owned data."""
    from . import prompts

    if repo_root:
        from . import gitops

        try:
            brr_dir = gitops.shared_brr_dir(repo_root)
        except Exception:  # noqa: BLE001 - non-repo invocations use bundled defaults
            brr_dir = repo_root / ".brr"
        project_profiles = brr_dir / "runners.md"
        if project_profiles.exists():
            return (
                str(project_profiles.resolve()),
                project_profiles.read_text(encoding="utf-8"),
            )
        legacy_prompt_profiles = brr_dir / "prompts" / "runners.md"
        if legacy_prompt_profiles.exists():
            return (
                str(legacy_prompt_profiles.resolve()),
                legacy_prompt_profiles.read_text(encoding="utf-8"),
            )
    return ("bundled:runners.md", prompts.read_prompt("runners.md", None))


def _load_profiles(repo_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load runner profiles from project data or bundled defaults."""
    global _profiles_cache, _profiles_cache_key
    key, text = _profiles_source(repo_root)
    if _profiles_cache is not None and (
        _profiles_cache_key is None or _profiles_cache_key == key
    ):
        return _profiles_cache
    from . import protocol

    if text:
        _profiles_cache = protocol.parse_frontmatter(text)
    else:
        _profiles_cache = {}
    _profiles_cache_key = key
    return _profiles_cache


def _selection_profiles(
    repo_root: Path | None = None, *, probe: bool = True,
) -> dict[str, dict[str, Any]]:
    """Declared profiles plus generated bundled Core profiles.

    ``_load_profiles()`` is the active ``runners.md`` source. This view keeps
    those entries authoritative, then adds invokable profiles derived from the
    bundled Core registry for any Shell declared in that source. The resolver and
    command builder use this view so ``core=haiku`` can select ``claude-haiku``
    even when ``runners.md`` only declares the base ``claude`` Shell.

    ``probe=False`` skips CLI-help model discovery (``probe_shell_models``),
    which shells out to every declared Shell binary on a cold cache. Callers
    *inside the invoke path* must pass it: selection has already happened by
    then, and a subprocess born mid-invocation to enumerate models is a cost
    (and, under test fakes, a crash) nobody there asked for.
    """
    declared = dict(_load_profiles(repo_root))
    from . import runner_cores

    generated = runner_cores.generated_profile_entries(declared, probe=probe)
    merged: dict[str, dict[str, Any]] = {
        name: dict(profile) for name, profile in generated.items()
    }
    for name, profile in declared.items():
        twin = merged.get(name)
        if twin is None:
            merged[name] = dict(profile)
            continue
        # Declared wins per field; the registry twin fills what the
        # declaration omits (model, class, cost_rank, auth metadata).
        # A full replace here made a bare project override silently shed
        # all Core metadata and render as ``core=default`` in the catalog.
        record = dict(twin)
        record.pop("generated_core", None)
        record.update(profile)
        merged[name] = record
    return merged


def profile_metadata(
    name: str, repo_root: Path | None = None
) -> dict[str, Any] | None:
    """Return metadata for a declared or generated runner profile."""
    profile = _selection_profiles(repo_root).get(name)
    return dict(profile) if profile is not None else None


def runner_profile(name: str, repo_root: Path | None = None) -> "RunnerProfile":
    """Return the typed Runner value used by selection and invocation."""
    from . import runner_select

    profile = _selection_profiles(repo_root).get(name)
    if profile is None:
        return runner_select.implicit_runner(name)
    return runner_select.runner_from_profile(name, profile)


def _profile_binary(name: str, profiles: dict[str, dict[str, Any]]) -> str:
    profile = profiles.get(name) or {}
    return str(profile.get("binary") or name)


def profile_hooks_flavour(
    name: str, repo_root: Path | None = None
) -> str | None:
    """Return the runner's declared hook *flavour*, or None.

    Tier 2 of the runner interface (``kb/design-runner-back-channel.md``):
    a profile opts into the hooks back channel with a ``hooks: <flavour>``
    field naming the runner family (``claude`` / ``codex`` / ``gemini``)
    whose native hook config brr generates. This reads the *declared*
    intent from the profile; whether the runner is actually hooks-capable
    is a separate runtime precheck (settings location writable, native
    config present), so a caller wires hooks only after confirming the
    flavour here *and* passing that precheck.
    """
    profile = _selection_profiles(repo_root).get(name) or {}
    flavour = profile.get("hooks")
    if not flavour:
        return None
    flavour = str(flavour).strip().lower()
    return flavour or None


def _runner_available(name: str, profiles: dict[str, dict[str, Any]]) -> bool:
    """A profile is available when it is actually invokable right now.

    Two gates: the Shell binary must be on PATH, and a profile that declares
    an ``auth_env`` requirement (API-key auth variants such as
    ``claude-bare-api-only``) needs that variable present in the environment.
    Listing a keyless API-only profile as available produced doomed fallback
    spawns and a bloated Runner catalog in the wake prompt.
    """
    if shutil.which(_profile_binary(name, profiles)) is None:
        return False
    profile = profiles.get(name) or {}
    auth_env = str(profile.get("auth_env") or "").strip()
    if auth_env and not os.environ.get(auth_env):
        return False
    return True


def _compose_shell_core(
    shell_pin: str, core_pin: str, profiles: dict[str, dict[str, Any]]
) -> str | None:
    """Resolve ``shell=`` + ``core=`` to the Shell+Core profile both name.

    ``shell:`` + ``core:`` set together mean one thing everywhere they can
    be written (spawn/respawn frontmatter, .brr/config, a tap): *this Shell
    running this Core*. Until 2026-07-13 the exact-pin path returned the
    shell pin outright and ``core=`` degraded to a stderr warning — found
    live when a spawn requesting ``shell: codex`` + ``core: gpt-5.4``
    dispatched a child whose argv carried no model flag at all: it ran the
    Shell's config-default model (a *stronger*, costlier core than
    requested — the economics leak both ways). The 2026-07-09 warn was the
    visibility half; this is the resolution half.

    Returns the composed profile name, or ``None`` when composition is not
    possible — the shell pin already pins a *different* model (a genuine
    conflict, the caller warns), or no available profile of that shell
    family matches the core.
    """
    pin_profile = profiles.get(shell_pin) or {}
    pin_model = str(pin_profile.get("model") or "").strip().lower()
    core_lower = core_pin.strip().lower()
    if pin_model:
        if pin_model == core_lower or pin_model.startswith(core_lower):
            return shell_pin  # already the combined profile
        return None  # conflicting explicit pins — warn, shell wins
    shell_family = str(
        pin_profile.get("shell") or pin_profile.get("binary") or shell_pin
    ).strip()
    matches: list[tuple[str, dict[str, Any]]] = []
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        family = str(
            profile.get("shell") or profile.get("binary") or name
        ).strip()
        if family != shell_family:
            continue
        model = str(profile.get("model") or "").strip().lower()
        model_match = bool(model) and (
            model == core_lower or model.startswith(core_lower)
        )
        name_lower = name.lower()
        name_match = (
            name_lower == core_lower or name_lower.endswith(f"-{core_lower}")
        )
        if not (model_match or name_match):
            continue
        if not _runner_available(name, profiles):
            continue
        matches.append((name, profile))
    if not matches:
        return None

    def _rank(item: tuple[str, dict[str, Any]]) -> tuple[int, float, str]:
        name, profile = item
        model = str(profile.get("model") or "").strip().lower()
        exact = 0 if model == core_lower else 1
        try:
            cost = float(profile.get("cost_rank"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            cost = float("inf")
        return (exact, cost, name)

    return min(matches, key=_rank)[0]


def _warn_if_shell_shadows_core(
    shell_pin: str, core_pin: str, profiles: dict[str, dict[str, Any]]
) -> None:
    """Called when :func:`_compose_shell_core` found no composition: the
    shell pin names a profile pinned to a *different* model, or no
    available profile of the pinned shell's family matches ``core=``. The
    shell pin still wins (an exact pin must stay predictable); this makes
    the dropped core visible instead of silent. History: caught live
    2026-07-09 — several days of runs resolved to the base ``claude``
    profile while the operator believed ``core=`` had pinned
    ``claude-fable-5``; nothing failed, so nothing surfaced it until quota
    usage on the pinned model stopped moving.
    """
    profile = profiles.get(shell_pin) or {}
    model = str(profile.get("model") or "").strip().lower()
    core_lower = core_pin.strip().lower()
    if model and (model == core_lower or model.startswith(core_lower)):
        return  # shell_pin already names a profile pinned to this core
    resolved = model or "default"
    print(
        f"brr: shell={shell_pin!r} and core={core_pin!r} were both set, "
        f"but they don't compose — no available {shell_pin!r}-family "
        f"profile matches that core, so core= is not consulted. This run "
        f"resolves to model={resolved!r}, not {core_pin!r}. Check the core "
        "id against `brnrd runners list`, or drop shell= and let core= "
        "filter cost-aware auto-selection.",
        file=sys.stderr,
    )


def detect_runner(repo_root: Path | None = None) -> str | None:
    """Return the first available built-in runner CLI name, or None."""
    profiles = _load_profiles(repo_root)
    for name in profiles:
        if profiles.get(name, {}).get("binary"):
            continue
        if _runner_available(name, profiles):
            return name
    return None


def detect_all_runners(repo_root: Path | None = None) -> list[str]:
    """Return all available runner CLI names found on PATH."""
    profiles = _load_profiles(repo_root)
    return [name for name in profiles if _runner_available(name, profiles)]


def available_selection_runners(repo_root: Path | None = None) -> list["RunnerProfile"]:
    """Available declared/generated Runner profiles for selection policy."""
    from . import runner_select

    profiles = _selection_profiles(repo_root)
    out: list[runner_select.RunnerProfile] = []
    for name, profile in profiles.items():
        if _runner_available(name, profiles):
            out.append(runner_select.runner_from_profile(name, profile))
    return out


def available_runner_catalog(
    repo_root: Path | None = None,
    *,
    selected: str | None = None,
) -> list[dict[str, Any]]:
    """Unified catalog of all known Runner profiles — the one projection.

    Returns every profile (declared + Core-registry-generated), including
    those whose Shell binary is not currently on PATH.  Each row carries:

    - ``on_path`` (bool) — Shell binary found by :func:`shutil.which`
    - ``available`` (bool) — on_path AND auth_env satisfied
    - ``availability`` — ``"available"`` | ``"shell-not-found"`` | ``"auth-env-missing"``
    - ``stale`` (bool) — freshness_date older than 30 days
    - ``pin`` — exact model ID when set (overrides alias for ``--model``)
    - ``selected`` — True when this profile matches *selected*

    Unavailable rows are included with marks so callers (CLI, prompt, dashboard)
    can show them rather than silently omitting them.  The selector continues to
    operate only on available profiles; the catalog is the user/resident-facing
    surface, not the invocation path.

    Dedupe: when two rows share the same ``(shell, effective_model)`` pair
    (where effective_model = pin or model), the declared profile wins over a
    generated one; if both are the same kind, lower cost_rank wins.
    """
    from . import runner_cores as _rc

    profiles = _selection_profiles(repo_root)
    selected_name = str(selected or "").strip()
    rows: list[dict[str, Any]] = []
    for name, profile in profiles.items():
        record = _catalog_record(name, profile, selected_name, profiles)
        if record:
            rows.append(record)

    # Dedupe on (shell, effective_model): declared profile wins over generated.
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        shell_key = str(row.get("shell") or "").lower()
        model_key = str(row.get("pin") or row.get("model") or "").lower()
        key = (shell_key, model_key)
        if not key[0] or not key[1]:
            # No useful key — always include (e.g. undeclared shell entries)
            rows_key = ("", str(row.get("name") or ""))
            deduped.setdefault(rows_key, row)
            continue
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = row
            continue
        # Declared wins over generated.
        existing_gen = existing.get("generated_core", False)
        row_gen = row.get("generated_core", False)
        if existing_gen and not row_gen:
            deduped[key] = row  # row is declared → replace
        elif not existing_gen and row_gen:
            pass  # existing is declared → keep
        else:
            # Both same kind: lower cost_rank wins; tie → alphabetical name.
            ex_rank = existing.get("cost_rank")
            ro_rank = row.get("cost_rank")
            if ro_rank is not None and (
                ex_rank is None or ro_rank < ex_rank
            ):
                deduped[key] = row

    result = list(deduped.values())
    return sorted(
        result,
        key=lambda item: (
            not item.get("available", True),   # available rows first
            item.get("cost_rank") is None,
            item.get("cost_rank") if item.get("cost_rank") is not None else 0,
            str(item.get("name") or ""),
        ),
    )


def _catalog_record(
    name: str,
    profile: dict[str, Any] | None,
    selected: str,
    profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    import datetime

    from . import runner_cores as _rc
    from . import runner_select

    if not isinstance(profile, dict):
        return None
    runner_profile = runner_select.runner_from_profile(name, profile)
    shell = str(
        profile.get("shell") or profile.get("binary") or runner_profile.profile
    ).strip() or None

    # Availability
    on_path = shutil.which(str(shell or "").strip()) is not None
    auth_env = str(profile.get("auth_env") or "").strip()
    if not on_path:
        availability = "shell-not-found"
    elif auth_env and not os.environ.get(auth_env):
        availability = "auth-env-missing"
    else:
        availability = "available"
    is_available = availability == "available"

    # Staleness: freshness_date older than 30 days.
    stale = False
    freshness_date = str(profile.get("freshness_date") or "").strip() or None
    if freshness_date:
        try:
            fd = datetime.date.fromisoformat(freshness_date)
            stale = (datetime.date.today() - fd).days > 30
        except ValueError:
            pass

    pin = str(profile.get("pin") or "").strip() or None

    record: dict[str, Any] = {
        "name": name,
        "shell": shell,
        "model": runner_profile.model,
        "provider": runner_profile.provider,
        "owner": runner_profile.owner,
        "class": runner_profile.cost_class,
        "cost_rank": runner_profile.cost_rank,
        "quota_source": runner_profile.quota_source,
        "hooks": runner_profile.hooks,
        "auth_variant": str(profile.get("auth_variant") or "").strip() or None,
        "auth_env": auth_env or None,
        "capability_score": runner_profile.capability_score,
        "capability_source": runner_profile.capability_source,
        "capability_freshness": runner_profile.capability_freshness,
        "generated_core": bool(profile.get("generated_core")),
        "on_path": on_path,
        "available": is_available,
        "availability": availability,
        "stale": stale,
        "freshness_date": freshness_date,
        "selected": name == selected or runner_profile.profile == selected,
    }
    if pin:
        record["pin"] = pin
    return {key: value for key, value in record.items() if value is not None}


def fallback_runner(
    repo_root: Path,
    current: str,
    failure_kind: str | None,
    *,
    tried: list[str] | tuple[str, ...] = (),
) -> str | None:
    """Return a conservative local fallback Runner profile, if one exists."""
    from . import runner_select

    candidate = runner_select.automatic_fallback_runner(
        available_selection_runners(repo_root),
        current=current,
        failure_kind=failure_kind,
        tried=tried,
    )
    return candidate.name if candidate is not None else None


def fallback_runner_profile(
    repo_root: Path,
    current: "RunnerProfile",
    failure_kind: str | None,
    *,
    tried: list[str] | tuple[str, ...] = (),
) -> "RunnerProfile | None":
    """Typed fallback for the daemon's dispatch path."""
    from . import runner_select

    return runner_select.automatic_fallback_runner(
        available_selection_runners(repo_root),
        current=current.name,
        failure_kind=failure_kind,
        tried=tried,
    )


def quality_escalation_runner(
    repo_root: Path,
    current: str,
    *,
    target_class: str | None = None,
    tried: list[str] | tuple[str, ...] = (),
) -> str | None:
    """Return a stronger local Runner for an explicit quality escalation."""
    from . import runner_select

    candidate = runner_select.quality_escalation_runner(
        available_selection_runners(repo_root),
        current=current,
        target_class=target_class or runner_select.STRONG,
        tried=tried,
    )
    return candidate.name if candidate is not None else None


def resolve_runner_profile(
    repo_root: Path, overrides: dict[str, Any] | None = None,
) -> "RunnerProfile":
    """Resolve one typed Shell+Core Runner for this repo.

    Resolution order (highest precedence first):

    1. **``shell=``** in ``.brr/config`` — pin a specific profile (Shell or
       Shell+Core) by name; skips cost-aware selection entirely. This is the
       new preferred knob (replaces the legacy ``runner=``).
    2. **``core=``** in ``.brr/config`` — filter available profiles to those
       whose declared ``model`` matches *core*, then pick the cheapest.
    3. **Legacy ``runner=``** — same as ``shell=`` for backward compatibility;
       ``runner=auto`` triggers cost-aware auto-detection.
    4. **Auto** — cost-aware selection via :func:`runner_select.select_runner`:
       cheapest available local profile at or below ``economy`` class.

    Raises ``RuntimeError`` when no profile can be resolved.
    """
    from . import config as conf
    from . import runner_select

    cfg = conf.load_config(repo_root)
    override_keys: set[str] = set()
    if overrides:
        for key in ("shell", "core", "runner", "runner_policy"):
            value = overrides.get(key)
            if value not in (None, ""):
                cfg[key] = value
                override_keys.add(key)
    # An event/tap-level override outranks a *config-file* pin. Without
    # this, a consumed spool-rack tap (daemon sets ``runner``) or a spawn
    # ``core:`` override loses silently to ``shell=`` in .brr/config —
    # found live 2026-07-11: a luna tap was consumed and the wake stamped
    # "requested from the dashboard spool rack", yet dispatched on the
    # config-pinned profile. Precedence *within* the config file is
    # unchanged; only cross-source shadowing is removed.
    if "shell" not in override_keys:
        if "runner" in override_keys or "core" in override_keys:
            cfg["shell"] = ""
        if "core" in override_keys and "runner" not in override_keys:
            cfg["runner"] = "auto"
    profiles = _selection_profiles(repo_root)

    # shell= is the new explicit pin. When set it is treated as an exact
    # profile override — no cost-aware movement, no dispatcher hop.
    shell_pin = str(cfg.get("shell", "")).strip() or None
    # core= filters the candidate set to profiles whose model matches.
    core_pin = str(cfg.get("core", "")).strip() or None
    # Legacy runner= stays for backward compatibility.
    runner_cfg = str(cfg.get("runner", "auto")).strip()

    # Exact-pin path: shell= or a non-"auto" runner= wins outright.
    explicit_pin = shell_pin or (runner_cfg if runner_cfg != "auto" else None)
    if explicit_pin:
        if _runner_available(explicit_pin, profiles):
            if core_pin and shell_pin:
                composed = _compose_shell_core(shell_pin, core_pin, profiles)
                if composed is not None:
                    return runner_profile(composed, repo_root)
                _warn_if_shell_shadows_core(shell_pin, core_pin, profiles)
            return runner_profile(explicit_pin, repo_root)
        raise RuntimeError(
            f"Runner '{explicit_pin}' not found on PATH. "
            "Check shell= (or runner=) in .brr/config."
        )

    # Cost-aware selection: build the available-profile set, optionally
    # filtered by core=, and let select_runner pick the cheapest. Model-less
    # base Shell profiles are kept for explicit shell= pins, but they should
    # not beat generated Core profiles in auto mode when the registry knows
    # concrete Cores for that Shell.
    generated_shells = {
        str(profile.get("shell") or profile.get("binary") or "").strip()
        for profile in profiles.values()
        if isinstance(profile, dict) and profile.get("generated_core")
    }
    all_profiles = []
    for name, profile in profiles.items():
        if not _runner_available(name, profiles):
            continue
        shell = str(profile.get("binary") or profile.get("shell") or name).strip()
        if (
            not core_pin
            and shell in generated_shells
            and not str(profile.get("model") or "").strip()
        ):
            continue
        all_profiles.append(runner_select.runner_from_profile(name, profile))
    if core_pin:
        # Filter to profiles whose declared model matches core_pin (exact
        # or prefix, case-insensitive), plus short profile aliases like
        # ``core=haiku`` → ``claude-haiku``. Fall back to all if none match so
        # an unrecognised core= doesn't silently kill all options.
        core_lower = core_pin.lower()

        def _core_matches(profile: runner_select.RunnerProfile) -> bool:
            model = (profile.model or "").lower()
            name = profile.name.lower()
            return (
                bool(model)
                and (model == core_lower or model.startswith(core_lower))
            ) or name == core_lower or name.endswith(f"-{core_lower}")

        filtered = [
            p for p in all_profiles
            if _core_matches(p)
        ]
        candidates = filtered or all_profiles
    else:
        candidates = all_profiles

    policy = str(
        cfg.get("runner_policy", runner_select.POLICY_COST_AWARE)
    ).strip() or runner_select.POLICY_COST_AWARE
    if policy not in {runner_select.POLICY_COST_AWARE, runner_select.POLICY_FIXED}:
        policy = runner_select.POLICY_COST_AWARE

    chosen = runner_select.select_runner(candidates, policy=policy)
    if chosen:
        return chosen

    raise RuntimeError(
        "No AI runner found. Install claude, codex, or gemini, "
        "or set shell= (or core=) in .brr/config."
    )


def resolve_runner(repo_root: Path, overrides: dict[str, Any] | None = None) -> str:
    """Compatibility projection of :func:`resolve_runner_profile` to its name."""
    return resolve_runner_profile(repo_root, overrides).name


def _build_cmd(
    runner_name: "str | RunnerProfile",
    prompt: str,
    cfg: dict[str, Any],
    repo_root: Path | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build subprocess argv for a built-in or named runner.

    Each runner is invoked headless with approvals bypassed (see the
    bundled/default runner profiles) and prints its final reply on stdout. brr
    captures stdout and writes it to the invocation's response file —
    runners do not need to be told where the response file lives.

    *extra_args* (e.g. codex's argv-installed hook overrides) are inserted
    before the prompt on the profile / bare path. A ``runner_cmd`` override is
    honoured verbatim — a pinned command is the user's, so extra args are not
    injected into it.
    """
    def _replace_placeholders(parts: list[str]) -> list[str]:
        return [s.replace("{prompt}", prompt) for s in parts]

    extra = list(extra_args or [])

    custom = cfg.get("runner_cmd")
    if custom:
        if isinstance(custom, list):
            return _replace_placeholders(custom)
        return _replace_placeholders(shlex.split(str(custom)))

    # The prompt is NOT appended here. It travels on stdin -- see `_prompt_stdin`.
    from . import runner_select

    if isinstance(runner_name, runner_select.RunnerProfile):
        profile_cmd = runner_name.cmd or runner_name.name
    else:
        profile = _selection_profiles(repo_root, probe=False).get(runner_name)
        profile_cmd = str(profile.get("cmd", runner_name)) if profile else runner_name
    if profile_cmd:
        cmd = shlex.split(profile_cmd)
        cmd.extend(extra)
        return cmd

    return [str(runner_name), *extra]


def _prompt_stdin(cfg: dict[str, Any], prompt: str) -> str | None:
    """The prompt to pipe, or ``None`` when a pinned ``runner_cmd`` owns its argv.

    **Why the prompt is not an argv token any more.**  It used to be, and on
    2026-07-14 that killed a run (``run-260714-1442-hgc3``).  The wake had grown to
    57,644 bytes and sat, whole, as ``argv[14]`` -- boot kernel, dominion playbook,
    kb slice, conversation.  Somewhere at byte 12,393 of it was the string
    ``bench run --scenario drift``, quoted as *documentation* inside the resident's
    own playbook.  The run then went to kill a stuck bench with

        pkill -f "bench run --scenario drift"

    and ``pkill -f`` matches against a process's **entire command line**.  It found
    that substring inside the runner's own ``/proc/self/cmdline`` and SIGTERM'd the
    process that issued it.  Exit 143, no output, cause unfindable for a week.

    A prompt in argv is a loaded gun pointed at the process holding it: *any*
    ``pkill -f`` / ``pgrep -f`` a run performs is matched against a haystack that
    contains the run's own prose.  It is also a plain confidentiality leak -- the
    whole dominion and kb are world-readable in ``ps`` output on a shared box.

    stdin costs nothing (unlike spilling to a file, which buys a Read turn every
    wake and taxes exactly the "perception is free" property the boot exists to
    protect).  Verified on both live Shells: ``claude --print`` reads a piped prompt,
    and codex announces ``Reading prompt from stdin...``.

    **The muted-fd invariant is preserved, not broken.**  ``stdin=DEVNULL`` was
    pinned so codex's stdin path sees an immediate EOF instead of hanging on an
    open-but-silent fd inherited from the daemon's terminal.  A pipe brr writes the
    prompt into and then *closes* gives exactly that: the prompt, then EOF.  What was
    ever forbidden is an fd nobody closes.

    A ``runner_cmd`` override is the one exception: ``{prompt}`` in a pinned command
    means *"put it here"*, and a pinned command is the user's.  That path keeps argv,
    and keeps ``_spill_oversized_argv`` behind it.
    """
    if cfg.get("runner_cmd"):
        return None
    return prompt


# A single argv string longer than this trips Linux's ``MAX_ARG_STRLEN`` --
# a *per-argument* cap of 128 KiB fixed since kernel 2.6.23, independent of
# (and much smaller than) the overall ``ARG_MAX`` (~2 MiB) that ``getconf
# ARG_MAX`` reports. Observed in production 2026-07-07: a director-tick
# wake's assembled prompt reached 176 KB (the growing self-inject playbook +
# kb recent-activity + the authored work surface), and ``execve`` rejected it outright
# with ``OSError: [Errno 7] Argument list too long`` before brr ever started
# the subprocess -- the whole thought was lost, not just slowed down. This
# threshold sits comfortably under the 131072-byte kernel cap so growth
# between checks doesn't re-trip it.
_MAX_PROMPT_ARG_BYTES = 100_000


def _spill_oversized_argv(cmd: list[str], repo_root: Path | None) -> list[str]:
    """Replace any argv element too large for ``execve`` with a file pointer.

    Every Tier-0 runner (claude, codex, gemini) is a coding agent with
    baseline file-read capability, so trading one oversized argv string for
    a short pointer plus one Read call costs a turn, not a redesign -- and
    it needs no CLI-specific stdin behaviour (codex documents reading a
    piped prompt from stdin; claude does not, and ``stdin=subprocess.DEVNULL``
    is otherwise load-bearing to keep codex's own stdin-read path from
    hanging on an open-but-silent fd -- see
    ``test_invoke_runner_passes_configured_timeout_to_communicate``).
    Spilling to disk sidesteps both: the file is on disk before the
    subprocess ever starts, this function never touches ``stdin``.
    """
    adjusted: list[str] = []
    for part in cmd:
        byte_len = len(part.encode("utf-8"))
        if byte_len <= _MAX_PROMPT_ARG_BYTES:
            adjusted.append(part)
            continue
        path = _spill_prompt_to_file(part, repo_root)
        adjusted.append(_prompt_pointer_text(path, byte_len))
    return adjusted


def _live_capture_dir(repo_root: Path | None) -> Path:
    """A directory the child writes its streams into *while it runs*.

    Deliberately resolved **without** shelling out to git. ``gitops.shared_brr_dir``
    would be the natural call, but it runs ``git rev-parse`` whenever ``.brr`` is not
    directly present -- and this sits on the hot path of *every* invocation, where a
    subprocess per run buys nothing. Walk up for an existing ``.brr``; fall back to
    the temp dir. A capture buffer wants to be cheap and always available, not exact.
    """
    root = (repo_root or Path.cwd()).resolve()
    base: Path | None = None
    for candidate in (root, *root.parents):
        if (candidate / ".brr").is_dir():
            base = candidate / ".brr" / "runner-capture"
            break
    if base is None:
        base = Path(tempfile.gettempdir()) / "brnrd-runner-capture"
    path = base / f"{int(time.time())}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_capture(path: Path) -> str:
    """Read back a stream file, tolerating a child killed mid-character."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _strip_prompt_echo(stderr: str, prompt: str) -> str:
    """Remove one exact prompt echo from a runner's display-safe stderr.

    Codex can print its complete stdin prompt to stderr before it starts work.
    That stream later feeds daemon failure packets and may be quoted to chat;
    retaining the raw capture there would reflect private prompt material back
    to the operator. The trace keeps the raw stream for local diagnosis.
    """
    if not stderr or not prompt:
        return stderr
    return stderr.replace(prompt, "", 1)


def _uses_codex_shell(selected: object, selected_name: str, cmd: list[str]) -> bool:
    """Whether an invocation uses Codex's prompt-echoing shell.

    Resolution must stay IO-free: this runs inside every invocation, after
    the runner has already exited, purely to decide a display scrub. The
    original resolved a bare name through ``_selection_profiles``, whose
    generated view probes Shell binaries via ``subprocess.run`` on a cold
    cache — a second child process born mid-invocation (and a crash under
    test fakes). Even ``_load_profiles`` shells out to git to locate the
    shared ``.brr``. The selected profile's own ``shell`` field and the argv
    actually executed answer the same question for free.
    """
    shell = getattr(selected, "shell", None)
    if shell is None and cmd:
        shell = Path(cmd[0]).name
    if not shell:
        shell = selected_name
    return str(shell).strip().lower() == "codex"


def _retire_capture_dir(capture_dir: Path, returncode: int) -> None:
    """Delete the capture on a clean run; *keep it* when something went wrong.

    A healthy run has already had its stdout handed to the response file and its
    trace, so the copy is litter. A run that died has exactly one artifact left in
    the world, and this is it -- so it stays on disk, where the next wake (or a
    human) can read what the dead runner managed to say before it went.
    """
    if returncode == 0:
        shutil.rmtree(capture_dir, ignore_errors=True)
        return
    try:
        (capture_dir / "returncode.txt").write_text(f"{returncode}\n", encoding="utf-8")
    except OSError:
        pass


def _spill_prompt_to_file(text: str, repo_root: Path | None) -> Path:
    """Persist oversized argv text to disk under the shared ``.brr`` dir."""
    from . import gitops

    root = repo_root or Path.cwd()
    overflow_dir = gitops.shared_brr_dir(root) / "prompt-overflow"
    overflow_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    path = overflow_dir / f"{int(time.time())}-{digest}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _prompt_pointer_text(path: Path, byte_len: int) -> str:
    return (
        f"Your full task prompt is {byte_len} bytes -- too large to pass as a "
        "command-line argument (Linux caps a single argv string at 128 KiB). "
        "It has been written to disk instead. Before doing anything else, "
        "read the complete prompt from this exact path and follow it exactly "
        f"as if it had been given to you directly:\n{path}"
    )


def _core_provenance_footer(expected: str | None, observed: str | None) -> str:
    """The line that rides along with a reply of uncertain provenance.

    Short, factual, and last: the reader gets the work first and the caveat
    where a caveat belongs. It names both vocabularies because that is the
    whole content of the doubt — a pin is a request, an attestation is an
    observation, and they are owned by different parties.
    """
    return (
        "— brnrd: this reply was produced by "
        f"`{observed or 'an unattested Core'}`, but `{expected}` was pinned "
        "for this run. The work is delivered as-is; judge it knowing which "
        "Core wrote it."
    )


def _write_response_file(response_path: str, stdout: str) -> None:
    """Persist the runner's stdout as the captured response file.

    The path is created relative to the host file system; brr always
    runs inside (or with a bind mount of) the repo, so the parent
    directory normally exists, but we mkdir defensively so a fresh
    ``responses/`` subtree can be created on the first run.
    """
    target = Path(response_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stdout, encoding="utf-8")


def _process_runner_stdout(
    runner_name: str,
    stdout: str,
    env: dict[str, str] | None = None,
) -> tuple[str, str | None]:
    """Normalize runner-specific stdout before response capture.

    Most runners already print the final reply as plain text. Claude's daemon
    profile opts into ``--output-format json`` so brr can collect spend/context
    accounting; for that Shell, unwrap ``result`` back to the user-facing reply
    and stash the level snapshot for the daemon's final portal refresh.
    """
    from . import claude_status

    if claude_status.supported(runner_name):
        return claude_status.capture_stdout_with_model(stdout, env)
    return stdout, None


def _copy_artifact_to_trace(
    trace_dir: Path,
    artifact: RunnerArtifactSpec,
    index: int,
) -> Path | None:
    if not artifact.copy_to_trace or not artifact.path.exists():
        return None
    artifacts_dir = trace_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    target = artifacts_dir / f"{index:02d}-{artifact.path.name}"
    shutil.copy2(artifact.path, target)
    return target


def _write_trace(result: RunnerResult) -> Path | None:
    trace_root = result.invocation.trace_root
    try:
        trace_dir = trace_root / f"{_slugify(result.invocation.label)}-{_trace_id()}"
        trace_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    (trace_dir / "prompt.md").write_text(result.invocation.prompt, encoding="utf-8")
    (trace_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
    (trace_dir / "stderr.txt").write_text(
        result.trace_stderr if result.trace_stderr is not None else result.stderr,
        encoding="utf-8",
    )

    records = []
    copied_artifacts = []
    for index, artifact in enumerate(result.invocation.required_artifacts, start=1):
        trace_copy = _copy_artifact_to_trace(trace_dir, artifact, index)
        copied_artifacts.append(
            RunnerArtifactRecord(
                path=artifact.path,
                label=artifact.label or str(artifact.path),
                exists=artifact.path.exists(),
                trace_copy=trace_copy,
            )
        )

    result.artifacts = copied_artifacts
    metadata = {
        "runner": result.runner_name,
        "kind": result.invocation.kind,
        "label": result.invocation.label,
        "cwd": str(result.invocation.cwd or ""),
        "command": result.command,
        "returncode": result.returncode,
        "response_path": result.invocation.response_path,
        "validation_ok": result.validation_ok,
        "retry_reason": result.retry_reason(),
        "artifacts": [
            {
                "label": artifact.label,
                "path": str(artifact.path),
                "exists": artifact.exists,
                "trace_copy": str(artifact.trace_copy) if artifact.trace_copy else None,
            }
            for artifact in result.artifacts
        ],
    }
    (trace_dir / "meta.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return trace_dir


def invoke_runner(
    runner_name: "str | RunnerProfile",
    invocation: RunnerInvocation,
    cfg: dict[str, Any] | None = None,
    *,
    trace: bool = False,
) -> RunnerResult:
    """Run a runner subprocess, validate outputs, and optionally persist a trace.

    If the invocation specifies a ``response_path``, captured stdout is
    written there on success. Runners are expected to print only their
    final reply to stdout (progress streams to stderr); brr has no need
    to know per-runner output flags.
    """
    cfg = cfg or {}

    from . import runner_select

    selected = invocation.selected_runner or runner_name
    selected_name = selected.name if isinstance(
        selected, runner_select.RunnerProfile
    ) else selected
    cmd = _build_cmd(
        selected,
        invocation.prompt,
        cfg,
        invocation.repo_root,
        extra_args=invocation.extra_runner_args,
    )
    cmd = _spill_oversized_argv(cmd, invocation.repo_root)
    prompt_stdin = _prompt_stdin(cfg, invocation.prompt)
    timeout = invocation.timeout_seconds or runner_timeout(cfg)
    # Always start from a cleaned base env so a parent agent session's
    # safe-mode / identity vars never leak into the runner (and silently
    # disable its hooks); layer the run's own env on top.
    proc_env = clean_runner_environ()
    if invocation.env:
        proc_env.update({str(k): str(v) for k, v in invocation.env.items()})
    stdout = ""
    stderr = ""
    returncode = 0
    timed_out = False
    # Capture to *disk*, not to in-memory pipes. `communicate()` returns only when
    # the child exits, so a killed runner used to hand back two empty strings --
    # every kill looked identical (budget SIGKILL, OOM, an agent's own stray
    # `pkill`) and none of them left a byte to diagnose. Observed 2026-07-07
    # (run-260707-1154-kem3) and again 2026-07-14 (run-260714-1442-hgc3): exit 143,
    # zero output, cause unknown for a week. Files are written by the child as it
    # runs, so whatever it managed to say survives its own death.
    capture_dir = _live_capture_dir(invocation.repo_root)
    out_path = capture_dir / "stdout.txt"
    err_path = capture_dir / "stderr.txt"
    try:
        with open(out_path, "w", encoding="utf-8") as f_out, \
                open(err_path, "w", encoding="utf-8") as f_err:
            proc_key = invocation.label or f"invoke-{id(invocation)}"
            with _proc_lock:
                # A duplicate label must not shadow a live sibling's handle
                # (it would orphan that proc from every kill path).
                base_key = proc_key
                bump = 1
                while proc_key in _active_procs:
                    proc_key = f"{base_key}#{bump}"
                    bump += 1
                proc = _active_procs[proc_key] = subprocess.Popen(
                    cmd,
                    cwd=invocation.cwd,
                    stdin=(
                        subprocess.PIPE if prompt_stdin is not None
                        else subprocess.DEVNULL
                    ),
                    stdout=f_out,
                    stderr=f_err,
                    text=True,
                    env=proc_env,
                )
            if prompt_stdin is not None and proc.stdin is not None:
                # Cannot deadlock: stdout/stderr are *files*, not pipes, so the child
                # is never blocked on a full pipe we are not draining. It reads the
                # prompt, sees EOF on close, and works.
                try:
                    proc.stdin.write(prompt_stdin)
                except BrokenPipeError:
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except BrokenPipeError:
                        pass
            try:
                returncode = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                returncode = 124
                timed_out = True
    except FileNotFoundError:
        stderr = f"executable '{cmd[0]}' not found on PATH"
        returncode = 127
    finally:
        with _proc_lock:
            for key, live in list(_active_procs.items()):
                if live.poll() is not None:
                    _active_procs.pop(key, None)

    if returncode != 127:
        stdout = _read_capture(out_path)
        stderr = _read_capture(err_path)
    trace_stderr = stderr
    if timed_out:
        stderr = (stderr + "\n" if stderr else "") + f"runner timed out after {timeout}s"
    if _uses_codex_shell(selected, selected_name, cmd):
        stderr = _strip_prompt_echo(stderr, invocation.prompt)
    _retire_capture_dir(capture_dir, returncode)

    stdout, observed_core = _process_runner_stdout(
        selected_name, stdout, invocation.env,
    )
    from . import runner_select

    mismatch = runner_select.core_mismatch(
        invocation.expected_core, observed_core,
    )
    if mismatch:
        attestation_error = (
            "Core attestation failed: requested "
            f"{invocation.expected_core!r}, Shell observed {observed_core!r}"
        )
        stderr = (stderr.rstrip() + "\n" if stderr.strip() else "") + attestation_error

    if invocation.response_path and returncode == 0 and stdout and stdout.strip():
        body = stdout
        if mismatch:
            # The reply is real work and ships; the provenance doubt ships
            # *with* it rather than in place of it. Withholding the answer
            # tells the user "nothing happened", which is the one thing that
            # is definitely false.
            body = body.rstrip() + "\n\n" + _core_provenance_footer(
                invocation.expected_core, observed_core,
            )
        _write_response_file(invocation.response_path, body)

    result = RunnerResult(
        invocation=invocation,
        runner_name=selected_name,
        command=cmd,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        trace_dir=None,
        artifacts=[],
        observed_core=observed_core,
        core_mismatch=mismatch,
        trace_stderr=trace_stderr,
    )
    if trace:
        result.trace_dir = _write_trace(result)
    else:
        result.artifacts = [
            RunnerArtifactRecord(
                path=spec.path,
                label=spec.label or str(spec.path),
                exists=spec.path.exists(),
            )
            for spec in invocation.required_artifacts
        ]
    return result


def run_executor(
    runner_name: str,
    prompt: str,
    cwd: Path | None = None,
    cfg: dict[str, Any] | None = None,
    response_path: str | None = None,
) -> str:
    """Run a runner subprocess with the given prompt, return stdout."""
    if cwd is None:
        raise RuntimeError("run_executor requires cwd to infer repo_root for tracing")
    invocation = RunnerInvocation(
        kind="executor",
        label=runner_name,
        prompt=prompt,
        cwd=cwd,
        repo_root=cwd,
        response_path=response_path,
    )
    result = invoke_runner(runner_name, invocation, cfg=cfg)
    result.raise_for_error()
    return result.stdout


# ── Run execution ───────────────────────────────────────────────────


def run_task(instruction: str) -> str:
    """Run a user instruction via the configured runner (for ``brnrd run``)."""
    from . import gitops
    repo_root = gitops.ensure_git_repo()
    from . import config as conf
    from . import prompts as _prompts

    cfg = conf.load_config(repo_root)
    runner_name = resolve_runner(repo_root)

    prompt = _prompts.build_run_prompt(instruction, repo_root)

    print(f"[brnrd] running: {instruction}")
    print(f"[brnrd] runner: {runner_name}")
    result = invoke_runner(
        runner_name,
        RunnerInvocation(
            kind="run",
            label=instruction[:40],
            prompt=prompt,
            cwd=repo_root,
            repo_root=repo_root,
        ),
        cfg=cfg,
    )
    result.raise_for_error()
    output = result.output
    print(output)
    return output


class TaskRunner:
    """One-at-a-time task execution in a background thread."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._instruction: str = ""
        self._result: dict | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def finished(self) -> bool:
        return self._thread is not None and not self._thread.is_alive()

    @property
    def instruction(self) -> str:
        return self._instruction

    def submit(self, instruction: str) -> bool:
        """Start a task. Returns False if already busy."""
        if self.busy:
            return False
        self._instruction = instruction
        self._result = None
        self._thread = threading.Thread(
            target=self._run, args=(instruction,), daemon=True,
        )
        self._thread.start()
        return True

    def poll_result(self) -> dict | None:
        """Non-blocking check for a completed task."""
        if not self.finished:
            return None
        result = {
            "instruction": self._instruction,
            **(self._result or {}),
        }
        self._thread = None
        self._instruction = ""
        self._result = None
        return result

    def shutdown(self, timeout: float = 10) -> None:
        """Wait for the current task to finish."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self, instruction: str) -> None:
        try:
            output = run_task(instruction)
            self._result = {"output": output}
        except Exception as e:
            self._result = {"error": str(e)}
