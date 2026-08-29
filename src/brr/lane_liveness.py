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
  the URL path, so a bare ``requests`` exception string leaks it. Every
  outcome string goes through :func:`_scrub`.
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

# One in-flight refresh at a time, process-wide: the daemon ticks every few
# seconds and a full sweep can take PROBE_TIMEOUT_SECONDS per lane, so without
# this a slow endpoint would stack threads.
_refresh_lock = threading.Lock()
_refreshing = False


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
        if isinstance(secret, str) and len(secret.strip()) >= 8:
            out = out.replace(secret.strip(), "<redacted>")
    out = " ".join(out.split())
    return out[:160]


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


def _classify_http(
    lane: str,
    status: int,
    *,
    api_ok: bool | None = None,
    api_error: str | None = None,
    secrets: tuple[str | None, ...] = (),
) -> dict[str, Any]:
    """Turn one HTTP answer into an outcome row.

    *api_ok* / *api_error* carry the envelope verdict for APIs that answer
    ``200`` with ``{"ok": false, "error": "invalid_auth"}`` — Telegram and
    Slack both do, and reading only the status code would render a revoked
    token as ``200``. That is the single most direct way to build this wrong.
    """
    if status in (401, 403):
        return _outcome(lane, "auth_failed", code=status)
    if status >= 500:
        return _outcome(lane, "error", code=status, detail="server error")
    if not 200 <= status < 300:
        return _outcome(lane, "error", code=status, detail="unexpected status")
    if api_ok is False:
        detail = _scrub(api_error or "api refused", *secrets)
        lowered = detail.lower()
        auth_shaped = any(
            mark in lowered
            for mark in ("auth", "token", "unauthorized", "invalid", "expired", "revoked")
        )
        return _outcome(
            lane,
            "auth_failed" if auth_shaped else "error",
            code=status,
            detail=detail,
        )
    return _outcome(lane, "ok", code=status)


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
    payload = _json_or_none(response)
    ok = payload.get("ok") if isinstance(payload, dict) else None
    error = str(payload.get("description") or "") if isinstance(payload, dict) else ""
    return _classify_http(
        "telegram", response.status_code, api_ok=ok, api_error=error, secrets=(token,)
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
    payload = _json_or_none(response)
    ok = payload.get("ok") if isinstance(payload, dict) else None
    error = str(payload.get("error") or "") if isinstance(payload, dict) else ""
    return _classify_http(
        "slack", response.status_code, api_ok=ok, api_error=error, secrets=(token,)
    )


def _probe_github(brr_dir: Path) -> dict[str, Any]:
    """``GET /rate_limit`` — the one GitHub read that costs literally nothing.

    Not ``/user`` (which :func:`brr.gates.github.state._validate_token` uses)
    for two reasons: ``/rate_limit`` is documented as not counting against the
    quota it reports, and it authenticates every GitHub credential *kind* —
    including the App installation token ``cloud_credentials`` mints, for
    which ``/user`` is a 403 that would render as a dead credential.
    """
    from .gates.github import state as gh_state
    from .gates import runtime as gate_runtime

    try:
        token = gh_state.resolve_token(gate_runtime.load_state(brr_dir, "github"))
    except Exception as exc:  # noqa: BLE001 - a credential read never breaks the sweep
        return _outcome("github", "error", detail=_scrub(exc))
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
    return _classify_http("github", response.status_code, secrets=(token,))


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
    try:
        return response.json()
    except ValueError:
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
        }
    return {
        "status": "stale" if age is None or age >= stale_after else "fresh",
        "checked_at": checked_at if isinstance(checked_at, str) else None,
        "age_seconds": age,
        "lanes": lanes,
    }


# ── Refresh ──────────────────────────────────────────────────────────


def refresh(repo_root: Path, *, brr_dir: Path | None = None) -> dict[str, Any]:
    """Probe every configured lane and write the cache. Daemon-side only.

    Never raises. A lane whose probe blows up in an unforeseen way lands as
    ``error`` with the scrubbed exception, because one bad lane must not cost
    the reader the other four.
    """
    from .gates import runtime as gate_runtime

    root = gitops.shared_brr_dir(repo_root) if brr_dir is None else brr_dir
    try:
        configured = gate_runtime.configured_gates(root)
    except Exception:  # noqa: BLE001 - discovery failing is not a reason to write nothing
        configured = []

    lanes: list[dict[str, Any]] = []
    for gate in configured:
        probe = PROBES.get(gate)
        if probe is None:
            lanes.append(_outcome(gate, "no_probe", detail="no probe implemented"))
            continue
        try:
            lanes.append(probe(root))
        except Exception as exc:  # noqa: BLE001 - one lane never kills the sweep
            lanes.append(_outcome(gate, "error", detail=_scrub(exc)))

    payload = {"schema": SCHEMA, "checked_at": _utc_now_iso(), "lanes": lanes}
    _write(repo_root, payload)
    return payload


def refresh_if_stale(
    repo_root: Path,
    *,
    ttl: float = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> bool:
    """Refresh when the cache is older than *ttl*. Returns whether it ran."""
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
        if read_state(repo_root, stale_after=ttl)["status"] == "fresh":
            return False
        _refreshing = True

    def _work() -> None:
        global _refreshing
        try:
            refresh(repo_root)
        except Exception as exc:  # noqa: BLE001 - a cache refresh never kills the daemon
            print(f"[brnrd] lane liveness refresh failed: {exc}")
        finally:
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=_work, name="lane-liveness", daemon=True).start()
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


def render_lane(row: dict[str, Any]) -> str:
    """One lane as ``name verdict`` — a status code, or a named non-answer.

    Never renders a non-answer in a shape a reader could skim as healthy:
    ``ok`` is the only outcome that prints a bare number.
    """
    lane = str(row.get("lane") or "?").strip() or "?"
    outcome = str(row.get("outcome") or "").strip()
    code = row.get("code")
    detail = str(row.get("detail") or "").strip()
    if outcome == "ok":
        return f"{lane} {code if isinstance(code, int) else 'ok'}"
    if outcome == "auth_failed":
        head = f"{lane} {code}" if isinstance(code, int) else f"{lane} refused"
        return f"{head} ({detail})" if detail else head
    if outcome == "no_probe":
        return f"{lane} not probed ({detail})" if detail else f"{lane} not probed"
    head = f"{lane} probe failed"
    if isinstance(code, int):
        head = f"{lane} probe failed ({code})"
    return f"{head} — {detail}" if detail and not isinstance(code, int) else head


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
        return [f"Lane liveness: never probed — {EDGE_NOTE}"]
    lanes = state.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        return []
    age = format_age(state.get("age_seconds"))
    stamp = _short_stamp(state.get("checked_at"))
    when = f"{stamp}, {age}" if stamp else age
    head = f"Lane liveness (stale — checked {when}):" if status == "stale" else (
        f"Lane liveness (checked {when}):"
    )
    body = " · ".join(render_lane(row) for row in lanes)
    return [head, f"- {body}", f"- {EDGE_NOTE}"]


def render_block(repo_root: Path, *, now: float | None = None) -> str:
    """The rendered block for *repo_root*, network-free. ``""`` when silent."""
    return "\n".join(render_lines(read_state(repo_root, now=now)))
