#!/usr/bin/env python3
"""Roll a mirrored image out to a Scaleway Serverless Container.

Why this is a script and not four ``curl`` lines. The four-line version
shipped in #894 and failed on both of its live runs with exactly this much
diagnosis::

    curl: (22) The requested URL returned error: 409

A status code and nothing else, because ``--fail -o /dev/null`` throws away
the body that carries Scaleway's own ``type`` and ``message``. Recovering the
reason cost a trip to the vendor's docs, and it was **two** mistakes:

- **``PATCH`` is not safe to fire blind.** Updating a container that is in a
  transient state — an operator editing its environment variables in the
  console, an earlier rollout still settling — answers ``409``
  ``transient_state``. That is what happened: the maintainer was hand-editing
  container variables that evening, the workflow raced him, and the step
  reported a number instead of the sentence Scaleway had already written.
- **``POST /deploy`` after ``PATCH`` is the documented cause of that same
  409.** ``UpdateContainer`` already redeploys the container; Scaleway's
  v1-migration guide says in as many words not to chain ``DeployContainer``
  after it. The extra call could only ever hurt.

Two shape decisions worth keeping:

**Settled is ``ready`` or ``error``; everything else is transient.** That is
deliberately the inversion of a status allow-list. A status Scaleway adds
next year lands in the "wait" bucket, not the "go ahead and PATCH" bucket —
the failure mode of enumerating members is meeting the one nobody listed.

**Every failure prints the API's own words.** The response body is read on
success *and* on failure, and a non-2xx is reported as
``type: message (HTTP nnn)``. A rollout that fails should never again be a
bare integer.

Reads ``SCW_SECRET_KEY`` and ``SCW_CONTAINER_ID`` from the environment; takes
the fully-tagged target image as its one argument and derives the region from
its registry host (``rg.<region>.scw.cloud``), so that fact has one copy.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API_ROOT = "https://api.scaleway.com/containers/v1beta1"

# The only two states a container is done moving in. Anything else — pending,
# deploying, creating, whatever gets added later — means "not yet".
SETTLED = frozenset({"ready", "error"})

SETTLE_TIMEOUT_S = 300.0
PATCH_ATTEMPTS = 6
PATCH_BACKOFF_S = 10.0
POLL_INTERVAL_S = 5.0


class ApiError(Exception):
    """A non-2xx answer, carrying the words Scaleway put in the body."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        detail = body.strip()
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            kind = parsed.get("type") or parsed.get("error") or ""
            message = parsed.get("message") or parsed.get("detail") or ""
            detail = f"{kind}: {message}".strip(": ") or detail
        super().__init__(f"{detail or 'no body'} (HTTP {status})")


def request(
    method: str,
    url: str,
    token: str,
    payload: dict | None = None,
    *,
    timeout: float = 30.0,
) -> dict:
    """One Scaleway API call. Returns the parsed body; raises ``ApiError``."""
    data = None
    headers = {"X-Auth-Token": token}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx carry the useful body
        raise ApiError(exc.code, exc.read().decode("utf-8", "replace")) from exc
    except urllib.error.URLError as exc:
        raise ApiError(0, f"could not reach {API_ROOT}: {exc.reason}") from exc
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def region_of(image: str) -> str:
    """``rg.fr-par.scw.cloud/ns/brnrd:tag`` -> ``fr-par``."""
    host = image.split("/", 1)[0]
    parts = host.split(".")
    if len(parts) < 2 or parts[0] != "rg":
        raise SystemExit(
            f"::error::cannot derive a region from registry host {host!r}; "
            "expected rg.<region>.scw.cloud"
        )
    return parts[1]


def container_status(base: str, token: str) -> tuple[str, str]:
    """Return ``(status, error_message)`` for the container."""
    body = request("GET", base, token)
    return str(body.get("status") or ""), str(body.get("error_message") or "")


def wait_settled(
    base: str,
    token: str,
    *,
    timeout_s: float = SETTLE_TIMEOUT_S,
    sleep=None,
    now=None,
) -> str:
    """Block until the container is ``ready`` or ``error``; return the status.

    Times out rather than waiting forever: a container wedged in a transient
    state is a Scaleway-side incident, and the release should say so.

    ``sleep`` / ``now`` resolve here rather than in the signature defaults:
    a default bound at definition time captures the real ``time.sleep`` and
    silently ignores a patched one, which is how a unit test comes to spend
    fifteen real seconds proving nothing.
    """
    sleep = sleep or time.sleep
    now = now or time.monotonic
    deadline = now() + timeout_s
    while True:
        status, _ = container_status(base, token)
        print(f"container status: {status or 'unknown'}", flush=True)
        if status in SETTLED:
            return status
        if now() >= deadline:
            raise SystemExit(
                f"::error::container stayed in transient state "
                f"{status or 'unknown'!r} for {timeout_s:.0f}s"
            )
        sleep(POLL_INTERVAL_S)


def patch_image(
    base: str,
    token: str,
    image: str,
    *,
    attempts: int = PATCH_ATTEMPTS,
    sleep=None,
) -> None:
    """Point the container at *image*, retrying only a 409.

    A 409 means somebody else is mid-change; that is worth waiting out. Any
    other status is a real refusal and is reported with Scaleway's own words.
    """
    sleep = sleep or time.sleep
    for attempt in range(1, attempts + 1):
        try:
            request("PATCH", base, token, {"registry_image": image})
            return
        except ApiError as exc:
            if exc.status != 409 or attempt == attempts:
                raise
            print(
                f"::warning::container busy ({exc}); "
                f"retry {attempt}/{attempts - 1} in {PATCH_BACKOFF_S:.0f}s",
                flush=True,
            )
            sleep(PATCH_BACKOFF_S)


def rollout(image: str, container_id: str, token: str) -> int:
    base = f"{API_ROOT}/regions/{region_of(image)}/containers/{container_id}"
    print(f"Rolling out {image}", flush=True)
    try:
        # Settle *before* the PATCH: the update is refused outright while the
        # container is moving, and #894 learned that the expensive way.
        wait_settled(base, token)
        patch_image(base, token, image)
        # No POST /deploy — UpdateContainer redeploys, and chaining the two is
        # the documented source of `409 transient_state`.
        status = wait_settled(base, token)
    except ApiError as exc:
        print(f"::error::Scaleway refused the rollout: {exc}", flush=True)
        return 1
    if status == "error":
        _, message = container_status(base, token)
        print(
            f"::error::container deploy failed: {message or 'unknown'}",
            flush=True,
        )
        return 1
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: scw_rollout.py <registry-image:tag>", file=sys.stderr)
        return 2
    token = os.environ.get("SCW_SECRET_KEY") or ""
    container_id = os.environ.get("SCW_CONTAINER_ID") or ""
    if not token or not container_id:
        print(
            "::error::SCW_SECRET_KEY and SCW_CONTAINER_ID are both required",
            flush=True,
        )
        return 2
    return rollout(argv[1], container_id, token)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
