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

    def __init__(
        self, *, whoami_value=None, read_value=None, search_value=None,
        click_send_return=None, whoami_raises=False,
    ):
        self.calls: list = []
        self.headless: bool | None = None
        self._whoami_value = whoami_value
        self._read_value = read_value if read_value is not None else {}
        self._search_value = search_value if search_value is not None else []
        self.search_tabs = []
        self._click_send_return = click_send_return
        self._whoami_raises = whoami_raises

    def __enter__(self):
        self.calls.append("__enter__")
        return self

    def __exit__(self, *exc):
        self.calls.append("__exit__")
        return False

    def whoami(self):
        self.calls.append("whoami")
        if self._whoami_raises:
            raise RuntimeError("navigation timeout")
        return self._whoami_value

    def wait_for_manual_login(self):
        self.calls.append("wait_for_manual_login")

    def read_url(self, url):
        self.calls.append(("read_url", url))
        return {**self._read_value, "url": url}

    def search(self, query, *, tab="live"):
        self.calls.append(("search", query))
        self.search_tabs.append(tab)
        return self._search_value

    def open_reply_composer(self, url):
        self.calls.append(("open_reply_composer", url))

    def open_post_composer(self):
        self.calls.append("open_post_composer")

    def fill_text(self, text):
        self.calls.append(("fill_text", text))

    def screenshot(self, path):
        self.calls.append(("screenshot", path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-png")

    def click_send(self):
        self.calls.append("click_send")
        return self._click_send_return


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
        ["draft-post", "-h"],
        ["post", "--help"],
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


# ── the compose lane: post/draft-post, same guardrails, no target url ──


def test_post_refuses_dash_led_text(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setenv("BRR_X_BROWSER_SEND", "1")
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["post", "--confirm", "--text", "-rf /"],
            paths, driver_factory=_refusing_factory,
        )
    assert "looks like a flag" in str(exc.value)


def test_draft_post_leading_space_escapes_the_dash_guard(tmp_path):
    paths = _paths(tmp_path)
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["draft-post", "--text", " -not a flag"],
        paths, driver_factory=_factory_for(driver),
    )
    assert ("fill_text", "-not a flag") in driver.calls


def test_draft_post_requires_text(tmp_path):
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(["draft-post"], paths, driver_factory=_refusing_factory)
    assert "--text" in str(exc.value)


def test_post_rejects_a_stray_positional_url(tmp_path, monkeypatch):
    """The compose lane has no reply target — a leftover positional after
    ``--text`` usually means the caller meant ``send``/``draft`` and typed
    the wrong verb, so this refuses instead of silently dropping it."""
    paths = _paths(tmp_path)
    monkeypatch.setenv("BRR_X_BROWSER_SEND", "1")
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["post", "--confirm", "--text", "hi", "https://x.com/a/status/1"],
            paths, driver_factory=_refusing_factory,
        )
    assert "no reply target" in str(exc.value)


def test_post_refuses_with_neither_arm(tmp_path):
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["post", "--text", "hi"], paths, driver_factory=_refusing_factory,
        )
    msg = str(exc.value)
    assert "--confirm" in msg and "BRR_X_BROWSER_SEND=1" in msg


def test_post_refuses_with_only_confirm_flag(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.delenv("BRR_X_BROWSER_SEND", raising=False)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["post", "--text", "hi", "--confirm"],
            paths, driver_factory=_refusing_factory,
        )
    assert "BRR_X_BROWSER_SEND=1" in str(exc.value)


def test_post_refuses_with_only_env_armed(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["post", "--text", "hi"], paths, driver_factory=_refusing_factory,
        )
    assert "--confirm" in str(exc.value)


def test_post_proceeds_when_both_arms_present_and_under_cap(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["post", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    assert driver.headless is False
    assert "open_post_composer" in driver.calls
    assert ("fill_text", "hi") in driver.calls
    assert "click_send" in driver.calls
    # The compose lane must ask for the compose url, never the reply modal.
    assert not any(
        isinstance(c, tuple) and c[0] == "open_reply_composer" for c in driver.calls
    )


def test_draft_post_never_calls_click_send(tmp_path, capsys):
    paths = _paths(tmp_path)
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["draft-post", "--text", "hello there"],
        paths, driver_factory=_factory_for(driver),
    )
    assert "click_send" not in driver.calls
    assert "open_post_composer" in driver.calls
    assert ("fill_text", "hello there") in driver.calls
    printed = capsys.readouterr().out.strip()
    assert Path(printed).exists()
    assert Path(printed).parent == paths.shots_dir


def test_post_and_send_share_the_same_hourly_cap_bucket(tmp_path, monkeypatch):
    """The task's own framing: post counts against the cap "exactly as a
    reply" — one shared bucket, not a second cap that would let a caller
    dodge the limit by alternating verbs."""
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    paths.config.write_text(json.dumps({"hourly_cap": 1}), encoding="utf-8")
    envoy_x_browser.run(
        ["send", "https://x.com/a/status/1", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(_FakeDriver()),
    )
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["post", "--text", "hi", "--confirm"],
            paths, driver_factory=_refusing_factory,
        )
    assert "cap" in str(exc.value)


def test_post_refuses_past_cap_even_when_armed(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    now = time.time()
    paths.config.write_text(json.dumps({"hourly_cap": 1}), encoding="utf-8")
    paths.state.write_text(json.dumps({"sends": [now]}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["post", "--text", "hi", "--confirm"],
            paths, driver_factory=_refusing_factory,
        )
    assert "cap" in str(exc.value)


def test_post_receipt_records_post_id_when_driver_yields_one(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver(click_send_return="1234567890")
    envoy_x_browser.run(
        ["post", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    lines = paths.log.read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[0])
    assert record["lane"] == "browser"
    assert record["kind"] == "post"
    assert record["text"] == "hi"
    assert record["confirm"] is True
    assert record["post_id"] == "1234567890"


def test_post_receipt_records_explicit_absence_not_a_guess(tmp_path, monkeypatch):
    """The task's own constraint: never invent an id. A driver that yields
    nothing must produce a `post_id: null` field a reader can see, not a
    missing key that reads as "forgot to check"."""
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver(click_send_return=None)
    envoy_x_browser.run(
        ["post", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    lines = paths.log.read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[0])
    assert "post_id" in record
    assert record["post_id"] is None


def test_post_receipt_appends_to_the_shared_api_lane_log(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    with open(paths.log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"lane": "api", "id": "1"}) + "\n")
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["post", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    lines = [json.loads(line) for line in paths.log.read_text(encoding="utf-8").splitlines()]
    assert [r.get("lane") for r in lines] == ["api", "browser"]


# ── the reply lane's own defect: no address, ever, until now ──────────


def test_send_receipt_records_post_id_when_driver_yields_one(tmp_path, monkeypatch):
    """The defect this whole task exists to fix: `_run_send` used to throw
    away `click_send()`'s return value entirely. A reply now gets the same
    honest-id contract `_run_post` already had."""
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver(click_send_return="1234567890")
    envoy_x_browser.run(
        ["send", "https://x.com/a/status/1", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["post_id"] == "1234567890"
    # `url` keeps its established meaning in a reply row -- the post being
    # replied to, not the one this row created. The collision this change
    # deliberately resolves additively, not by rename.
    assert record["url"] == "https://x.com/a/status/1"


def test_send_receipt_records_explicit_absence_not_a_guess(tmp_path, monkeypatch):
    """Same contract `_run_post`'s comment already states: a driver that
    yields nothing produces an honest `post_id: null`, never an id
    invented from the reply target's own url."""
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver(click_send_return=None)
    envoy_x_browser.run(
        ["send", "https://x.com/a/status/1", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert "post_id" in record
    assert record["post_id"] is None
    assert "self_url" not in record


# ── self_url: the canonical address of the post this row created ──────


def test_send_receipt_records_self_url_from_the_live_handle(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver(click_send_return="42", whoami_value="brnrd_resident")
    envoy_x_browser.run(
        ["send", "https://x.com/a/status/1", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["self_url"] == "https://x.com/brnrd_resident/status/42"
    assert "whoami" in driver.calls


def test_post_receipt_records_self_url_from_the_live_handle(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver(click_send_return="42", whoami_value="brnrd_resident")
    envoy_x_browser.run(
        ["post", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["self_url"] == "https://x.com/brnrd_resident/status/42"


def test_self_url_omitted_when_post_id_is_none(tmp_path, monkeypatch):
    """Never a URL built around a missing id -- the key must be absent
    entirely, not present as null and not guessed. whoami() must not even
    be asked to resolve a handle for an id that doesn't exist."""
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver(click_send_return=None, whoami_value="brnrd_resident")
    envoy_x_browser.run(
        ["post", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert "self_url" not in record
    assert "whoami" not in driver.calls


def test_self_url_omitted_when_whoami_fails(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver(click_send_return="42", whoami_value=None)
    envoy_x_browser.run(
        ["post", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["post_id"] == "42"
    assert "self_url" not in record


def test_a_raising_whoami_never_costs_the_post_its_receipt(tmp_path, monkeypatch):
    """The self-URL lookup happens AFTER the post has already shipped, so
    it must not be able to take the receipt down with it.

    `whoami()` opens with a bare `page.goto(HOME_URL)` — a navigation
    timeout or a session that died between the send and this call is
    enough to raise. If that propagates, `_record_send` (the hourly cap's
    only writer) and `_append_receipt` both never run, and a live public
    post exists with no receipt line and no cap increment. The
    already-covered case is a whoami that *returns* nothing; this is the
    one that *raises*."""
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver(click_send_return="42", whoami_raises=True)
    envoy_x_browser.run(
        ["post", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["post_id"] == "42"
    assert "self_url" not in record
    # the cap tick is the other casualty of a raise here
    assert envoy_x_browser.cap_status(paths)["used"] == 1


def test_a_raising_whoami_never_costs_a_reply_its_receipt(tmp_path, monkeypatch):
    """Same guarantee on the reply lane, which gained the `self_url`
    lookup in the same change."""
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver(click_send_return="42", whoami_raises=True)
    envoy_x_browser.run(
        ["send", "https://x.com/someone/status/1", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["post_id"] == "42"
    assert record["url"] == "https://x.com/someone/status/1"
    assert "self_url" not in record
    assert envoy_x_browser.cap_status(paths)["used"] == 1


# ── --form: caller-supplied rhetorical form, free text, never an arm ──


def test_form_recorded_verbatim_on_send(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["send", "https://x.com/a/status/1", "--text", "hi", "--confirm",
         "--form", "the open question"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["form"] == "the open question"


def test_form_recorded_verbatim_on_post(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["post", "--text", "hi", "--confirm", "--form", "the measured number"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["form"] == "the measured number"


def _form_argv(verb: str, label: str) -> list[str]:
    if verb == "send":
        return [
            "send", "https://x.com/a/status/1", "--text", "hi", "--confirm",
            "--form", label,
        ]
    return ["post", "--text", "hi", "--confirm", "--form", label]


@pytest.mark.parametrize("verb", ["post", "send"])
def test_declared_form_is_silent_and_recorded_verbatim(
    tmp_path, monkeypatch, capsys, verb,
):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    paths.config.write_text(
        json.dumps({"forms": ["the open question", "the measured number"]}),
        encoding="utf-8",
    )
    envoy_x_browser.run(
        _form_argv(verb, "the open question"), paths,
        driver_factory=_factory_for(_FakeDriver(click_send_return="42")),
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "id 42" in captured.out
    record = json.loads(paths.log.read_text(encoding="utf-8").strip())
    assert record["form"] == "the open question"


@pytest.mark.parametrize("verb", ["post", "send"])
def test_undeclared_form_warns_once_but_ships_and_records_verbatim(
    tmp_path, monkeypatch, capsys, verb,
):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    paths.config.write_text(
        json.dumps({"forms": ["the open question", "the measured number"]}),
        encoding="utf-8",
    )
    label = "the wrong axis"
    envoy_x_browser.run(
        _form_argv(verb, label), paths,
        driver_factory=_factory_for(_FakeDriver(click_send_return="42")),
    )
    captured = capsys.readouterr()
    assert captured.err.splitlines() == [
        "warning: form 'the wrong axis' is not declared; declared forms: "
        "the open question, the measured number"
    ]
    assert "id 42" in captured.out
    record = json.loads(paths.log.read_text(encoding="utf-8").strip())
    assert record["form"] == label


@pytest.mark.parametrize("verb", ["post", "send"])
@pytest.mark.parametrize(
    "config",
    [
        {},
        {"forms": "the open question"},
        {"forms": ["the open question", 7]},
    ],
)
def test_absent_or_malformed_forms_config_emits_no_warning(
    tmp_path, monkeypatch, capsys, verb, config,
):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    paths.config.write_text(json.dumps(config), encoding="utf-8")
    envoy_x_browser.run(
        _form_argv(verb, "outside any vocabulary"), paths,
        driver_factory=_factory_for(_FakeDriver(click_send_return="42")),
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "id 42" in captured.out


def test_form_absent_when_not_passed(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["post", "--text", "hi", "--confirm"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert "form" not in record


def test_form_refuses_dash_led_value(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setenv("BRR_X_BROWSER_SEND", "1")
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["post", "--confirm", "--text", "hi", "--form", "-rf /"],
            paths, driver_factory=_refusing_factory,
        )
    assert "looks like a flag" in str(exc.value)


def test_form_leading_space_escapes_the_dash_guard(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _armed_env(monkeypatch)
    driver = _FakeDriver()
    envoy_x_browser.run(
        ["post", "--text", "hi", "--confirm", "--form", " -not a flag"],
        paths, driver_factory=_factory_for(driver),
    )
    record = json.loads(paths.log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["form"] == "-not a flag"


def test_form_cannot_arm_send_alone(tmp_path):
    """The task's own explicit constraint: --form must not be able to
    substitute for --confirm or BRR_X_BROWSER_SEND=1 -- it is checked and
    recorded, never read by the arming guard, and the driver must never be
    constructed on this path."""
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["send", "https://x.com/a/status/1", "--text", "hi",
             "--form", "the artifact"],
            paths, driver_factory=_refusing_factory,
        )
    msg = str(exc.value)
    assert "--confirm" in msg and "BRR_X_BROWSER_SEND=1" in msg


def test_form_cannot_arm_post_alone(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.delenv("BRR_X_BROWSER_SEND", raising=False)
    with pytest.raises(SystemExit) as exc:
        envoy_x_browser.run(
            ["post", "--text", "hi", "--form", "the artifact"],
            paths, driver_factory=_refusing_factory,
        )
    msg = str(exc.value)
    assert "--confirm" in msg and "BRR_X_BROWSER_SEND=1" in msg


def test_draft_and_draft_post_refuse_a_stray_form_flag(tmp_path):
    """draft/draft-post write no receipt row at all -- a --form flag there
    would be argv nobody reads, so it is deliberately not offered (see the
    module docstring). A stray --form falls through to the existing
    leftover-positional refusal rather than being silently swallowed."""
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit):
        envoy_x_browser.run(
            ["draft", "https://x.com/a/status/1", "--text", "hi", "--form", "x"],
            paths, driver_factory=_refusing_factory,
        )
    with pytest.raises(SystemExit):
        envoy_x_browser.run(
            ["draft-post", "--text", "hi", "--form", "x"],
            paths, driver_factory=_refusing_factory,
        )


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
        ["draft-post", "--text", "hi"],
        ["post", "--text", "hi", "--confirm"],
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


def test_check_names_the_profile_it_asked(tmp_path, capsys):
    """A logged-out answer must say *which* profile answered.

    The shim resolves its paths from its own directory and more than one
    copy of it exists, so running the wrong copy finds an empty profile
    beside itself and reports ``logged_in_as: null`` — identical to a live
    copy whose session died. Without the directory in the answer the two
    cases are one case. Asserted against ``paths.profile_dir`` rather than
    a literal, so a rename of the well-known filename cannot leave this
    test green over a stale string.
    """
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value=None)
    envoy_x_browser.run(["check", "--json"], paths, driver_factory=_factory_for(driver))
    out = json.loads(capsys.readouterr().out)
    assert out["profile_dir"] == str(paths.profile_dir)
    assert envoy_x_browser.PROFILE_DIRNAME in out["profile_dir"]


def test_check_human_output_names_the_profile_too(tmp_path, capsys):
    """The plain line carries it as well — a human reading `check` output is
    exactly the reader who ran the wrong copy."""
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value="brnrd_resident")
    envoy_x_browser.run(["check"], paths, driver_factory=_factory_for(driver))
    line = capsys.readouterr().out
    assert str(paths.profile_dir) in line


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


def test_search_defaults_to_the_latest_tab(tmp_path):
    """Latest stays the default: this verb's older callers were written
    against an unfiltered reverse-chronological feed, and a ranked tab
    silently drops small matches — which is the right trade for finding a
    conversation and the wrong one for monitoring a term."""
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value="brnrd_resident")
    envoy_x_browser.run(["search", "brnrd"], paths, driver_factory=_factory_for(driver))
    assert driver.search_tabs == ["live"]


def test_search_top_flag_asks_for_the_ranked_tab(tmp_path):
    """`--top` is the whole difference between "everything that matched"
    and "what is actually being read" — a two-follower account replying
    under an unread post is a reply nobody sees, so the ranked tab is the
    one that answers a scouting question."""
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value="brnrd_resident")
    envoy_x_browser.run(["search", "brnrd", "--top"], paths, driver_factory=_factory_for(driver))
    assert driver.search_tabs == ["top"]


def test_search_top_flag_is_not_mistaken_for_the_query(tmp_path):
    """`--top` is stripped before the single-positional check, the way
    `--json` already is. Without that, `search q --top` reads as two
    positionals and dies in argv parsing — the flag would be unusable and
    the failure would look like a usage error in the caller's query."""
    paths = _paths(tmp_path)
    driver = _FakeDriver(whoami_value="brnrd_resident")
    envoy_x_browser.run(
        ["search", "agent memory", "--top", "--json"], paths, driver_factory=_factory_for(driver)
    )
    assert ("search", "agent memory") in driver.calls


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
