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

## Commit ordering (#1045)

#1044 gave ``deploy`` its own ``concurrency`` group, so two rollouts that
genuinely overlap no longer race — the newer one cancels the older. That
does not order anything: ``deploy`` starts when *its own* ``build`` job
finishes, and build durations vary by minutes (cold vs warm layer cache), so
two pushes far enough apart to never overlap can still land their deploys
out of commit order. #1039's direction 2 is the fix actually shipped here:
refuse a rollout whose commit is not a descendant of what is already
running.

**Where "what is already running" comes from.** The container's own
``registry_image`` field (:func:`read_deployed_commit`), not
``GET https://brnrd.dev/v1/stats/version`` (the endpoint #1039 used to
*measure* the original defect). The version endpoint depends on the app
being up to answer — exactly the condition least likely to hold during the
kind of incident this guard exists to prevent from compounding. The
Scaleway API is a dependency this script already has for the PATCH itself;
reading its answer to "what image are you serving" adds no new trust
boundary.

**Three outcomes, not two** (:func:`ancestry_status`). A shallow checkout —
``actions/checkout`` defaults to ``fetch-depth: 1`` — cannot resolve a
commit outside its single fetched commit, and neither can an unresolvable
or missing deployed-image tag. That is ``"unknown"``, and it is handled
differently from ``"not_ancestor"``: refusing a rollout because the check
*could not run* would turn a cold cache or a momentary API miss into a
deploy freeze, so ``"unknown"`` rolls out anyway, loudly. Only a *confident*
``"not_ancestor"`` — both commits resolved locally, neither is the other's
ancestor — declines, and it declines as a ``::notice::``, not a failure: an
out-of-order deploy that backs off is the correct outcome, because a newer
commit is already deployed.

The publish workflow's ``deploy`` job checks out with ``fetch-depth: 0``
(full history) specifically so this check resolves more often than
``"unknown"`` — see the comment there for the cost/benefit.
"""

from __future__ import annotations

import json
import os
import subprocess
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


def _deployed_sha_tag(container_info: dict) -> str | None:
    """Pull the ``sha-<7>`` tag out of a container's ``registry_image``.

    Pure function over an already-fetched body, kept separate from the
    network call so the parsing rule (only *this* shape is a commit
    reference) is testable without an API double. ``None`` for a container
    with no image on record yet (first-ever deploy), or one whose image
    tag doesn't look like the tag this workflow mints (#848) — a foreign
    tag is not evidence of "no ancestor", it is evidence this check cannot
    speak to the image at all.
    """
    image = str(container_info.get("registry_image") or "")
    _, _, tag = image.rpartition(":")
    if tag.startswith("sha-") and len(tag) > len("sha-"):
        return tag[len("sha-"):]
    return None


def read_deployed_commit(base: str, token: str) -> str | None:
    """The short sha this container reports running, right now.

    A dedicated ``GET``, asked once before the settle/patch loop in
    :func:`rollout` even starts — the question "what was deployed before
    this rollout touched anything" must not be answered from a read that
    happens after a PATCH has already landed.
    """
    return _deployed_sha_tag(request("GET", base, token))


def _resolve_commit(repo_root, rev: str) -> str | None:
    """``git rev-parse --verify <rev>^{commit}``, or ``None`` when it can't resolve.

    A missing ref and a shallow checkout that never fetched the object are
    indistinguishable to this call, and #1045 treats them identically:
    "not resolvable in this checkout" is not a claim that the commit
    doesn't exist anywhere.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{rev}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def ancestry_status(
    repo_root, deployed_rev: str | None, candidate_rev: str = "HEAD",
) -> tuple[str, str | None, str | None]:
    """Is *candidate_rev* a descendant of *deployed_rev*?

    Returns ``(status, deployed_sha, candidate_sha)``. ``status`` is one of:

    - ``"ancestor"`` — *deployed_rev* is reachable from *candidate_rev*;
      rolling out moves the container forward.
    - ``"not_ancestor"`` — both resolved locally, and neither is the
      other's ancestor (or *candidate_rev* is actually the older one).
      Confident, and confidently a decline.
    - ``"unknown"`` — *deployed_rev* is empty, or either revision could not
      be resolved in this checkout (shallow history, a foreign tag, an
      object never fetched). Never guessed past this point — the caller
      rolls out rather than freezing on an unresolvable check.

    The resolved shas ride along even on ``"unknown"`` (whichever side did
    resolve) so a caller's notice can be as specific as the checkout allows.
    """
    if not deployed_rev:
        return "unknown", None, None
    deployed_sha = _resolve_commit(repo_root, deployed_rev)
    candidate_sha = _resolve_commit(repo_root, candidate_rev)
    if deployed_sha is None or candidate_sha is None:
        return "unknown", deployed_sha, candidate_sha
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", deployed_sha, candidate_sha],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return "ancestor", deployed_sha, candidate_sha
    if result.returncode == 1:
        return "not_ancestor", deployed_sha, candidate_sha
    # >1 is a genuine git error (bad object, corrupt repo) on revisions this
    # function itself just resolved — should not happen, but "unknown" is
    # the only honest answer for a code path with no test coverage of its
    # own git failing after the fact.
    return "unknown", deployed_sha, candidate_sha


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


def check_commit_ordering(base: str, token: str, repo_root) -> bool:
    """Should this rollout proceed? Prints its own reasoning either way.

    ``True`` for "go ahead" — either this commit is a confirmed descendant
    of what's deployed, or the check could not resolve an answer and the
    honest default is to roll out (#1045's `unknown` branch). ``False``
    only for a confident ``not_ancestor``: a newer commit is already
    running, so declining is the whole behaviour, not a failure.

    Split from :func:`main` so a test can drive the ordering decision
    without also exercising argv/credential parsing.
    """
    deployed_tag: str | None = None
    try:
        deployed_tag = read_deployed_commit(base, token)
    except ApiError as exc:
        print(
            f"::warning::could not read the currently deployed image ({exc}); "
            "commit ordering will read as unknown",
            flush=True,
        )

    status, deployed_sha, candidate_sha = ancestry_status(repo_root, deployed_tag)
    if status == "not_ancestor":
        print(
            "::notice::declining rollout — deployed commit "
            f"{deployed_sha or deployed_tag} is not an ancestor of "
            f"{candidate_sha or 'HEAD'}; a newer commit is already running, "
            "which is the desired end state",
            flush=True,
        )
        return False
    if status == "unknown":
        reason = (
            f"tag {deployed_tag!r} not resolvable in this checkout"
            if deployed_tag
            else "no deployed commit on record (first deploy?)"
        )
        print(
            f"::notice::commit ordering unknown ({reason}); rolling out anyway",
            flush=True,
        )
    return True


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
    image = argv[1]
    base = f"{API_ROOT}/regions/{region_of(image)}/containers/{container_id}"
    repo_root = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    if not check_commit_ordering(base, token, repo_root):
        return 0
    return rollout(image, container_id, token)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
