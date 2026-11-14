import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    _g(s, "config", "user.name", "t")
    _g(s, "config", "user.email", "t@t")
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


@pytest.mark.parametrize("mode", ["landed", "new", "conflict", "behind"])
def test_tick_reconciles_remote_main(tmp_path, mode, monkeypatch):
    home, s, bare = _setup(tmp_path)
    other = tmp_path / "other"
    _g(tmp_path, "clone", "-q", str(bare), str(other))
    path = "a" if mode == "conflict" else "b"
    (other / path).write_text("remote")
    _g(other, "add", "-A")
    _g(other, "commit", "-qm", "theirs")
    remote = _g(other, "rev-parse", "HEAD")
    _g(other, "push", "-q", "origin", "main")
    if mode == "landed":
        # Same snapshot, different history, followed by a local edit. The
        # snapshot proof must preserve that later edit rather than conflict.
        (s / "b").write_text("remote")
        _g(s, "add", "-A")
        _g(s, "commit", "-qm", "rebased landing")
        (s / "b").write_text("local followup")
    if mode != "behind":
        (s / "a").write_text("local")
        _g(s, "commit", "-qam", "local")
    head = _g(s, "rev-parse", "HEAD")
    tree = _g(s, "rev-parse", "HEAD^{tree}")
    logs = []
    m = mirror.Mirror(home, lambda *args: logs.append(args))
    cfg = SimpleNamespace(forge_token="tok")
    if mode == "behind":
        m.fetched -= mirror.FETCH_EVERY
    m.step(cfg)
    assert m.worker is not None
    m.worker.join(timeout=10)
    assert not m.worker.is_alive()
    if mode == "conflict":
        with pytest.raises(mirror.PublishError, match="cannot merge origin/main: a"):
            m.step(cfg)  # The loop guard receives the named failure as its notice.
        assert _g(s, "rev-parse", "HEAD") == head
        assert _g(bare, "rev-parse", "main") == remote
        assert not (s / ".git" / "MERGE_HEAD").exists()
        assert _g(s, "status", "--porcelain") == ""
        from brr.loom.runtime import health, ledger, loom
        home.install_id = lambda: "test"
        home.main_thread = lambda: "inbox"
        notices = []
        monkeypatch.setattr(health, "NOTICE_AFTER", 0)
        monkeypatch.setattr(health, "append", lambda *a: None)
        monkeypatch.setattr(ledger, "read_facts", lambda *a: [])
        monkeypatch.setattr(loom, "notice", lambda *a, **kw: notices.append(a))
        guard = health.Health(home, [], lambda *a: None)
        guard.guard("mirror", m.step, cfg, backoff=False)
        assert len(notices) == 1
        assert notices[0][2] == "degraded"
        assert "cannot merge origin/main: a" in notices[0][4]
        return
    assert m.failing is None
    _g(s, "merge-base", "--is-ancestor", remote, "main")
    assert _g(bare, "rev-parse", "main") == _g(s, "rev-parse", "main")
    assert _g(s, "rev-parse", "main^1") == head
    assert _g(s, "rev-parse", "main^2") == remote
    if mode == "landed":
        assert _g(s, "rev-parse", "HEAD^{tree}") == tree
    else:
        assert (s / "b").read_text() == "remote"


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


def test_mirror_retries_when_the_forge_does_not_answer(tmp_path, monkeypatch):
    home, s, _ = _setup(tmp_path)
    (s / "a").write_text("2")
    _g(s, "commit", "-qam", "two")
    _g(s, "remote", "set-url", "origin", "https://github.com/o/r.git")
    answers = iter([None, True])
    monkeypatch.setattr(mirror, "is_private", lambda url, tok: next(answers))
    monkeypatch.setattr(mirror, "reconcile", lambda *a: "sha")
    monkeypatch.setattr(mirror, "push", lambda *a: None)
    logs = []
    m = mirror.Mirror(home, lambda h, msg: logs.append(msg))
    cfg = SimpleNamespace(forge_token="command: printf tok")
    m.step(cfg)
    assert m.worker is None
    assert logs == ["mirror: could not ask the forge whether origin is private; will retry"]
    m.step(cfg)
    assert m.worker is not None
    m.worker.join()
    assert len(logs) == 1


def test_is_private_reads_a_rate_limited_403_as_no_answer(monkeypatch):
    import io
    import urllib.error
    from email.message import Message

    def answer(code, **headers):
        msg = Message()
        for k, v in headers.items():
            msg[k.replace("_", "-")] = v

        def fake(req, timeout):
            raise urllib.error.HTTPError(req.full_url, code, "x", msg, io.BytesIO(b""))
        return fake

    monkeypatch.setattr(mirror, "resolve_secret", lambda spec: "tok")
    url = "https://github.com/o/r.git"
    for code, headers, want in [(403, {"x_ratelimit_remaining": "0"}, None),
                                (403, {"retry_after": "30"}, None),
                                (403, {}, False), (404, {}, False), (500, {}, None)]:
        monkeypatch.setattr(mirror.urllib.request, "urlopen", answer(code, **headers))
        assert mirror.is_private(url, "x") is want, (code, headers)


def test_idle_ticks_do_not_fetch_until_slow_pace(tmp_path, monkeypatch):
    home, s, bare = _setup(tmp_path)
    now = [1000.0]
    monkeypatch.setattr(mirror.time, "monotonic", lambda: now[0])
    calls = []
    original = mirror.reconcile

    def reconcile(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(mirror, "reconcile", reconcile)
    m = mirror.Mirror(home, lambda *a: None)
    cfg = SimpleNamespace(forge_token="tok")
    for elapsed in range(0, mirror.FETCH_EVERY, mirror.EVERY):
        now[0] = 1000.0 + elapsed
        m.step(cfg)
        assert m.worker is None
    assert calls == []
    now[0] = 1000.0 + mirror.FETCH_EVERY
    m.step(cfg)
    m.worker.join(timeout=10)
    assert not m.worker.is_alive()
    assert m.failing is None
    assert len(calls) == 1
    now[0] += mirror.EVERY
    m.step(cfg)
    assert len(calls) == 1
