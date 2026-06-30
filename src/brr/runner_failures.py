"""Classify runner failures into operator-actionable buckets."""

from __future__ import annotations

import re


TIMED_OUT = "timed_out"
QUOTA_EXHAUSTED = "quota_exhausted"
AUTH_ERROR = "auth_error"
PROVIDER_ERROR = "provider_error"
RUNNER_ERROR = "runner_error"
NO_OUTPUT = "no_output"


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


def classify_failure(
    *,
    timed_out: bool = False,
    exit_code: object | None = None,
    detail: str | None = None,
) -> str:
    """Return the failure kind for a failed runner attempt.

    The classifier is intentionally conservative: timeout is mechanical,
    then we look for explicit quota/auth/provider signatures in the runner's
    own text. Anything else remains a generic runner error so the daemon does
    not invent recovery policy from weak evidence.
    """
    if timed_out:
        return TIMED_OUT
    text = str(detail or "").strip().lower()
    if text:
        if _matches_any(text, _QUOTA_PATTERNS):
            return QUOTA_EXHAUSTED
        if _matches_any(text, _AUTH_PATTERNS):
            return AUTH_ERROR
        if _matches_any(text, _PROVIDER_PATTERNS):
            return PROVIDER_ERROR
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
        RUNNER_ERROR: "runner failed",
        NO_OUTPUT: "runner produced no reply",
    }.get(kind, "runner failed")


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)
