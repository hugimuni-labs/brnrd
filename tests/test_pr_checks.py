"""Tests for `brr.pr_checks` — GitHub check conclusions for claimed PRs.

Three joins, three test classes:

- `owners()` reads which live runs claimed which PR numbers, off the same
  relics/`.pr` control files `remote_scm` already reads — no new bookkeeping.
- `read()` classifies one `gh` check-runs + required-checks read into
  pending/success/failure, refusing to answer off a stale head.
- `refresh()` is the wiring into `forge_pr_cache`'s own refresh cycle: one
  `pr_checks_concluded` event per (run, pr, sha, signature) generation,
  deduped through `protocol.known_origin_ids` the same way every other
  cache-driven event is.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from brr import pr_checks, protocol
from brr.run import Run


def _claim(runtime: Path, run_id: str, event_id: str, *, status: str = "running", pr=None, pr_url=None):
    """A run (default: running) whose outbox claims a PR, via `.relics.jsonl`."""
    run = Run(id=run_id, event_id=event_id, body="x", status=status)
    run.save(runtime / "runs")
    outbox = runtime / "outbox" / event_id
    outbox.mkdir(parents=True)
    if pr is not None:
        (outbox / ".relics.jsonl").write_text(json.dumps({"kind": "pr", "ref": str(pr)}) + "\n")
    if pr_url is not None:
        (outbox / ".relics.jsonl").write_text(json.dumps({"kind": "pr", "ref": pr_url}) + "\n")
    return outbox


class TestOwners:
    def test_a_running_runs_relic_claims_its_pr_number(self, tmp_path):
        runtime = tmp_path / ".brr"
        _claim(runtime, "run-1", "evt-1", pr=274)
        found = pr_checks.owners(tmp_path, "acme/widget")
        assert set(found) == {274}
        task, outbox = found[274][0]
        assert task.id == "run-1"
        assert outbox == runtime / "outbox" / "evt-1"

    def test_a_done_run_is_not_an_owner(self, tmp_path):
        runtime = tmp_path / ".brr"
        _claim(runtime, "run-1", "evt-1", status="done", pr=274)
        assert pr_checks.owners(tmp_path, "acme/widget") == {}

    def test_the_pr_control_file_claims_too(self, tmp_path):
        runtime = tmp_path / ".brr"
        outbox = _claim(runtime, "run-1", "evt-1")
        (outbox / ".pr").write_text("99\n")
        found = pr_checks.owners(tmp_path, "acme/widget")
        assert set(found) == {99}

    def test_a_claim_url_scoped_to_a_different_repo_is_not_this_repos_owner(self, tmp_path):
        runtime = tmp_path / ".brr"
        _claim(runtime, "run-1", "evt-1", pr_url="https://github.com/other/repo/pull/5")
        assert pr_checks.owners(tmp_path, "acme/widget") == {}

    def test_two_running_runs_can_claim_the_same_pr(self, tmp_path):
        runtime = tmp_path / ".brr"
        _claim(runtime, "run-1", "evt-1", pr=5)
        _claim(runtime, "run-2", "evt-2", pr=5)
        found = pr_checks.owners(tmp_path, "acme/widget")
        assert len(found[5]) == 2

    def test_no_runs_at_all_is_the_empty_map(self, tmp_path):
        assert pr_checks.owners(tmp_path, "acme/widget") == {}


def _pages(*runs):
    return [{"check_runs": list(runs)}]


class TestRead:
    """`read(repo_root, label, pr, sha, timeout)` — three `gh` reads, joined."""

    def _fake(self, monkeypatch, *, check_runs, required, head_sha):
        def dispatch(cmd, **kwargs):
            assert cmd[0] == "gh"
            if cmd[1] == "api":
                return subprocess.CompletedProcess(cmd, 0, json.dumps(_pages(*check_runs)), "")
            if cmd[1:3] == ["pr", "checks"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(required), "")
            if cmd[1:3] == ["pr", "view"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"headRefOid": head_sha}), "")
            raise AssertionError(f"unexpected gh invocation: {cmd}")
        monkeypatch.setattr(pr_checks.subprocess, "run", dispatch)

    def test_all_required_checks_passed_reads_success(self, tmp_path, monkeypatch):
        self._fake(
            monkeypatch,
            check_runs=[{"name": "backend", "status": "completed", "conclusion": "success"}],
            required=[{"name": "backend", "bucket": "pass", "state": "SUCCESS"}],
            head_sha="deadbeef",
        )
        result = pr_checks.read(tmp_path, "acme/widget", 5, "deadbeef", 5.0)
        assert result == {
            "status": "completed",
            "conclusion": "success",
            "signature": result["signature"],
        }
        assert result["signature"]

    def test_a_failed_required_check_reads_failure(self, tmp_path, monkeypatch):
        self._fake(
            monkeypatch,
            check_runs=[{"name": "backend", "status": "completed", "conclusion": "failure"}],
            required=[{"name": "backend", "bucket": "fail", "state": "FAILURE"}],
            head_sha="deadbeef",
        )
        result = pr_checks.read(tmp_path, "acme/widget", 5, "deadbeef", 5.0)
        assert result["status"] == "completed"
        assert result["conclusion"] == "failure"

    def test_a_still_running_required_check_reads_pending(self, tmp_path, monkeypatch):
        self._fake(
            monkeypatch,
            check_runs=[{"name": "backend", "status": "in_progress", "conclusion": None}],
            required=[{"name": "backend", "bucket": "pending", "state": "PENDING"}],
            head_sha="deadbeef",
        )
        assert pr_checks.read(tmp_path, "acme/widget", 5, "deadbeef", 5.0) == {"status": "pending"}

    def test_no_required_checks_falls_back_to_every_observed_run(self, tmp_path, monkeypatch):
        self._fake(
            monkeypatch,
            check_runs=[{"name": "backend", "status": "completed", "conclusion": "success"}],
            required=[],
            head_sha="deadbeef",
        )
        result = pr_checks.read(tmp_path, "acme/widget", 5, "deadbeef", 5.0)
        assert result["status"] == "completed"
        assert result["conclusion"] == "success"

    def test_no_required_checks_and_no_observed_runs_is_pending_not_success(self, tmp_path, monkeypatch):
        self._fake(monkeypatch, check_runs=[], required=[], head_sha="deadbeef")
        # `_json` refuses an empty check-runs page list outright (see below),
        # so this shape has to come from a page holding one *empty* check_runs
        # list rather than zero pages, or the read never reaches the fallback.
        def dispatch(cmd, **kwargs):
            if cmd[1] == "api":
                return subprocess.CompletedProcess(cmd, 0, json.dumps([{"check_runs": []}]), "")
            if cmd[1:3] == ["pr", "checks"]:
                return subprocess.CompletedProcess(cmd, 0, "[]", "")
            if cmd[1:3] == ["pr", "view"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"headRefOid": "deadbeef"}), "")
            raise AssertionError(cmd)
        monkeypatch.setattr(pr_checks.subprocess, "run", dispatch)
        assert pr_checks.read(tmp_path, "acme/widget", 5, "deadbeef", 5.0) == {"status": "pending"}

    def test_the_pr_moved_during_the_refresh_reads_pending_not_the_old_verdict(self, tmp_path, monkeypatch):
        self._fake(
            monkeypatch,
            check_runs=[{"name": "backend", "status": "completed", "conclusion": "success"}],
            required=[{"name": "backend", "bucket": "pass", "state": "SUCCESS"}],
            head_sha="newsha",  # pr view reports a head the caller didn't ask about
        )
        result = pr_checks.read(tmp_path, "acme/widget", 5, "oldsha", 5.0)
        assert result == {"status": "pending", "reason": "head changed during refresh"}

    def test_an_empty_check_runs_response_is_a_read_failure_not_a_verdict(self, tmp_path, monkeypatch):
        def dispatch(cmd, **kwargs):
            if cmd[1] == "api":
                return subprocess.CompletedProcess(cmd, 0, "[]", "")
            raise AssertionError(cmd)
        monkeypatch.setattr(pr_checks.subprocess, "run", dispatch)
        with pytest.raises(ValueError):
            pr_checks.read(tmp_path, "acme/widget", 5, "deadbeef", 5.0)


class TestRefresh:
    """`refresh()` — the forge-cache wiring: rows in, at most one event out."""

    def _fake_success(self, monkeypatch, *, sha="deadbeef"):
        def dispatch(cmd, **kwargs):
            if cmd[1] == "api":
                return subprocess.CompletedProcess(
                    cmd, 0,
                    json.dumps([{"check_runs": [
                        {"name": "backend", "status": "completed", "conclusion": "success"}
                    ]}]),
                    "",
                )
            if cmd[1:3] == ["pr", "checks"]:
                return subprocess.CompletedProcess(
                    cmd, 0, json.dumps([{"name": "backend", "bucket": "pass", "state": "SUCCESS"}]), "",
                )
            if cmd[1:3] == ["pr", "view"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"headRefOid": sha}), "")
            raise AssertionError(cmd)
        monkeypatch.setattr(pr_checks.subprocess, "run", dispatch)

    def test_a_claimed_open_pr_with_concluded_checks_raises_one_event(self, tmp_path, monkeypatch):
        runtime = tmp_path / ".brr"
        _claim(runtime, "run-1", "evt-1", pr=5)
        self._fake_success(monkeypatch)
        rows = [{"number": 5, "state": "OPEN", "head_sha": "deadbeef"}]

        pr_checks.refresh(tmp_path, "acme/widget", rows, 5.0)

        assert rows[0]["checks"]["status"] == "completed"
        events = protocol.list_pending(runtime / "inbox")
        assert len(events) == 1
        ev = events[0]
        assert ev["source"] == "pr_checks_concluded"
        assert str(ev["pr"]) == "5"
        assert ev["sha"] == "deadbeef"
        assert ev["conclusion"] == "success"
        assert ev["spawn_message_for_event"] == "evt-1"

    def test_a_second_refresh_of_the_same_generation_raises_nothing_new(self, tmp_path, monkeypatch):
        runtime = tmp_path / ".brr"
        _claim(runtime, "run-1", "evt-1", pr=5)
        self._fake_success(monkeypatch)
        rows = [{"number": 5, "state": "OPEN", "head_sha": "deadbeef"}]

        pr_checks.refresh(tmp_path, "acme/widget", rows, 5.0)
        pr_checks.refresh(tmp_path, "acme/widget", list(rows), 5.0)

        assert len(protocol.list_pending(runtime / "inbox")) == 1

    def test_an_unclaimed_pr_is_never_read(self, tmp_path, monkeypatch):
        runtime = tmp_path / ".brr"
        runtime.mkdir()

        def fail(cmd, **kwargs):
            raise AssertionError(f"gh called for an unclaimed PR: {cmd}")
        monkeypatch.setattr(pr_checks.subprocess, "run", fail)

        rows = [{"number": 5, "state": "OPEN", "head_sha": "deadbeef"}]
        pr_checks.refresh(tmp_path, "acme/widget", rows, 5.0)
        assert "checks" not in rows[0]

    def test_a_closed_claimed_pr_is_skipped(self, tmp_path, monkeypatch):
        runtime = tmp_path / ".brr"
        _claim(runtime, "run-1", "evt-1", pr=5)

        def fail(cmd, **kwargs):
            raise AssertionError(f"gh called for a closed PR: {cmd}")
        monkeypatch.setattr(pr_checks.subprocess, "run", fail)

        rows = [{"number": 5, "state": "MERGED", "head_sha": "deadbeef"}]
        pr_checks.refresh(tmp_path, "acme/widget", rows, 5.0)
        assert "checks" not in rows[0]

    def test_a_row_with_no_head_sha_yet_is_skipped(self, tmp_path, monkeypatch):
        runtime = tmp_path / ".brr"
        _claim(runtime, "run-1", "evt-1", pr=5)

        def fail(cmd, **kwargs):
            raise AssertionError(f"gh called with no head_sha: {cmd}")
        monkeypatch.setattr(pr_checks.subprocess, "run", fail)

        rows = [{"number": 5, "state": "OPEN"}]
        pr_checks.refresh(tmp_path, "acme/widget", rows, 5.0)
        assert "checks" not in rows[0]

    def test_a_gh_failure_records_an_error_row_and_no_event(self, tmp_path, monkeypatch):
        runtime = tmp_path / ".brr"
        _claim(runtime, "run-1", "evt-1", pr=5)

        def dispatch(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "gh: rate limited")
        monkeypatch.setattr(pr_checks.subprocess, "run", dispatch)

        rows = [{"number": 5, "state": "OPEN", "head_sha": "deadbeef"}]
        pr_checks.refresh(tmp_path, "acme/widget", rows, 5.0)

        assert rows[0]["checks"]["status"] == "error"
        assert protocol.list_pending(runtime / "inbox") == []

    def test_no_claimed_prs_at_all_does_not_touch_gh(self, tmp_path, monkeypatch):
        runtime = tmp_path / ".brr"
        runtime.mkdir()

        def fail(cmd, **kwargs):
            raise AssertionError(f"gh called with no claims: {cmd}")
        monkeypatch.setattr(pr_checks.subprocess, "run", fail)

        pr_checks.refresh(tmp_path, "acme/widget", [{"number": 5, "state": "OPEN", "head_sha": "x"}], 5.0)
