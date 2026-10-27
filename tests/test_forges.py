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


