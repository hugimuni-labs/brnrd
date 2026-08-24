"""Cloud gate — drains a brnrd repo inbox into the local ``.brr/``."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from .. import claude_status, claude_usage, codex_status, codex_usage, emotes, gitops, presence, protocol, run_ledger, run_progress, runner_quota, usage_samples
from .. import conversations, dominion, run_stop_request, schedule as schedule_mod, wake_request
from ..cli import brnrd_cmd
from ..gates.github.parse import parse_origin_url
from ..run import Run, list_runs, run_manifest_path
from . import cloud_credentials as _credentials
from . import cloud_publisher as _publisher
from . import delivery, runtime

_CREDENTIAL_COMPAT_NAMES = (
    "publishing_token_seconds_remaining",
    "ensure_publishing_credential_fresh",
    "github_credentials_dir",
    "_atomic_write_private",
    "_write_github_credential_pointer",
    "_refresh_publishing_credential",
    "_try_refresh_publishing_credential",
)
_PUBLISHER_COMPAT_NAMES = (
    "_PUBLISH_CORPUS_SLICES",
    "_PUBLISH_TICK_ORDER",
    "_PUBLISH_LANES",
    "_PUBLISH_OFF",
    "_CORPUS_FILE_CAP_BYTES",
    "_RUNS_WINDOW_DAYS_DEFAULT",
    "_RUN_DIR_RE",
    "_CODEX_WINDOW_LABELS",
    "_WAKE_CLAIM_TIMEOUT_S",
    "_LIVE_RUN_TRUNCATION_MARK",
    "_LIVE_RUN_IDENTITY_FIELDS",
    "_LIVE_RUN_STRING_BOUNDS",
    "_RUN_LEDGER_SNAPSHOT_LIMIT",
    "_resolve_publish_scopes",
    "_publish_lane",
    "_dashboard_publish_tick",
    "_dashboard_publish_loop",
    "_iso_from_epoch",
    "_iso_from_event",
    "_summary",
    "_runner_payload",
    "_run_activity_records",
    "_schedule_activity_records",
    "_respawn_activity_records",
    "_activity_snapshot",
    "_publish_activity",
    "_publish_config",
    "_run_file_date",
    "_publish_selection",
    "_corpus_resolve",
    "_corpus_fingerprint",
    "_corpus_payload",
    "_corpus_publish_hash",
    "_publish_corpus",
    "_quota_window",
    "_codex_window_label",
    "_codex_quota_windows",
    "_codex_quota_shell",
    "_claude_week_model_windows",
    "_claude_quota_shell",
    "_claude_credits_block",
    "_quota_snapshot",
    "_shell_level_label",
    "quota_shell_labels",
    "_gate_health_snapshot",
    "_publish_quota",
    "_runners_snapshot",
    "_publish_runners",
    "claim_wake_request",
    "_live_run_progress",
    "_bounded_live_run",
    "_live_runs_snapshot",
    "_mood_payload",
    "_daemon_mood_payload",
    "_spawn_pool_width",
    "_dispatch_run_stops",
    "_report_live_runs_losses",
    "_publish_live_runs",
    "_github_repo_label",
    "_pr_review_repo_labels",
    "_pr_review_snapshot",
    "_publish_pr_review_queue",
    "_run_ledger_snapshot",
    "_publish_run_ledger",
    "_CLAUDE_QUOTA_PUBLISH_MAX_AGE_SECONDS",
    "_CODEX_QUOTA_PUBLISH_MAX_AGE_SECONDS",
    "_DASHBOARD_PUBLISH_INTERVAL_S",
)
for _compat_name in (*_CREDENTIAL_COMPAT_NAMES, *_PUBLISHER_COMPAT_NAMES):
    _compat_module = (
        _credentials
        if _compat_name in _CREDENTIAL_COMPAT_NAMES
        else _publisher
    )
    globals()[_compat_name] = getattr(_compat_module, _compat_name)
del _compat_name
del _compat_module

#: #1205: the relay API used to be reply-shaped only (``POST
#: /v1/daemons/responses``, keyed on an inbound event's ``cloud_event_id``)
#: — an *unaddressed* cloud send was structurally impossible, and the daemon
#: read this declaration (never a hardcoded gate-name check) to refuse the
#: attempt loudly at synthesis instead of queueing it into a drawer this
#: gate's own delivery loop would never open. The server grew a fresh-send
#: primitive (``POST /v1/daemons/messages``, keyed on platform chat identity
#: rather than an event) to retire that impossibility — see ``post()`` in
#: ``_deliver_responses`` below, which now falls back to it whenever the
#: synthesized event carries no ``cloud_event_id``. True here means what it
#: means on every other built-in gate: capable, the default this attribute
#: exists to let a gate opt *out* of, not into.
CAN_SEND_UNADDRESSED = True

_POLL_WAIT_S = 25
_HTTP_TIMEOUT_S = 60
_DEFAULT_DAEMON_NAME = "daemon"
_RESPONSE_LIMITS = {"telegram": 3900, "whatsapp": 4000}
_SESSION = requests.Session()
# Codex's probe is a ~1.5s process spawn against an account-metadata endpoint
# (no model tokens), so it can refresh well inside the dashboard's 300s
# staleness threshold without costing anything but wall-clock.
# Dashboard snapshots (activity/surface/quota/live-runs/PR-review-queue/run-ledger) used
# to publish once per `_loop_once` iteration, which is paced by the inbox
# long-poll above (`_POLL_WAIT_S = 25`) — a constant chosen for chat
# responsiveness, never for dashboard freshness. That coupling capped every
# dashboard snapshot at ~25s stale by construction. Publishing runs on its
# own short cadence instead — see kb/plan-loom-realtime-build.md slice 0.
_PUBLISHING_TOKEN_REFRESH_S = 10 * 60
# A runner env *snapshots* BRNRD_MANAGED_GITHUB_TOKEN when it is built and
# holds that copy for the whole run. The poll-loop threshold above is a
# ceiling on staleness for the daemon, but it is a floor of only ten minutes
# for whoever is dispatched just before a renewal — so a runner inherits
# anywhere from 10 to 60 minutes of token life with no way to tell which, and
# discovers the short end by failing to push work it has already committed.
# Observed twice: a push died 18 minutes into a run (2026-07-19), and a run
# woke to an already-expired token (2026-07-20). Dispatch therefore asks for a
# *floor* rather than inheriting the poll loop's ceiling.
_PUBLISHING_TOKEN_DISPATCH_MIN_S = 50 * 60
_publishing_token_expires_at = 0.0
# Set by run_loop: the brr dir whose cloud state mints the managed token.
_publishing_state_dir: Path | None = None
# Where the daemon publishes the managed GitHub credential as a *pointer* the
# runner reads at use time (issue #477): a runner env is a frozen snapshot and
# can never see a refreshed token exported into it, so instead of handing it a
# value we hand it this directory. gh reads the current token from `hosts.yml`
# via GH_CONFIG_DIR; git's credential helper cats `token` on each push. Every
# renewal rewrites both files atomically, so a run already in flight picks up
# the new token without re-exec. Absent when no cloud gate mints a token here.
_GITHUB_CREDENTIAL_SUBPATH = ("credentials", "github")
_CREDENTIAL_DIR_MODE = 0o700
_CREDENTIAL_FILE_MODE = 0o600
_publishing_token_retry_at = 0.0
_publishing_token_lock = threading.Lock()


class BrnrdAuthError(RuntimeError):
    pass


class CloudUnavailableError(RuntimeError):
    """The account service could not be reached at the HTTP boundary."""


_AUTH_HINT = "Re-run `brnrd account connect` to link this daemon to your brnrd repo."

# A 401 is retried, not fatal — see `run_loop`. Slow cadence, because the one
# case that deserves patience (a transient server-side auth failure) resolves
# on its own, and the one that doesn't (a truly bad token) should keep saying
# so once every five minutes rather than kill the gate on its way out.
_AUTH_RETRY_MIN_S = 5
_AUTH_RETRY_CAP_S = 300


# Gateway-transient statuses worth a short retry: the hosted brnrd sits
# behind a router that answers 502/503/504 for the duration of a deploy
# window (main auto-deploys on merge). A single blip tracebacked
# `brnrd connect` mid-deploy (2026-07-09); a couple of paced retries ride
# out the blip without hiding a real outage — the last error still raises.
# Upstream never saw these requests (the router refused them), so retrying
# non-idempotent methods doesn't double-deliver.
_RETRY_STATUSES = frozenset({502, 503, 504})
_RETRY_SLEEPS_S = (2.0, 5.0)


def _request(base_url: str, method: str, path: str, *, token: str | None = None, json: dict | None = None, params: dict | None = None, timeout: float = _HTTP_TIMEOUT_S, retry: bool = True) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    sleeps = _RETRY_SLEEPS_S if retry else ()
    for attempt, sleep_s in enumerate((*sleeps, None)):
        try:
            resp = _SESSION.request(method, base_url.rstrip("/") + path, json=json, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            raise CloudUnavailableError(
                f"the account service at {base_url.rstrip('/')} is unreachable "
                f"({exc}). Check the URL and network connection, then re-run "
                "`brnrd account connect`."
            ) from exc
        if resp.status_code in _RETRY_STATUSES and sleep_s is not None:
            print(f"[brnrd:cloud] {method} {path} -> {resp.status_code} (gateway); retry {attempt + 1}/{len(sleeps)} in {sleep_s:.0f}s")
            time.sleep(sleep_s)
            continue
        break
    if resp.status_code == 401:
        raise BrnrdAuthError(f"brnrd {method} {path} -> 401: {resp.text[:200]} — {_AUTH_HINT}")
    if not 200 <= resp.status_code < 300:
        # `.status_code` lets a caller distinguish *which* non-2xx this was
        # (e.g. connect()'s poll loop treating a 410 pair-expiry as a clean,
        # retriable stop rather than an unhandled crash) without parsing the
        # message text — see the pitfall on verifying by behaviour, not by
        # string-matching a shape meant for humans.
        err = RuntimeError(f"brnrd {method} {path} -> {resp.status_code}: {resp.text[:200]}")
        err.status_code = resp.status_code  # type: ignore[attr-defined]
        raise err
    return resp.json() if resp.content else {}


def _state_dir(
    brr_dir: Path,
    *,
    account_id: str | None = None,
    create: bool = False,
) -> Path:
    """Resolve the account-owned gate-state directory."""

    from .. import account as account_mod, config as conf

    # Account gate startup passes the home itself as its runtime root.
    if (brr_dir / "account" / "gates" / "cloud.json").exists():
        return brr_dir / "account"

    repo_root = brr_dir.parent
    cfg = conf.load_config(repo_root)
    if account_id:
        cfg = {**cfg, "account.id": account_id, "home.kind": "account"}
    try:
        ctx = account_mod.resolve_context(repo_root, cfg, create=create)
    except Exception:
        return brr_dir
    if ctx.kind != "account":
        return brr_dir
    return account_mod.context_home_root(ctx) / "account"


# ── The bearer token's own location — split out of ``cloud.json`` ─────
#
# ``account/gates/cloud.json`` is tracked in the account home's git repo
# (the account dominion) so the pairing identity survives a restore. The
# daemon token used to live in that same file, which meant it rode every
# ``dominion.commit()`` -> ``gitops.commit_all()`` capture: ``git add -A``
# on the whole home root cannot tell a secret from a note, and the account
# daemon's own bearer token shipped in 107 commits before this split
# existed. ``account.CLOUD_TOKEN_FILENAME`` names the same basename
# (duplicated rather than imported — this module deliberately has no
# module-level dependency on ``account``, see ``_state_dir``'s deferred
# import above; ``test_cloud_token_filename_matches_account`` pins the two).
_TOKEN_FILENAME = "cloud.token"


def _token_path(state_dir: Path) -> Path:
    """Where the live bearer token lives — never inside ``cloud.json``.

    Same directory ``cloud.json`` itself resolves under (``<state_dir>/
    gates/``); ``account.GITIGNORE`` keeps it out of the tracked tree.
    Referenced only from :func:`_read_token`, :func:`_write_token`, and
    :func:`disconnect` (cleanup) — see ``test_cloud_token_split.py`` for the
    structural guard that keeps it that way.
    """
    return state_dir / "gates" / _TOKEN_FILENAME


def _write_token(state_dir: Path, token: str) -> None:
    """THE lawful writer of the bearer token's on-disk location."""
    _atomic_write_private(_token_path(state_dir), token)


def _read_token(state_dir: Path, raw_state: dict) -> str | None:
    """THE lawful reader of the bearer token's on-disk location.

    New-location-first (:func:`_token_path`) — a hit there is authoritative.
    Otherwise falls back to *raw_state*'s legacy ``token`` field, which is
    where every install on disk carries it today, and migrates on the spot
    through :func:`_save_state_to_dir` — new file written, ``cloud.json``
    rewritten without the field — so the fallback drains instead of living
    forever. Called only from :func:`_load_state_from_dir`.
    """
    path = _token_path(state_dir)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token:
        return token
    legacy = raw_state.get("token")
    if not legacy:
        return None
    legacy = str(legacy)
    _save_state_to_dir(state_dir, {**raw_state, "token": legacy})
    return legacy


def _load_state_from_dir(state_dir: Path) -> dict:
    """THE lawful raw reader of persisted cloud-gate state.

    Every caller in this module that needs the gate's state resolves
    through here — including the ``migrated`` probe in :func:`connect`,
    which reads a directory that may differ from the caller's own
    ``brr_dir`` resolution — so the token merge/migrate step
    (:func:`_read_token`) runs exactly once per read, in one place. This is
    the only call to ``runtime.load_state`` for the cloud gate; the merged
    ``token`` field it returns is what every read call site in this module
    (``state["token"]`` / ``state.get("token")``) has always consumed, so
    none of them needed to change for the token to move off disk.
    """
    raw = runtime.load_state(state_dir, "cloud")
    token = _read_token(state_dir, raw)
    if token:
        raw["token"] = token
    else:
        raw.pop("token", None)
    return raw


def _save_state_to_dir(state_dir: Path, state: dict) -> None:
    """THE lawful raw writer of persisted cloud-gate state.

    Splits *state* before it touches disk: everything except ``token`` goes
    to ``cloud.json`` (``gates.runtime.save_state``); ``token`` — when
    present — goes only to :func:`_write_token`'s sibling file. This is the
    only call to ``runtime.save_state`` for the cloud gate, so "cloud.json
    never carries a token key" is a property of this one function's body
    rather than a promise every future writer has to remember to keep.
    """
    token = state.get("token")
    persisted = {k: v for k, v in state.items() if k != "token"}
    runtime.save_state(state_dir, "cloud", persisted)
    if token:
        _write_token(state_dir, str(token))


def _load_state(brr_dir: Path) -> dict:
    return _load_state_from_dir(_state_dir(brr_dir))


def _save_state(
    brr_dir: Path,
    state: dict,
    *,
    account_id: str | None = None,
) -> None:
    _save_state_to_dir(
        _state_dir(brr_dir, account_id=account_id, create=bool(account_id)),
        state,
    )


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def local_repo_identity(repo_root: Path) -> str:
    """Synthesize a forge-shaped identity for a checkout with no forge.

    ``owner/name`` (`_repo_parts` on the backend requires exactly that shape,
    and ``local`` reads honestly as the owner — this repo's forge *is* the
    machine it lives on). ``name`` is the folder's own slug plus a 6-hex
    suffix from the resolved absolute path — always, not only on a
    collision, because the client has no way to ask the server "does this
    name already exist under a *different* path" without a round trip, and a
    suffix that only sometimes appears is a name that silently changes shape
    out from under a repo that was fine yesterday. Stable across repeated
    pairings of the same folder (that's the whole point — a reconnect must
    resolve to the same ``Repo`` row); two folders that happen to share a
    basename never alias into one (#1167 backchannel follow-up: "local forge
    is fine" — this is the shape that makes it actually safe to be fine).
    """
    resolved = repo_root.resolve()
    slug = _SLUG_RE.sub("-", resolved.name.lower()).strip("-") or "repo"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:6]
    return f"local/{slug}-{digest}"


def _repo_capabilities(brr_dir: Path) -> dict:
    repo_root = brr_dir.parent
    caps: dict[str, object] = {"repo_root": str(repo_root)}
    try:
        remote = gitops.default_remote(repo_root)
        if remote:
            url = gitops.remote_url(repo_root, remote)
            if url:
                caps["git_remote"] = url
                repo_full_name = parse_origin_url(url)
                if repo_full_name:
                    caps["repo_full_name"] = repo_full_name
                    caps["forge"] = "github"
        if "repo_full_name" not in caps and gitops.is_working_tree(repo_root):
            # No remote, or a remote whose URL isn't GitHub-shaped, but this
            # *is* a real checkout: it still has an identity, just not one
            # hosted anywhere brnrd knows how to name — synthesize the local
            # one rather than sending nothing and falling back to the
            # dropdown-of-already-connected-repos flow (the one genuine dead
            # end #1167 left open: "Zero code path exists to connect a
            # bare/no-remote folder right now"). A directory that is not a
            # git checkout at all gets no synthesized identity — that
            # fallback is still correct, and still the only path, for a
            # `brnrd account connect` run outside any repo.
            caps["repo_full_name"] = local_repo_identity(repo_root)
            caps["forge"] = "local"
        caps["branch"] = gitops.current_branch(repo_root)
        default_branch = gitops.default_branch(repo_root)
        if default_branch:
            caps["default_branch"] = default_branch
    except Exception:
        pass
    return caps


def _set_credential_expiry(value: float) -> None:
    global _publishing_token_expires_at
    _publishing_token_expires_at = value


def _set_credential_retry_at(value: float) -> None:
    global _publishing_token_retry_at
    _publishing_token_retry_at = value


def _credential_context() -> _credentials.CredentialContext:
    return _credentials.CredentialContext(
        request=_request,
        load_state=_load_state,
        state_dir=_publishing_state_dir,
        token_expires_at=_publishing_token_expires_at,
        set_token_expires_at=_set_credential_expiry,
        retry_at=_publishing_token_retry_at,
        set_retry_at=_set_credential_retry_at,
        lock=_publishing_token_lock,
    )


def _publisher_context() -> _publisher.PublisherContext:
    return _publisher.PublisherContext(
        request=_request,
        load_state=_load_state,
        corpus_resolve=globals()["_corpus_resolve"],
        quota_snapshot=globals()["_quota_snapshot"],
        runners_snapshot=globals()["_runners_snapshot"],
        pr_review_repo_labels=globals()["_pr_review_repo_labels"],
    )

_credentials.configure_context(_credential_context)
_publisher.configure_context(_publisher_context)


def is_configured(brr_dir: Path) -> bool:
    state = _load_state(brr_dir)
    return bool(
        state.get("token")
        and state.get("brnrd_url")
        and (state.get("account_id") or state.get("repo_id"))
    )


def addressed(fm: Mapping[str, object]) -> bool:
    """True when *fm* carries a reply-shaped cloud address (a ``cloud_event_id``).

    Pre-#1205 this was consulted on every out-of-bound cloud send, back when
    :data:`CAN_SEND_UNADDRESSED` was ``False`` and an unaddressed message had
    no other way to go out. Now that the fresh-send primitive exists,
    ``daemon._gate_addressed`` only reaches this predicate for a gate whose
    module still declares itself incapable — which cloud no longer does — so
    this stays live as the hook a *future* reply-shaped-only gate would
    implement, and as the still-true answer to "does *fm* carry a
    cloud_event_id" for any caller that wants that specifically (``post()``
    below, for one: it still prefers the addressed reply lane when the
    event carries the id, and falls to the fresh-send lane only when it
    doesn't).
    """
    return bool(fm.get("cloud_event_id"))


def read_server_fingerprint(brr_dir: Path) -> dict | None:
    """What prod last reported on the inbox long-poll, or ``None``.

    Reads only the local file :func:`_loop_once` persists — never a network
    call, so the wake-time renderer that calls this stays network-free.
    ``None`` covers both "no cloud gate configured" and "configured, but no
    fingerprint landed yet" — the caller names which in its own line.
    """
    return runtime.load_server_fingerprint(_state_dir(brr_dir), "cloud")


def relay_pack(brr_dir: Path, pack: dict, *, ttl_s: int | None = None) -> str | None:
    state = _load_state(brr_dir)
    if not (state.get("token") and state.get("brnrd_url")):
        return None
    body: dict = {"pack": pack}
    if ttl_s:
        body["ttl_s"] = ttl_s
    try:
        result = _request(state["brnrd_url"], "POST", "/v1/daemons/pack", token=state["token"], json=body)
    except Exception as e:
        print(f"[brnrd:cloud] pack relay failed: {e}")
        return None
    url = result.get("render_url")
    return url if isinstance(url, str) and url else None


_CONFIG_CHANGE_MINT_TIMEOUT_S = 10.0


def propose_config_change(
    brr_dir: Path,
    *,
    proposal_id: str,
    config_key: str,
    current_value: Any,
    requested_value: Any,
    reason: str = "",
    timeout: float = _CONFIG_CHANGE_MINT_TIMEOUT_S,
) -> dict[str, Any] | None:
    """Mint a brnrd.dev approve/confirm URL for a proposed config-key change.

    Loom envelope Phase 2 (kb/design-multi-workstream-concurrency.md
    §"Named forks — round 2"): when a resident wants more of an
    allowlisted, user-tunable ceiling than ``.brr/config`` currently
    grants, the change is never applied unilaterally, and never on a
    chat-typed approval — it rides the same device-flow shape as
    ``routers/pairing.py``'s daemon pairing, gated behind the account
    owner's login (``src/brnrd/routers/config_approval.py``). Returns
    ``None`` (never raises) when this daemon isn't cloud-connected, since a
    repo with no brnrd.dev account has no approver to escalate to — the
    caller falls back to a locally-parked-only proposal in that case.
    A connected daemon whose mint *request* fails returns
    ``{"error": <detail>}`` instead, so the caller can report the real
    failure rather than misdiagnosing it as not-connected.

    Called synchronously from ``daemon.py``'s outbox drain (a deliberate,
    narrow exception to gates normally talking to the daemon only through
    the filesystem — see ``gates/README.md``): this is a rare,
    resident-initiated action, not a routine dispatch-loop tick, so the
    shorter-than-default ``timeout`` bounds how long a slow/unreachable
    server can stall that drain rather than avoiding the call entirely; a
    fully async two-phase mint (park now, mint on a later tick) was
    considered and set aside because it would leave a proposal's approve
    link — and any minting failure — invisible to the user until a
    separate poll noticed it.
    """
    state = _load_state(brr_dir)
    if not (state.get("token") and state.get("brnrd_url")):
        return None
    try:
        return _request(
            state["brnrd_url"],
            "POST",
            "/v1/daemons/config-requests",
            token=state["token"],
            json={
                "proposal_id": proposal_id,
                "config_key": config_key,
                "current_value": "" if current_value is None else str(current_value),
                "requested_value": str(requested_value),
                "reason": reason,
            },
            timeout=timeout,
        )
    except Exception as e:
        # Distinguish "connected but the mint failed" from "not connected":
        # the caller's user-facing message must not tell a cloud-connected
        # account to run `brnrd account connect` when the real story is e.g. a 422
        # (server allowlist out of lockstep — observed live 2026-07-11) or
        # a deploy-window 502. The error detail is the actionable part.
        print(f"[brnrd:cloud] config-change proposal mint failed: {e}")
        return {"error": str(e)}


def connect(brr_dir: Path, *, brnrd_url: str, daemon_name: str = _DEFAULT_DAEMON_NAME, poll_interval_s: float = 2.0, timeout_s: float = 600.0, out: Callable[[str], None] = print) -> dict:
    # Sent unauthenticated, before any token exists — this is what lets the
    # approval page lead with "connect <this repo>" instead of a blind
    # dropdown of everything the account already has enabled (the "enable a
    # repository" website step this was built to retire). `repo_root` never
    # leaves this machine: only the fields the server schema accepts.
    initial_caps = _repo_capabilities(brr_dir)
    pair_body = {
        k: v
        for k, v in {
            "repo_full_name": initial_caps.get("repo_full_name"),
            "git_remote": initial_caps.get("git_remote"),
            "branch": initial_caps.get("branch"),
            "default_branch": initial_caps.get("default_branch"),
            "forge": initial_caps.get("forge"),
        }.items()
        if isinstance(v, str) and v
    }
    pair = _request(brnrd_url, "POST", "/v1/accounts/pair", json=pair_body or None)
    out(f"[brnrd] Approve this daemon at: {pair['pair_url']}")
    out(f"[brnrd] Pairing code: {pair['pair_code']}")
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            status = _request(brnrd_url, "GET", f"/v1/accounts/pair/{pair['pair_code']}", params={"poll_secret": pair["poll_secret"]})
        except RuntimeError as exc:
            # The server holds its own TTL on the pair code (`pair_ttl_s`,
            # independent of and not necessarily equal to our own
            # `timeout_s` deadline below) — a detour on the approval side
            # (e.g. connecting a repo before a suggested one exists) can
            # burn past it first. Surfaced as the same clean, retriable
            # message as our own client-side timeout below, instead of the
            # raw `brnrd GET … -> 410: {...}` traceback this used to crash
            # with (observed live: the approval page's own "connect a
            # repository" detour outlasting the code).
            if getattr(exc, "status_code", None) == 410:
                raise TimeoutError(
                    "the pairing code expired before it was approved. Approve "
                    f"the pairing link promptly, then re-run `{brnrd_cmd()} account connect`."
                ) from exc
            raise
        if status.get("status") == "paired" and status.get("daemon_token"):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(
                "account pairing was not approved before the timeout. Approve "
                f"the pairing link, then re-run `{brnrd_cmd()} account connect`."
            )
        time.sleep(poll_interval_s)
    state = _load_state(brr_dir)
    account_id = str(status.get("account_id") or "") or None
    # Resolve — and *create* — the destination state dir before deciding the
    # cursor. That resolution is what runs `account.migrate_cloud_gate_state`,
    # which moves a legacy repo-local `cloud.json` into the account home; the
    # read above used `create=False`, so it can miss the file that holds the
    # only copy of `since`. Saving straight through then overwrote the
    # just-rescued cursor with a zero — and a zero cursor asks the server for
    # the account's entire event history (2026-07-30: 339 events, all already
    # answered). Take the highest cursor either copy knows and never write a
    # lower one: a cursor that is too high self-heals server-side
    # (`inbox.clamp_since`), one that is too low replays history.
    dest = _state_dir(brr_dir, account_id=account_id, create=True)
    migrated = _load_state_from_dir(dest)
    since = max(int(state.get("since") or 0), int(migrated.get("since") or 0))
    capabilities = dict(migrated.get("capabilities") or state.get("capabilities") or {})
    capabilities.update(_repo_capabilities(brr_dir))
    state.update({
        "brnrd_url": brnrd_url.rstrip("/"),
        "token": status["daemon_token"],
        "account_id": status.get("account_id"),
        "repo_id": status["repo_id"],
        "daemon_name": daemon_name,
        "capabilities": capabilities,
        "since": since,
    })
    _save_state(brr_dir, state, account_id=account_id)
    # Pairing mints a daemon token, but the publish endpoints bind that token
    # to a concrete Daemon row created by /register.  Do this in the process
    # that owns the handshake instead of waiting for `brnrd up` to restart:
    # an already-running gate registered its previous token and otherwise
    # keeps publishing the new identity into 404s indefinitely.
    _register(brr_dir, state)
    out(f"[brnrd] Connected to brnrd account {status.get('account_id')}.")
    return state


def disconnect(brr_dir: Path) -> bool:
    """Remove this daemon's local managed-gate identity.

    The account home, its repo registry, knowledge, and resident memory are
    deliberately not removed: disconnect severs the transport, not the
    durable work. Check both the account-owned state directory and the legacy
    repo-local one so the command also repairs installations that have not
    completed the gate-state migration.
    """
    state_dirs = {_state_dir(brr_dir), brr_dir}
    removed = False
    for state_dir in state_dirs:
        for path in (
            runtime.state_path(state_dir, "cloud"),
            runtime.health_path(state_dir, "cloud"),
            _token_path(state_dir),
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            else:
                removed = True
        credential_dir = state_dir / "credentials" / "github"
        if credential_dir.is_dir():
            shutil.rmtree(credential_dir)
            removed = True
    return removed


def setup(brr_dir: Path) -> None:
    print(
        f"[brnrd] Run `{brnrd_cmd()} account connect` to link this daemon "
        "to a brnrd repo."
    )


def auth(brr_dir: Path) -> None:
    setup(brr_dir)


def bind(brr_dir: Path) -> None:
    setup(brr_dir)


def run_loop(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    global _publishing_state_dir
    # Remember where this gate's state lives so dispatch-time freshness checks
    # can find it. Runner env assembly has no brr_dir in hand (and shouldn't
    # need one — the credential belongs to the gate, not to the runner).
    _publishing_state_dir = brr_dir
    state = _load_state(brr_dir)
    registered = False
    try:
        _register(brr_dir, state)
        registered = True
    except Exception as e:
        # A failed register is never fatal, not even a 401. The hosted brnrd
        # answers with whatever its router has during a deploy window, and a
        # daemon that gives up here is a daemon whose cloud gate is dead until
        # someone notices — which is exactly what a restart *during* the
        # outage used to reproduce (2026-07-12: messages to the cloud bot
        # vanished, the restart didn't help, the daemon looked healthy).
        # The poll loop below re-attempts registration once it gets through.
        print(f"[brnrd:cloud] register failed: {e}, will retry")
    _try_refresh_publishing_credential(state, force=True, brr_dir=brr_dir)
    threading.Thread(
        target=_dashboard_publish_loop,
        # #1396/#1437: this run's own responses_dir, not the fixed
        # `brr_dir / "responses"` layout — account mode's real value
        # diverges (see `_dashboard_publish_tick`'s docstring).
        args=(brr_dir, inbox_dir, responses_dir),
        daemon=True,
        name="cloud-dashboard-publish",
    ).start()
    backoff = 1
    auth_backoff = _AUTH_RETRY_MIN_S
    while True:
        try:
            _try_refresh_publishing_credential(_load_state(brr_dir), brr_dir=brr_dir)
            _loop_once(brr_dir, inbox_dir, responses_dir)
            runtime.record_loop_health(brr_dir, "cloud", ok=True)
            backoff = 1
            auth_backoff = _AUTH_RETRY_MIN_S
            if not registered:
                try:
                    _register(brr_dir, _load_state(brr_dir))
                    registered = True
                    print("[brnrd:cloud] re-registered after recovery")
                except Exception as e:  # keep draining; capabilities lag, chat works
                    print(f"[brnrd:cloud] re-register failed: {e}")
        except BrnrdAuthError as e:
            # 401 used to end the thread. Auth failure is not reliably
            # permanent — a mid-deploy router, a cold start, a token the
            # server re-issues — and the failure mode of exiting is the worst
            # one available: chat messages disappear with no error anywhere
            # the user can see, while `brr up` keeps reporting a healthy
            # daemon. Retrying at a slow cadence costs one request per five
            # minutes and keeps a genuinely bad token loudly visible instead
            # of silently terminal.
            runtime.record_loop_health(brr_dir, "cloud", ok=False, error=str(e))
            print(f"[brnrd:cloud] auth failed: {e}, retrying in {auth_backoff}s")
            time.sleep(auth_backoff)
            auth_backoff = min(auth_backoff * 2, _AUTH_RETRY_CAP_S)
        except Exception as e:
            runtime.record_loop_health(brr_dir, "cloud", ok=False, error=str(e))
            print(f"[brnrd:cloud] error: {e}, retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)


def _register(brr_dir: Path, state: dict) -> None:
    caps = dict(state.get("capabilities") or {})
    caps.update(_repo_capabilities(brr_dir))
    _request(state["brnrd_url"], "POST", "/v1/daemons/register", token=state["token"], json={"daemon_name": state.get("daemon_name", _DEFAULT_DAEMON_NAME), "capabilities": caps})


def _sanitize_meta_str(value: str) -> str:
    """Flatten newlines in a sender-controlled string before it enters frontmatter.

    Cloud events relay Telegram display names / usernames that have passed
    through the brnrd server but are ultimately sender-controlled.  Newlines
    in those fields would forge extra frontmatter fields via ``create_event``'s
    meta injection path (#413 §7 S3).  Replace ``\\n`` / ``\\r`` with a space.
    """
    return value.replace("\r", " ").replace("\n", " ")


def _origin_meta(reply_to: dict) -> dict:
    platform = reply_to.get("platform") or ""
    meta: dict[str, object] = {"cloud_platform": platform, "cloud_chat_id": "", "cloud_topic_id": ""}
    if platform == "telegram":
        chat_id = reply_to.get("chat_id")
        topic_id = reply_to.get("topic_id")
        meta["cloud_chat_id"] = "" if chat_id is None else chat_id
        meta["cloud_topic_id"] = "" if topic_id is None else topic_id
        # Sanitize sender-controlled strings — user display name and username
        # are relayed from Telegram and may contain embedded newlines that
        # would forge extra frontmatter fields (#413 §7 S3).
        _TELEGRAM_COPIES = {
            "message_id": "cloud_message_id",
            "user_id": "cloud_user_id",
        }
        _TELEGRAM_STR_COPIES = {
            "user": "cloud_user",
            "username": "cloud_username",
        }
        for src, dst in _TELEGRAM_COPIES.items():
            value = reply_to.get(src)
            if value not in (None, ""):
                meta[dst] = value
        for src, dst in _TELEGRAM_STR_COPIES.items():
            value = reply_to.get(src)
            if value not in (None, ""):
                meta[dst] = _sanitize_meta_str(str(value))
        return meta
    if platform == "github":
        repo = str(reply_to.get("repo") or "")
        issue_number = reply_to.get("issue_number")
        meta["cloud_chat_id"] = f"{repo}#{issue_number}" if repo and issue_number not in (None, "") else ""
        copies = {"repo": "github_repo", "kind": "github_kind", "issue_number": "github_issue_number", "comment_id": "github_comment_id", "author": "github_author", "html_url": "github_html_url", "trigger": "github_trigger", "mention": "github_mention", "pr_number": "github_pr_number", "branch_target": "branch_target"}
        for src, dst in copies.items():
            value = reply_to.get(src)
            if value not in (None, ""):
                meta[dst] = value
        return meta
    if platform == "whatsapp":
        # chat_id is the customer's own wa_id (E.164 phone number, no
        # topic concept) — same identity carries both the conversation key
        # and the reply-to address (see routers.webhooks's WhatsApp block).
        chat_id = reply_to.get("chat_id")
        meta["cloud_chat_id"] = "" if chat_id is None else chat_id
        message_id = reply_to.get("message_id")
        if message_id not in (None, ""):
            meta["cloud_message_id"] = message_id
    return meta


# #525 — attachment ingestion through the server's read-through proxy.
# Same hard ceiling as the local telegram gate's download path; the server
# additionally enforces its own (config-able, default smaller) cap.
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _download_attachment(base_url: str, token: str, event_id: str, index: int, dest: Path) -> bool:
    """Fetch one attachment via the proxy into *dest*. Returns success.

    Best-effort by design, like the local gate's ``_download_telegram_file``:
    any failure (expired telegram file link → 502, over-cap → 413, network
    hiccup) returns ``False`` so ingestion degrades to an annotated event
    rather than dropping the message. Not ``_request`` — that seam is JSON;
    this one streams bytes.
    """
    url = base_url.rstrip("/") + f"/v1/daemons/events/{event_id}/attachments/{index}"
    try:
        resp = _SESSION.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=_HTTP_TIMEOUT_S, stream=True)
        if not 200 <= resp.status_code < 300:
            print(f"[brnrd:cloud] attachment {event_id}[{index}] -> {resp.status_code}: {resp.text[:120]}")
            return False
        size = 0
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(65536):
                size += len(chunk)
                if size > _MAX_ATTACHMENT_BYTES:
                    return False
                fh.write(chunk)
    except requests.RequestException as exc:
        print(f"[brnrd:cloud] attachment {event_id}[{index}] fetch failed: {exc}")
        return False
    return True


def _safe_attachment_name(pointer: dict, index: int) -> str:
    """Filename for a pointer — basename only, never a smuggled path."""
    raw = str(pointer.get("filename") or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if raw in ("", ".", ".."):
        raw = f"attachment-{index:02d}"
    return raw[:128]


def _attachment_names(raw: object) -> list[str] | None:
    """One best-effort filename per attachment on the wire, or ``None``.

    The bytes are fetched **by index** (``/attachments/{i}``), never by any
    field of the pointer — so all this has to recover is *how many* and *what
    to call them*. That makes it worth being generous about shape and strict
    about silence.

    ``None`` is the load-bearing return: *the event announced attachments and
    no count could be derived from them*. The predecessor of this function was
    a filter —

        pointers = [p for p in (ev.get("attachments") or []) if isinstance(p, dict)]

    — which answered that case with ``[]``, the same value it returns for an
    event that simply has no attachments. Zero pointers, zero downloads, zero
    failures, zero annotation: a drop byte-identical to a no-op at every
    surface a reader can see, including the resident's. It cost two days and a
    direct user question (#1154). **A filter is not a parser** — it cannot say
    "I did not understand this", and a shape it does not understand is
    precisely the shape that arrives after the other end changes.
    """
    if not raw:
        return []
    if isinstance(raw, (str, bytes)):
        # A bare filename, not an iterable of pointers. Iterating it yields
        # characters, which is how this shape used to vanish.
        name = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        return [_safe_attachment_name({"filename": name}, 0)]
    if isinstance(raw, dict):
        return [_safe_attachment_name(raw, 0)]
    if isinstance(raw, (list, tuple)):
        names: list[str] = []
        for i, item in enumerate(raw):
            if isinstance(item, dict):
                names.append(_safe_attachment_name(item, i))
            elif isinstance(item, str):
                names.append(_safe_attachment_name({"filename": item}, i))
            else:
                names.append(f"attachment-{i:02d}")
        return names
    return None


def _log_raw_attachments_observation(ev: dict) -> None:
    """Unconditional wire-shape trace for one inbound event (#1156 §2).

    #1155 hardened ``_attachment_names`` so an unparseable shape is loud
    instead of silent — but its own incident report named a *cause*
    ("the wire sent a bare string") that was inferred from a filename
    convention, never actually observed on the wire: nothing recorded what
    ``attachments`` looked like before the parser ran. That gap survives
    #1155 intact for the case that matters most — a shape the parser
    accepts without complaint but that still doesn't end in bytes, which
    is silent by construction on the unrecognised-only path #1155 shipped.
    Logged for every inbound event, not only ones that carry attachments,
    through the same ``print`` channel this gate already narrates failures
    on (stdout, captured to the daemon's own log — never the wake response).
    Type name plus a size-bounded ``repr`` only: an attachment pointer is
    ``{"file_id", "filename", "kind", "file_size"}`` (``schemas.py``), never
    a bearer token, but the bound is unconditional regardless of what the
    field actually holds.
    """
    raw = ev.get("attachments")
    print(
        f"[brnrd:cloud] event {ev.get('event_id')} attachments wire shape: "
        f"type={type(raw).__name__} repr={repr(raw)[:200]}"
    )


def _ingest_event_attachments(
    state: dict, ev: dict, workdir: Path,
) -> tuple[list[Path], list[str], str | None]:
    """Pull *ev*'s attachments down into local files under *workdir*.

    Returns ``(downloaded_paths, failed_names, unrecognised)``. Downloaded
    files land in the exact ``attachment_files`` shape the telegram and github
    gates produce (one convention, three gates — see
    ``gates/github/attachments.py``); failures come back by name for an honest
    #553-style annotation in the event body, and ``unrecognised`` carries a
    description of an announced-but-unparseable ``attachments`` field so the
    drop can be announced too (#1154).
    """
    files: list[Path] = []
    failed: list[str] = []
    raw = ev.get("attachments")
    names = _attachment_names(raw)
    if names is None:
        # Name the observed shape, not just the fact — the next occurrence
        # should hand back enough to write the parser without another round
        # trip to the wire.
        return files, failed, f"{type(raw).__name__}: {str(raw)[:160]}"
    for i, name in enumerate(names):
        dest = workdir / f"{i:02d}-{name}" if len(names) > 1 else workdir / name
        if _download_attachment(state["brnrd_url"], state["token"], str(ev.get("event_id") or ""), i, dest):
            files.append(dest)
        else:
            failed.append(name)
    return files, failed, None


def _annotate_failures(
    body: str, failed: list[str], unrecognised: str | None = None,
) -> str:
    notes = [
        f"[attachment \"{name}\" could not be fetched — the source media may have "
        "expired or the file exceeds the size cap; ask the sender to re-send it]"
        for name in failed
    ]
    if unrecognised:
        notes.append(
            "[this event announced attachments in a shape this daemon could not read, "
            f"so none were fetched — observed {unrecognised}. The bytes did not arrive; "
            "report this shape rather than assuming the message had no images]"
        )
    if not notes:
        return body
    joined = "\n".join(notes)
    return f"{body}\n\n{joined}" if body else joined


def _reconcile_grown_attachments(
    state: dict, inbox_dir: Path, local_event: dict, ev: dict,
) -> bool:
    """Fold newly-arrived attachments into an already-ingested event.

    #1396 — a merged Telegram album item bumps the server-side event's
    ``seq`` so it re-enters this daemon's delivery window
    (``inbox_service._merge_into_open_media_group``), but its *identity* —
    ``event_id`` — never changes; that is the whole point, one logical
    message rather than several. Treating any already-seen
    ``cloud_event_id`` as a pure replay (the pre-#1396 behaviour, and the
    finding this closes) silently drops that growth: the poll re-serves 3
    photos, this daemon already has 1 on disk from an earlier poll, and the
    other 2 vanish with no error anywhere. This reconciles instead — the
    merge only ever *appends* attachments server-side
    (``attachments_of(existing) + attachments``), so anything beyond the
    count already local is new by construction; download just that tail and
    fold it in. Returns ``True`` on a real reconcile (something appended),
    ``False`` when there was nothing new — the caller's replay-drop path
    still applies to an honest replay.
    """
    already = len(protocol.event_attachment_names(local_event))
    names = _attachment_names(ev.get("attachments"))
    if not names or len(names) <= already:
        return False
    event_id = str(ev.get("event_id") or "")
    with tempfile.TemporaryDirectory() as tmpdir:
        new_files: list[Path] = []
        failed: list[str] = []
        for i in range(already, len(names)):
            dest = Path(tmpdir) / f"{i:02d}-{names[i]}"
            if _download_attachment(state["brnrd_url"], state["token"], event_id, i, dest):
                new_files.append(dest)
            else:
                failed.append(names[i])
        added = protocol.append_event_attachments(inbox_dir, local_event, new_files)
    if failed:
        print(
            f"[brnrd:cloud] event {event_id} grew {len(failed)} attachment(s) this "
            f"daemon could not fetch on reconcile — {failed}"
        )
    if added:
        print(
            f"[brnrd:cloud] event {event_id} gained {len(added)} attachment(s) from "
            "a merged album item"
        )
    return bool(added)


def _loop_once(brr_dir: Path, inbox_dir: Path, responses_dir: Path) -> None:
    state = _load_state(brr_dir)
    since = state.get("since", 0)
    result = _request(state["brnrd_url"], "GET", "/v1/daemons/inbox", token=state["token"], params={"since": since, "wait": _POLL_WAIT_S})
    # What prod is actually running, riding this same long-poll response
    # (schemas.InboxResponse.server, brnrd 2026-07-30) — no new request, no
    # new poll loop. Absent on an older brnrd that doesn't send the block
    # yet; the wake renders that as "no cloud fingerprint yet", not a crash.
    server = result.get("server")
    if isinstance(server, dict):
        runtime.save_server_fingerprint(_state_dir(brr_dir), "cloud", server)
    events = result.get("events", [])
    # Ingest on identity, not on position. ``since`` is the only thing that
    # normally keeps an answered event off the wire, and it is one integer in
    # one local file — ``connect`` writes ``since: 0`` whenever it cannot find
    # the previous value, and the server has no delivery state of its own to
    # correct it with. That happened on 2026-07-30 after the Scaleway cutover:
    # 339 events replayed in a single poll, every one of them already
    # answered, 120 of them still sitting in this very directory stamped
    # ``delivered``. The daemon had the evidence and never looked at it.
    known = protocol.known_origin_events(inbox_dir, "cloud_event_id") if events else {}
    replayed = 0
    for ev in events:
        local_event = known.get(str(ev.get("event_id") or ""))
        if local_event is not None:
            # Already ingested under some earlier cursor — normally what
            # makes a cursor reset cost nothing. But the *same* event_id can
            # legitimately re-arrive carrying more attachments than it did
            # the first time (a Telegram album item merging into it
            # server-side, #1396's `_merge_into_open_media_group`, bumps its
            # seq to re-enter this daemon's delivery window without changing
            # its identity) — that growth must be folded in, not dropped as
            # an ordinary replay.
            if not _reconcile_grown_attachments(state, inbox_dir, local_event, ev):
                replayed += 1
            continue
        _log_raw_attachments_observation(ev)
        # #525 — pointers become local files *now*, at ingestion time: the
        # server holds no bytes, telegram links expire, and the wake's Read
        # tool wants a plain local path (``attachment_files`` convention).
        with tempfile.TemporaryDirectory() as tmpdir:
            attachment_files, failed, unrecognised = _ingest_event_attachments(
                state, ev, Path(tmpdir)
            )
            if unrecognised:
                # Loud, not silent: an announced attachment that never became
                # bytes is a drop, and the operator is the only one who can
                # act on the shape (#1154).
                print(
                    f"[brnrd:cloud] event {ev.get('event_id')} announced attachments "
                    f"this daemon could not read — {unrecognised}"
                )
            protocol.create_event(
                inbox_dir,
                source="cloud",
                body=_annotate_failures(ev.get("body") or "", failed, unrecognised),
                attachment_files=attachment_files or None,
                cloud_event_id=ev["event_id"],
                repo_label=ev.get("repo_label") or "",
                **_origin_meta(ev.get("reply_to") or {}),
            )
    if replayed:
        # Loud, not silent: a dropped replay means the cursor and the server
        # disagree about history, and that is worth an operator seeing once
        # per poll rather than discovering as a queue that will not drain.
        print(
            f"[brnrd:cloud] dropped {replayed} already-ingested event(s) — "
            f"the poll cursor ({since}) is behind what this inbox has answered"
        )
    cursor = result.get("cursor", since)
    if cursor != since:
        # Trust the server's cursor in both directions: it moves up as
        # events deliver, and moves *down* when the server detects a
        # cursor from an older DB epoch and heals it (brnrd
        # ``inbox_service.clamp_since``). Rejecting the lower value kept a
        # stale cursor stale forever.
        state["since"] = cursor
        _save_state(brr_dir, state)
    _deliver_responses(brr_dir, inbox_dir, responses_dir, state)
    _close_noted_events(inbox_dir, state)


#: How many noted closes one poll will push. A resident can retire a very
#: large batch in a single run (1,202 on 2026-08-14), and each close is its
#: own round trip; unbounded, that one sweep would hold the gate loop for
#: minutes and starve the poll that feeds it. The remainder rides the next
#: poll — the local state is already terminal, so nothing is lost by taking
#: several ticks to tell the server about it.
_NOTED_CLOSE_BATCH = 50


def _close_noted_events(inbox_dir: Path, state: dict) -> None:
    """Tell the server about events this daemon retired without speaking.

    ``note:`` is silent *to the correspondent* — that is its whole point —
    but it was silent to the **server** too, and that is a different thing
    wearing the same word. A noted event kept ``status = queued`` in the
    events table forever: it never got a terminal ``done`` post, so nothing
    ever closed it. Consequences, both measured on 2026-08-14:

    - the queued set only ever grew, and it is the one structural defence
      against a replay (``brnrd/inbox.py`` ``_QUEUED_ONLY_RATIONALE``);
    - ``clamp_since``'s floor is ``oldest_queued - 1``, so every ancient
      never-closed letter pinned the cursor heal at the beginning of time.

    A fresh account home then polled ``since = 0`` and was handed 1,226
    events, the great majority already read and deliberately closed on the
    machine that came before. The evidence of the close existed; it just
    never crossed the wire.

    Best-effort and idempotent. A failure leaves the local stamp unwritten,
    so the next poll retries; a 404 means the server already forgot the row
    (GC, or a different daemon closed it), which is the same end state and
    is stamped rather than retried forever.
    """
    if not (state.get("brnrd_url") and state.get("token")):
        return
    closed = 0
    for event in protocol.list_noted(inbox_dir, "cloud"):
        if closed >= _NOTED_CLOSE_BATCH:
            break
        if event.get("cloud_closed_at"):
            continue
        cloud_event_id = str(event.get("cloud_event_id") or "").strip()
        if not cloud_event_id:
            # Locally minted, never came from the cloud: nothing to tell.
            continue
        try:
            _request(
                state["brnrd_url"], "POST", "/v1/daemons/responses",
                token=state["token"],
                json={
                    "event_id": cloud_event_id,
                    "body_markdown": "",
                    "status": "noted",
                },
                retry=False,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort sweep
            if getattr(exc, "status_code", None) != 404:
                # Transient or auth: leave it unstamped and retry next poll.
                # One line, not one per event, or a large batch would bury
                # the log it is trying to explain.
                if closed == 0:
                    print(
                        f"[brnrd:cloud] noted-close sweep deferred: {exc}"
                    )
                return
        try:
            protocol.update_event_meta(
                event,
                cloud_closed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        except OSError:
            continue
        closed += 1


def _deliver_responses(brr_dir: Path, inbox_dir: Path, responses_dir: Path, state: dict) -> None:
    # Interim messages must NOT post ``status: "done"``: the server marks the
    # event responded on the first done-status post and silently skips the
    # platform forward for every later one — so a run's first interim used to
    # consume the event's single delivery slot and the terminal reply vanished
    # while the daemon cleaned it up as delivered (found live 2026-07-18, the
    # overnight fleet closeout that never reached the maintainer).
    overflow_cache = delivery.OverflowCache(_state_dir(brr_dir), "cloud")

    def post(event: dict, body: str, status: str) -> dict:
        cloud_event_id = event.get("cloud_event_id")
        if not cloud_event_id:
            # #1205: no inbound event to answer against — the fresh-send
            # primitive, keyed on platform chat identity instead of an
            # event id. `cloud_platform` carries this synthesized event's
            # own addressing meta when a caller set one
            # (`_deliver_out_of_bound`'s `target_meta`); default to
            # telegram — the only platform the server can originate a
            # fresh send on today — when it didn't, which is the common
            # shape: `notify.gate`'s fallback path never sets one.
            platform = str(event.get("cloud_platform") or "") or "telegram"
            limit = _RESPONSE_LIMITS.get(platform)
            if limit is not None:
                body = delivery.resolve_overflow(
                    body,
                    limit=limit,
                    gist_fn=delivery.post_gist,
                    cache=overflow_cache,
                )
            try:
                return _request(
                    state["brnrd_url"], "POST", "/v1/daemons/messages",
                    token=state["token"],
                    json={"body_markdown": body, "platform": platform},
                )
            except RuntimeError as e:
                if getattr(e, "status_code", None) == 501:
                    # No resolver wired for this platform server-side — a
                    # future retry cannot change that without a server
                    # deploy, same posture as the missing-address case this
                    # replaced.
                    raise runtime.PermanentDeliveryError(str(e)) from e
                raise
        limit = _RESPONSE_LIMITS.get(event.get("cloud_platform") or "")
        if limit is not None:
            body = delivery.resolve_overflow(
                body,
                limit=limit,
                gist_fn=delivery.post_gist,
                cache=overflow_cache,
            )
        payload = {"event_id": cloud_event_id, "body_markdown": body, "status": status}
        # Conversation identity for brnrd's metadata-only conversation graph
        # (kb/plan-conversation-id-propagation.md): the existing
        # conversation_key string, omitted when the event resolves to none.
        conversation_id = conversations.conversation_key_for_event(event)
        if conversation_id:
            payload["conversation_id"] = conversation_id
        return _request(state["brnrd_url"], "POST", "/v1/daemons/responses", token=state["token"], json=payload)

    runtime.deliver_stream(
        inbox_dir,
        responses_dir,
        "cloud",
        deliver_partial=lambda event, body: post(event, body, "processing"),
        deliver_terminal=lambda event, body: post(event, body, "done"),
        brr_dir=_state_dir(brr_dir),
    )


class _CloudCardTransport:
    def __init__(self, state: dict, event_id: str) -> None:
        self._state = state
        self._event_id = event_id

    def _post(self, body: dict) -> dict:
        return _request(self._state["brnrd_url"], "POST", "/v1/daemons/card", token=self._state["token"], json=body)

    def send(self, text: str, *, reply_to: int | None = None) -> int | str | None:
        return self._post({"event_id": self._event_id, "text": text}).get("message_id")

    def edit(self, message_id: int | str, text: str) -> None:
        try:
            self._post({"event_id": self._event_id, "text": text, "message_id": message_id})
        except RuntimeError as exc:
            # The server maps a Telegram "message gone" (``tg.CardGone``)
            # to 409 on this endpoint (``routers/daemons.py`` post_card) —
            # the one status this transport can trust as "actually gone"
            # rather than a passing 5xx/timeout.
            if getattr(exc, "status_code", None) == 409:
                raise delivery.CardGone(str(exc)) from exc
            raise


def _card_text_for(brr_dir: Path, conv_key: str, run_id: str, platform: str) -> str | None:
    if platform == "telegram":
        from . import telegram
        return telegram.card_text(brr_dir, conv_key, run_id)
    if platform == "whatsapp":
        view = run_progress.project_run(brr_dir, conv_key, run_id)
        if view is None:
            return None
        return run_progress.render_text(view, compact=True)
    return None


def render_update(brr_dir: Path, packet: Any) -> None:
    if getattr(packet, "type", None) not in run_progress.CARD_PACKETS:
        return
    state = _load_state(brr_dir)
    if not (state.get("token") and state.get("brnrd_url")):
        return
    conv_key = getattr(packet, "conversation_key", "") or ""
    run_id = run_progress.run_id_from_packet(packet)
    if not conv_key or not run_id:
        return
    task = Run.from_file(run_manifest_path(brr_dir / "runs", run_id))
    if task is None or task.source != "cloud":
        return
    cloud_event_id = task.meta.get("cloud_event_id")
    if not cloud_event_id:
        return
    text = _card_text_for(brr_dir, conv_key, run_id, str(task.meta.get("cloud_platform") or ""))
    if text is None:
        return
    delivery.update_card(brr_dir, "cloud", run_id, text, transport=_CloudCardTransport(state, str(cloud_event_id)), render_tag=getattr(packet, "type", None))
