"""Shared pytest fixtures.

Kept deliberately small: most tests build their own tmp repos via
``_helpers``. The one cross-cutting concern is keeping the daemon's
account-scoped store out of the developer's real home.
"""

import pytest

from brr import gitops


@pytest.fixture(autouse=True)
def _hermetic_git_env(tmp_path_factory, monkeypatch):
    """Pin git's ambient environment — machine-independence *and* containment.

    Two jobs, and reading this fixture as only the first is what let #746
    happen.

    **Machine-independence.** CI runners have no git identity at all (``git
    commit`` fails, which surfaces downstream as a misleading "src refspec
    HEAD does not match any" push error in code that commits, e.g.
    ``home_link``), while a developer's global config can carry commit
    signing, ``core.hooksPath``, or a non-``main`` ``init.defaultBranch`` —
    any of which changes test behavior invisibly. Point
    ``GIT_CONFIG_GLOBAL`` at a known minimal config and drop the system
    config entirely. Repo-local config written by ``_helpers.init_git_repo``
    still wins where tests set it.

    **Containment.** ``GIT_DIR``/``GIT_WORK_TREE`` outrank *every* cwd-based
    discovery mechanism — ``cwd=``, ``-C <path>``, an absolute pathspec — so
    a process that inherits them addresses the pinned tree no matter which
    repository it names. #703 pins both into a run run's environment on
    purpose (``daemon._child_git_pin``), and a run that runs this suite
    inherits them: the ~319 ``["git", …]`` call sites under ``tests/`` then
    ``git init`` and ``git config`` into the *shared* host checkout rather
    than into their own tmpdir. That is #746 — a test's
    ``[user] Test/test@example.com`` and a stray ``init`` commit landed in
    the maintainer's live repository, and separately a ``core.worktree``
    write repointed it for fifteen minutes while every command exited 0.

    So this fixture is a boundary, not a convenience: the suite's git may
    only ever touch trees the suite itself built. Dropped here rather than
    at each call site because the fixture is the one place it can be said
    once. Names come from :data:`gitops.DISCOVERY_OVERRIDE_VARS` — the same
    tuple ``gitops.explicit_repo_env`` and ``cli._drop_inherited_git_pin``
    read, so there is one list of these two variables in the project and not
    a third copy typed out here.
    """
    for var in gitops.DISCOVERY_OVERRIDE_VARS:
        monkeypatch.delenv(var, raising=False)
    cfg = tmp_path_factory.getbasetemp() / "gitconfig-hermetic"
    if not cfg.exists():
        cfg.write_text(
            "[user]\n"
            "\tname = brr tests\n"
            "\temail = tests@brr.invalid\n"
            "[init]\n"
            "\tdefaultBranch = main\n"
            "[commit]\n"
            "\tgpgsign = false\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    yield


@pytest.fixture(autouse=True)
def _isolate_account_state(tmp_path_factory, monkeypatch):
    """Redirect ``XDG_STATE_HOME`` at a per-test temp dir.

    The account context auto-creates a local-first store under
    ``XDG_STATE_HOME/brnrd`` (``~/.local/state/brnrd`` by default) whenever the
    daemon is started from a git worktree. Without isolation, full-daemon tests
    write into the developer's real home *and*, worse, read a stale registry
    left by a previous run — so one test's repo (e.g. this checkout as
    ``default_repo``) leaks into an unrelated test and event routing silently
    no-ops. Pointing ``XDG_STATE_HOME`` at a fresh temp dir per test makes the
    default account location pristine and disposable. Tests that set
    ``home.path`` explicitly are unaffected.
    """
    state_home = tmp_path_factory.mktemp("xdg-state")
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    yield


@pytest.fixture(autouse=True)
def _isolate_codex_home(tmp_path_factory, monkeypatch):
    """Point ``CODEX_HOME`` at an empty per-test dir.

    ``runner_cores._models_from_disk`` reads ``$CODEX_HOME/models_cache.json``
    as the codex model-discovery source. Without isolation, a developer's real
    ``~/.codex`` cache leaks host models into catalog/probe tests. Tests that
    exercise the disk probe clear ``probe_shell_models``'s ``lru_cache``
    themselves; clearing it here would force re-probing inside tests that fake
    ``subprocess.Popen`` and rely on the primed cache.
    """
    codex_home = tmp_path_factory.mktemp("codex-home")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    yield


@pytest.fixture(autouse=True)
def _no_codex_app_server_probe(monkeypatch):
    """Never spawn ``codex app-server`` from the unit suite (#315).

    ``codex_usage.probe_rate_limits`` shells out to a real Codex and makes a
    real backend call — correct in production (it is how an idle Codex reports
    quota at all), fatal in tests: it would make the suite depend on a
    logged-in Codex, add a second of latency per collector call, and give
    different answers on CI than on a maintainer's box. Patched to
    "unavailable", which is also the honest default for a machine with no
    Codex: collectors then degrade to the cached snapshot or the passive
    rollout read. Tests that mean to exercise the probe patch it back.
    """
    from brr import codex_usage

    real_probe = codex_usage.probe_rate_limits
    monkeypatch.setattr(codex_usage, "probe_rate_limits", lambda **kwargs: None)
    # Yielded so a test that *means* to exercise the real probe (its
    # never-raises contract) can reach past the patch for it.
    yield real_probe
