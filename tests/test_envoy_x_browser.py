"""Tests for the X browser envoy's mechanics (``envoy_x_browser.py``) —
the guardrail logic, exercised entirely through the ``driver_factory``
seam so none of it needs Playwright installed. Playwright is not on this
machine (nor a runtime dependency of the project — see the ``browser``
extra in ``pyproject.toml``); ``_PlaywrightDriver`` itself is therefore
never imported or instantiated here. Per ``AGENTS.md``'s testing
discipline, every guard below is neutered once (a driver that raises if
it is ever constructed) to confirm it actually blocks the browser, not
just that it prints a message.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from brr import account, envoy_x_browser

from _helpers import init_git_repo


# ── fixtures ─────────────────────────────────────────────────────────


def _paths(tmp_path: Path) -> envoy_x_browser.Paths:
    d = tmp_path / "some-account-home" / "account"
    d.mkdir(parents=True)
    return envoy_x_browser.Paths.in_dir(d)


def _refusing_factory(paths, *, headless):  # noqa: ARG001
    raise AssertionError("driver_factory must not be called")


class _FakeDriver:
    """Records every call; ``__enter__``/``__exit__`` never touch a
    browser. Constructed via a small factory closure so tests can assert
    on ``headless`` and on the call log after the run.
    """

    def __init__(self, *, whoami_value=None, read_value=None, search_value=None):
        self.calls: list = []
        self.headless: bool | None = None
        self._whoami_value = whoami_value
        self._read_value = read_value if read_value is not None else {}
        self._search_value = search_value if search_value is not None else []

    def __enter__(self):
        self.calls.append("__enter__")
        return self

    def __exit__(self, *exc):
        self.calls.append("__exit__")
        return False

    def whoami(self):
        self.calls.append("whoami")
        return self._whoami_value

    def wait_for_manual_login(self):
        self.calls.append("wait_for_manual_login")

    def read_url(self, url):
        self.calls.append(("read_url", url))
        return {**self._read_value, "url": url}

    def search(self, query):
        self.calls.append(("search", query))
        return self._search_value

    def open_reply_composer(self, url):
        self.calls.append(("open_reply_composer", url))

    def fill_text(self, text):
        self.calls.append(("fill_text", text))

    def screenshot(self, path):
        self.calls.append(("screenshot", path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-png")

    def click_send(self):
        self.calls.append("click_send")


def _factory_for(driver: _FakeDriver):
    def factory(paths, *, headless):  # noqa: ARG001
        driver.headless = headless
        return driver

    return factory


def _armed_env(monkeypatch):
    monkeypatch.setenv("BRR_X_BROWSER_SEND", "1")


# ── argv guards: -h/--help before any driver ─────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["-h"],
        ["--help"],
        ["login", "-h"],
        ["check", "--help"],
        ["read", "-h"],
        ["search", "--help"],
        ["draft", "-h"],
        ["send", "--help"],
    ],
)
def test_help_never_creates_a_driver(tmp_path, argv):
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(argv, paths, driver_factory=_refusing_factory)
    assert "Usage:" in str(exc.value)
    # Must resolve through the -h/--help short-circuit itself, not just
    # happen to raise a Usage-shaped message via some other path (e.g. the
    # unknown-verb fallback, which also starts with TOP_USAGE).
    assert "unknown verb" not in str(exc.value)


def test_empty_argv_prints_usage_without_a_driver(tmp_path):
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run([], paths, driver_factory=_refusing_factory)
    assert "Usage:" in str(exc.value)


def test_unknown_verb_refuses_without_a_driver(tmp_path):
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(["fly"], paths, driver_factory=_refusing_factory)
    assert "unknown verb" in str(exc.value)


# ── argv guards: leading-dash text refused, escape hatch works ───────


def test_send_refuses_dash_led_url(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setenv("BRR_X_BROWSER_SEND", "1")
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["send", "--confirm", "-x/status/1", "--text", "hi"],
            paths, driver_factory=_refusing_factory,
        )
    assert "looks like a flag" in str(exc.value)


def test_draft_refuses_dash_led_text(tmp_path):
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["draft", "https://x.com/a/status/1", "--text", "-rf /"],
            paths, driver_factory=_refusing_factory,
        )
    assert "looks like a flag" in str(exc.value)


def test_draft_leading_space_escapes_the_dash_guard(tmp_path):
    paths = _paths(tmp_path)
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["draft", "https://x.com/a/status/1", "--text", " -not a flag"],
        paths, driver_factory=_factory_for(driver),
    )
    assert ("fill_text", "-not a flag") in driver.calls


def test_read_refuses_dash_led_url(tmp_path):
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(["read", "--all"], paths, driver_factory=_refusing_factory)
    assert "looks like a flag" in str(exc.value)


# ── the disarmed-send refusal: both arms missing, each arm alone ─────


def test_send_refuses_with_neither_arm(tmp_path):
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["send", "https://x.com/a/status/1", "--text", "hi"],
            paths, driver_factory=_refusing_factory,
        )
    msg = str(exc.value)
    assert "--confirm" in msg and "BRR_X_BROWSER_SEND=1" in msg


def test_send_refuses_with_only_confirm_flag(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.delenv("BRR_X_BROWSER_SEND", raising=False)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["send", "https://x.com/a/status/1", "--text", "hi", "--confirm"],
            paths, driver_factory=_refusing_factory,
        )
    assert "BRR_X_BROWSER_SEND=1" in str(exc.value)


def test_send_refuses_with_only_env_armed(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["send", "https://x.com/a/status/1", "--text", "hi"],
            paths, driver_factory=_refusing_factory,
        )
    assert "--confirm" in str(exc.value)


def test_send_proceeds_when_both_arms_present_and_under_cap(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["send", "https://x.com/a/status/1", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    assert driver.headless is False
    assert ("open_reply_composer", "https://x.com/a/status/1") in driver.calls
    assert ("fill_text", "hi") in driver.calls
    assert "click_send" in driver.calls


# ── the hourly cap: arithmetic + enforcement even when armed ─────────


def test_cap_status_defaults_when_config_absent(tmp_path):
    paths = _paths(tmp_path)
    status = envoy_x_browser.cap_status(paths)
    assert status == {
        "cap": envoy_x_browser.DEFAULT_HOURLY_CAP,
        "used": 0,
        "remaining": envoy_x_browser.DEFAULT_HOURLY_CAP,
    }


def test_cap_status_reads_config_override(tmp_path):
    paths = _paths(tmp_path)
    paths.config.write_text(json.dumps({"hourly_cap": 5}), encoding="utf-8")
    assert envoy_x_browser.cap_status(paths)["cap"] == 5


@pytest.mark.parametrize("bad", ["not json", "[]", json.dumps({"hourly_cap": "nope"})])
def test_cap_status_falls_back_on_malformed_config(tmp_path, bad):
    paths = _paths(tmp_path)
    paths.config.write_text(bad, encoding="utf-8")
    assert envoy_x_browser.cap_status(paths)["cap"] == envoy_x_browser.DEFAULT_HOURLY_CAP


def test_cap_status_counts_only_the_trailing_hour(tmp_path):
    paths = _paths(tmp_path)
    now = 10_000_000.0
    paths.state.write_text(
        json.dumps({"sends": [now - 4000, now - 3599, now - 100]}), encoding="utf-8"
    )
    status = envoy_x_browser.cap_status(paths, now=now)
    assert status["used"] == 2  # the -4000s entry aged out past 3600s


def test_send_refuses_past_cap_even_when_armed(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    now = time.time()
    paths.config.write_text(json.dumps({"hourly_cap": 1}), encoding="utf-8")
    paths.state.write_text(json.dumps({"sends": [now]}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["send", "https://x.com/a/status/1", "--text", "hi", "--confirm"],
            paths, driver_factory=_refusing_factory,
        )
    assert "cap" in str(exc.value)


def test_record_send_appends_and_prunes(tmp_path):
    paths = _paths(tmp_path)
    now = 10_000_000.0
    paths.state.write_text(json.dumps({"sends": [now - 5000]}), encoding="utf-8")
    envoy_x_browser._record_send(paths, now=now)
    data = json.loads(paths.state.read_text(encoding="utf-8"))
    assert data["sends"] == [now]  # the aged-out entry was dropped


# ── the kill switch: refuses every verb but check ─────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["login"],
        ["read", "https://x.com/a/status/1"],
        ["search", "q"],
        ["draft", "https://x.com/a/status/1", "--text", "hi"],
        ["send", "https://x.com/a/status/1", "--text", "hi", "--confirm"],
    ],
)
def test_kill_switch_refuses_every_verb_but_check(tmp_path, monkeypatch, argv):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    paths.kill_switch.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(argv, paths, driver_factory=_refusing_factory)
    assert "kill switch" in str(exc.value)


def test_kill_switch_does_not_block_check(tmp_path):
    paths = _paths(tmp_path)
    paths.kill_switch.write_text("", encoding="utf-8")
    driver = _FakeDriver(whoami_value="brnrd_resident")
    envoy_x_browser.run(["check", "--json"], paths, driver_factory=_factory_for(driver))
    assert "whoami" in driver.calls


def test_check_reports_kill_switch_state_json(tmp_path, capsys):
    paths = _paths(tmp_path)
    paths.kill_switch.write_text("", encoding="utf-8")
    driver = _FakeDriver(whoami_value=None)
    envoy_x_browser.run(["check", "--json"], paths, driver_factory=_factory_for(driver))
    out = json.loads(capsys.readouterr().out)
    assert out["kill_switch"] is True
    assert out["logged_in_as"] is None
    assert out["cap"] == envoy_x_browser.DEFAULT_HOURLY_CAP


# ── draft: fills, screenshots, never sends ────────────────────────────


def test_draft_never_calls_click_send(tmp_path, capsys):
    paths = _paths(tmp_path)
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["draft", "https://x.com/a/status/1", "--text", "hello there"],
        paths, driver_factory=_factory_for(driver),
    )
    assert "click_send" not in driver.calls
    assert ("fill_text", "hello there") in driver.calls
    printed = capsys.readouterr().out.strip()
    assert Path(printed).exists()
    assert Path(printed).parent == paths.shots_dir


def test_draft_requires_text(tmp_path):
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["draft", "https://x.com/a/status/1"], paths, driver_factory=_refusing_factory,
        )
    assert "--text" in str(exc.value)


# ── read / search: structured output, headless ────────────────────────


def test_read_prints_structured_json_headless(tmp_path, capsys):
    paths = _paths(tmp_path)
    driver = _FakeDriver(
        whoami_value="brnrd_resident",
        read_value={"author": "@a", "text": "hi", "timestamp": "t", "metrics": {}},
    )
    envoy_x_browser.run(
        ["read", "https://x.com/a/status/1"], paths, driver_factory=_factory_for(driver),
    )
    assert driver.headless is True
    assert "whoami" in driver.calls
    out = json.loads(capsys.readouterr().out)
    assert out["author"] == "@a"
    assert out["url"] == "https://x.com/a/status/1"


def test_search_prints_structured_json_headless(tmp_path, capsys):
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value="brnrd_resident", search_value=[{"author": "@a", "text": "hi"}])
    envoy_x_browser.run(["search", "brnrd"], paths, driver_factory=_factory_for(driver))
    assert driver.headless is True
    assert "whoami" in driver.calls
    out = json.loads(capsys.readouterr().out)
    assert out == [{"author": "@a", "text": "hi"}]


def test_search_returns_a_real_empty_result_when_logged_in(tmp_path, capsys):
    """A genuine "logged in, nothing matched" search must stay expressible
    — the whole point is that this and the logged-out case below are no
    longer byte-identical."""
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value="brnrd_resident", search_value=[])
    envoy_x_browser.run(["search", "brnrd"], paths, driver_factory=_factory_for(driver))
    assert ("search", "brnrd") in driver.calls
    out = json.loads(capsys.readouterr().out)
    assert out == []


# ── read / search: a dead session refuses instead of scraping blind ────


def test_read_refuses_when_logged_out(tmp_path):
    """The defect this pins: a logged-out session must not come back as a
    well-formed, null-filled ``read`` result. Neutering ``_require_session``
    (or dropping its call in ``_run_read``) turns this red — a driver whose
    ``read_url`` would otherwise be reached and happily return the
    null-filled shape."""
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value=None)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["read", "https://x.com/a/status/1"], paths, driver_factory=_factory_for(driver),
        )
    # The refusal must name *both* causes: whoami() returns None for a dead
    # session and for a profile link that missed its timeout, and the remedies
    # differ. An either/or assertion on one phrase would go green over a
    # message that confidently sends the reader to re-log a live session.
    message = str(exc.value)
    assert "login" in message and "retry" in message
    assert "whoami" in driver.calls
    assert not any(isinstance(c, tuple) and c[0] == "read_url" for c in driver.calls)


def test_search_refuses_when_logged_out(tmp_path):
    """Same defect, the ``search`` half: a dead session must not come back
    as an empty-but-successful result list — that shape is byte-identical
    to a real empty search otherwise."""
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value=None)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(["search", "brnrd"], paths, driver_factory=_factory_for(driver))
    # The refusal must name *both* causes: whoami() returns None for a dead
    # session and for a profile link that missed its timeout, and the remedies
    # differ. An either/or assertion on one phrase would go green over a
    # message that confidently sends the reader to re-log a live session.
    message = str(exc.value)
    assert "login" in message and "retry" in message
    assert "whoami" in driver.calls
    assert not any(isinstance(c, tuple) and c[0] == "search" for c in driver.calls)


def test_read_and_search_reuse_the_same_driver_for_the_session_check(tmp_path):
    """``_require_session`` must consult ``whoami()`` on the driver the
    verb already opened, never a second browser — the factory is called
    exactly once either way."""
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value="brnrd_resident", read_value={"text": "hi"})
    calls = {"n": 0}

    def counting_factory(p, *, headless):  # noqa: ARG001
        calls["n"] += 1
        return driver

    envoy_x_browser.run(
        ["read", "https://x.com/a/status/1"], paths, driver_factory=counting_factory,
    )
    assert calls["n"] == 1


# ── login: waits for the human, verifies, reports the handle ──────────


def test_login_reports_the_verified_handle(tmp_path, capsys):
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value="brnrd_resident")
    envoy_x_browser.run(["login"], paths, driver_factory=_factory_for(driver))
    assert driver.headless is False
    assert "wait_for_manual_login" in driver.calls
    assert "@brnrd_resident" in capsys.readouterr().out


def test_login_refuses_when_verification_fails(tmp_path):
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value=None)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(["login"], paths, driver_factory=_factory_for(driver))
    assert "could not verify" in str(exc.value)


# ── the receipt log: shared with the API lane, marked by lane ─────────


def test_send_receipt_shape_and_lane(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["send", "https://x.com/a/status/1", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    lines = paths.log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["lane"] == "browser"
    assert record["url"] == "https://x.com/a/status/1"
    assert record["text"] == "hi"
    assert record["confirm"] is True
    assert "at" in record


def test_send_receipt_appends_to_the_shared_api_lane_log(tmp_path, monkeypatch):
    """The receipt log is ``envoy_x.Paths.log`` under the same directory —
    a browser send and an API-lane post land in the same file."""
    from brr import envoy_x

    paths = _paths(tmp_path)
    api_paths = envoy_x.Paths.in_dir(paths.log.parent)
    assert api_paths.log == paths.log

    _armed_env(monkeypatch)
    with open(paths.log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"lane": "api", "id": "1"}) + "\n")
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["send", "https://x.com/a/status/1", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    lines = [json.loads(line) for line in paths.log.read_text(encoding="utf-8").splitlines()]
    assert [r.get("lane") for r in lines] == ["api", "browser"]


# ── the profile dir is excluded from git ───────────────────────────────


def test_profile_dirname_matches_the_accounts_gitignore_rule():
    assert account._BROWSER_PROFILE_RELPATH == f"account/{envoy_x_browser.PROFILE_DIRNAME}"
    assert f"/{account._BROWSER_PROFILE_RELPATH}/" in account.GITIGNORE


def test_profile_dir_is_gitignored_and_uncommittable_in_a_real_account_home(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    monkeypatch.chdir(repo)
    ctx = account.resolve_context(repo, {}, create=True)
    home = account.context_home_root(ctx)

    paths = envoy_x_browser.Paths.in_dir(home / "account")
    paths.profile_dir.mkdir(parents=True)
    cookie_file = paths.profile_dir / "Cookies"
    cookie_file.write_text("very secret session cookie", encoding="utf-8")

    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(cookie_file)],
        cwd=home, capture_output=True,
    )
    assert ignored.returncode == 0, "the profile dir must be covered by .gitignore"

    subprocess.run(["git", "add", "-A"], cwd=home, check=True, capture_output=True)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=home, capture_output=True, text=True, check=True,
    )
    assert envoy_x_browser.PROFILE_DIRNAME not in status.stdout
