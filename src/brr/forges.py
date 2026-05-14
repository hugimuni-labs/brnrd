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
