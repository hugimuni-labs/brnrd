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
NO_OUTPUT = "no_output"
CORE_MISMATCH = "core_mismatch"
INTERRUPTED = "interrupted"
# The host (not the runner) died mid-run: daemon process killed by a
# suspend/crash/OOM while the run was in flight. Never produced by
# ``classify_failure`` — stamped only by the daemon's boot-time
# interrupted-run marker (#316), after proving the dispatching process
# is gone.
HOST_INTERRUPTED = "host_interrupted"


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
        if _matches_any(text, _QUOTA_PATTERNS):
            return QUOTA_EXHAUSTED
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
        PROVIDER_ERROR: "runner provider failed",
        TRANSPORT_ERROR: "runner connection dropped mid-response",
        RUNNER_ERROR: "runner failed",
        NO_OUTPUT: "runner produced no reply",
        CORE_MISMATCH: "runner Core attestation failed",
        INTERRUPTED: "runner was interrupted (external kill or shell interrupt)",
        HOST_INTERRUPTED: (
            "run was interrupted by a host/daemon restart mid-flight"
        ),
    }.get(kind, "runner failed")


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)
