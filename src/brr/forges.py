"""Forge URL inference for the daemon's post-task response.

Given the configured ``origin`` remote URL, brr derives a clickable
"view branch" URL the gate can put in front of the user. Templates
cover the cloud-hosted big three (GitHub, GitLab, Bitbucket) plus the
Gitea / Forgejo family that uses the same path shape; self-hosted
instances of those forges land via host-pattern match (e.g. any host
containing ``gitlab.`` is treated as GitLab) or via an explicit
``[forge]`` override in ``.brr/config`` for one-off internal domains.

The module is intentionally observational: it reads a remote-URL
string and returns a URL string. No subprocess, no auth, no network.
Action-shaped behaviour like opening a PR/MR belongs in a post-task
hook outside core; this layer just hands the user a reviewable link.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


# Known forge kinds. ``unknown`` is the explicit "we don't have a
# template for this" state so callers can distinguish "no remote" from
# "remote we can't interpret".
_KIND_GITHUB = "github"
_KIND_GITLAB = "gitlab"
_KIND_BITBUCKET = "bitbucket"
_KIND_GITEA = "gitea"

KNOWN_KINDS: frozenset[str] = frozenset({
    _KIND_GITHUB, _KIND_GITLAB, _KIND_BITBUCKET, _KIND_GITEA,
})


@dataclass(frozen=True)
class ForgeMatch:
    """Resolved forge metadata for an ``origin`` remote.

    ``host`` is the web host as it appears in the resulting URL (no
    scheme, no path). ``owner`` and ``repo`` come from the remote
    path; ``owner`` may contain slashes for nested-group forges
    (GitLab subgroups).
    """

    kind: str
    host: str
    owner: str
    repo: str


# Host-pattern → forge kind. Listed most-specific first so the cloud
# hosts win over their self-hosted fuzzy patterns. Each regex is full
# match against the lowercased host.
_HOST_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^github\.com$"), _KIND_GITHUB),
    (re.compile(r"^gitlab\.com$"), _KIND_GITLAB),
    (re.compile(r"^bitbucket\.org$"), _KIND_BITBUCKET),
    (re.compile(r"^codeberg\.org$"), _KIND_GITEA),
    # Self-hosted GitLab is overwhelmingly named ``gitlab.<corp>``.
    # Catching the prefix here keeps the common case zero-config; the
    # rare ``git.example.com`` self-host falls through to the
    # ``forge.kind`` override path.
    (re.compile(r"^gitlab\.[^.]+(?:\..+)?$"), _KIND_GITLAB),
    (re.compile(r"^(?:gitea|forgejo)\..+$"), _KIND_GITEA),
)


# URL templates per forge kind. ``{host}`` is the web host (taken from
# the override or the parsed remote), ``{owner}`` is the path prefix
# up to the repo, ``{repo}`` is the leaf, ``{branch}`` is the branch
# name. Templates intentionally avoid query-string PR-create deep
# links because those are forge-version-sensitive; the branch view
# url is the durable common denominator.
_BRANCH_TEMPLATES: dict[str, str] = {
    _KIND_GITHUB:    "https://{host}/{owner}/{repo}/tree/{branch}",
    _KIND_GITLAB:    "https://{host}/{owner}/{repo}/-/tree/{branch}",
    _KIND_BITBUCKET: "https://{host}/{owner}/{repo}/branch/{branch}",
    _KIND_GITEA:     "https://{host}/{owner}/{repo}/src/branch/{branch}",
}


# File blob templates. ``{path}`` is a repo-relative path (slashes kept).
# Used to project a file committed to a forge-hosted repo — e.g. a run-state
# document in the account dominion repo — to a clickable web URL.
_BLOB_TEMPLATES: dict[str, str] = {
    _KIND_GITHUB:    "https://{host}/{owner}/{repo}/blob/{branch}/{path}",
    _KIND_GITLAB:    "https://{host}/{owner}/{repo}/-/blob/{branch}/{path}",
    _KIND_BITBUCKET: "https://{host}/{owner}/{repo}/src/{branch}/{path}",
    _KIND_GITEA:     "https://{host}/{owner}/{repo}/src/branch/{branch}/{path}",
}


# Single-commit templates. Used by run relics (``kb/design-run-relics.md``)
# to turn a ``git log`` short sha into a clickable link the same way a
# branch or blob already projects to one.
_COMMIT_TEMPLATES: dict[str, str] = {
    _KIND_GITHUB:    "https://{host}/{owner}/{repo}/commit/{sha}",
    _KIND_GITLAB:    "https://{host}/{owner}/{repo}/-/commit/{sha}",
    _KIND_BITBUCKET: "https://{host}/{owner}/{repo}/commits/{sha}",
    _KIND_GITEA:     "https://{host}/{owner}/{repo}/commit/{sha}",
}


# Issue-thread templates. Pull and merge requests use their own native
# templates below; conflating the two worked on GitHub through a redirect but
# produced dead links on the other supported forges.
_ISSUE_TEMPLATES: dict[str, str] = {
    _KIND_GITHUB:    "https://{host}/{owner}/{repo}/issues/{number}",
    _KIND_GITLAB:    "https://{host}/{owner}/{repo}/-/issues/{number}",
    _KIND_BITBUCKET: "https://{host}/{owner}/{repo}/issues/{number}",
    _KIND_GITEA:     "https://{host}/{owner}/{repo}/issues/{number}",
}

_PULL_REQUEST_TEMPLATES: dict[str, str] = {
    _KIND_GITHUB:    "https://{host}/{owner}/{repo}/pull/{number}",
    _KIND_GITLAB:    "https://{host}/{owner}/{repo}/-/merge_requests/{number}",
    _KIND_BITBUCKET: "https://{host}/{owner}/{repo}/pull-requests/{number}",
    _KIND_GITEA:     "https://{host}/{owner}/{repo}/pulls/{number}",
}

_PULL_REQUEST_NUMBER_RE = re.compile(r"(?:#)?([1-9]\d*)")
_PULL_REQUEST_PATH_RE = re.compile(
    r"^/(?:[^/]+/){2,}(?:pull|pulls|pull-requests|-/merge_requests)/"
    r"([1-9]\d*)(?:/|$)"
)


# Remote-URL parsers. Order matters: SSH form before HTTPS form
# because both can start with ``git@`` if someone mis-types.
# SSH path-style: ``[user@]host:owner/repo[.git]``. The path is
# disallowed from starting with ``/`` so unknown ``scheme://…`` URLs
# don't accidentally match with host taken from the scheme; absolute
# remote paths (``git@host:/srv/repos/foo``) don't map to any forge
# anyway, so rejecting them here costs nothing.
_SSH_RE = re.compile(
    r"^(?:[^@\s/]+@)?(?P<host>[^@:\s/]+):(?P<path>[^/\s][^\s]*?)(?:\.git)?/?$"
)
_HTTP_RE = re.compile(
    r"^(?P<scheme>https?|git|ssh)://"
    r"(?:[^@/]+@)?"
    r"(?P<host>[^/:]+)(?::\d+)?"
    r"/(?P<path>[^\s]+?)(?:\.git)?/?$"
)


def parse_remote(remote_url: str) -> tuple[str, str, str] | None:
    """Return ``(host, owner, repo)`` for *remote_url*, or ``None``.

    Accepts the three forms ``git`` actually uses:

    - SSH path-style: ``git@host:owner/repo[.git]``
    - URL form: ``{https,http,git,ssh}://[user@]host[:port]/owner/repo[.git]``

    Path components after ``owner`` are joined with ``/`` so nested
    GitLab subgroups (``group/sub/repo``) round-trip cleanly. Returns
    ``None`` for empty input, unrecognised shapes, or zero-segment
    paths.
    """
    if not remote_url:
        return None
    url = remote_url.strip()
    if not url:
        return None
    match = _HTTP_RE.match(url) or _SSH_RE.match(url)
    if match is None:
        return None
    host = match.group("host").lower()
    path = match.group("path").strip("/")
    if not path:
        return None
    segments = path.split("/")
    if len(segments) < 2:
        return None
    owner = "/".join(segments[:-1])
    repo = segments[-1]
    if not owner or not repo:
        return None
    return host, owner, repo


def detect_forge(
    remote_url: str,
    *,
    override_kind: str | None = None,
    override_url_base: str | None = None,
) -> ForgeMatch | None:
    """Resolve *remote_url* to a :class:`ForgeMatch`, or ``None``.

    The default detection runs the host through :data:`_HOST_PATTERNS`.
    Two overrides let an operator teach brr about internal hosts
    without us baking their domain into the table:

    - ``override_kind`` forces a forge kind regardless of the host.
    - ``override_url_base`` replaces the web host in the resulting
      URL (useful when the SSH remote uses a bare hostname but the
      web UI lives at a different domain or behind a path prefix).

    Both come from ``.brr/config`` ``[forge]`` keys; either may be
    set on its own.
    """
    parsed = parse_remote(remote_url)
    if parsed is None and override_url_base is None:
        return None
    if parsed is None:
        # Override-only path: a user with a bare ``forge.url_base`` and
        # an unparseable remote can't be helped — we still need the
        # owner/repo from the path. Bail rather than produce a wrong
        # link.
        return None
    host, owner, repo = parsed
    kind = override_kind
    if kind is None:
        for pattern, candidate in _HOST_PATTERNS:
            if pattern.match(host):
                kind = candidate
                break
    if kind not in KNOWN_KINDS:
        return None
    web_host = _strip_scheme(override_url_base) if override_url_base else host
    return ForgeMatch(kind=kind, host=web_host, owner=owner, repo=repo)


def view_branch_url(
    remote_url: str,
    branch: str,
    *,
    override_kind: str | None = None,
    override_url_base: str | None = None,
) -> str | None:
    """Return a clickable URL for *branch* on the forge, or ``None``.

    Returns ``None`` for empty branch names, unparseable remotes,
    unknown forge kinds, or branch names that look unsafe to drop
    into a URL (whitespace / control characters). The caller can
    surface the URL when present and stay quiet otherwise.
    """
    if not branch or not _is_url_safe_branch(branch):
        return None
    match = detect_forge(
        remote_url,
        override_kind=override_kind,
        override_url_base=override_url_base,
    )
    if match is None:
        return None
    template = _BRANCH_TEMPLATES.get(match.kind)
    if template is None:
        return None
    return template.format(
        host=match.host,
        owner=match.owner,
        repo=match.repo,
        branch=branch,
    )


def view_blob_url(
    remote_url: str,
    branch: str,
    rel_path: str,
    *,
    override_kind: str | None = None,
    override_url_base: str | None = None,
) -> str | None:
    """Return a clickable URL for the file *rel_path* on *branch*, or ``None``.

    The repo-relative *rel_path* keeps its slashes; both it and *branch* are
    validated against the same URL-safe character set used for branch links
    (letters, digits, ``-_./%``), so a malformed packet can't smuggle
    whitespace or control characters into the URL. Returns ``None`` for empty
    or unsafe inputs, unparseable remotes, or unknown forge kinds — the caller
    surfaces the URL when present and stays quiet otherwise.
    """
    rel = (rel_path or "").strip().lstrip("/")
    if not branch or not _is_url_safe_branch(branch):
        return None
    if not rel or not _is_url_safe_branch(rel):
        return None
    match = detect_forge(
        remote_url,
        override_kind=override_kind,
        override_url_base=override_url_base,
    )
    if match is None:
        return None
    template = _BLOB_TEMPLATES.get(match.kind)
    if template is None:
        return None
    return template.format(
        host=match.host,
        owner=match.owner,
        repo=match.repo,
        branch=branch,
        path=rel,
    )


def commit_url(
    remote_url: str,
    sha: str,
    *,
    override_kind: str | None = None,
    override_url_base: str | None = None,
) -> str | None:
    """Return a clickable URL for commit *sha* on the forge, or ``None``.

    Unlike :func:`thread_url` (which takes an explicit ``repo_path`` because
    an issue/PR reference can point at a different repo than the one the
    daemon is running in), a commit relic is always local to *remote_url* —
    the run's own origin — so owner/repo come from the same parse as
    :func:`view_branch_url`.
    """
    sha = (sha or "").strip()
    if not sha or not _is_url_safe_branch(sha):
        return None
    match = detect_forge(
        remote_url,
        override_kind=override_kind,
        override_url_base=override_url_base,
    )
    if match is None:
        return None
    template = _COMMIT_TEMPLATES.get(match.kind)
    if template is None:
        return None
    return template.format(host=match.host, owner=match.owner, repo=match.repo, sha=sha)


def thread_url(
    remote_url: str,
    repo_path: str,
    number: int | str,
    *,
    override_kind: str | None = None,
    override_url_base: str | None = None,
) -> str | None:
    """Return a clickable URL for issue/PR *number* on *repo_path*, or ``None``.

    The forge *kind* and web *host* come from the configured ``origin``
    remote (so self-hosted overrides apply), but the ``owner/repo`` comes
    from *repo_path* — the repo the conversation thread is actually about,
    which may differ from origin on a multi-repo project. Returns ``None``
    for a non-numeric *number*, an unparseable ``owner/repo`` shape, an
    unknown forge kind, or an undetectable remote.
    """
    try:
        num = int(str(number).strip())
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    path = (repo_path or "").strip().strip("/")
    if "/" not in path:
        return None
    owner, _, repo = path.rpartition("/")
    if not owner or not repo:
        return None
    match = detect_forge(
        remote_url,
        override_kind=override_kind,
        override_url_base=override_url_base,
    )
    if match is None:
        return None
    template = _ISSUE_TEMPLATES.get(match.kind)
    if template is None:
        return None
    return template.format(host=match.host, owner=owner, repo=repo, number=num)


def pull_request_url(
    remote_url: str,
    repo_path: str,
    number: int | str,
    *,
    override_kind: str | None = None,
    override_url_base: str | None = None,
) -> str | None:
    """Return the forge-native pull/merge-request URL for *number*."""
    try:
        num = int(str(number).strip())
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    path = (repo_path or "").strip().strip("/")
    if "/" not in path:
        return None
    owner, _, repo = path.rpartition("/")
    if not owner or not repo:
        return None
    match = detect_forge(
        remote_url,
        override_kind=override_kind,
        override_url_base=override_url_base,
    )
    if match is None:
        return None
    template = _PULL_REQUEST_TEMPLATES.get(match.kind)
    if template is None:
        return None
    return template.format(host=match.host, owner=owner, repo=repo, number=num)


def parse_pull_request_number(value: str) -> str | None:
    """Read a PR/MR number from a control-file value, or return ``None``.

    The control accepts an explicit number (``274`` / ``#274``) or a full
    HTTP(S) URL in any forge-native shape emitted by
    :func:`pull_request_url`. Requiring the whole scalar or a URL with at
    least ``owner/repo`` path segments keeps commit shas such as ``ea35206``
    and arbitrary trailing digits from masquerading as PRs.
    """
    text = (value or "").strip()
    match = _PULL_REQUEST_NUMBER_RE.fullmatch(text)
    if match:
        return match.group(1)
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    match = _PULL_REQUEST_PATH_RE.fullmatch(parsed.path)
    return match.group(1) if match else None


# ── Internals ────────────────────────────────────────────────────────


# Branch names in git accept almost anything but slashes and dots; the
# real risk for URL emission is control characters or whitespace
# sneaking in from a malformed packet. Allow the printable subset git
# itself uses (letters, digits, ``-_./``) plus ``%`` so callers can
# pre-escape if they want to.
_URL_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/\-%]+$")


def _is_url_safe_branch(branch: str) -> bool:
    """Branches with whitespace or control chars don't belong in a URL."""
    return bool(_URL_SAFE_BRANCH_RE.match(branch))


def _strip_scheme(url_base: str) -> str:
    """Return *url_base* with the ``https://`` / ``http://`` prefix
    stripped and any trailing slash removed, so the result slots into
    the URL template's ``{host}`` placeholder uniformly.
    """
    text = url_base.strip()
    for prefix in ("https://", "http://"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    return text.rstrip("/")
