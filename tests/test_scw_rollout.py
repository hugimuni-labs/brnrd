"""``scripts/scw_rollout.py`` — the container rollout, after #894's 409.

#894 shipped the rollout as four inline ``curl`` calls and it failed on both
of its live runs with ``curl: (22) The requested URL returned error: 409`` —
no reason, because ``--fail -o /dev/null`` discarded the body Scaleway had
written one. Two defects under that one number, both pinned here:

- the ``PATCH`` fired without waiting for the container to settle, so an
  operator editing container variables in the console was enough to refuse
  the release;
- ``POST /deploy`` was chained after ``PATCH``, which is the vendor-documented
  cause of the very ``409`` observed.

The regression these tests exist to catch is the third one: **a rollout
failure that reaches the log as an integer.**
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "scw_rollout.py"


def _load():
    spec = importlib.util.spec_from_file_location("scw_rollout_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()


class _Api:
    """A scripted Scaleway, recording every call it is asked to make."""

    def __init__(self, statuses, *, patch_results=(), error_message=""):
        self.statuses = list(statuses)
        self.patch_results = list(patch_results)
        self.error_message = error_message
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, url, token, payload=None, **kw):
        self.calls.append((method, url))
        if method == "GET":
            status = self.statuses.pop(0) if self.statuses else "ready"
            return {"status": status, "error_message": self.error_message}
        if method == "PATCH":
            if self.patch_results:
                outcome = self.patch_results.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
            return {}
        return {}

    @property
    def methods(self):
        return [m for m, _ in self.calls]


class _Clock:
    """A fake clock where sleeping is what advances time.

    Stands in for the whole ``time`` module so no test spends a real second,
    and so a timeout can be driven by the code's own sleeps.
    """

    def __init__(self, sleeps: list[float]) -> None:
        self.sleeps = sleeps
        self._t = 0.0

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._t += seconds

    def monotonic(self) -> float:
        return self._t


def _install(monkeypatch, api, sleeps=None):
    monkeypatch.setattr(mod, "request", api)
    monkeypatch.setattr(mod, "time", _Clock(sleeps if sleeps is not None else []))


# ── region derivation: one copy of the fact ──────────────────────────


def test_region_comes_from_the_registry_host():
    assert mod.region_of("rg.fr-par.scw.cloud/brnrd/brnrd:sha-abc1234") == "fr-par"
    assert mod.region_of("rg.nl-ams.scw.cloud/ns/img:tag") == "nl-ams"


def test_a_registry_host_that_is_not_scaleways_is_refused_loudly():
    with pytest.raises(SystemExit) as excinfo:
        mod.region_of("ghcr.io/hugimuni-labs/brnrd:sha-abc1234")
    assert "::error::" in str(excinfo.value)


# ── settled is ready-or-error, everything else waits ─────────────────


def test_transient_states_are_waited_out_not_enumerated(monkeypatch):
    # `creating` is not in any allow-list this script carries; it must still
    # read as "not yet" rather than "go ahead". That inversion is the point.
    api = _Api(["creating", "pending", "deploying", "ready"])
    sleeps: list[float] = []
    _install(monkeypatch, api, sleeps)
    assert mod.wait_settled("https://api/x", "tok") == "ready"
    assert len(sleeps) == 3


def test_a_container_wedged_in_a_transient_state_fails_the_release(monkeypatch):
    api = _Api(["pending"] * 200)
    sleeps: list[float] = []
    _install(monkeypatch, api, sleeps)
    with pytest.raises(SystemExit) as excinfo:
        mod.wait_settled("https://api/x", "tok", timeout_s=300.0)
    assert "transient state" in str(excinfo.value)
    # It really polled for the whole window rather than giving up on the first
    # non-ready read: 300s at a 5s interval.
    assert sum(sleeps) >= 300.0


# ── the PATCH: retry a 409, surface anything else ────────────────────


def test_a_409_is_waited_out_rather_than_reported_as_a_failure(monkeypatch):
    busy = mod.ApiError(409, json.dumps({"type": "transient_state", "message": "busy"}))
    api = _Api([], patch_results=[busy, busy, None])
    sleeps: list[float] = []
    _install(monkeypatch, api, sleeps)
    mod.patch_image("https://api/x", "tok", "img:tag", sleep=lambda s: sleeps.append(s))
    assert api.methods == ["PATCH", "PATCH", "PATCH"]
    assert sleeps == [mod.PATCH_BACKOFF_S, mod.PATCH_BACKOFF_S]


def test_a_non_409_refusal_is_not_retried(monkeypatch):
    denied = mod.ApiError(403, json.dumps({"type": "denied", "message": "no rights"}))
    api = _Api([], patch_results=[denied])
    _install(monkeypatch, api)
    with pytest.raises(mod.ApiError):
        mod.patch_image("https://api/x", "tok", "img:tag", sleep=lambda s: None)
    assert api.methods == ["PATCH"]


def test_a_permanently_busy_container_gives_up_with_the_api_words(monkeypatch):
    busy = mod.ApiError(409, json.dumps({"type": "transient_state", "message": "busy"}))
    api = _Api([], patch_results=[busy] * mod.PATCH_ATTEMPTS)
    _install(monkeypatch, api)
    with pytest.raises(mod.ApiError) as excinfo:
        mod.patch_image("https://api/x", "tok", "img:tag", sleep=lambda s: None)
    assert "transient_state: busy" in str(excinfo.value)


# ── the error message is the whole point ─────────────────────────────


def test_an_api_error_renders_scaleways_own_sentence():
    exc = mod.ApiError(409, json.dumps({"type": "transient_state", "message": "wait"}))
    rendered = str(exc)
    assert "transient_state: wait" in rendered
    assert "409" in rendered


def test_an_unparseable_body_still_carries_something(monkeypatch):
    assert "gateway down" in str(mod.ApiError(502, "gateway down"))
    assert "no body" in str(mod.ApiError(500, "   "))


# ── the whole rollout ────────────────────────────────────────────────


def test_the_rollout_never_posts_deploy(monkeypatch):
    # Chaining DeployContainer after UpdateContainer is the documented cause
    # of `409 transient_state`. UpdateContainer redeploys on its own.
    api = _Api(["ready", "ready"])
    _install(monkeypatch, api)
    assert mod.rollout("rg.fr-par.scw.cloud/ns/img:tag", "cid", "tok") == 0
    assert api.methods == ["GET", "PATCH", "GET"]
    assert not any(url.endswith("/deploy") for _, url in api.calls)


def test_the_rollout_settles_before_it_patches(monkeypatch):
    api = _Api(["deploying", "ready", "ready"])
    _install(monkeypatch, api)
    assert mod.rollout("rg.fr-par.scw.cloud/ns/img:tag", "cid", "tok") == 0
    # GET, GET (still moving), then the PATCH — never PATCH first.
    assert api.methods[:3] == ["GET", "GET", "PATCH"]


def test_a_refused_rollout_reports_the_reason_not_a_status_code(monkeypatch, capsys):
    denied = mod.ApiError(403, json.dumps({"type": "denied", "message": "bad key"}))
    api = _Api(["ready"], patch_results=[denied])
    _install(monkeypatch, api)
    assert mod.rollout("rg.fr-par.scw.cloud/ns/img:tag", "cid", "tok") == 1
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "denied: bad key" in out


def test_a_container_that_lands_in_error_reports_its_error_message(monkeypatch, capsys):
    api = _Api(["ready", "error"], error_message="port 8080 never opened")
    _install(monkeypatch, api)
    assert mod.rollout("rg.fr-par.scw.cloud/ns/img:tag", "cid", "tok") == 1
    assert "port 8080 never opened" in capsys.readouterr().out


def test_missing_credentials_are_named_before_any_request(monkeypatch):
    monkeypatch.delenv("SCW_SECRET_KEY", raising=False)
    monkeypatch.delenv("SCW_CONTAINER_ID", raising=False)
    called = []
    monkeypatch.setattr(mod, "request", lambda *a, **k: called.append(a))
    assert mod.main(["scw_rollout.py", "rg.fr-par.scw.cloud/ns/img:tag"]) == 2
    assert called == []


# ── the workflow calls it, and calls nothing else ────────────────────


def test_the_workflow_delegates_the_rollout_to_this_script():
    text = (REPO_ROOT / ".github" / "workflows" / "publish-container.yml").read_text(
        encoding="utf-8"
    )
    _, _, tail = text.partition("Deploy the mirrored image to the Serverless Container")
    step, _, _ = tail.partition("- name: Note the skipped deploy tail")
    assert "scripts/scw_rollout.py" in step
    # The inline curl shape is what broke; it must not creep back.
    assert "curl" not in step
    assert "/deploy" not in step
