from types import SimpleNamespace

from brr.loom.runtime import github_health, health
from brr.loom.runtime.cpu import CpuHigh
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.loom import _render_boundary

from _step import write_thread


def test_boundary_shows_only_open_host_streaks(tmp_path, monkeypatch):
    write_thread(tmp_path, "inbox", "Brnrd", "inbox")
    home = Home(tmp_path, install="aaaa")
    context = SimpleNamespace(thread="inbox", fuel="", why="", shell="fake")
    now = [1_800_000_000.0]
    monkeypatch.setattr(health, "time", SimpleNamespace(time=lambda: now[0]))
    guard = health.Health(home, [], lambda *_: None)

    def line():
        rendered = _render_boundary(home, read_facts(home), "s-aaaa-test", 1, context)
        return [row for row in rendered.splitlines() if row.startswith(("body:", "degraded:"))]

    def overload():
        raise CpuHigh(.45, 600, .35)

    assert line() == []
    guard.guard("cpu", overload, backoff=False)
    now[0] += 28 * 60
    assert line() == ["body: cpu 45% of a core for 28 min"]
    now[0] += 92 * 60
    assert line() == ["body: cpu 45% of a core for 2h"]
    now[0] += 1
    assert line() == ["body: cpu 45% of a core for 2h (long)"]
    guard.guard("cpu", lambda: None, backoff=False)
    assert line() == []


def test_credential_probe_reaches_boundary_and_clears(tmp_path, monkeypatch):
    write_thread(tmp_path, "inbox", "Brnrd", "inbox")
    home = Home(tmp_path, install="aaaa")
    context = SimpleNamespace(thread="inbox", fuel="", why="", shell="fake")
    now, calls = [0], []
    monkeypatch.setattr(github_health, "time", SimpleNamespace(
        monotonic=lambda: now[0], time=lambda: 1_800_000_000 + now[0]))
    check = github_health.CredentialCheck(home, lambda *_: None)
    monkeypatch.setenv("BODY_LINE_TEST_TOKEN", "example-token")

    def gh(command, *, env, **_):
        assert command == ["gh", "api", "--hostname", "github.com", "--silent", "rate_limit"]
        assert env["GH_TOKEN"] == "example-token"
        calls.append(now[0])
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stderr=b"gh: Bad credentials (HTTP 401)")
        if len(calls) == 2:
            return SimpleNamespace(returncode=1, stderr=b"gh: connection failed")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(github_health, "subprocess", SimpleNamespace(
        run=gh, DEVNULL=-3, PIPE=-1, TimeoutExpired=TimeoutError))

    def render():
        return _render_boundary(home, read_facts(home), "s-aaaa-test", 1, context)

    spec = "env: BODY_LINE_TEST_TOKEN"
    for minute in (0, 10, 20):
        now[0] = minute * 60
        check.step(spec)
        check.worker.join()
        check.step(spec)
        if minute < 20:
            assert "body: github credential refused since " in render()
            now[0] += 599
            restarted = github_health.CredentialCheck(home, lambda *_: None)
            restarted.step(spec)
            assert restarted.worker is None
            check.step(spec)
    assert "body:" not in render()
    assert calls == [0, 600, 1200]
    assert not any(f.kind == "letter" for f in read_facts(home))
