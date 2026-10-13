"""``publish`` is the one way a project checkout's commits leave. Local bare repos only."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import _env
from brr.loom.runtime.publish import PublishError, publish, push_command
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import git

from test_project_repos import (
    _credential_home, _home, _kill, _note, _room, _seat, _source, _started, _tick,
)


def _open(tmp_path: Path, *, config: str = "") -> tuple[Path, Path, Path, Path]:
    """A started body with a ``widget`` checkout: ``(root, room, checkout, bare)``."""
    root = _home(tmp_path)
    if config:
        path = root / "loom" / "config.toml"
        path.write_text(path.read_text() + config)
    bare, _work = _source(tmp_path)
    _seat(root, "work", [str(bare)])
    home = Home(root)
    _tick(home, {}, Router(home))
    assert _started(root)
    room = _room(root)
    return root, room, room / "widget", bare


def _tip(repo: Path, ref: str) -> str | None:
    proc = git(repo, "rev-parse", "--verify", "--quiet", ref, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _published(root: Path) -> list:
    return [fact for fact in read_facts(Home(root)) if fact.kind == "branch.published"]


def _verb(room: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """The verb as a body runs it: the body's environment, no ``--room``."""
    return subprocess.run(
        [sys.executable, "-m", "brr.loom.runtime", "publish", *args],
        env=_env(room), cwd=room / "self", capture_output=True, text=True, timeout=120,
    )


def test_the_verb_pushes_the_thread_branch_and_records_it(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        commit = _tip(checkout, "HEAD")
        proc = _verb(room, "widget")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == f"loom/work {commit}\n"
        assert _tip(bare, "refs/heads/loom/work") == commit
        assert _tip(bare, "refs/heads/main") != commit
        assert _tip(checkout, "refs/remotes/origin/loom/work") == commit
        facts = _published(root)
        assert len(facts) == 1
        data = facts[0].data
        assert (data["thread"], data["repo"], data["branch"], data["commit"]) == (
            "work", "widget", "loom/work", commit)
        assert facts[0].by == f"strand:{room.name}"
        # The same tip again is the same fact.
        assert _verb(room, "widget").returncode == 0
        assert len(_published(root)) == 1
    finally:
        _kill(read_facts(Home(root)))


def test_a_tainted_strand_is_refused_and_told_what_to_send(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        strand = room.name
        append(Home(root), Fact(
            kind="label.tainted", by=f"strand:{strand}", id="taint-1",
            data={"strand": strand, "tool": "WebFetch"},
        ))
        proc = _verb(room, "widget")
        assert proc.returncode == 1
        assert "read untrusted text" in proc.stderr
        assert "--clean --kind done --ref widget" in proc.stderr
        assert _tip(bare, "refs/heads/loom/work") is None
        assert _published(root) == []
    finally:
        _kill(read_facts(Home(root)))


def test_a_pending_web_receipt_refuses_before_the_loom_has_read_it(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        out = room / "port" / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "taint-abc.json").write_text('{"tool": "WebFetch"}')
        with pytest.raises(PublishError, match="untrusted"):
            publish(room, "widget")
        assert _tip(bare, "refs/heads/loom/work") is None
    finally:
        _kill(read_facts(Home(root)))


def test_another_author_on_the_branch_is_refused_by_commit(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "mine.txt", "mine\n")
        env = _env(room)
        env["GIT_AUTHOR_EMAIL"] = "mallory@example.com"
        (checkout / "theirs.txt").write_text("theirs\n")
        git(checkout, "add", "theirs.txt", env=env)
        git(checkout, "commit", "-m", "theirs", env=env)
        short = git(checkout, "rev-parse", "--short", "HEAD").stdout.strip()
        with pytest.raises(PublishError) as caught:
            publish(room, "widget")
        text = str(caught.value)
        assert f"{short} mallory@example.com" in text
        assert "ada@example.com" in text
        assert text.count("@example.com") == 2, text
        assert _tip(bare, "refs/heads/loom/work") is None
        assert _published(root) == []
    finally:
        _kill(read_facts(Home(root)))


def test_the_push_goes_to_the_seat_source_not_the_checkout_remote(tmp_path: Path) -> None:
    """A body that rewrites its checkout's origin does not redirect the push."""
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        commit = _tip(checkout, "HEAD")
        elsewhere = tmp_path / "elsewhere.git"
        git(None, "init", "--bare", str(elsewhere))
        git(checkout, "remote", "set-url", "origin", str(elsewhere))
        git(checkout, "remote", "set-url", "--push", "origin", str(elsewhere))
        publish(room, "widget")
        assert _tip(bare, "refs/heads/loom/work") == commit
        assert _tip(elsewhere, "refs/heads/loom/work") is None
    finally:
        _kill(read_facts(Home(root)))


def test_a_repo_the_seat_does_not_declare_is_refused(tmp_path: Path) -> None:
    root, room, _checkout, _bare = _open(tmp_path)
    try:
        git(None, "init", "-b", "loom/work", str(room / "sneak"))
        _note(room / "sneak", room, "a.txt", "a\n")
        with pytest.raises(PublishError, match="declares no repo named sneak"):
            publish(room, "sneak")
        with pytest.raises(PublishError, match="not a project repo"):
            publish(room, "self")
    finally:
        _kill(read_facts(Home(root)))


def test_a_refused_push_keeps_gits_words_and_records_nothing(tmp_path: Path) -> None:
    root, room, checkout, bare = _open(tmp_path)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        hook = bare / "hooks" / "pre-receive"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\necho 'branch protection says no' >&2\nexit 1\n")
        hook.chmod(0o755)
        with pytest.raises(PublishError, match="branch protection says no"):
            publish(room, "widget")
        assert _published(root) == []
        assert _tip(checkout, "refs/remotes/origin/loom/work") is None
    finally:
        _kill(read_facts(Home(root)))


def test_the_token_answers_only_its_host_and_never_enters_argv(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("ghs_secret\n")
    args, env, lane, _note_ = push_command(
        "https://example.com/acme/widget.git", "loom/work", str(token))
    assert lane == "token"
    assert "ghs_secret" not in " ".join(args)
    assert all("ghs_secret" not in value for value in env.values())
    config = args[:args.index("push")]
    # A machine gitconfig with its own helper and an ssh rewrite: neither is read.
    machine = _credential_home(tmp_path)
    with (machine / ".gitconfig").open("a") as handle:
        handle.write('[url "git@example.com:"]\n\tinsteadOf = https://example.com/\n')
    env["HOME"] = str(machine)

    def fill(host: str) -> str:
        ask = f"protocol=https\nhost={host}\npath=acme/widget.git\n\n"
        return subprocess.run(
            ["git", *config, "credential", "fill"], cwd=tmp_path, env=env, input=ask,
            capture_output=True, text=True, timeout=30,
        ).stdout

    assert "password=ghs_secret" in fill("example.com")
    assert "username=x-access-token" in fill("example.com")
    assert "password=" not in fill("evil.example.net")
    rewritten = subprocess.run(
        ["git", *config, "ls-remote", "--get-url", "https://example.com/acme/widget.git"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    assert rewritten == "https://example.com/acme/widget.git"


@pytest.mark.parametrize("source", [
    "git@github.com:acme/widget.git", "ssh://git@github.com/acme/widget.git",
    "http://example.com/acme/widget.git",
])
def test_a_token_is_not_traded_for_the_machines_keys(tmp_path: Path, source: str) -> None:
    token = tmp_path / "token"
    token.write_text("ghs_secret\n")
    with pytest.raises(PublishError, match="only sent over https"):
        push_command(source, "loom/work", str(token))


def test_no_token_pushes_as_the_machine_and_says_so(tmp_path: Path) -> None:
    args, _env_, lane, note = push_command(
        "https://example.com/acme/widget.git", "loom/work", None)
    assert lane == "machine"
    assert "no forge_token_file" in note and "this machine's own git setup" in note
    assert args[-3:] == ["--", "https://example.com/acme/widget.git",
                         "refs/heads/loom/work:refs/heads/loom/work"]
