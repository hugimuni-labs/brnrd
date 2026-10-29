"""Tests for CLI dispatch."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from brr.cli import main

from _helpers import _says, init_git_repo


REPO = Path(__file__).parents[1]


def test_legend_is_hidden_but_still_parses():
    from brr.cli import HIDDEN_COMMANDS

    assert "legend" in HIDDEN_COMMANDS


# ── brnrd relic issue (#686) ─────────────────────────────────────────────────
#
# The front door onto `.relics.jsonl`. Issue produce is the one relic kind
# the daemon cannot derive — `gh issue close` happens in the resident's
# shell — so the record exists only if the resident writes it, and until now
# that meant remembering a JSON shape.


def _relic_env(monkeypatch, outbox):
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))


# ── brnrd relic pr ───────────────────────────────────────────────────────────
#
# The second-PR front door: `.pr` holds exactly one PR per run, so a run
# that opens more than one had no legal way to self-report the rest onto
# the same `{"kind": "pr", "number": N}` grammar `collect()` parses.


# ── brnrd relic merge ────────────────────────────────────────────────────────


# ── brnrd relic item (#972, THE WELD) ────────────────────────────────────────
#
# The warp-item half of the manifest: the run's ancestry address
# (`<layer>#<slug>`), written by the daemon at ignition or by a resident that
# only learned mid-run which item it serves.


def test_relic_issue_feeds_the_produce_split(tmp_path, monkeypatch):
    """End to end: what the command writes is what the display block counts."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _relic_env(monkeypatch, outbox)

    from brr import relics as relics_mod

    assert main(["relic", "issue", "686", "--opened"]) == 0
    assert main(["relic", "issue", "317", "--closed"]) == 0
    actions = relics_mod.issue_actions(relics_mod.read_reported(outbox))
    assert relics_mod.issues_phrase(actions) == "1 created · 1 completed"


# ── brnrd relic comment / message / file (#1060) ────────────────────────────
#
# `_PROMISABLE` names nine kinds; `relic` used to expose two front doors
# (`issue`, `item`). These three close the gap for the hand-attested kinds
# that had no subcommand at all — same shape as `issue`/`item`: one
# positional for the grammar's own field, the same outside-a-run and
# failed-append refusals.


# ── brnrd await (#959, collapsed by #1187) ────────────────────────────


class _FakeClock:
    """A monotonic clock that only moves when something sleeps.

    ``cmd_await``'s slice ceiling and ``do.await_verdict``'s staging wait are
    both real wall-clock loops; driving them from a fake clock keeps the
    tests instant while still exercising the true loop bodies.
    """

    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.slept = []
        self._on_sleep = on_sleep

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.slept.append(seconds)
        if self._on_sleep is not None:
            self._on_sleep()


def test_add_registers_repo_in_connected_account_home(monkeypatch, tmp_path, capsys):
    current = tmp_path / "current"
    target = tmp_path / "target"
    init_git_repo(current)
    init_git_repo(target)
    cloud_dir = current / ".brr" / "gates"
    cloud_dir.mkdir(parents=True)
    (cloud_dir / "cloud.json").write_text(
        json.dumps({
            "brnrd_url": "https://brnrd.example",
            "token": "tok",
            "account_id": "acct-1",
            "repo_id": "repo-1",
        }),
        encoding="utf-8",
    )
    monkeypatch.chdir(current)

    assert main(["account", "add", str(target)]) is None

    out = capsys.readouterr().out
    assert "added target" in out
    # tests/conftest isolates XDG_STATE_HOME at a generated temp path, so read
    # the resolver instead of guessing its parent from this test's tmp_path.
    from brr import account, config as conf

    ctx = account.resolve_context(current, conf.load_config(current), create=False)
    registry = json.loads((ctx.dominion_repo / "account" / "repos.json").read_text())
    assert {item["label"] for item in registry["repos"]} == {
        "home", "current", "target",
    }
    assert registry["home_kind"] == "account"
    assert registry["account_id"] == "acct-1"


def test_account_disconnect_removes_gate_state_but_keeps_the_home(
    monkeypatch, tmp_path, capsys
):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    from brr import account
    from brr.gates import cloud, runtime

    ctx = account.resolve_context(
        repo,
        {
            "home.kind": "account",
            "account.id": "acct-1",
            "repo.label": "owner/repo",
        },
    )
    account_root = account.context_home_root(ctx) / "account"
    runtime.save_state(
        account_root,
        "cloud",
        {
            "brnrd_url": "https://brnrd.example",
            "token": "secret",
            "account_id": "acct-1",
            "repo_id": "repo-1",
        },
    )
    credential = account_root / "credentials" / "github" / "token"
    credential.parent.mkdir(parents=True)
    credential.write_text("derived-token\n", encoding="utf-8")
    # Disconnect revokes the token on brnrd (#2528); brnrd.example is not a server.
    asked = []
    monkeypatch.setattr(cloud, "revoke", lambda state_dir: asked.append(state_dir) or [])

    assert main(["account", "disconnect"]) is None
    assert account_root in asked

    assert not runtime.state_path(account_root, "cloud").exists()
    assert not credential.parent.exists()
    assert (account_root / "repos.json").exists()
    assert not cloud.is_configured(repo / ".brr")
    assert "Disconnected this daemon" in capsys.readouterr().out


def test_account_connect_refused_connection_exits_with_recovery_not_traceback(
    tmp_path,
):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    env = {
        **os.environ,
        "BRNRD_URL": "http://127.0.0.1:9",
        "PYTHONPATH": str(REPO / "src"),
    }

    result = subprocess.run(
        [sys.executable, "-m", "brr", "account", "connect"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "account service at http://127.0.0.1:9 is unreachable" in output
    assert _says(output, "Check the URL and network connection")
    assert _says(output, "re-run `brnrd account connect`")
    assert "Traceback" not in output


def test_account_connect_pairs_installs_and_starts_service(
    monkeypatch, tmp_path, capsys,
):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    # The initialized-repo shape; the no-AGENTS.md path installs identically
    # since #1244 fork 1 — see test_account_connect_installs_without_agents_md.
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    # No background service on this machine — `connect` skips the install
    # entirely when one is already up (a machine has one daemon serving
    # every connected repo), and without this the suite would read the
    # developer's own live LaunchAgent.
    monkeypatch.setattr("brr.daemon_install.service_alive", lambda: None)
    calls = []

    monkeypatch.setattr(
        "brr.gates.cloud.connect",
        lambda brr_dir, **kwargs: calls.append(("connect", brr_dir, kwargs)),
    )
    monkeypatch.setattr(
        "brr.daemon_install.install",
        lambda **kwargs: calls.append(("install", kwargs)) or 0,
    )

    assert main([
        "account",
        "connect",
        "https://brnrd.example",
        "--daemon-name",
        "wyrd-box",
        "--yes-linger",
    ]) is None

    assert calls == [
        (
            "connect",
            repo / ".brr",
            {
                "brnrd_url": "https://brnrd.example",
                "daemon_name": "wyrd-box",
            },
        ),
        (
            "install",
            {
                "no_start": False,
                "prompt_linger": True,
                "assume_yes_linger": True,
            },
        ),
    ]
    assert _says(capsys.readouterr().out, "Connected and listening in the background")


def _pair_a_machine_account(
    tmp_path, *, url: str = "https://brnrd.example", account_id: str = "acc_reuse",
):
    """A valid machine-level pairing, the shape `account connect` leaves one.

    Built the way a real machine got it: paired from a *first* repo, so the
    account home is created by resolving that repo with an explicit account
    id, and the gate state lands in the account-owned state dir with its
    token in the sibling file — exactly what `cloud.machine_account`
    validates. No network, no daemon, no credential mutation.
    """
    from brr import account
    from brr.gates import cloud

    first = tmp_path / "the-first-repo"
    init_git_repo(first)
    ctx = account.resolve_context(
        first, {"account.id": account_id, "home.kind": "account"}, create=True,
    )
    home = account.context_home_root(ctx)
    cloud._save_state_to_dir(home / "account", {
        "brnrd_url": url,
        "account_id": account_id,
        "repo_id": "r-first",
        "token": "tok-reuse",
    })
    return home


def test_account_connect_reuses_a_machine_account_without_pairing(
    monkeypatch, tmp_path, capsys,
):
    """2026-09-29, pinned: a new folder on a machine that already has a
    connected account must not print a pairing code. `connect` looks
    machine-side first and registers this repo through the same
    `account register_repo` path `account add` walks. Not one request may
    leave the machine: no pair POST, no poll, nothing. The account's
    `default_repo` is preserved — an ordinary connect must not redirect
    unrelated incoming chat (`account add` may flip it deliberately).

    `--local-memory` is test isolation, not a behaviour choice: with the
    repo registered, the tail's memory step resolves the *account* home and
    would reach for `gh` on a developer machine."""
    home = _pair_a_machine_account(tmp_path)
    repo = tmp_path / "the-new-folder"
    init_git_repo(repo)
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    requests = []
    monkeypatch.setattr(
        "brr.gates.cloud._request",
        lambda _url, method, path, **_kw: requests.append((method, path)) or {},
    )
    monkeypatch.setattr(
        "brr.gates.cloud.connect",
        lambda *_a, **_k: pytest.fail(
            "connect re-paired a machine that already has an account"
        ),
    )
    installed = []
    monkeypatch.setattr("brr.daemon_install.service_alive", lambda: None)
    monkeypatch.setattr(
        "brr.daemon_install.install",
        lambda **kw: installed.append(kw) or 0,
    )

    assert main([
        "account", "connect", "https://brnrd.example", "--local-memory",
    ]) is None

    assert requests == [], "the reuse path must not touch the network"
    assert installed, "the shared tail must still install the service"
    out = capsys.readouterr().out
    assert _says(out, "already paired to account acc_reuse")
    assert _says(out, "no new pairing needed")
    assert _says(out, "Connected and listening in the background")

    # Registration went through the existing path, but the account's
    # default repo is preserved: the new folder is listed, the old default
    # still routes.
    registry = json.loads((home / "account" / "repos.json").read_text(encoding="utf-8"))
    labels = {entry["label"] for entry in registry["repos"]}
    assert "the-new-folder" in labels
    assert registry["default_repo"] == "the-first-repo"
    # And the repo now resolves the machine account's own gate state — the
    # daemon reading `is_configured` from this folder sees the account.
    from brr.gates import cloud

    assert cloud.is_configured(repo / ".brr") is True


def test_account_connect_stops_when_several_accounts_could_be_reused(
    monkeypatch, tmp_path, capsys,
):
    """Two valid accounts on one machine are a choice the human owns: never
    silently pick the first sorted one — stop, before registering or
    pairing, and name the way to pick (BRNRD_HOME + re-run)."""
    _pair_a_machine_account(tmp_path, account_id="acc_one")
    _pair_a_machine_account(tmp_path, account_id="acc_two")
    repo = tmp_path / "the-new-folder"
    init_git_repo(repo)
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    monkeypatch.setattr(
        "brr.gates.cloud.connect",
        lambda *_a, **_k: pytest.fail(
            "an ambiguous machine must not reach pairing either"
        ),
    )

    with pytest.raises(SystemExit) as excinfo:
        main(["account", "connect", "https://brnrd.example", "--local-memory"])

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "several accounts on this machine are already paired" in out
    assert "acc_one" in out and "acc_two" in out
    assert "BRNRD_HOME=<home> brnrd account connect" in out
    # Nothing was registered anywhere: both registries still list only the
    # repo they were built from.
    accounts_root = (
        Path(os.environ["XDG_STATE_HOME"]) / "brnrd" / "accounts"
    )
    for account_dir in ("acc_one", "acc_two"):
        registry = json.loads(
            (accounts_root / account_dir / "home" / "account" / "repos.json")
            .read_text(encoding="utf-8")
        )
        labels = {entry["label"] for entry in registry["repos"]}
        assert "the-new-folder" not in labels


def test_account_connect_brnrd_home_resolves_an_ambiguous_machine(
    monkeypatch, tmp_path, capsys,
):
    """BRNRD_HOME is the explicit pick: with two valid accounts, it names
    the one to reuse without asking. An explicit home that holds no valid
    pairing is the whole candidate set — no silent fall-through to the
    scan."""
    _pair_a_machine_account(tmp_path, account_id="acc_one")
    home_two = _pair_a_machine_account(tmp_path, account_id="acc_two")
    repo = tmp_path / "the-new-folder"
    init_git_repo(repo)
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("BRNRD_HOME", str(home_two))

    monkeypatch.setattr(
        "brr.gates.cloud.connect",
        lambda *_a, **_k: pytest.fail("the explicit account was not reused"),
    )
    monkeypatch.setattr(
        "brr.daemon_install.service_alive",
        lambda: (4242, tmp_path / "elsewhere"),
    )

    assert main([
        "account", "connect", "https://brnrd.example", "--local-memory",
    ]) is None

    out = capsys.readouterr().out
    assert _says(out, "already paired to account acc_two")
    registry = json.loads(
        (home_two / "account" / "repos.json").read_text(encoding="utf-8")
    )
    labels = {entry["label"] for entry in registry["repos"]}
    assert "the-new-folder" in labels


def test_account_connect_pair_flag_forces_a_fresh_pairing(
    monkeypatch, tmp_path, capsys,
):
    """`--pair` is the explicit opt-out: a machine account exists and is
    valid, and the caller asked to pair anyway (a second account on the
    same server). Discovery must not turn an explicit request into a
    silent reuse."""
    _pair_a_machine_account(tmp_path)
    repo = tmp_path / "the-second-folder"
    init_git_repo(repo)
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    paired = []
    monkeypatch.setattr(
        "brr.gates.cloud.connect",
        lambda _brr_dir, **kw: paired.append(kw) or {},
    )
    # A running daemon keeps the tail off the service install — this test
    # is about the pairing fork, not the install.
    monkeypatch.setattr(
        "brr.daemon_install.service_alive",
        lambda: (4242, tmp_path / "the-first-repo"),
    )

    assert main([
        "account", "connect", "https://brnrd.example", "--pair", "--local-memory",
    ]) is None

    assert paired and paired[0]["brnrd_url"] == "https://brnrd.example"


def test_account_connect_pairs_rather_than_join_another_server(
    monkeypatch, tmp_path, capsys,
):
    """An account paired to one server is not the account a connect aimed at
    another server wants: joining it would leave the daemon polling a URL
    its caller never named."""
    home = _pair_a_machine_account(tmp_path, url="https://brnrd.example")
    repo = tmp_path / "the-other-server-folder"
    init_git_repo(repo)
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    paired = []
    monkeypatch.setattr(
        "brr.gates.cloud.connect",
        lambda _brr_dir, **kw: paired.append(kw) or {},
    )
    monkeypatch.setattr(
        "brr.daemon_install.service_alive",
        lambda: (4242, tmp_path / "elsewhere"),
    )

    assert main([
        "account", "connect", "https://other.example", "--local-memory",
    ]) is None

    assert paired and paired[0]["brnrd_url"] == "https://other.example"
    registry = json.loads((home / "account" / "repos.json").read_text(encoding="utf-8"))
    labels = {entry["label"] for entry in registry["repos"]}
    assert "the-other-server-folder" not in labels


def test_account_connect_installs_without_agents_md(
    monkeypatch, tmp_path, capsys,
):
    """#1244 fork 1: `connect` before `init` now installs normally.

    This used to skip the service install entirely (#1238's preflight):
    `daemon.start` immediately hard-exited with no `AGENTS.md`, so
    installing anyway handed the service manager a job whose first line
    was that exit — a crash loop with nothing proving it. `daemon.start`
    no longer exits for that reason (it prints and boots), so the skip's
    whole premise is gone and install proceeds exactly as it would for an
    initialized repo — see `test_account_connect_pairs_installs_and_starts_service`,
    the same shape, with no `AGENTS.md` written."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    assert not (repo / "AGENTS.md").exists()
    monkeypatch.chdir(repo)
    # No background service on this machine — `connect` skips the install
    # entirely when one is already up (a machine has one daemon serving
    # every connected repo), and without this the suite would read the
    # developer's own live LaunchAgent.
    monkeypatch.setattr("brr.daemon_install.service_alive", lambda: None)
    installed = []

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
    monkeypatch.setattr(
        "brr.daemon_install.install",
        lambda **kwargs: installed.append(kwargs) or 0,
    )

    assert main(["account", "connect", "https://brnrd.example"]) is None

    assert len(installed) == 1
    out = capsys.readouterr().out
    assert _says(out, "Connected and listening in the background")


def test_account_connect_queues_greeting_when_a_door_is_configured(
    monkeypatch, tmp_path, capsys,
):
    """#1244 fork 2: connect ends by queueing the first-wake greeting when
    `AGENTS.md` is missing and a door that can actually carry it (here,
    telegram, bound before this connect ran) is configured."""
    from brr import protocol
    from brr.gates import runtime as gate_runtime

    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    gate_runtime.save_state(repo / ".brr", "telegram", {"token": "t", "chat_id": 777})

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
    monkeypatch.setattr("brr.daemon_install.install", lambda **_kw: 0)

    assert main(["account", "connect", "https://brnrd.example"]) is None

    out = capsys.readouterr().out
    assert _says(out, "queued the setup interview")
    assert "telegram" in out
    pending = protocol.list_pending(repo / ".brr" / "inbox")
    assert len(pending) == 1
    assert pending[0]["source"] == "telegram"


def test_account_connect_no_door_names_the_fallback(monkeypatch, tmp_path, capsys):
    """No chat gate configured (only the just-completed cloud pairing, which
    can't originate a message) ⇒ no greeting queued, and the reply names
    both remaining paths (`brnrd init` at a terminal, `--defaults`)."""
    from brr import protocol

    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
    monkeypatch.setattr("brr.daemon_install.install", lambda **_kw: 0)

    assert main(["account", "connect", "https://brnrd.example"]) is None

    out = capsys.readouterr().out
    assert "no interview queued" in out
    assert "--defaults" in out
    assert "brnrd init" in out
    assert protocol.list_pending(repo / ".brr" / "inbox") == []


def test_account_connect_defaults_flag_writes_init_defaults_not_the_interview(
    monkeypatch, tmp_path, capsys,
):
    """The signed opt-out rider: `--defaults` skips the interview and writes
    today's `brnrd init` defaults directly — no greeting event queued."""
    from brr import protocol

    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
    monkeypatch.setattr("brr.daemon_install.install", lambda **_kw: 0)
    calls = []
    monkeypatch.setattr(
        "brr.adopt.init_repo",
        lambda *a, **kw: calls.append((a, kw)),
    )

    assert main([
        "account", "connect", "https://brnrd.example", "--defaults",
    ]) is None

    assert calls == [((), {"defaults": True, "knowledge_shape": "home"})]
    assert _says(capsys.readouterr().out, "writing brnrd init defaults")
    assert protocol.list_pending(repo / ".brr" / "inbox") == []


def test_connect_memory_links_both_private_repos_by_default(
    monkeypatch, tmp_path, capsys,
):
    from types import SimpleNamespace
    from brr import cli, home_link

    monkeypatch.setattr(
        "brr.account.resolve_context",
        lambda *_a, **_kw: SimpleNamespace(kind="account", home_root=tmp_path, dominion_repo=tmp_path),
    )
    monkeypatch.setattr(home_link, "gh_available", lambda: True)
    monkeypatch.setattr("brr.config.load_config", lambda _root: {})
    monkeypatch.setattr(
        "brr.knowledge.ensure_checkout", lambda *_a, **_kw: tmp_path / ".brnrd-kb",
    )
    calls = []
    monkeypatch.setattr(
        home_link, "link_home",
        lambda *a, **kw: calls.append((a, kw)) or [
            home_link.RepoLinkResult(
                "dominion", tmp_path / "home", "https://x/home", "adopted", True,
            ),
            home_link.RepoLinkResult(
                "knowledge", tmp_path / "knowledge", "https://x/kb", "created", True,
            ),
        ],
    )

    cli._connect_memory(tmp_path, local_only=False)

    assert len(calls) == 1
    out = capsys.readouterr().out
    assert "private memory ready" in out
    assert "dominion: adopted" in out and "knowledge: created" in out
    assert (tmp_path / "surface" / "topics" / "adoption.md").exists()


def test_connect_memory_local_only_is_an_explicit_opt_out(
    monkeypatch, tmp_path, capsys,
):
    from types import SimpleNamespace
    from brr import cli, home_link

    monkeypatch.setattr(
        "brr.account.resolve_context",
        lambda *_a, **_kw: SimpleNamespace(kind="account", home_root=tmp_path, dominion_repo=tmp_path),
    )
    monkeypatch.setattr("brr.config.load_config", lambda _root: {})
    checkout = tmp_path / ".brnrd-kb"
    ensured = []
    monkeypatch.setattr(
        "brr.knowledge.ensure_checkout",
        lambda *a, **kw: ensured.append((a, kw)) or checkout,
    )
    monkeypatch.setattr(
        home_link, "link_home",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("local-only must not link remotes")
        ),
    )

    cli._connect_memory(tmp_path, local_only=True)

    assert len(ensured) == 1
    out = capsys.readouterr().out
    assert "explicit --local-memory" in out
    assert str(checkout) in out


def test_account_connect_skips_setup_entirely_once_agents_md_exists(
    monkeypatch, tmp_path, capsys,
):
    """An already-initialized repo gets neither the greeting nor the
    `--defaults` writer — connect is purely pairing there, unchanged."""
    from brr import protocol

    repo = tmp_path / "repo"
    init_git_repo(repo)
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
    monkeypatch.setattr("brr.daemon_install.install", lambda **_kw: 0)
    monkeypatch.setattr(
        "brr.adopt.init_repo",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("init_repo must not run when AGENTS.md exists"),
        ),
    )

    assert main(["account", "connect", "https://brnrd.example"]) is None

    out = capsys.readouterr().out
    assert not _says(out, "queued the setup interview")
    assert "no interview queued" not in out
    assert protocol.list_pending(repo / ".brr" / "inbox") == []


def _scaffold_project_home(tmp_path, name="repo"):
    """A just-scaffolded project home, matching account.py's own test helper."""
    from brr import account

    repo = tmp_path / name
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    (repo / ".brr" / "inbox").mkdir(parents=True)
    (repo / ".brr" / "responses").mkdir(parents=True)
    home = tmp_path / f"{name}-home"
    account.resolve_context(repo, {"home.path": str(home)})
    return home


def test_home_sweep_orphans_lists_without_deleting_by_default(monkeypatch, tmp_path, capsys):
    home = _scaffold_project_home(tmp_path)
    monkeypatch.setattr("brr.account.list_project_homes", lambda: [home])

    assert main(["home", "sweep-orphans"]) == 0

    out = capsys.readouterr().out
    assert str(home) in out
    assert "orphan" in out
    assert "dry run" in out
    assert home.is_dir()  # nothing deleted


def test_home_sweep_orphans_keeps_a_home_with_real_content(monkeypatch, tmp_path, capsys):
    home = _scaffold_project_home(tmp_path)
    (home / "runs" / "repo" / "run-x").mkdir(parents=True)
    (home / "runs" / "repo" / "run-x" / "state.md").write_text("# state\n", encoding="utf-8")
    monkeypatch.setattr("brr.account.list_project_homes", lambda: [home])

    assert main(["home", "sweep-orphans"]) == 0

    out = capsys.readouterr().out
    assert "keep" in out
    assert "0 default-scaffold" in out
    assert home.is_dir()


def test_home_sweep_orphans_delete_yes_removes_the_orphan(monkeypatch, tmp_path, capsys):
    home = _scaffold_project_home(tmp_path)
    monkeypatch.setattr("brr.account.list_project_homes", lambda: [home])

    def _fail_confirm(*_a, **_kw):  # pragma: no cover
        raise AssertionError("--yes must not prompt")

    monkeypatch.setattr("brr.adopt._confirm", _fail_confirm)

    assert main(["home", "sweep-orphans", "--delete", "--yes"]) == 0

    out = capsys.readouterr().out
    assert "deleted" in out
    assert not home.parent.exists()


def test_home_sweep_orphans_delete_without_yes_needs_a_tty(monkeypatch, tmp_path):
    home = _scaffold_project_home(tmp_path)
    monkeypatch.setattr("brr.account.list_project_homes", lambda: [home])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(SystemExit) as exc:
        main(["home", "sweep-orphans", "--delete"])
    assert "--yes" in str(exc.value)
    assert home.is_dir()  # refused, not deleted


def test_daemon_up_engine2_uses_account_paths_and_direct_serve(monkeypatch, tmp_path):
    from types import SimpleNamespace

    calls = []
    home = tmp_path / "home"
    inbox = home / "dispatch" / "inbox"
    responses = home / "dispatch" / "responses"
    monkeypatch.setattr("brr.cli._repo_root", lambda: tmp_path)
    monkeypatch.setattr("brr.dev_reload.clear_reexec_marker", lambda: None)
    monkeypatch.setattr("brr.dev_reload.capture_image_fingerprint", lambda: None)
    monkeypatch.setattr(
        "brr.account.resolve_context",
        lambda _root, _cfg: SimpleNamespace(
            enabled=True, home_root=home, dispatch_inbox=inbox,
            responses_dir=responses))

    class FakeDaemon2:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

        def serve(self, role="any"):
            calls.append(("serve", role))

        def stop(self):
            calls.append("stop")

    import subprocess
    workers = []
    real_popen = subprocess.Popen

    class FakePopen:
        def __new__(cls, argv, *args, **kwargs):
            if "brr.daemon2" not in list(argv):
                return real_popen(argv, *args, **kwargs)  # git and friends
            return super().__new__(cls)

        def __init__(self, argv, **_kwargs):
            self.argv, self.terminated = argv, False
            workers.append(self)

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("brr.daemon2.runtime.Daemon2", FakeDaemon2)
    monkeypatch.setattr("subprocess.Popen", FakePopen)
    main(["daemon", "up", "--engine", "2"])

    assert calls == [
        ((tmp_path, home), {"runtime_dir": tmp_path / ".brr",
                            "inbox_dir": inbox, "responses_dir": responses,
                            "dev_reload_enabled": None}),
        ("serve", "resident"),
    ]
    # Strand followers carry strands beside the resident loop, then stop with it.
    assert len(workers) == 3
    assert all(w.argv[w.argv.index("--role") + 1] == "strand" for w in workers)
    assert all(w.argv[w.argv.index("--inbox") + 1] == str(inbox) for w in workers)
    assert all(w.terminated for w in workers)
    assert not (tmp_path / ".brr" / "daemon.pid").exists()
    assert not (tmp_path / ".brr" / "daemon2.active").exists()


# ── brnrd runners list (step 2, design-runner-cores.md) ───────────────────────


# ── the CLI surface itself (#49) ──────────────────────────────────────────────
#
# The verb list is a contract: `brnrd --help` is the front door, and every doc,
# prompt, and muscle-memory habit spells against it. Before #49 the tree had
# drifted for months without a single test noticing. These pin the surface so
# the next drift is a test failure, not a discovery.


def test_help_stays_small_enough_to_read():
    # The whole point of the noun consolidation: a front door a human can scan.
    # Not a golden count — a ceiling. Adding a top-level verb should require
    # arguing that it earns one of these slots.
    from brr.cli import PUBLIC_COMMANDS

    # `enable` (#856) briefly took a nineteenth slot; #1746 folded its one
    # live behaviour (`--borrowed`) into `init` and retired the verb, so the
    # ceiling stood at 18.
    #
    # 21 since 2026-09-10, and the three arguments are recorded here rather
    # than in a commit message, because this assertion is the thing that
    # demands them:
    #
    #   `config`  — it is the verb that sets every default, and it was
    #     hidden. Measured from the maintainer's own terminal: `brnrd -h`
    #     listed sixteen verbs and not one of them was how you set anything.
    #     A front door that is scannable and tells you nothing about
    #     configuration is not the property this ceiling protects.
    #
    #   `resume` / `drop` (#1881) — the hand-controls for pause-on-message.
    #     Hidden on 2026-09-10 precisely because nobody had argued for them;
    #     argued the same day, by the maintainer: after an interruption the
    #     *user at their own keyboard* is a legitimate caller, and a user
    #     cannot reach for a verb the front door does not name. Splitting
    #     them — `resume` public, `drop` hidden — would be worse than either,
    #     since they are one control with two directions.
    #
    # The ceiling worked exactly as designed in all three cases: it caught
    # verbs that had never been argued for, and moved only once they were.
    assert len(PUBLIC_COMMANDS) <= 21


def test_daemon_up_service_path_reports_available_update(monkeypatch, capsys):
    import argparse

    from brr import release_availability
    from brr.cli import cmd_daemon_up

    monkeypatch.setattr("brr.daemon_install.start_service", lambda: 0)
    monkeypatch.setattr(
        release_availability,
        "refresh_if_stale",
        lambda _root: release_availability.Availability("0.1.0", "0.2.0"),
    )

    assert cmd_daemon_up(argparse.Namespace(foreground=False, dev_reload=None)) == 0
    assert "[brnrd] update available: 0.1.0 → 0.2.0" in capsys.readouterr().out


# ── brnrd gate list / account status / completions (#49, new surfaces) ────────


def test_account_status_does_not_create_the_home_it_reports(monkeypatch, tmp_path):
    # A status command that materializes a home is lying about what it found.
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    seen = {}

    real = __import__("brr.account", fromlist=["resolve_context"]).resolve_context

    def spy(repo_root, cfg=None, *, create=True):
        seen["create"] = create
        return real(repo_root, cfg, create=create)

    monkeypatch.setattr("brr.account.resolve_context", spy)

    assert main(["account", "status"]) == 0
    assert seen["create"] is False


# ── brnrd kb — optional query (#649) ─────────────────────────────────────────


def _kb_repo(tmp_path):
    """Set up a minimal repo+kb dir for cmd_kb tests."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    kb = repo / "kb"
    kb.mkdir()
    # Two pages so compute_graph_stats returns a non-empty GraphStats.
    (kb / "index.md").write_text("# Index\n", encoding="utf-8")
    (kb / "subject-test.md").write_text("# Test subject\n", encoding="utf-8")
    return repo, kb


# ── #1193 — never answer from a home you just made ──────────────────────


def test_cmd_kb_project_fallback_names_the_reason_and_scaffolds_nothing(
    tmp_path, capsys, monkeypatch
):
    """No account link resolvable ⇒ the read names the fallback and why.

    The bug: `cmd_kb` resolved through `resolve_context()`'s default
    `create=True`, which silently mints a fresh, empty project home
    (mkdir + git init) whenever no account link is found, then reported on
    it exactly as it would a real, populated home — nothing distinguished
    "genuinely empty" from "just invented for this call". Real
    `ensure_checkout`/`resolve_context` run here (not mocked): the
    assertion that matters is that neither the reason nor the absence of a
    scaffolded home is a mock artifact.
    """
    from brr import account as account_mod

    repo, kb = _kb_repo(tmp_path)
    monkeypatch.setattr("brr.cli._repo_root", lambda: repo)
    monkeypatch.setattr("brr.knowledge.active_kb_dir", lambda root, cfg=None: kb)

    # Where a project-fallback home would land for this repo, computed the
    # same read-only way the fix does it — never created ahead of the call.
    project_home = account_mod.resolve_context(repo, {}, create=False).dominion_repo
    assert not project_home.exists(), "fixture must start with no home minted"

    rc = main(["kb"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "project fallback" in out
    assert _says(out, "no account link found")
    assert str(repo) in out
    # The whole point: reading must not have originated the thing it reports
    # having not found linked.
    assert not project_home.exists(), "a read must not scaffold a home to report on"


# ── `brnrd prompts wake` — a run's context, both halves ──────────────────


# ── brnrd prompts replay: w-56 rung 1 ─────────────────────────────────
#
# CLI-level coverage; `tests/test_replay.py` covers `brr.replay` itself
# (locate mechanism, splice, curated-extract handling) against real
# `build_daemon_prompt_with_score` fixtures. These tests only need to check
# the CLI wires args -> `replay_mod` correctly and reports exit codes.


# ── brnrd gate-run: the shipped `hooks.gate_command` writer ──────────
#
# hooks.gate_command arms a Stop-hook obligation that reads
# .gate-receipts.json — a map keyed per tree (#820) — but only this repo's
# own unshipped scripts/gate.py ever wrote one — any other adopter got a
# permanent "the gate never ran" (kb/design-io-layer-trim.md, THE OBLIGATION
# NOTHING CAN SATISFY). These drive the real `brnrd gate-run` entry point
# end to end.


# ── The spelling the launcher earned (npx) ──────────────────────────
#
# `npx brnrd` execs a binary inside a managed venv under
# ~/.local/share/brnrd; it never puts `brnrd` on the user's PATH. The
# launcher says so with BRNRD_LAUNCHER=npx, and `cli.brnrd_cmd()` is the
# one place that reads it. Pinned on the *account connect* path (the
# second thing a new install does) and on the parser itself.
#
# Each case carries its absent twin: with the variable unset — pip, uv,
# pipx, `npm install -g` — the bare spelling must survive untouched.


class TestNpxSpelling:
    def test_brnrd_cmd_reads_the_environment_at_call_time(self, monkeypatch):
        """Not captured at import: the launcher may set it after we load."""
        from brr import cli

        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        assert cli.brnrd_cmd() == "brnrd"
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        assert cli.brnrd_cmd() == "npx brnrd"
        # An unrecognised launcher is not a licence to invent a spelling.
        monkeypatch.setenv("BRNRD_LAUNCHER", "somethingelse")
        assert cli.brnrd_cmd() == "brnrd"

    def test_usage_line_names_the_runnable_command(self, monkeypatch, capsys):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        with pytest.raises(SystemExit):
            main(["--help"])
        assert "usage: npx brnrd" in capsys.readouterr().out

    def test_usage_line_stays_bare_for_a_path_install(self, monkeypatch, capsys):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        with pytest.raises(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        assert "usage: brnrd" in out
        assert "npx" not in out

    def _timeout_connect(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        from brr.gates import cloud

        replies = iter([
            {"pair_code": "BR-TEST", "pair_url": "https://pair",
             "poll_secret": "secret"},
            {"status": "pending"},
        ])
        ticks = iter([0.0, 601.0])
        monkeypatch.setattr(cloud, "_request", lambda *_a, **_kw: next(replies))
        monkeypatch.setattr(cloud.time, "monotonic", lambda: next(ticks))

        with pytest.raises(SystemExit) as excinfo:
            main(["account", "connect", "https://brnrd.example"])
        return str(excinfo.value)

    def test_pairing_timeout_recovery_is_npx_spelled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        assert _says(self._timeout_connect(
            tmp_path, monkeypatch,
        ), "re-run `npx brnrd account connect`")

    def test_pairing_timeout_recovery_stays_bare_for_a_path_install(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        message = self._timeout_connect(tmp_path, monkeypatch)
        assert _says(message, "re-run `brnrd account connect`")
        assert "npx" not in message

    def _connect_no_service(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)
        monkeypatch.setattr("brr.gates.cloud.connect", lambda *_a, **_kw: {})
        monkeypatch.setattr("brr.daemon_install.install", lambda **_kw: None)
        main(["account", "connect", "--no-service"])

    def test_foreground_escape_is_npx_spelled(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        self._connect_no_service(tmp_path, monkeypatch)
        assert _says(capsys.readouterr().out, "Run `npx brnrd up --foreground`")

    def test_foreground_escape_stays_bare_for_a_path_install(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        self._connect_no_service(tmp_path, monkeypatch)
        out = capsys.readouterr().out
        assert _says(out, "Run `brnrd up --foreground`")
        assert "npx" not in out

    def test_cloud_gate_setup_pointer_is_npx_spelled(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        main(["gate", "setup", "cloud"])

        assert _says(capsys.readouterr().out, "Run `npx brnrd account connect`")

    def test_cloud_gate_setup_pointer_stays_bare_for_a_path_install(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        main(["gate", "setup", "cloud"])

        out = capsys.readouterr().out
        assert _says(out, "Run `brnrd account connect`")
        assert "npx" not in out

    def test_account_add_without_a_connected_home_is_npx_spelled(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        with pytest.raises(SystemExit) as excinfo:
            main(["account", "add", str(repo)])

        assert _says(str(excinfo.value), "run `npx brnrd account connect` first")

    def test_account_add_without_a_connected_home_stays_bare(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        with pytest.raises(SystemExit) as excinfo:
            main(["account", "add", str(repo)])

        message = str(excinfo.value)
        assert _says(message, "run `brnrd account connect` first")
        assert "npx" not in message

    def test_account_status_project_home_pointer_is_npx_spelled(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        assert main(["account", "status"]) == 0

        assert _says(capsys.readouterr().out, "`npx brnrd account connect` links it")

    def test_account_status_project_home_pointer_stays_bare(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        assert main(["account", "status"]) == 0

        out = capsys.readouterr().out
        assert _says(out, "`brnrd account connect` links it")
        assert "npx" not in out

    def _up_before_init(self, tmp_path, monkeypatch, capsys):
        """``brnrd up`` in a repo that was never adopted.

        Driven at ``daemon.start`` rather than through ``main(["up"])``:
        the CLI verb prefers an *installed service* when one exists, so
        going through it would have the suite poke the developer's own
        systemd user manager instead of the code path under test.

        #1244 fork 1: this used to hard-exit right here (`SystemExit`,
        "run `brnrd init` first") before `daemon.start` ever wrote a
        pidfile — the crash loop #1238 could only work around downstream
        of. The daemon now boots anyway: it prints the same fact instead
        of dying on it, then falls into the normal main loop. This drives
        that loop one scan past boot and escapes it the same way every
        other loop-wiring test in this suite does — `list_pending` raises
        `StopIteration` on first call, since there is nothing pending to
        dispatch and the assertion is about boot, not dispatch.
        """
        from brr import daemon as daemon_mod

        repo = tmp_path / "repo"
        init_git_repo(repo)
        monkeypatch.chdir(repo)

        monkeypatch.setattr(daemon_mod, "read_pid", lambda _b: None)
        monkeypatch.setattr(daemon_mod, "_write_pid", lambda _b: None)
        monkeypatch.setattr(daemon_mod, "_clear_pid", lambda _b: None)
        monkeypatch.setattr(daemon_mod, "_start_gates", lambda *_a: [])
        monkeypatch.setattr(daemon_mod.signal, "signal", lambda *_a: None)
        monkeypatch.setattr(daemon_mod, "publish", lambda *_a, **_k: None)
        monkeypatch.setattr(daemon_mod.conf, "load_config", lambda _r: {})

        def _stop_the_loop(*_a, **_k):
            raise StopIteration

        monkeypatch.setattr(daemon_mod.protocol, "list_pending", _stop_the_loop)

        with pytest.raises(StopIteration):
            daemon_mod.start(repo)
        return capsys.readouterr().out

    def test_up_before_init_is_npx_spelled(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("BRNRD_LAUNCHER", "npx")
        out = self._up_before_init(tmp_path, monkeypatch, capsys)
        assert "no AGENTS.md yet" in out
        assert "npx brnrd init" in out

    def test_up_before_init_stays_bare_for_a_path_install(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("BRNRD_LAUNCHER", raising=False)
        out = self._up_before_init(tmp_path, monkeypatch, capsys)
        assert "no AGENTS.md yet" in out
        assert "brnrd init" in out
        assert "npx" not in out


