import subprocess
from pathlib import Path
from types import SimpleNamespace

from brr.loom.runtime import mirror


def _g(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


def _setup(tmp_path):
    bare = tmp_path / "remote.git"
    _g(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    home = tmp_path / "home"
    (home / "self").mkdir(parents=True)
    s = home / "self"
    _g(s, "init", "-q", "-b", "main")
    (s / "a").write_text("1")
    _g(s, "add", "-A"); _g(s, "commit", "-qm", "one")
    _g(s, "remote", "add", "origin", str(bare))
    _g(s, "push", "-q", "-u", "origin", "main")
    return SimpleNamespace(root=home), s, bare


def test_mirror_pushes_when_main_is_ahead(tmp_path):
    home, s, bare = _setup(tmp_path)
    assert mirror.pending(s) is None
    (s / "a").write_text("2")
    _g(s, "commit", "-qam", "two")
    url, sha = mirror.pending(s)
    mirror.push(s, url, sha, "tok")
    assert _g(bare, "rev-parse", "main") == sha
    assert mirror.pending(s) is None


def test_mirror_leaves_a_diverged_remote_alone(tmp_path):
    home, s, bare = _setup(tmp_path)
    other = tmp_path / "other"
    _g(tmp_path, "clone", "-q", str(bare), str(other))
    (other / "b").write_text("x")
    _g(other, "add", "-A"); _g(other, "commit", "-qm", "theirs"); _g(other, "push", "-q", "origin", "main")
    (s / "a").write_text("2")
    _g(s, "commit", "-qam", "two")
    _g(s, "fetch", "-q", "origin")
    assert mirror.pending(s) is None


def test_mirror_without_token_does_nothing(tmp_path):
    home, s, _ = _setup(tmp_path)
    (s / "a").write_text("2")
    _g(s, "commit", "-qam", "two")
    m = mirror.Mirror(home, lambda *_: None)
    m.step(SimpleNamespace(forge_token=None))
    assert m.worker is None


def test_token_lane_asks_the_token_not_the_repos_helper(tmp_path):
    home, s, _ = _setup(tmp_path)
    marker = tmp_path / "marker"
    helper = tmp_path / "helper.sh"
    helper.write_text(f"#!/bin/sh\ntouch {marker}\necho username=his\necho password=his\n")
    helper.chmod(0o755)
    _g(s, "config", "credential.helper", str(helper))
    args, env, lane, _ = mirror.push_command("https://github.com/o/r.git", "main",
                                             "command: printf tok")
    assert lane == "token"
    i = args.index("push")
    out = subprocess.run(["git", *args[:i], "credential", "fill"], cwd=s, env=env, text=True,
                         input="protocol=https\nhost=github.com\n\n", capture_output=True)
    assert "password=tok" in out.stdout
    assert not marker.exists()


def test_mirror_refuses_a_remote_not_known_private(tmp_path, monkeypatch):
    home, s, _ = _setup(tmp_path)
    (s / "a").write_text("2")
    _g(s, "commit", "-qam", "two")
    _g(s, "remote", "set-url", "origin", "https://github.com/o/r.git")
    monkeypatch.setattr(mirror, "is_private", lambda url, tok: False)
    logs = []
    m = mirror.Mirror(home, lambda h, msg: logs.append(msg))
    cfg = SimpleNamespace(forge_token="command: printf tok")
    m.step(cfg)
    m.checked = None
    m.step(cfg)
    assert m.worker is None
    assert logs == ["mirror: origin is not private; not mirroring"]


def test_diverged_remote_is_named(tmp_path):
    home, s, bare = _setup(tmp_path)
    other = tmp_path / "other"
    _g(tmp_path, "clone", "-q", str(bare), str(other))
    (other / "b").write_text("x")
    _g(other, "add", "-A"); _g(other, "commit", "-qm", "theirs"); _g(other, "push", "-q", "origin", "main")
    (s / "a").write_text("2")
    _g(s, "commit", "-qam", "two")
    url, sha = mirror.pending(s)
    try:
        mirror.push(s, url, sha, "tok")
    except mirror.PublishError as exc:
        assert "remote diverged from self main; mirror paused" in str(exc)
    else:
        raise AssertionError("push should have been rejected")
