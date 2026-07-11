"""Shared pytest fixtures.

Kept deliberately small: most tests build their own tmp repos via
``_helpers``. The one cross-cutting concern is keeping the daemon's
account-scoped store out of the developer's real home.
"""

import pytest


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
