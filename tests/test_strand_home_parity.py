"""The orphan bench: every checkout shape a strand can wake in resolves the
*same* home as the checkout it was cut from.

Born from the measured orphan (#1852, 2026-09-08): a ``git clone --shared``
strand resolved a ``project`` home — no account knowledge ("no kb is wired
up for this repo yet", on a repo with 203 pages), no dominion, security
config fail-open — and nothing in the daemon said so. ``account.home_parity``
is the live guard (``envs.__init__`` calls it at the seam that makes the
checkout); this bench drives the same predicate through each shape so a
shape nobody listed fails here first.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from brr import account, config as conf, gitops, knowledge
from tests.test_config_trust import init_git_repo


ORIGIN = "https://github.com/acme/widgets.git"


@pytest.fixture
def account_repo(tmp_path, monkeypatch):
    """A registered repo with a security config and one kb page in its home."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("BRNRD_HOME", raising=False)
    repo = tmp_path / "repo"
    init_git_repo(repo)
    (repo / ".brr").mkdir()
    home = tmp_path / "state" / "brnrd" / "accounts" / "acc_test" / "home"
    (home / "account").mkdir(parents=True)
    (home / "account" / "repos.json").write_text(
        json.dumps({"account_id": "acc_test", "repos": [{"path": str(repo)}]}),
        encoding="utf-8",
    )
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "docker.image=from-security\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", ORIGIN],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "seed"],
        check=True, capture_output=True,
    )
    ctx = account.resolve_context(repo, conf.load_config(repo), create=False)
    assert ctx.kind == "account"
    kb_dir = account.repo_knowledge_path(ctx, account.repo_label(repo, conf.load_config(repo)))
    kb_dir.mkdir(parents=True)
    (kb_dir / "index.md").write_text("# kb\n", encoding="utf-8")
    conf._SECURITY_PATH_CACHE.clear()
    return repo


def _shared_clone(repo: Path, dest: Path) -> Path:
    """The strand shape (`worktree.create_clone`): --shared, origin repointed, host marker."""
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(repo), str(dest)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(dest), "remote", "set-url", "origin", ORIGIN],
        check=True, capture_output=True,
    )
    (dest / ".git" / gitops._CLONE_HOST_ROOT_MARKER).write_text(str(repo), encoding="utf-8")
    return dest


def _linked_worktree(repo: Path, dest: Path) -> Path:
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wt", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def _host(repo: Path, dest: Path) -> Path:
    return repo


SHAPES = {
    "shared-clone": _shared_clone,
    "linked-worktree": _linked_worktree,
    "host": _host,
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_checkout_shape_resolves_the_parents_home(account_repo, tmp_path, shape):
    child = SHAPES[shape](account_repo, tmp_path / shape)

    assert account.home_parity(account_repo, child) is None, shape

    # The three consumers the orphan lost, each read from the child.
    host_cfg = conf.load_config(account_repo)
    child_cfg = conf.load_config(child)
    assert knowledge.active_kb_dir(child, child_cfg) == knowledge.active_kb_dir(
        account_repo, host_cfg
    )
    assert conf.security_config_path(
        child, conf._read_flat(conf.repo_config_path(child))
    ) == conf.security_config_path(
        account_repo, conf._read_flat(conf.repo_config_path(account_repo))
    )


def test_an_unmarked_clone_is_named_as_an_orphan(account_repo, tmp_path):
    """The guard's positive case: a clone with no host marker cannot reach the
    account and must say so — one line naming both homes — never ``None``."""
    clone = tmp_path / "orphan"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(account_repo), str(clone)],
        check=True, capture_output=True,
    )
    reason = account.home_parity(account_repo, clone)
    assert reason is not None
    assert "'project'" in reason and "'account'" in reason
