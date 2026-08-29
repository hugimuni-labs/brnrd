"""Lane liveness — ``200`` beside ``set``, for the credentials brnrd owns.

``set`` is a fact about a file. ``200`` is a fact about the world. The wake
already served the first (``bot token set``) and never the second, so a
resident that needed to know whether a credential still *worked* had exactly
one place to keep that: a note. A note cannot refresh itself, and the two
surfaces that rotted through August both stored a verdict about external
state ("brnrd.dev cookie: dead since 08-15") while every note that never
rotted stored a coordinate or a command. The rot was caused by the gap, not
by inattention — so this module closes the gap rather than nagging about the
notes.

**Every rule below is a way this could be built wrong, and each one was.**

- *A missing answer renders as missing, never as healthy.* ``absent`` (nobody
  has ever probed here), ``error`` (we asked and could not tell), and
  ``no_probe`` (this lane has no safe read-only check) are three distinct
  renderings, and none of them looks like ``200``. The whole item exists
  because a surface that narrows renders identically to one that didn't.
- *Never spend a credential to test it.* Read-only endpoints only. A lane
  whose only authenticated read has a side effect — consuming a cursor,
  rotating a refresh token — gets ``no_probe`` **with its reason rendered**,
  not a probe that quietly costs something.
- *The probed value never appears in the rendered block.* ``.card`` is
  mirrored to the dashboard unredacted, and Telegram carries its bot token in
  the URL path, so a bare ``requests`` exception string leaks it. Two
  defences, because they cover different failures: a probe that knows which
  secret it holds routes its detail through :func:`_scrub`; a **catch-all**,
  which by construction does not know, reports only the exception type via
  :func:`_blind_detail`.
- *No verdict is cached where a later wake could read it as fresh.* The cache
  carries ``checked_at`` and the renderer always prints the age. Past
  :data:`STALE_AFTER_SECONDS` it says ``stale`` and prints the age anyway.

**Assembly stays network-free**, the same constraint :mod:`brr.forge_pr_cache`
exists to hold: the prompt path may only ever *read* this cache. The probes
run on the daemon's own tick (:func:`refresh_if_stale_async`), TTL-guarded,
bounded, off the loop thread — never at prompt-assembly time.

One deliberate divergence from ``forge_pr_cache``: a failed refresh here does
**not** carry the last good rows forward. There, stale PR rows are still worth
seeing with their true age attached. Here the failure *is* the answer a reader
needs — "we asked GitHub and could not tell" is a different world-state from
"GitHub said 200 four minutes ago", and showing the older ``200`` under a
fresh attempt is precisely the failure-indistinguishable-from-success this
module was built to end.

**This surface names its own edge.** It enumerates *configured gates*, from
:func:`brr.gates.runtime.configured_gates` — discovery, never a hardcoded
list, because a class defined by listing its members meets the member nobody
listed. But credentials living outside gate state (the X envoy's
``x-brnrd-resident.env``, an operator's ad-hoc ``.tmp/*.cookie``) are not
enumerable from here, so the rendered block says so rather than letting three
green lanes read as "every credential is live".
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from . import gitops

CACHE_NAME = "lane-liveness.json"
SCHEMA = 1

#: The key this facet occupies on the wake's communication snapshot. Named
#: once and imported by both ends (``daemon`` writes it, ``prompts`` reads it)
#: so the two cannot drift: a typo on either side would unwire the block
#: silently, and a block that silently stops rendering is exactly the failure
#: this module exists to make impossible.
FACET_KEY = "lanes"

#: Credential liveness moves on human timescales (a revoke, an expiry), and
#: every probe is a real network round-trip against someone else's API. Five
#: minutes matches ``forge_pr_cache`` and keeps the block honest without
#: hammering Telegram/Slack/GitHub from every daemon tick.
DEFAULT_TTL_SECONDS = 300.0

#: Beyond this the rendered block calls itself stale and labels its age.
#: Deliberately *longer* than the TTL: between the two, a block that has not
#: refreshed yet still reads with its real age rather than crying stale on a
#: cache the daemon is about to renew anyway.
STALE_AFTER_SECONDS = 900.0

#: Per-probe wall-clock ceiling. Small on purpose — this runs on a background
#: thread the daemon does not wait for, but five lanes serialised behind a
#: wedged endpoint should still finish inside one TTL window.
PROBE_TIMEOUT_SECONDS = 6.0

_SESSION = requests.Session()

#: Sentinel for :func:`_classify_http`: this lane reports purely by status
#: code and declares no JSON envelope, so no body check applies.
_NO_BODY_CHECK = object()

# One in-flight refresh at a time, process-wide: the daemon ticks every few
# seconds and a full sweep can take PROBE_TIMEOUT_SECONDS per lane, so without
# this a slow endpoint would stack threads.
_refresh_lock = threading.Lock()
_refreshing = False

#: Epoch of the last refresh whose cache write failed. Guards the one path
#: where the on-disk cache cannot carry the TTL (read-only FS, ENOSPC).
_last_failed_write = 0.0


# ── Outcomes ─────────────────────────────────────────────────────────
#
# ``ok``          the credential answered as itself. ``code`` is the HTTP status.
# ``auth_failed`` the endpoint answered, and said no. ``code`` is 401/403, or
#                 the API's own auth-level refusal on a 200 envelope (Slack
#                 and Telegram both signal ``invalid_auth`` inside a 200).
# ``error``       we asked and could not tell — timeout, DNS, 5xx, bad JSON.
#                 NOT a synonym for dead: a 401 answers "this request is not
#                 authenticated"; it never answers "this credential is spent",
#                 and neither does a timeout.
# ``no_probe``    configured, but there is no read-only check that costs
#                 nothing. Carries ``detail`` — the reason, rendered.


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scrub(text: str, *secrets: str | None) -> str:
    """Strip every credential value out of *text*, then bound its length.

    Telegram puts the bot token in the URL path, so ``str(RequestException)``
    contains it verbatim; Slack and GitHub can echo an ``Authorization``
    header into a proxy error. Nothing from a probe reaches the cache — which
    is read by a ``.card`` mirrored to the dashboard unredacted — without
    passing through here.
    """
    out = str(text)
    for secret in secrets:
        # >= 4, not >= 8: nothing enforces that a probed credential is long,
        # and a guard that silently stops redacting below some length is the
        # same class of bug as a verdict that silently stops refreshing.
        if isinstance(secret, str) and len(secret.strip()) >= 4:
            out = out.replace(secret.strip(), "<redacted>")
    out = " ".join(out.split())
    return out[:160]


def _blind_detail(exc: BaseException) -> str:
    """The exception's *type* only — never its message.

    For the catch-all handlers, which exist precisely for failures nobody
    foresaw. :func:`_scrub` can only remove secrets a caller knows to pass it,
    and an unforeseen exception is by definition one whose message I cannot
    promise is clean. A class name is diagnostic enough to start from and
    cannot carry a credential.
    """
    return f"unexpected {type(exc).__name__}"


def _outcome(
    lane: str,
    outcome: str,
    *,
    code: int | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {"lane": lane, "outcome": outcome, "code": code}
    if detail:
        row["detail"] = detail
    return row


#: Error markers that mean *the credential was refused*, as opposed to *the
#: request was malformed*. Deliberately specific: a bare ``invalid`` or
#: ``token`` matches Slack's ``invalid_arguments`` / ``invalid_cursor``, which
#: a perfectly live credential can produce — and sending a reader to rotate a
#: working token is its own kind of false verdict.
_AUTH_MARKERS = (
    "auth",           # invalid_auth · not_authed · missing_auth
    "unauthorized",
    "forbidden",
    "token_revoked",
    "token_expired",
    "expired",
    "revoked",
)


def _classify_http(
    lane: str,
    status: int,
    *,
    payload: Any = _NO_BODY_CHECK,
    ok_key: str | None = "ok",
    error_key: str | None = None,
    secrets: tuple[str | None, ...] = (),
) -> dict[str, Any]:
    """Turn one HTTP answer into an outcome row.

    Two separate traps live here, and both render a dead or unreached lane as
    ``200`` if you miss them.

    **The envelope.** Telegram and Slack answer ``200`` with
    ``{"ok": false, "error": "invalid_auth"}``. Reading only the status code
    renders a revoked token as green.

    **The body that is not an envelope at all.** A corporate MITM proxy, a
    captive portal, or a Cloudflare interstitial answers ``200 text/html``.
    The request never reached the API, ``response.json()`` raises, and a
    classifier that treats "no envelope" as "no refusal" prints ``200`` for a
    probe that spoke to a proxy. So a lane that *declares* an envelope
    (*payload* passed) must **get** one: a 2xx that is not a JSON object, or a
    JSON object missing the key that carries the verdict, is ``error`` — we
    asked and could not tell. Only a lane that passes no *payload* at all is
    classified on the status code alone.
    """
    if status in (401, 403):
        return _outcome(lane, "auth_failed", code=status)
    if status >= 500:
        return _outcome(lane, "error", code=status, detail="server error")
    if not 200 <= status < 300:
        return _outcome(lane, "error", code=status, detail="unexpected status")

    if payload is _NO_BODY_CHECK:
        return _outcome(lane, "ok", code=status)
    if not isinstance(payload, dict):
        # Includes the bodyless 2xx (204) and the HTML interstitial.
        return _outcome(
            lane, "error", code=status, detail="2xx with no JSON object body",
        )
    if ok_key is None:
        return _outcome(lane, "ok", code=status)
    verdict = payload.get(ok_key)
    if verdict is True:
        return _outcome(lane, "ok", code=status)
    if verdict is None:
        return _outcome(
            lane, "error", code=status,
            detail=f"2xx envelope carries no {ok_key!r}",
        )
    detail = _scrub(
        (error_key and str(payload.get(error_key) or "")) or "api refused",
        *secrets,
    )
    lowered = detail.lower()
    auth_shaped = any(mark in lowered for mark in _AUTH_MARKERS)
    return _outcome(
        lane,
        "auth_failed" if auth_shaped else "error",
        code=status,
        detail=detail,
    )


# ── Probes ───────────────────────────────────────────────────────────


def _probe_telegram(brr_dir: Path) -> dict[str, Any]:
    """``getMe`` — Telegram's own identity read, and what ``auth`` validates with."""
    from .gates import runtime as gate_runtime

    token = str(gate_runtime.load_state(brr_dir, "telegram").get("token") or "").strip()
    if not token:
        return _outcome("telegram", "no_probe", detail="no token in gate state")
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        response = _SESSION.get(url, timeout=PROBE_TIMEOUT_SECONDS)
    except requests.Timeout:
        return _outcome("telegram", "error", detail="timeout")
    except requests.RequestException as exc:
        return _outcome("telegram", "error", detail=_scrub(exc, token))
    return _classify_http(
        "telegram", response.status_code,
        payload=_json_or_none(response), ok_key="ok", error_key="description",
        secrets=(token,),
    )


def _probe_slack(brr_dir: Path) -> dict[str, Any]:
    """``auth.test`` — read-only, and already what ``slack.auth`` validates with."""
    from .gates import runtime as gate_runtime

    token = str(gate_runtime.load_state(brr_dir, "slack").get("token") or "").strip()
    if not token:
        return _outcome("slack", "no_probe", detail="no token in gate state")
    try:
        response = _SESSION.post(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        return _outcome("slack", "error", detail="timeout")
    except requests.RequestException as exc:
        return _outcome("slack", "error", detail=_scrub(exc, token))
    return _classify_http(
        "slack", response.status_code,
        payload=_json_or_none(response), ok_key="ok", error_key="error",
        secrets=(token,),
    )


def _probe_github(brr_dir: Path) -> dict[str, Any]:
    """``GET /rate_limit`` — the one GitHub read that costs literally nothing.

    Not ``/user`` (which :func:`brr.gates.github.state._validate_token` uses):
    ``/rate_limit`` is documented as not counting against the quota it reports,
    which is the strictest available reading of *never spend a credential to
    test it*, and it authenticates every GitHub credential *kind* rather than
    just user-shaped ones. (An earlier draft of this docstring justified it by
    the App installation token ``cloud_credentials`` mints — that was wrong:
    :func:`~brr.gates.github.state.resolve_token` reads only a stored token,
    ``gh auth token``, or ``GH_TOKEN``/``GITHUB_TOKEN``, so this probe can
    never see that credential. The two real reasons stand on their own.)

    **This lane's wall-clock ceiling is not** :data:`PROBE_TIMEOUT_SECONDS`.
    ``resolve_token`` may fall through to ``gh auth token``, its own
    ``subprocess.run(timeout=10)``, *before* the HTTP call starts — so a wedged
    ``gh`` makes this lane ~16s, not 6s. It runs on a background thread inside
    a 300s TTL and cannot stall a wake, so the extra latency is accepted rather
    than worked around by reaching into the gate's credential resolution.
    """
    from .gates.github import state as gh_state
    from .gates import runtime as gate_runtime

    try:
        token = gh_state.resolve_token(gate_runtime.load_state(brr_dir, "github"))
    except Exception as exc:  # noqa: BLE001 - a credential read never breaks the sweep
        return _outcome("github", "error", detail=_blind_detail(exc))
    token = (token or "").strip()
    if not token:
        return _outcome("github", "no_probe", detail="no token resolved")
    try:
        response = _SESSION.get(
            "https://api.github.com/rate_limit",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        return _outcome("github", "error", detail="timeout")
    except requests.RequestException as exc:
        return _outcome("github", "error", detail=_scrub(exc, token))
    return _classify_http(
        "github", response.status_code,
        payload=_json_or_none(response), ok_key=None, secrets=(token,),
    )


def _probe_cloud(brr_dir: Path) -> dict[str, Any]:
    """No probe, and the reason is the point.

    ``/v1/daemons`` exposes exactly two authenticated GETs, and the only one
    that would answer "is this bearer token alive" is ``/v1/daemons/inbox`` —
    the gate loop's own cursored long-poll. Racing the loop's cursor from a
    second caller is a side effect, and a 25-second long-poll is not
    probe-shaped. A dedicated whoami route is the fix; until then this lane
    renders as unprobed rather than as healthy.
    """
    return _outcome(
        "cloud",
        "no_probe",
        detail="no whoami route; the only authenticated read is the gate's own long-poll cursor",
    )


def _probe_signal(brr_dir: Path) -> dict[str, Any]:
    """No probe: this lane has no credential to test.

    ``signal-cli-rest-api`` is a self-hosted container the gate reaches by URL
    — ``/v1/about`` is unauthenticated. Probing it would answer *is the
    container up*, which is a different question, and rendering that answer in
    a credential-liveness column is exactly the conflation this block exists to
    end.
    """
    return _outcome(
        "signal",
        "no_probe",
        detail="local bridge, no credential to test",
    )


#: Probe per built-in gate. A gate that is configured but absent from this map
#: still renders — as ``no_probe``, "no probe implemented" — so adding a gate
#: to ``BUILTIN_GATES`` and forgetting it here shows up as a visible gap
#: rather than as a lane silently missing from the block.
PROBES: dict[str, Callable[[Path], dict[str, Any]]] = {
    "telegram": _probe_telegram,
    "slack": _probe_slack,
    "github": _probe_github,
    "cloud": _probe_cloud,
    "signal": _probe_signal,
}


def _json_or_none(response: requests.Response) -> Any:
    """The decoded body, or ``None`` when it is not JSON.

    ``requests.exceptions.JSONDecodeError`` is a ``ValueError``, but
    ``ChunkedEncodingError`` — raised when a lazily-read body dies mid-transfer
    — is not, so catching ``ValueError`` alone lets a transport failure escape
    the probe and lose its lane classification. ``None`` is never "no
    refusal": :func:`_classify_http` treats it as ``error`` for any lane that
    declared an envelope.
    """
    try:
        return response.json()
    except (ValueError, requests.RequestException):
        return None


# ── Cache ────────────────────────────────────────────────────────────


def cache_path(repo_root: Path) -> Path:
    return gitops.shared_brr_dir(repo_root) / CACHE_NAME


def parse_iso(raw: Any) -> float | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def load(repo_root: Path) -> dict[str, Any] | None:
    """The cache as written, however old — ``None`` when nothing has probed here."""
    try:
        data = json.loads(cache_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(repo_root: Path, payload: dict[str, Any]) -> Path | None:
    path = cache_path(repo_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return None
    return path


def read_state(
    repo_root: Path,
    *,
    now: float | None = None,
    stale_after: float = STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """Network-free read: the cache plus its own freshness verdict.

    ``status`` is ``absent`` | ``stale`` | ``fresh``, and ``lanes`` is ``None``
    for ``absent`` — never ``[]``, which a reader could take for "no lanes are
    configured" rather than "nobody has looked".
    """
    cached = load(repo_root)
    if cached is None:
        return {
            "status": "absent",
            "checked_at": None,
            "age_seconds": None,
            "lanes": None,
            "discovery": None,
        }
    checked_at = cached.get("checked_at")
    checked_epoch = parse_iso(checked_at)
    age: float | None = None
    if checked_epoch is not None:
        age = max(0.0, (time.time() if now is None else now) - checked_epoch)
    rows = cached.get("lanes")
    lanes = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else None
    if lanes is None:
        return {
            "status": "absent",
            "checked_at": None,
            "age_seconds": None,
            "lanes": None,
            "discovery": None,
        }
    discovery = cached.get("discovery")
    return {
        "status": "stale" if age is None or age >= stale_after else "fresh",
        "checked_at": checked_at if isinstance(checked_at, str) else None,
        "age_seconds": age,
        "lanes": lanes,
        # Older caches (written before this field existed) carry no
        # ``discovery`` key. That reads as ``None`` — unknown — and never as
        # ``"ok"``, which would be a guess about a sweep this code did not run.
        "discovery": discovery if discovery in ("ok", "failed") else None,
        "discovery_detail": cached.get("discovery_detail"),
    }


# ── Refresh ──────────────────────────────────────────────────────────


def refresh(repo_root: Path) -> dict[str, Any]:
    """Probe every configured lane and write the cache. Daemon-side only.

    Never raises. A lane whose probe blows up in an unforeseen way lands as
    ``error`` with the scrubbed exception, because one bad lane must not cost
    the reader the other four.
    """
    from .gates import runtime as gate_runtime

    try:
        root = gitops.shared_brr_dir(repo_root)
    except Exception as exc:  # noqa: BLE001 - "never raises" has to be true
        payload = {
            "schema": SCHEMA, "checked_at": _utc_now_iso(),
            "discovery": "failed", "discovery_detail": _blind_detail(exc),
            "lanes": [],
        }
        _write(repo_root, payload)
        return payload

    discovery = "ok"
    discovery_detail: str | None = None
    try:
        configured = gate_runtime.configured_gates(root)
    except Exception as exc:  # noqa: BLE001 - one failure, rendered, not swallowed
        configured = []
        discovery = "failed"
        discovery_detail = _blind_detail(exc)

    lanes: list[dict[str, Any]] = []
    for gate in configured:
        probe = PROBES.get(gate)
        if probe is None:
            lanes.append(_outcome(gate, "no_probe", detail="no probe implemented"))
            continue
        try:
            lanes.append(probe(root))
        except Exception as exc:  # noqa: BLE001 - one lane never kills the sweep
            lanes.append(_outcome(gate, "error", detail=_blind_detail(exc)))

    payload = {
        "schema": SCHEMA,
        "checked_at": _utc_now_iso(),
        "discovery": discovery,
        "lanes": lanes,
    }
    if discovery_detail:
        payload["discovery_detail"] = discovery_detail
    if _write(repo_root, payload) is None:
        # The cache is the TTL. With no file on disk, `read_state` answers
        # `absent` forever and `refresh_if_stale` would re-probe on *every*
        # daemon tick — hammering Telegram/Slack/GitHub for as long as the disk
        # stays unwritable. This in-memory floor is the backstop for that.
        global _last_failed_write
        _last_failed_write = time.time()
    return payload


def refresh_if_stale(
    repo_root: Path,
    *,
    ttl: float = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> bool:
    """Refresh when the cache is older than *ttl*. Returns whether it ran."""
    if (now or time.time()) - _last_failed_write < ttl:
        return False
    state = read_state(repo_root, now=now, stale_after=ttl)
    if state["status"] == "fresh":
        return False
    refresh(repo_root)
    return True


def refresh_if_stale_async(repo_root: Path, *, ttl: float = DEFAULT_TTL_SECONDS) -> bool:
    """Fire :func:`refresh_if_stale` on a daemon thread; never blocks the loop.

    Same contract as ``forge_pr_cache.refresh_if_stale_async``: the scan tick
    is a few seconds and a full lane sweep is several HTTP round-trips, so
    doing it inline would stall dispatch on an event queue waiting for nothing.
    """
    global _refreshing

    with _refresh_lock:
        if _refreshing:
            return False
        if time.time() - _last_failed_write < ttl:
            return False
        if read_state(repo_root, stale_after=ttl)["status"] == "fresh":
            return False
        _refreshing = True

    def _work() -> None:
        global _refreshing
        try:
            refresh(repo_root)
        except Exception as exc:  # noqa: BLE001 - a cache refresh never kills the daemon
            # The type only: this is the catch-all, so it cannot know which
            # secret the failing lane held, and the daemon log is not a place
            # to find out.
            print(f"[brnrd] lane liveness refresh failed: {_blind_detail(exc)}")
        finally:
            with _refresh_lock:
                _refreshing = False

    try:
        threading.Thread(target=_work, name="lane-liveness", daemon=True).start()
    except RuntimeError:
        # Thread creation can fail under exhaustion. Without this, `_refreshing`
        # stays True for the life of the process and the daemon never probes
        # again — a permanent silent stop, which is the failure mode this whole
        # module is about.
        with _refresh_lock:
            _refreshing = False
        return False
    return True


# ── Rendering ────────────────────────────────────────────────────────


def format_age(seconds: float | None) -> str:
    if seconds is None:
        return "age unknown"
    total = int(seconds)
    if total < 90:
        return f"{total}s ago"
    if total < 5400:
        return f"{total // 60}m ago"
    if total < 172800:
        return f"{total // 3600}h ago"
    return f"{total // 86400}d ago"


def _short_stamp(raw: Any) -> str:
    epoch = parse_iso(raw)
    if epoch is None:
        return ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%H:%MZ")


#: Status codes that *are themselves* the refusal, and so may be rendered as
#: the verdict. Anything else — notably the ``200`` Telegram and Slack wrap a
#: revoked token in — must not have its number printed beside a dead lane.
_AUTH_CODES = frozenset({401, 403})


def render_lane(row: dict[str, Any]) -> str:
    """One lane as ``name verdict`` — a status code, or a named non-answer.

    **Only a live credential ever prints a bare number.** The subtle case is an
    auth failure carried inside a ``200``: printing ``telegram 200
    (Unauthorized)`` puts the exact token a skimming reader is looking for —
    ``200`` — next to a dead lane, which is this feature's own failure mode
    turned on its rendering. So a refusal prints its code only when the code
    *is* the refusal (401/403); a refused ``200`` renders ``auth failed`` and
    the number is dropped entirely.
    """
    lane = str(row.get("lane") or "?").strip() or "?"
    outcome = str(row.get("outcome") or "").strip()
    code = row.get("code")
    detail = str(row.get("detail") or "").strip()
    if outcome == "ok":
        return f"{lane} {code}" if isinstance(code, int) else f"{lane} ok"
    if outcome == "auth_failed":
        head = (
            f"{lane} {code}"
            if isinstance(code, int) and code in _AUTH_CODES
            else f"{lane} auth failed"
        )
        return f"{head} ({detail})" if detail else head
    if outcome == "no_probe":
        return f"{lane} not probed ({detail})" if detail else f"{lane} not probed"
    bits = [str(code)] if isinstance(code, int) else []
    if detail:
        bits.append(detail)
    return f"{lane} probe failed ({'; '.join(bits)})" if bits else f"{lane} probe failed"


#: The boundary this surface cannot see past, rendered rather than assumed.
#: Three green lanes must not read as "every credential is live" — the
#: maintainer's own worked example (a ``.tmp/*.cookie`` for brnrd.dev) is not
#: gate state and is not enumerable from here.
EDGE_NOTE = (
    "covers configured gates only — credentials outside gate state "
    "(envoy carriers, ad-hoc cookies) are not enumerated here"
)


def render_lines(state: dict[str, Any]) -> list[str]:
    """The wake block, or ``[]`` when there is not even an absence to report.

    ``absent`` renders — loudly — because "nobody has probed" is exactly the
    answer a reader must not mistake for "everything is fine". The only empty
    return is a cache that probed and found no gate configured at all, where
    there is genuinely no lane to speak about.
    """
    status = str(state.get("status") or "")
    if status == "absent":
        return ["Lane liveness: never probed", f"- {EDGE_NOTE}"]
    lanes = state.get("lanes")
    if not isinstance(lanes, list):
        return ["Lane liveness: never probed", f"- {EDGE_NOTE}"]

    age = format_age(state.get("age_seconds"))
    stamp = _short_stamp(state.get("checked_at"))
    when = f"{stamp}, {age}" if stamp else age
    head = f"Lane liveness (stale — checked {when}):" if status == "stale" else (
        f"Lane liveness (checked {when}):"
    )

    if not lanes:
        # A sweep that ran and found nothing is a real answer, and it is not
        # the same answer as a sweep that could not look. Rendering neither —
        # dropping the whole block — makes both read as "nothing to worry
        # about", which is the exact shape this module was built to forbid.
        if state.get("discovery") == "failed":
            detail = str(state.get("discovery_detail") or "").strip()
            reason = f" ({detail})" if detail else ""
            body = f"lane discovery failed{reason} — cannot say which lanes exist"
        else:
            body = "no configured gate to probe"
        return [head, f"- {body}", f"- {EDGE_NOTE}"]

    rows = [render_lane(row) for row in lanes]
    if state.get("discovery") == "failed":
        rows.append("lane discovery failed — this list may be short")
    return [head, f"- {' · '.join(rows)}", f"- {EDGE_NOTE}"]


def render_block(repo_root: Path, *, now: float | None = None) -> str:
    """The rendered block for *repo_root*, network-free. ``""`` when silent."""
    return "\n".join(render_lines(read_state(repo_root, now=now)))
