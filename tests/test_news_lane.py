"""Focused tests for the news lane: item shape, producer fan-in, chat cadence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr import news_lane, release_availability


def _repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / ".brr").mkdir()
    release_availability.cache_path(tmp_path).parent.mkdir(parents=True)
    return tmp_path


def _write_release_cache(repo: Path, **fields) -> None:
    path = release_availability.cache_path(repo)
    path.write_text(json.dumps(fields), encoding="utf-8")


def _item(**overrides):
    fields = dict(
        kind="release", subject="pypi", prior="0.1.0", current="0.2.0",
        observed_at=1.0, source="x",
    )
    fields.update(overrides)
    return news_lane.NewsItem(**fields)


# --- NewsItem --------------------------------------------------------------


def test_news_item_key_is_kind_and_subject():
    assert _item().key == "release:pypi"


def test_news_item_render_shows_transition_when_prior_known():
    assert _item().render() == "pypi update available: 0.1.0 → 0.2.0"


def test_news_item_render_falls_back_without_prior():
    item = news_lane.NewsItem(
        kind="model", subject="claude-opus-5", prior=None, current="available",
        observed_at=1.0, source="catalog",
    )
    assert item.render() == "claude-opus-5: available"


def test_news_item_render_names_the_expiry_when_set():
    item = _item(current="gpt-5-codex", expires_at="2026-12-01")
    assert item.render() == "pypi: gpt-5-codex (retires 2026-12-01)"


# --- release_producer / collect ---------------------------------------------


def test_release_producer_reports_both_channels_by_name(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _write_release_cache(
        repo,
        schema=1, checked_at=1.0, latest="9.9.9",
        npm_checked_at=1.0, npm_latest="9.9.8",
    )

    items = news_lane.release_producer(repo)
    by_subject = {item.subject: item for item in items}

    assert set(by_subject) == {"pypi", "npm"}
    assert by_subject["pypi"].current == "9.9.9"
    assert by_subject["npm"].current == "9.9.8"
    assert all(item.kind == "release" and item.expires_at is None for item in items)


def test_release_producer_silent_when_up_to_date(tmp_path, monkeypatch):
    from brr import __version__

    repo = _repo(tmp_path, monkeypatch)
    _write_release_cache(
        repo,
        schema=1, checked_at=1.0, latest=__version__,
        npm_checked_at=1.0, npm_latest=__version__,
    )

    assert news_lane.release_producer(repo) == []


def test_collect_is_fail_open_per_producer(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)

    def boom(_repo_root):
        raise RuntimeError("producer bug")

    good_item = _item()
    items = news_lane.collect(repo, producers=(boom, lambda _r: [good_item]))
    assert items == [good_item]


# --- chat policy -------------------------------------------------------------


def test_chat_policy_defaults_unlisted_kind_to_dashboard_only():
    assert news_lane.is_chat_worthy("some-future-kind") is False


def test_release_kind_is_chat_worthy_per_maintainer_briefing_scope():
    # The maintainer named "published brnrd releases" explicitly as in
    # scope for the daily briefing (see the strand's report) — pinned here
    # so a change is visible in review rather than silent.
    assert news_lane.is_chat_worthy("release") is True


# --- interrupt lane (expiry) -------------------------------------------------


def test_pending_interrupts_ignores_items_without_expiry(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    item = _item()  # no expires_at
    assert news_lane.pending_interrupts(repo, producers=(lambda _r: [item],)) == []


def test_pending_interrupts_returns_unannounced_expiring_item(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    item = _item(kind="core-retirement", expires_at="2026-12-01")
    monkeypatch.setitem(news_lane.CHAT_POLICY, "core-retirement", True)

    assert news_lane.pending_interrupts(repo, producers=(lambda _r: [item],)) == [item]


def test_pending_interrupts_respects_chat_policy(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    item = _item(kind="core-retirement", expires_at="2026-12-01")
    monkeypatch.setitem(news_lane.CHAT_POLICY, "core-retirement", False)

    assert news_lane.pending_interrupts(repo, producers=(lambda _r: [item],)) == []


def test_pending_interrupts_deduped_by_current_value(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    item = _item(kind="core-retirement", expires_at="2026-12-01")
    monkeypatch.setitem(news_lane.CHAT_POLICY, "core-retirement", True)
    news_lane.record_announced(repo, item)

    assert news_lane.pending_interrupts(repo, producers=(lambda _r: [item],)) == []


def test_pending_interrupts_fires_again_on_changed_value(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setitem(news_lane.CHAT_POLICY, "core-retirement", True)
    first = _item(kind="core-retirement", current="gpt-5-codex", expires_at="2026-12-01")
    news_lane.record_announced(repo, first)

    second = _item(kind="core-retirement", current="gpt-5-codex-mini", expires_at="2026-12-15")
    assert news_lane.pending_interrupts(repo, producers=(lambda _r: [second],)) == [second]


# --- briefing lane (batched, daily) ------------------------------------------


def test_due_briefing_none_when_nothing_chat_worthy(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setitem(news_lane.CHAT_POLICY, "release", False)
    item = _item()
    assert news_lane.due_briefing(repo, now=1.0, producers=(lambda _r: [item],)) is None


def test_due_briefing_excludes_expiry_items(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setitem(news_lane.CHAT_POLICY, "release", True)
    item = _item(expires_at="2026-12-01")
    assert news_lane.due_briefing(repo, now=1.0, producers=(lambda _r: [item],)) is None


def test_due_briefing_bundles_unannounced_items(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setitem(news_lane.CHAT_POLICY, "release", True)
    pypi = _item(subject="pypi", current="0.2.0")
    npm = _item(subject="npm", current="0.2.1")

    briefing = news_lane.due_briefing(repo, now=1.0, producers=(lambda _r: [pypi, npm],))

    assert briefing is not None
    assert set(briefing.items) == {pypi, npm}
    assert "pypi update available" in briefing.render()
    assert "npm update available" in briefing.render()


def test_due_briefing_respects_interval_after_first_send(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setitem(news_lane.CHAT_POLICY, "release", True)
    item = _item()

    first = news_lane.due_briefing(repo, now=1000.0, producers=(lambda _r: [item],))
    assert first is not None
    news_lane.record_briefing_sent(repo, first)

    # Same item, well inside the interval: no second briefing, regardless
    # of whether it was ever marked "announced" as an individual value.
    still_soon = news_lane.due_briefing(
        repo, now=1000.0 + 60, producers=(lambda _r: [item],),
    )
    assert still_soon is None


def test_due_briefing_fires_again_after_interval_with_new_item(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setitem(news_lane.CHAT_POLICY, "release", True)
    first_item = _item(current="0.2.0")
    first = news_lane.due_briefing(repo, now=1000.0, producers=(lambda _r: [first_item],))
    news_lane.record_briefing_sent(repo, first)

    later_same = news_lane.due_briefing(
        repo,
        now=1000.0 + news_lane.BRIEFING_INTERVAL_SECONDS + 1,
        producers=(lambda _r: [first_item],),
    )
    assert later_same is None  # same value already said, interval elapsed but nothing new

    changed_item = _item(current="0.3.0")
    later_changed = news_lane.due_briefing(
        repo,
        now=1000.0 + news_lane.BRIEFING_INTERVAL_SECONDS + 1,
        producers=(lambda _r: [changed_item],),
    )
    assert later_changed is not None
    assert later_changed.items == (changed_item,)


def test_due_briefing_never_returns_empty_and_does_not_reset_clock(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setitem(news_lane.CHAT_POLICY, "release", True)
    item = _item()
    briefing = news_lane.due_briefing(repo, now=1000.0, producers=(lambda _r: [item],))
    news_lane.record_briefing_sent(repo, briefing)

    # Nothing new: no briefing, and (unobservable directly, but exercised by
    # test_due_briefing_fires_again_after_interval_with_new_item above) the
    # clock only ever advances on an actual send.
    assert news_lane.due_briefing(repo, now=1000.0 + 1, producers=(lambda _r: [item],)) is None


# --- ledger persistence -------------------------------------------------------


def test_ledger_persists_across_process_boundary(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    item = _item()
    news_lane.record_announced(repo, item)

    reloaded = json.loads(news_lane.ledger_path(repo).read_text(encoding="utf-8"))
    assert reloaded["announced"] == {"release:pypi": "0.2.0"}


def test_record_briefing_sent_persists_both_ledger_and_clock(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setitem(news_lane.CHAT_POLICY, "release", True)
    item = _item()
    briefing = news_lane.Briefing(items=(item,), generated_at=42.0)

    news_lane.record_briefing_sent(repo, briefing)

    reloaded = json.loads(news_lane.ledger_path(repo).read_text(encoding="utf-8"))
    assert reloaded["announced"] == {"release:pypi": "0.2.0"}
    assert reloaded["last_briefing_at"] == 42.0


def test_record_announced_propagates_a_real_write_failure(tmp_path, monkeypatch):
    """Found reviewing #1761, before merge: `_write_state` used to swallow
    `OSError` (`except OSError: pass`), so `daemon._news_record_or_disable`'s
    breaker — which exists specifically to catch a failed record and disable
    the chat lane before a resend-every-heartbeat loop — never saw the one
    class of failure its own docstring names (disk full, read-only FS,
    permission denied). `test_a_delivered_item_that_cannot_be_recorded_...`
    above proves the breaker trips when `record()` raises; this proves
    `record()` (`news_lane.record_announced`, going through the real
    `_write_state`) actually *does* raise on a real unwritable ledger path,
    rather than returning quietly and letting the next tick resend.
    """
    repo = _repo(tmp_path, monkeypatch)
    # Make the ledger *path itself* a directory, so the atomic tmp-write
    # succeeds but the final `temporary.replace(path)` fails with a real
    # OSError (IsADirectoryError) — no mocking of the write call itself,
    # the filesystem does it.
    ledger_path = news_lane.ledger_path(repo)
    ledger_path.mkdir(parents=True, exist_ok=True)

    with pytest.raises(OSError):
        news_lane.record_announced(repo, _item())


def test_a_delivered_item_that_cannot_be_recorded_disables_the_chat_lane(monkeypatch, capsys):
    """The send-then-record ordering has one bad tail, and this closes it.

    Recording *after* a confirmed send is right: recording first would lose the
    fact whenever a send fails. But `_announce_pending_news` rides the ~10s
    heartbeat, so a persistently unwritable ledger means a delivered item is
    never marked delivered — and the user's chat receives the same message
    every ten seconds, forever. One failure is a message plus a log line; a
    loop is an incident.
    """
    from brr import daemon as daemon_mod

    monkeypatch.setattr(daemon_mod, "_news_announce_disabled", False, raising=False)
    calls: list[str] = []

    def boom() -> None:
        calls.append("attempted")
        raise OSError("read-only file system")

    daemon_mod._news_record_or_disable(boom, "interrupt release:pypi")

    assert calls == ["attempted"], "the record was never attempted"
    assert daemon_mod._news_announce_disabled is True, (
        "a delivered-but-unrecorded item must disable the lane, not fall through "
        "to the next tick"
    )
    out = capsys.readouterr().out
    assert "could not be recorded" in out and "disabled for this process" in out

    # And a successful record leaves the lane alone.
    monkeypatch.setattr(daemon_mod, "_news_announce_disabled", False, raising=False)
    daemon_mod._news_record_or_disable(lambda: None, "briefing")
    assert daemon_mod._news_announce_disabled is False
