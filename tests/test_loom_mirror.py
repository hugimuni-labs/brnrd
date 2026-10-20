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
