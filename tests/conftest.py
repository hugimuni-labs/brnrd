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

    Three jobs, and reading this fixture as only the first is what let #746
    happen, or #1264 after it.

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
    repository it names. #703 pins both into a worker run's environment on
    purpose (``daemon._child_git_pin``), and a worker that runs this suite
    inherits them: the ~319 ``["git", …]`` call sites under ``tests/`` then
    ``git init`` and ``git config`` into the *shared* host checkout rather
    than into their own tmpdir. That is #746 — a test's
    ``[user] Test/test@example.com`` and a stray ``init`` commit landed in
    the maintainer's live repository, and separately a ``core.worktree``
    write repointed it for fifteen minutes while every command exited 0.

    **Identity.** ``GIT_AUTHOR_*``/``GIT_COMMITTER_*`` resolve *before* any
    ``-c user.name=``/``-c user.email=`` a call site passes, exactly the way
    ``GIT_DIR``/``GIT_WORK_TREE`` outrank ``cwd=``/``-C`` above — same
    precedence shape, different axis (who committed, not which tree).
    ``daemon.py``'s worker env pins these four unconditionally for *every*
    strand and resident run (#1135/#1251, so a run's own commits land
    authored as the bot), and a strand running this very suite inherits them
    the same way it inherits the discovery pair. Unscrubbed, that silently
    breaks any fixture asserting on committer identity — a strand-run
    ``test_derive_auto_squash_merge_requires_github_committer`` reds out
    because its ``-c user.email=noreply@github.com`` loses to the inherited
    bot email — for reasons that have nothing to do with the code under
    test (#1264).

    So this fixture is a boundary, not a convenience: the suite's git may
    only ever touch trees the suite itself built, authored as whatever
    identity the test itself set up. Dropped here rather than at each call
    site because the fixture is the one place it can be said once. Names
    come from :data:`gitops.DISCOVERY_OVERRIDE_VARS` (read by
    ``gitops.explicit_repo_env`` and ``cli._drop_inherited_git_pin``) and
    :data:`gitops.IDENTITY_OVERRIDE_VARS` (the same four names
    ``gitops.bot_identity_env`` sets), so there is one list of each concern
    in the project and not a third copy typed out here.
    """
    for var in (*gitops.DISCOVERY_OVERRIDE_VARS, *gitops.IDENTITY_OVERRIDE_VARS):
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


@pytest.fixture(autouse=True)
def _no_claude_usage_pty_scrape(monkeypatch):
    """Never spawn a real ``claude /usage`` PTY scrape from the unit suite (#1552).

    ``claude_usage.capture_usage_raw`` shells out to a real Claude and drives
    its interactive TUI — correct in production, but fatal in tests on a
    maintainer's logged-in machine: every fresh test outbox can pay another
    multi-second scrape, while CI without Claude never exposes the cost.
    Patched to an empty screen so collectors take their normal unavailable
    path. Tests keep the parser, cache, and refresh machinery real and can
    patch this finer-grained seam when they mean to exercise probe behaviour.
    """
    from brr import claude_usage

    real_capture = claude_usage.capture_usage_raw
    monkeypatch.setattr(claude_usage, "capture_usage_raw", lambda **kwargs: b"")
    yield real_capture


@pytest.fixture(autouse=True)
def _no_real_power_assertion(monkeypatch):
    """Never spawn a real ``caffeinate``/``systemd-inhibit`` from the unit
    suite (#1485).

    ``invoke_runner`` starts a companion power-assertion process alongside
    every runner subprocess it launches, found via ``shutil.which``. On a
    real macOS (or systemd-inhibit-equipped Linux) dev/CI box that binary is
    often genuinely present, so an unpatched suite would spawn a real
    companion process per ``invoke_runner`` call -- and worse, land a
    *second* ``subprocess.Popen`` call through whatever fake a test
    installed, silently clobbering a ``captured["cmd"] = cmd``-shaped dict
    that assumed exactly one call. Delegates to the real ``shutil.which``
    for every other name, so real shell detection (``claude``, ``codex``,
    ...) is untouched. Tests exercising the power assertion itself
    (``TestPowerAssertion``) patch this back locally.
    """
    from brr import runner as runner_mod

    real_which = runner_mod.shutil.which

    def _which(name, *args, **kwargs):
        if name in ("caffeinate", "systemd-inhibit"):
            return None
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(runner_mod.shutil, "which", _which)
    yield
