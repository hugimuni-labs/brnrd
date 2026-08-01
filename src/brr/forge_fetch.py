"""Host-side, read-only GitHub views for isolated runs.

This module is the security boundary behind the outbox ``fetch:`` portal.
It deliberately does not accept a URL, REST path, GraphQL document, method,
hostname, owner, or repository from the running agent.  The daemon supplies
the run's already-resolved repository and this module sends one fixed GraphQL
query whose variables are only ``owner``, ``name``, and a positive issue/PR
number.

The allowlist is therefore structural rather than an endpoint list:

* ``issue`` exposes the issue's fixed metadata/body/labels and first 50
  comments;
* ``pr`` exposes the PR's fixed metadata/body/labels/review decision, first
  100 changed-file summaries, first 50 conversation comments, and first 50
  reviews with their first 50 inline comments.

No patch contents, repository search, cross-repository selector, pagination
cursor, arbitrary query, or mutation can be expressed by the caller.  The
fixed connection limits bound GitHub work; :data:`MAX_RESPONSE_BYTES` bounds
what crosses back into the run; :data:`MAX_REQUESTS_PER_RUN` is enforced by
the daemon for the lifetime of a run.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any

from .gates import cloud
from .gates.github import client, state


ALLOWED_KINDS = frozenset({"issue", "pr"})
MAX_REQUESTS_PER_RUN = 4
MAX_RESPONSE_BYTES = 64 * 1024

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# One immutable query, with no caller-controlled syntax.  The two inline
# fragments are the executable allowlist: adding a field here is a security-
# boundary change because it gives an injected run new information.
_VIEW_QUERY = """
query BrnrdForgeView($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issueOrPullRequest(number: $number) {
      __typename
      ... on Issue {
        number
        title
        body
        state
        url
        createdAt
        updatedAt
        closedAt
        author { login }
        assignees(first: 20) { totalCount nodes { login } }
        labels(first: 20) { totalCount nodes { name color description } }
        comments(first: 50) {
          totalCount
          pageInfo { hasNextPage }
          nodes { author { login } body createdAt updatedAt url }
        }
      }
      ... on PullRequest {
        number
        title
        body
        state
        url
        createdAt
        updatedAt
        closedAt
        mergedAt
        isDraft
        baseRefName
        headRefName
        reviewDecision
        additions
        deletions
        changedFiles
        author { login }
        assignees(first: 20) { totalCount nodes { login } }
        labels(first: 20) { totalCount nodes { name color description } }
        files(first: 100) {
          totalCount
          pageInfo { hasNextPage }
          nodes { path additions deletions changeType }
        }
        comments(first: 50) {
          totalCount
          pageInfo { hasNextPage }
          nodes { author { login } body createdAt updatedAt url }
        }
        reviews(first: 50) {
          totalCount
          pageInfo { hasNextPage }
          nodes {
            author { login }
            state
            body
            submittedAt
            url
            comments(first: 50) {
              totalCount
              pageInfo { hasNextPage }
              nodes { author { login } body path line createdAt updatedAt url }
            }
          }
        }
      }
    }
  }
  rateLimit { cost remaining resetAt }
}
""".strip()


class ForgeFetchError(RuntimeError):
    """A safe, user-visible failure of the host-side read."""


@dataclass(frozen=True)
class FetchResult:
    body: str
    byte_count: int
    truncated: bool


def valid_repo(repo: str) -> bool:
    """Whether *repo* is an unambiguous GitHub ``owner/name`` slug."""
    return bool(_REPO_RE.fullmatch(repo.strip()))


def _managed_token(brr_dir: Path) -> str | None:
    """Read the daemon-refreshed installation-token pointer when present."""
    try:
        cloud.ensure_publishing_credential_fresh(brr_dir)
    except Exception:
        pass
    pointer = cloud.github_credentials_dir(brr_dir)
    if pointer is not None:
        try:
            token = (pointer / "token").read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        if token:
            return token
    token = os.environ.get("BRNRD_MANAGED_GITHUB_TOKEN", "").strip()
    return token or None


def resolve_host_token(brr_dir: Path) -> str | None:
    """Resolve the GitHub identity the host gate already owns.

    An explicitly stored self-hosted gate token wins.  Managed mode then uses
    the daemon-refreshed installation-token pointer rather than falling
    through to the operator's broader ``gh`` identity.  The gate's normal
    CLI/environment fallbacks remain last for first-time self-hosted setups.
    """
    gate_state = state._load_state(brr_dir)
    stored = gate_state.get("token")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return _managed_token(brr_dir) or state.resolve_token(gate_state)


def _graphql(token: str, variables: dict[str, object]) -> dict[str, Any]:
    payload, _headers = client._request(
        token,
        "POST",
        "/graphql",
        body={"query": _VIEW_QUERY, "variables": variables},
    )
    if not isinstance(payload, dict):
        raise ForgeFetchError("GitHub returned no object")
    errors = payload.get("errors")
    if errors:
        message = json.dumps(errors, ensure_ascii=False, default=str)
        raise ForgeFetchError(f"GitHub GraphQL error: {message[:500]}")
    return payload


def cap_response(text: str, limit: int = MAX_RESPONSE_BYTES) -> FetchResult:
    """UTF-8-cap *text*, appending a visible truncation marker when needed."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return FetchResult(text, len(encoded), False)
    marker = f"\n\n[forge response truncated at {limit} bytes]".encode("utf-8")
    prefix = encoded[: max(0, limit - len(marker))]
    while prefix:
        try:
            clipped = prefix.decode("utf-8")
            break
        except UnicodeDecodeError as exc:
            prefix = prefix[:exc.start]
    else:
        clipped = ""
    body = clipped + marker.decode("utf-8")
    return FetchResult(body, len(body.encode("utf-8")), True)


def fetch_view(
    brr_dir: Path,
    repo: str,
    kind: str,
    number: int,
) -> FetchResult:
    """Return the fixed issue or PR view for *number* in the run's *repo*."""
    repo = repo.strip()
    kind = kind.strip().casefold()
    if not valid_repo(repo):
        raise ForgeFetchError("the run has no unambiguous GitHub owner/repo")
    if kind not in ALLOWED_KINDS:
        raise ForgeFetchError(f"unsupported read kind {kind!r}")
    if number <= 0:
        raise ForgeFetchError("number must be a positive integer")

    token = resolve_host_token(brr_dir)
    if not token:
        raise ForgeFetchError("the daemon has no GitHub gate credential")
    owner, name = repo.split("/", 1)
    payload = _graphql(
        token,
        {"owner": owner, "name": name, "number": number},
    )
    data = payload.get("data")
    repository = data.get("repository") if isinstance(data, dict) else None
    obj = (
        repository.get("issueOrPullRequest")
        if isinstance(repository, dict) else None
    )
    if not isinstance(obj, dict):
        raise ForgeFetchError(f"{repo} has no issue or PR #{number}")
    expected = "Issue" if kind == "issue" else "PullRequest"
    if obj.get("__typename") != expected:
        actual = "PR" if obj.get("__typename") == "PullRequest" else "issue"
        raise ForgeFetchError(f"{repo} #{number} is a {actual}, not a {kind}")

    view = {
        "repo": repo,
        "kind": kind,
        "number": number,
        "object": obj,
        "rate_limit": data.get("rateLimit") if isinstance(data, dict) else None,
    }
    text = (
        f"# Forge read: {repo} {kind} #{number}\n\n"
        + json.dumps(view, ensure_ascii=False, indent=2, sort_keys=True)
    )
    return cap_response(text)
