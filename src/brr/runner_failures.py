"""Classify runner failures into operator-actionable buckets."""

from __future__ import annotations

import re


TIMED_OUT = "timed_out"
QUOTA_EXHAUSTED = "quota_exhausted"
AUTH_ERROR = "auth_error"
PROVIDER_ERROR = "provider_error"
# The turn died *in transit*: the connection to the model dropped, the
# response stream ended mid-message. Distinct from PROVIDER_ERROR (the
# provider answered, and said no) and from RUNNER_ERROR (the runner itself
# is broken) because it is the one failure class where nothing was decided
# — the work was never done, so a fresh attempt is not a duplicate.
TRANSPORT_ERROR = "transport_error"
RUNNER_ERROR = "runner_error"
# The runner never reached its provider: an egress proxy refused the
# tunnel. Distinct from AUTH_ERROR because the *credential was never
# presented* — a `solitary` sidecar denies CONNECT to any host off the
# allowlist and answers 403, and `\b403\b` is an AUTH_ERROR pattern, so
# an egress denial used to be reported to the operator as "runner
# authentication failed" (#1118). The remedy is `solitary.allow` or the
# Shell's own host set, and it has nothing to do with a token.
EGRESS_BLOCKED = "egress_blocked"
NO_OUTPUT = "no_output"
CORE_MISMATCH = "core_mismatch"
INTERRUPTED = "interrupted"
# The host (not the runner) died mid-run: daemon process killed by a
# suspend/crash/OOM while the run was in flight. Never produced by
# ``classify_failure`` — stamped only by the daemon's boot-time
# interrupted-run marker (#316), after proving the dispatching process
# is gone.
HOST_INTERRUPTED = "host_interrupted"
# The runner *did* run, and its own text says the host suspended out from
# under it mid-request -- distinct from HOST_INTERRUPTED (daemon-restart
# proof, #316, never produced here) and from TRANSPORT_ERROR (a dropped
# connection with no stated cause): this is the one failure kind whose
# text names its own cause, so the correspondent-facing reply can say
# "the host went to sleep" instead of relaying a raw API error string
# (#1485; the exact string measured 2026-08-18: "API Error: Your computer
# went to sleep mid-response.").
HOST_SUSPENDED = "host_suspended"


_QUOTA_PATTERNS = (
    r"\bsession limit\b",
    r"\brate limit\b",
    r"\bquota\b",
    r"\busage limit\b",
    r"\blimit(?:s)? (?:reached|exceeded|exhausted)\b",
    r"\btoo many requests\b",
    r"\b429\b",
    r"\bresets?\b",
    r"\binsufficient[_ -]quota\b",
)

_AUTH_PATTERNS = (
    r"\bunauthori[sz]ed\b",
    r"\bnot authenticated\b",
    r"\blogin required\b",
    r"\bauth(?:entication)? failed\b",
    r"\binvalid api key\b",
    r"\bapi key\b.*\binvalid\b",
    r"\b401\b",
    r"\b403\b",
)

# Egress-denial signatures. Every one of these names the *proxy* rather
# than the endpoint, which is what makes the 403 disambiguable: a bare
# 403 stays AUTH_ERROR, a 403 the runner attributes to a CONNECT tunnel
# does not. Matched **before** the auth patterns for that reason.
#
# `not on allowlist` is the sidecar's own deny wording
# (``data/solitary_proxy.py``) — present when a caller hands in the
# sidecar log, absent from the runner's stdout, and harmless either way.
_EGRESS_PATTERNS = (
    r"\bproxy connection failed\b",
    r"\bproxy connect\b.*\bfailed\b",
    r"\b(?:http )?connect (?:to .* )?failed with status\b",
    r"\btunnel connection failed\b",
    r"\bnot on allowlist\b",
    r"\bproxy\b.*\b(?:403|407)\b",
    r"\b(?:403|407)\b.*\bproxy\b",
)

_PROVIDER_PATTERNS = (
    r"\bprovider\b.*\b(unavailable|down|error)\b",
    r"\bservice unavailable\b",
    r"\boverloaded\b",
    r"\btemporarily unavailable\b",
    r"\b5\d\d\b",
)

# Transport signatures — the text a dropped connection leaves behind.
#
# This is a member list, so it will meet the member nobody listed. That is
# acceptable *here*, and only because the two errors cost different amounts:
# a **false negative** costs exactly what the pre-#729 behaviour cost — one
# transport death classified as deterministic and never retried
# (``run-260725-0820-gc3n``: dead 78s in on "Connection closed
# mid-response", no branch, no report, recovered only because a human
# re-dispatched by hand) — while a **false positive** costs one duplicate
# attempt, itself bounded by ``response_retries``. Adding the next
# signature is a one-line edit in this tuple.
#
# Deliberately *not* here: a provider 5xx / "overloaded" / "service
# unavailable", which #729 proposed as members. Those already classify as
# PROVIDER_ERROR, and PROVIDER_ERROR has a *better* recovery than a retry —
# ``runner_select.automatic_fallback_runner`` moves the work to a different
# provider. The daemon consults ``failure_kind`` only when the result has no
# retry reason, so making a 5xx retryable here would silently switch that
# fallback off in exchange for re-dialling the provider that just failed.
_TRANSPORT_PATTERNS = (
    r"\bconnection closed\b",
    r"\bconnection error\b",
    r"\bconnection reset\b",
    r"\bconnection aborted\b",
    r"\bserver disconnected\b",
    r"\bremote end closed connection\b",
    r"\bstream (?:closed|ended|interrupted|disconnected)\b",
    r"\bincomplete chunked read\b",
)

# Host-suspend signatures (#1485) -- the wording a Shell prints when the
# in-flight request died because the *machine* slept, not because the
# provider or the network did anything. Checked ahead of every other list:
# it is the most specific signal available (the runner is naming its own
# cause), so it must win over a generic transport/provider match on the
# same text.
_HOST_SUSPEND_PATTERNS = (
    r"\bcomputer went to sleep\b",
    r"\bsystem went to sleep\b",
    r"\bwent to sleep mid-response\b",
    r"\bmachine (?:was )?(?:asleep|suspended)\b",
)


def looks_like_transport_failure(text: str | None) -> bool:
    """Whether *text* carries a transport signature (case-insensitive).

    Callers hold the question of *which* captured text to ask about — see
    ``runner.RunnerResult.transport_failure``, which asks about stdout and
    stderr both because which stream a Shell prints provider errors to is a
    per-Shell accident.
    """
    return _matches_any(str(text or "").lower(), _TRANSPORT_PATTERNS)


def classify_failure(
    *,
    timed_out: bool = False,
    exit_code: object | None = None,
    detail: str | None = None,
    transport: bool = False,
) -> str:
    """Return the failure kind for a failed runner attempt.

    The classifier is intentionally conservative: timeout is mechanical,
    then we look for explicit quota/auth/provider/transport signatures in the
    runner's own text. Anything else remains a generic runner error so the
    daemon does not invent recovery policy from weak evidence.

    ``transport`` lets a caller that has already inspected the *whole*
    capture (both streams) hand in that verdict — ``detail`` is usually
    ``RunnerResult.error_detail()``, which prefers stderr and so can miss a
    signature that landed on stdout.
    """
    if timed_out:
        return TIMED_OUT
    text = str(detail or "").strip().lower()
    if text:
        if "turn interrupted" in text:
            return INTERRUPTED
        if _matches_any(text, _HOST_SUSPEND_PATTERNS):
            return HOST_SUSPENDED
        if _matches_any(text, _QUOTA_PATTERNS):
            return QUOTA_EXHAUSTED
        # Before AUTH: an egress denial answers 403, and `\b403\b` is an
        # auth signature. The proxy-naming text is the only thing that
        # tells the two apart, so it has to be asked first (#1118).
        if _matches_any(text, _EGRESS_PATTERNS):
            return EGRESS_BLOCKED
        if _matches_any(text, _AUTH_PATTERNS):
            return AUTH_ERROR
        if _matches_any(text, _PROVIDER_PATTERNS):
            return PROVIDER_ERROR
        if _matches_any(text, _TRANSPORT_PATTERNS):
            return TRANSPORT_ERROR
    if transport:
        return TRANSPORT_ERROR
    if exit_code not in (None, "", 0):
        return RUNNER_ERROR
    return NO_OUTPUT


def reason_prefix(kind: str) -> str:
    """Human-readable prefix for the terminal failure response."""
    return {
        TIMED_OUT: "runner timed out",
        QUOTA_EXHAUSTED: "runner quota was exhausted",
        AUTH_ERROR: "runner authentication failed",
        EGRESS_BLOCKED: (
            "runner egress was blocked — a proxy refused the tunnel to the "
            "model provider (in `solitary`, the host is off the allowlist; "
            "widen it with `solitary.allow`)"
        ),
        PROVIDER_ERROR: "runner provider failed",
        TRANSPORT_ERROR: "runner connection dropped mid-response",
        RUNNER_ERROR: "runner failed",
        NO_OUTPUT: "runner produced no reply",
        CORE_MISMATCH: "runner Core attestation failed",
        INTERRUPTED: "runner was interrupted (external kill or shell interrupt)",
        HOST_INTERRUPTED: (
            "run was interrupted by a host/daemon restart mid-flight"
        ),
        HOST_SUSPENDED: (
            "the host went to sleep mid-run (#1485) — retrying now that a "
            "power assertion holds the machine awake for a runner's "
            "lifetime should avoid a repeat"
        ),
    }.get(kind, "runner failed")


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)
