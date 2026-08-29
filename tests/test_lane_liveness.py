"""Lane liveness in the wake — ``200`` beside ``set`` (w-71).

Every test here drives the **caller the wake actually uses**: gate state is
written through each gate's own real writer, the cache is produced by a real
``lane_liveness.refresh()`` against a faked HTTP session, and the assertion is
made on ``prompts._format_communication_snapshot`` — the function that renders
the resident's wake block. No fixture in this file is a hand-built dict that
production could not produce.

The four invariants, each of which is a way this could be built wrong:

1. **A missing answer renders as missing, never as healthy.** Absent, error,
   and no-probe are three distinct renderings and none of them is a bare code.
2. **The render path never touches the network.** The HTTP session is poisoned
   during every render assertion.
3. **No probed value reaches any surface.** ``.card`` is mirrored to the
   dashboard unredacted, and Telegram carries the bot token in the URL path.
4. **No verdict is cached without its age.** Stale says stale, and prints it.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest import mock

import pytest
import requests

from brr import lane_liveness, prompts
from brr.gates import runtime as gate_runtime

from _helpers import commit_files, init_git_repo


TELEGRAM_TOKEN = "8675309:AAH-super-secret-telegram-value"
SLACK_TOKEN = "xoxb-0000-super-secret-slack-value"
GITHUB_TOKEN = "ghp_supersecretgithubvalue0000000000"


# ── Fixtures that production can produce ─────────────────────────────


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"README.md": "hi\n"})
    return repo


def _configure_gates(repo: Path, *, telegram=True, slack=True, github=False,
                     cloud=False, signal=False) -> Path:
    """Write real gate state through the real writer, so ``is_configured`` agrees."""
    brr_dir = repo / ".brr"
    if telegram:
        gate_runtime.save_state(brr_dir, "telegram", {"token": TELEGRAM_TOKEN})
    if slack:
        gate_runtime.save_state(brr_dir, "slack", {
            "token": SLACK_TOKEN, "channel": "C1", "bot_user_id": "U1",
        })
    if github:
        gate_runtime.save_state(brr_dir, "github", {
            "token": GITHUB_TOKEN, "repo": "o/r",
        })
    if cloud:
        # Through the cloud gate's own writer: its state does not live where
        # ``gate_runtime.save_state`` puts every other gate's (the bearer token
        # is split out of ``cloud.json`` entirely), so a hand-placed file here
        # would be a fixture production cannot produce.
        from brr.gates import cloud as cloud_gate
        cloud_gate._save_state(brr_dir, {
            "token": "cloud-secret", "brnrd_url": "https://brnrd.dev",
            "account_id": "acc_1",
        })
    if signal:
        gate_runtime.save_state(brr_dir, "signal", {
            "api_url": "http://127.0.0.1:8080", "number": "+15551234567",
        })
    return brr_dir


class _Response:
    def __init__(self, status: int, payload: object = None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _session(answers: dict[str, object]):
    """A fake session keyed by a substring of the URL.

    A value that is an exception instance is raised, which is how the timeout
    and transport-failure paths get exercised through the same door.
    """
    def _answer(url, **kwargs):
        for mark, value in answers.items():
            if mark in url:
                if isinstance(value, BaseException):
                    raise value
                return value
        raise AssertionError(f"probe hit an unstubbed URL: {url}")

    fake = mock.MagicMock()
    fake.get.side_effect = _answer
    fake.post.side_effect = _answer
    return fake


def _render(repo: Path) -> str:
    """The wake block, through the exact renderer the daemon calls.

    The HTTP session is poisoned for the duration: prompt assembly reading the
    network would fail here rather than silently becoming the shape this whole
    module exists to forbid.
    """
    poison = mock.MagicMock()
    poison.get.side_effect = AssertionError("render path made a network call")
    poison.post.side_effect = AssertionError("render path made a network call")
    with mock.patch.object(lane_liveness, "_SESSION", poison):
        # The facet build is inside the poison too: in production it runs in
        # daemon.py at snapshot time, which is just as much "assembly" as the
        # render itself, and a probe there would break the same promise.
        facet = lane_liveness.read_state(repo)
        return prompts._format_communication_snapshot({
            lane_liveness.FACET_KEY: facet, "current_thread": "t",
        })


# ── 1. A missing answer renders as missing ───────────────────────────


def test_never_probed_renders_loudly_and_is_not_silence(tmp_path):
    """No cache is not "fine" — and it is not nothing, either."""
    repo = _repo(tmp_path)
    rendered = _render(repo)
    assert "Lane liveness: never probed" in rendered
    assert lane_liveness.EDGE_NOTE in rendered
    assert "200" not in rendered


def test_probe_failure_never_renders_as_a_bare_code(tmp_path):
    """A timeout and a 200 must not be skimmable as the same thing."""
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    with mock.patch.object(
        lane_liveness, "_SESSION",
        _session({"api.telegram.org": requests.Timeout("boom")}),
    ):
        lane_liveness.refresh(repo)
    rendered = _render(repo)
    assert "telegram probe failed" in rendered
    assert "timeout" in rendered
    assert "telegram 200" not in rendered


def test_a_200_envelope_saying_not_ok_is_an_auth_failure_not_a_200(tmp_path):
    """Telegram and Slack both refuse a dead token *inside* a 200.

    Reading only ``response.status_code`` renders a revoked credential as
    healthy — the single most direct way to build this feature wrong.
    """
    repo = _repo(tmp_path)
    _configure_gates(repo)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(200, {"ok": False, "description": "Unauthorized"}),
        "slack.com": _Response(200, {"ok": False, "error": "invalid_auth"}),
    })):
        lane_liveness.refresh(repo)
    rendered = _render(repo)
    lanes = {row["lane"]: row for row in lane_liveness.read_state(repo)["lanes"]}
    assert lanes["telegram"]["outcome"] == "auth_failed"
    assert lanes["slack"]["outcome"] == "auth_failed"
    assert "telegram auth failed (Unauthorized)" in rendered
    assert "slack auth failed (invalid_auth)" in rendered
    # And the number is gone entirely. Printing `telegram 200 (Unauthorized)`
    # would put the exact token a skimmer looks for beside a dead lane —
    # this feature's own failure mode, turned on its rendering.
    assert "200" not in rendered


@pytest.mark.parametrize("body,label", [
    (None, "HTML interstitial (unparseable body)"),
    ([1, 2, 3], "JSON that is not an object"),
    ({"result": {}}, "JSON object with no `ok` key"),
])
def test_a_200_that_is_not_the_promised_envelope_is_never_ok(tmp_path, body, label):
    """A captive portal / MITM proxy / Cloudflare page answers `200 text/html`.

    The request never reached Telegram. A classifier that treats "no envelope"
    as "no refusal" prints `telegram 200` for a probe that spoke to a proxy —
    failure rendering as health, which is the one thing this must never do.
    """
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(200, body),
    })):
        lane_liveness.refresh(repo)
    rows = lane_liveness.read_state(repo)["lanes"]
    assert rows[0]["outcome"] == "error", label
    rendered = _render(repo)
    assert "telegram probe failed" in rendered, label
    assert "telegram 200" not in rendered, label


def test_a_204_does_not_print_a_bare_code(tmp_path):
    """Same class as the interstitial: a bodyless 2xx proves nothing."""
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(204, None),
    })):
        lane_liveness.refresh(repo)
    rendered = _render(repo)
    assert "telegram 204" not in rendered
    assert "telegram probe failed" in rendered


def test_a_live_credential_still_reads_green(tmp_path):
    """The envelope rule must not make every lane permanently red."""
    repo = _repo(tmp_path)
    _configure_gates(repo, github=True)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(200, {"ok": True, "result": {"id": 1}}),
        "slack.com": _Response(200, {"ok": True, "user_id": "U1"}),
        "api.github.com": _Response(200, {"resources": {}, "rate": {}}),
    })):
        lane_liveness.refresh(repo)
    rendered = _render(repo)
    assert "telegram 200" in rendered
    assert "slack 200" in rendered
    assert "github 200" in rendered


def test_a_non_auth_api_error_is_not_reported_as_a_dead_credential(tmp_path):
    """Slack's `invalid_arguments` on a live token must not say "auth failed"."""
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=False, slack=True)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "slack.com": _Response(200, {"ok": False, "error": "invalid_arguments"}),
    })):
        lane_liveness.refresh(repo)
    assert lane_liveness.read_state(repo)["lanes"][0]["outcome"] == "error"
    assert "slack auth failed" not in _render(repo)


def test_a_real_401_still_prints_its_code_because_the_code_is_the_refusal(tmp_path):
    """The 200-envelope rule must not swallow the honest case."""
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=False, slack=False, github=True)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.github.com": _Response(401, {"message": "Bad credentials"}),
    })):
        lane_liveness.refresh(repo)
    assert "github 401" in _render(repo)


def test_live_and_dead_lanes_render_distinctly_in_the_wake_block(tmp_path):
    """The shape the item asked for: a code per lane, beside the lane's name."""
    repo = _repo(tmp_path)
    _configure_gates(repo, github=True)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(200, {"ok": True, "result": {"id": 1}}),
        "slack.com": _Response(200, {"ok": True, "user_id": "U1"}),
        "api.github.com": _Response(401, {"message": "Bad credentials"}),
    })):
        lane_liveness.refresh(repo)
    rendered = _render(repo)
    assert "telegram 200" in rendered
    assert "slack 200" in rendered
    assert "github 401" in rendered


def test_a_lane_with_no_safe_probe_renders_its_reason(tmp_path):
    """`cloud` and `signal` are unprobeable for stated reasons — and say so."""
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=False, slack=False, cloud=True, signal=True)
    lane_liveness.refresh(repo)
    rendered = _render(repo)
    assert "cloud not probed" in rendered
    assert "long-poll cursor" in rendered
    assert "signal not probed" in rendered
    assert "no credential to test" in rendered


def test_a_configured_gate_absent_from_PROBES_still_gets_a_row(tmp_path):
    """A gate added to BUILTIN_GATES and forgotten here must be visible, not gone."""
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    with mock.patch.dict(lane_liveness.PROBES, {}, clear=True):
        lane_liveness.refresh(repo)
    rendered = _render(repo)
    assert "telegram not probed (no probe implemented)" in rendered


def test_the_block_names_its_own_edge(tmp_path):
    """Three green lanes must not read as "every credential is live"."""
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(200, {"ok": True}),
    })):
        lane_liveness.refresh(repo)
    rendered = _render(repo)
    assert lane_liveness.EDGE_NOTE in rendered


# ── 2. The render path never touches the network ─────────────────────


def test_render_is_network_free(tmp_path):
    """`_render` poisons the session; a probing renderer fails this outright."""
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(200, {"ok": True}),
    })):
        lane_liveness.refresh(repo)
    # Two renders, no stub: both read the cache only.
    assert "telegram 200" in _render(repo)
    assert "telegram 200" in _render(repo)


def test_read_state_makes_no_call_even_with_no_cache(tmp_path):
    repo = _repo(tmp_path)
    _configure_gates(repo)
    poison = mock.MagicMock()
    poison.get.side_effect = AssertionError("read_state probed")
    poison.post.side_effect = AssertionError("read_state probed")
    with mock.patch.object(lane_liveness, "_SESSION", poison):
        state = lane_liveness.read_state(repo)
    assert state["status"] == "absent"
    assert state["lanes"] is None  # never [], which reads as "no lanes exist"


# ── 3. No probed value reaches any surface ───────────────────────────


@pytest.mark.parametrize("answer", [
    # Each of these takes a *different* branch of `_classify_http`, and the
    # last two are the ones that actually construct a detail string — the only
    # paths on which a token could be emitted at all. The first two are the
    # trivially-safe branches, kept so a regression that starts emitting detail
    # on them is caught too.
    _Response(200, {"ok": True}),
    _Response(401, {"ok": False, "description": "Unauthorized"}),
    _Response(200, {"ok": False, "error": "invalid_auth"}),
    _Response(200, "not an object at all"),
])
def test_the_token_never_reaches_the_cache_or_the_block(tmp_path, answer):
    repo = _repo(tmp_path)
    _configure_gates(repo, github=True)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": answer,
        "slack.com": answer,
        "api.github.com": answer,
    })):
        lane_liveness.refresh(repo)
    raw = lane_liveness.cache_path(repo).read_text(encoding="utf-8")
    rendered = _render(repo)
    for secret in (TELEGRAM_TOKEN, SLACK_TOKEN, GITHUB_TOKEN):
        assert secret not in raw
        assert secret not in rendered


def test_a_transport_error_carrying_the_token_in_its_url_is_scrubbed(tmp_path):
    """Telegram puts the bot token in the URL path, so the *exception* leaks it.

    This is not hypothetical: ``requests`` embeds the full URL in the message
    of a connection error, and ``gates/telegram.py`` already scrubs for the
    same reason. Here the fake raises a message containing the live token.
    """
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    leaky = requests.ConnectionError(
        f"HTTPSConnectionPool(host='api.telegram.org'): "
        f"/bot{TELEGRAM_TOKEN}/getMe refused"
    )
    with mock.patch.object(
        lane_liveness, "_SESSION", _session({"api.telegram.org": leaky}),
    ):
        lane_liveness.refresh(repo)
    raw = lane_liveness.cache_path(repo).read_text(encoding="utf-8")
    assert TELEGRAM_TOKEN not in raw
    assert "<redacted>" in raw
    assert TELEGRAM_TOKEN not in _render(repo)


# ── 4. No verdict is cached without its age ──────────────────────────


def test_a_stale_verdict_says_stale_and_prints_its_age(tmp_path):
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(200, {"ok": True}),
    })):
        lane_liveness.refresh(repo)
    later = time.time() + lane_liveness.STALE_AFTER_SECONDS + 28800
    state = lane_liveness.read_state(repo, now=later)
    assert state["status"] == "stale"
    rendered = prompts._format_communication_snapshot(
        {"current_thread": "t", "lanes": state}
    )
    assert "Lane liveness (stale" in rendered
    assert "h ago" in rendered


def test_a_fresh_verdict_still_prints_its_age(tmp_path):
    """Even green carries the age — a cached verdict without one is the bug."""
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(200, {"ok": True}),
    })):
        lane_liveness.refresh(repo)
    rendered = _render(repo)
    assert "checked " in rendered
    assert "ago" in rendered


def test_a_failed_refresh_never_carries_forward_an_older_green(tmp_path):
    """Unlike ``forge_pr_cache``, the failure *is* the answer here.

    Showing yesterday's ``200`` under today's attempt is the
    failure-indistinguishable-from-success this module was built to end.
    """
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(200, {"ok": True}),
    })):
        lane_liveness.refresh(repo)
    assert "telegram 200" in _render(repo)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": requests.Timeout("gone"),
    })):
        lane_liveness.refresh(repo)
    rendered = _render(repo)
    assert "telegram 200" not in rendered
    assert "telegram probe failed" in rendered


# ── Cadence / plumbing ───────────────────────────────────────────────


def test_refresh_if_stale_respects_the_ttl(tmp_path):
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    stub = _session({"api.telegram.org": _Response(200, {"ok": True})})
    with mock.patch.object(lane_liveness, "_SESSION", stub):
        assert lane_liveness.refresh_if_stale(repo) is True
        assert lane_liveness.refresh_if_stale(repo) is False
        assert lane_liveness.refresh_if_stale(
            repo, now=time.time() + lane_liveness.DEFAULT_TTL_SECONDS + 1
        ) is True


def test_one_lane_blowing_up_does_not_cost_the_others(tmp_path):
    repo = _repo(tmp_path)
    _configure_gates(repo)

    def _explode(_brr_dir):
        # The message carries a live token, which is the case the catch-all
        # cannot scrub: it does not know which secret this lane holds.
        raise RuntimeError(f"probe is broken near {TELEGRAM_TOKEN}")

    with mock.patch.dict(lane_liveness.PROBES, {"telegram": _explode}):
        with mock.patch.object(lane_liveness, "_SESSION", _session({
            "slack.com": _Response(200, {"ok": True}),
        })):
            lane_liveness.refresh(repo)
    rendered = _render(repo)
    assert "slack 200" in rendered
    assert "telegram probe failed" in rendered
    # The unforeseen path reports the exception *type* and nothing else — a
    # message nobody anticipated is a message nobody can promise is clean.
    assert "unexpected RuntimeError" in rendered
    assert TELEGRAM_TOKEN not in rendered
    assert TELEGRAM_TOKEN not in lane_liveness.cache_path(repo).read_text("utf-8")


def test_the_daemon_tick_refreshes_and_the_wake_facet_reads_it(tmp_path):
    """The two wiring points, exercised as production wires them."""
    from brr import daemon

    assert daemon.lane_liveness is lane_liveness
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(200, {"ok": True}),
    })):
        assert lane_liveness.refresh_if_stale_async(repo) is True
        # Join the worker *inside* the patch. Letting the patch exit while the
        # thread may still be running restores the real session and fires a
        # live request to api.telegram.org carrying the fixture token — and
        # leaks `_refreshing = True` into the next test.
        for thread in threading.enumerate():
            if thread.name == "lane-liveness":
                thread.join(timeout=30)
        assert lane_liveness.read_state(repo)["status"] == "fresh"
    assert "telegram 200" in _render(repo)


def test_the_two_wiring_points_exist_in_the_daemon(tmp_path):
    """A structural guard, because neither point is reachable from a test.

    Both live inside ``daemon.start()``'s scan loop and snapshot assembly,
    which no unit test drives. An adversarial review of this file mutated each
    of them — renaming the snapshot key, deleting the tick call — and the
    suite stayed green, so the coverage claim this test replaces was false.

    The key itself is now a shared constant rather than two string literals,
    which is the real fix: ``daemon`` writes ``lane_liveness.FACET_KEY`` and
    ``prompts`` reads it, so they cannot drift. This pins the remaining half —
    that the tick still calls the refresh at all.
    """
    from brr import daemon

    source = Path(daemon.__file__).read_text(encoding="utf-8")
    assert "lane_liveness.refresh_if_stale_async(repo_root)" in source, (
        "the daemon tick no longer refreshes the lane cache — the block would "
        "render `never probed` forever, a correct guard nothing feeds"
    )
    assert "lane_liveness.FACET_KEY" in source, (
        "the daemon no longer attaches the lane facet to the wake snapshot"
    )
    assert "lane_liveness.FACET_KEY" in Path(prompts.__file__).read_text("utf-8"), (
        "the wake renderer no longer reads the lane facet off the snapshot"
    )


def test_a_sweep_that_found_no_gate_says_so_instead_of_vanishing(tmp_path):
    """An empty sweep is an answer. Dropping the block makes it look like fine.

    This test previously asserted the opposite — that the block disappears —
    which locked in the bug: "we looked and there is nothing configured" and
    "we could not look" both rendered as silence, and silence reads as health.
    """
    repo = _repo(tmp_path)
    lane_liveness.refresh(repo)
    state = lane_liveness.read_state(repo)
    assert state["lanes"] == []
    assert state["discovery"] == "ok"
    rendered = _render(repo)
    assert "Lane liveness" in rendered
    assert "no configured gate to probe" in rendered


def test_discovery_failure_is_rendered_not_swallowed(tmp_path):
    """`configured_gates` blowing up must not read like "no lanes exist"."""
    repo = _repo(tmp_path)
    _configure_gates(repo)
    with mock.patch.object(
        gate_runtime, "configured_gates", side_effect=OSError("gate dir gone"),
    ):
        lane_liveness.refresh(repo)
    state = lane_liveness.read_state(repo)
    assert state["discovery"] == "failed"
    rendered = _render(repo)
    assert "lane discovery failed" in rendered
    assert "cannot say which lanes exist" in rendered


def test_an_older_cache_without_a_discovery_field_reads_as_unknown(tmp_path):
    """Never as "ok" — that would be a guess about a sweep this code did not run."""
    repo = _repo(tmp_path)
    _configure_gates(repo, telegram=True, slack=False)
    with mock.patch.object(lane_liveness, "_SESSION", _session({
        "api.telegram.org": _Response(200, {"ok": True}),
    })):
        lane_liveness.refresh(repo)
    path = lane_liveness.cache_path(repo)
    import json as _json
    data = _json.loads(path.read_text("utf-8"))
    del data["discovery"]
    path.write_text(_json.dumps(data), encoding="utf-8")
    assert lane_liveness.read_state(repo)["discovery"] is None
