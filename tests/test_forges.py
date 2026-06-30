"""Tests for :mod:`brr.forges` — remote URL parsing and forge URL inference."""

from __future__ import annotations

import pytest

from brr import forges


# ── parse_remote ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "remote, expected",
    [
        # SSH form (most common)
        ("git@github.com:Gurio/brr.git", ("github.com", "Gurio", "brr")),
        ("git@github.com:Gurio/brr", ("github.com", "Gurio", "brr")),
        # HTTPS form, with and without .git suffix
        ("https://github.com/Gurio/brr.git", ("github.com", "Gurio", "brr")),
        ("https://github.com/Gurio/brr", ("github.com", "Gurio", "brr")),
        # HTTPS with embedded credentials (token-auth checkouts)
        ("https://user@github.com/Gurio/brr.git", ("github.com", "Gurio", "brr")),
        ("https://user:tok@github.com/Gurio/brr.git", ("github.com", "Gurio", "brr")),
        # SSH URL form
        ("ssh://git@github.com/Gurio/brr.git", ("github.com", "Gurio", "brr")),
        # git:// form (rare; some legacy mirrors)
        ("git://github.com/Gurio/brr.git", ("github.com", "Gurio", "brr")),
        # GitLab subgroup: owner is joined with slashes
        ("git@gitlab.com:group/sub/repo.git",
         ("gitlab.com", "group/sub", "repo")),
        ("https://gitlab.com/group/sub/repo.git",
         ("gitlab.com", "group/sub", "repo")),
        # Trailing slash on URL form is forgiven
        ("https://github.com/Gurio/brr/", ("github.com", "Gurio", "brr")),
        # Self-hosted GitLab
        ("git@gitlab.internal.example.com:team/proj.git",
         ("gitlab.internal.example.com", "team", "proj")),
        # HTTPS with explicit port
        ("https://git.example.com:8443/team/proj.git",
         ("git.example.com", "team", "proj")),
    ],
)
def test_parse_remote_round_trips_common_forms(remote, expected):
    assert forges.parse_remote(remote) == expected


@pytest.mark.parametrize(
    "remote",
    [
        "",
        "   ",
        "not-a-remote",
        "https://github.com/onepart",  # missing repo
        "https://github.com/",  # missing both
        "git@github.com:",  # missing path entirely
        "scp://example.com/path/repo.git",  # unknown scheme
    ],
)
def test_parse_remote_returns_none_for_invalid_inputs(remote):
    assert forges.parse_remote(remote) is None


def test_parse_remote_lowercases_the_host():
    """Hosts are case-insensitive in DNS; the parser normalizes them so
    detection patterns can stay lowercase and unambiguous."""
    assert forges.parse_remote("git@GitHub.com:Gurio/brr.git") == (
        "github.com", "Gurio", "brr",
    )


# ── detect_forge ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "remote, kind",
    [
        ("git@github.com:Gurio/brr.git", "github"),
        ("https://github.com/Gurio/brr", "github"),
        ("git@gitlab.com:group/repo.git", "gitlab"),
        ("git@gitlab.internal.example.com:team/repo.git", "gitlab"),
        ("git@bitbucket.org:team/repo.git", "bitbucket"),
        ("git@codeberg.org:user/repo.git", "gitea"),
        ("git@gitea.example.com:user/repo.git", "gitea"),
        ("git@forgejo.example.com:user/repo.git", "gitea"),
    ],
)
def test_detect_forge_recognizes_known_hosts(remote, kind):
    match = forges.detect_forge(remote)
    assert match is not None
    assert match.kind == kind


def test_detect_forge_returns_none_for_unknown_host():
    """A bare ``git.example.com`` could be running anything; without an
    override we don't guess."""
    assert forges.detect_forge("git@git.example.com:team/repo.git") is None


def test_detect_forge_override_kind_forces_template():
    """``forge.kind = gitlab`` in config teaches brr what's at an
    internal host that doesn't match any default pattern."""
    match = forges.detect_forge(
        "git@git.example.com:team/repo.git",
        override_kind="gitlab",
    )
    assert match is not None
    assert match.kind == "gitlab"
    assert match.host == "git.example.com"


def test_detect_forge_override_url_base_replaces_host():
    """When the SSH host differs from the web host (mirrored remotes,
    proxied internal git), the override pins the web host the URL
    should use without changing forge detection."""
    match = forges.detect_forge(
        "git@git-ssh.example.com:team/repo.git",
        override_kind="gitlab",
        override_url_base="https://gitlab.example.com",
    )
    assert match is not None
    assert match.host == "gitlab.example.com"
    assert match.owner == "team"
    assert match.repo == "repo"


# ── view_branch_url ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "remote, branch, expected",
    [
        (
            "git@github.com:Gurio/brr.git",
            "brr/task-260513-1449-r120",
            "https://github.com/Gurio/brr/tree/brr/task-260513-1449-r120",
        ),
        (
            "https://gitlab.com/group/sub/repo.git",
            "feature/foo",
            "https://gitlab.com/group/sub/repo/-/tree/feature/foo",
        ),
        (
            "git@bitbucket.org:team/repo.git",
            "main",
            "https://bitbucket.org/team/repo/branch/main",
        ),
        (
            "git@codeberg.org:user/repo.git",
            "wip",
            "https://codeberg.org/user/repo/src/branch/wip",
        ),
    ],
)
def test_view_branch_url_produces_expected_links(remote, branch, expected):
    assert forges.view_branch_url(remote, branch) == expected


@pytest.mark.parametrize(
    "remote, branch, rel_path, expected",
    [
        (
            "git@github.com:Gurio/brr.git",
            "main",
            "run-state/Gurio__brr/run-260630.md",
            "https://github.com/Gurio/brr/blob/main/run-state/Gurio__brr/run-260630.md",
        ),
        (
            "https://gitlab.com/group/sub/repo.git",
            "main",
            "run-state/local/run.md",
            "https://gitlab.com/group/sub/repo/-/blob/main/run-state/local/run.md",
        ),
        (
            "git@codeberg.org:user/repo.git",
            "main",
            "run-state/x/run.md",
            "https://codeberg.org/user/repo/src/branch/main/run-state/x/run.md",
        ),
    ],
)
def test_view_blob_url_produces_expected_links(remote, branch, rel_path, expected):
    assert forges.view_blob_url(remote, branch, rel_path) == expected


def test_view_blob_url_strips_leading_slash():
    """A leading slash on the relative path must not double up in the URL."""
    assert forges.view_blob_url(
        "git@github.com:Gurio/brr.git", "main", "/run-state/run.md",
    ) == "https://github.com/Gurio/brr/blob/main/run-state/run.md"


def test_view_blob_url_returns_none_for_empty_or_unsafe_path():
    assert forges.view_blob_url("git@github.com:Gurio/brr.git", "main", "") is None
    assert forges.view_blob_url(
        "git@github.com:Gurio/brr.git", "main", "bad name.md",
    ) is None


def test_view_blob_url_returns_none_for_unknown_remote():
    assert forges.view_blob_url(
        "git@git.example.com:team/repo.git", "main", "run-state/run.md",
    ) is None


def test_view_branch_url_returns_none_for_unknown_remote():
    """No template for a bare ``git.example.com`` means no link — the
    daemon stays quiet rather than emit a guessed URL."""
    assert forges.view_branch_url(
        "git@git.example.com:team/repo.git", "feature/x",
    ) is None


def test_view_branch_url_returns_none_for_empty_branch():
    """Daemon may call before a branch exists; absent input → no link."""
    assert forges.view_branch_url(
        "git@github.com:Gurio/brr.git", "",
    ) is None


def test_view_branch_url_returns_none_for_branch_with_whitespace():
    """Whitespace or control chars suggest a malformed packet; refuse
    rather than emit a broken URL."""
    assert forges.view_branch_url(
        "git@github.com:Gurio/brr.git", "bad name",
    ) is None


def test_view_branch_url_honors_overrides_together():
    """An override pair should produce a working link for an internal
    host the host-pattern table doesn't recognise."""
    url = forges.view_branch_url(
        "git@git.internal.example.com:team/repo.git",
        "brr/feature",
        override_kind="gitlab",
    )
    assert url == "https://git.internal.example.com/team/repo/-/tree/brr/feature"


def test_view_branch_url_url_base_strips_scheme_and_slash():
    """``forge.url_base = https://gitlab.example.com/`` and ``gitlab.example.com``
    must both produce the same URL — the function normalises the input."""
    a = forges.view_branch_url(
        "git@ssh.example.com:team/repo.git",
        "main",
        override_kind="gitlab",
        override_url_base="https://gitlab.example.com/",
    )
    b = forges.view_branch_url(
        "git@ssh.example.com:team/repo.git",
        "main",
        override_kind="gitlab",
        override_url_base="gitlab.example.com",
    )
    assert a == b == "https://gitlab.example.com/team/repo/-/tree/main"
