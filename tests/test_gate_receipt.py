"""The shipped gate-receipt writer (`brr.gate_receipt`).

`hooks.gate_command` arms a Stop-hook obligation that reads
`.gate-receipt.json`, but nothing under `src/brr/` ever wrote one — only
this repo's own unshipped `scripts/gate.py` did (kb/design-io-layer-trim.md,
THE OBLIGATION NOTHING CAN SATISFY). These tests drive the shipped writer
directly, then `tests/test_cli.py::test_gate_run_*` drives it through the
real `brnrd gate-run` entry point a resident actually invokes.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from brr import gate_receipt


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    """A git repo with one commit, one tracked edit, one untracked file —
    the same fixture shape `scripts/gate.py`'s own test suite uses."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "tracked.py").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    (repo / "tracked.py").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "brand-new.py").write_text("never committed\n", encoding="utf-8")
    return repo


# ── tree_referents / untracked_digest ────────────────────────────────


def test_untracked_digest_is_content_sensitive_not_just_name_sensitive(tmp_path):
    repo = _repo(tmp_path)
    first = gate_receipt.untracked_digest(repo)
    assert first != ""

    (repo / "brand-new.py").write_text("edited\n", encoding="utf-8")
    assert gate_receipt.untracked_digest(repo) != first


def test_untracked_digest_empty_when_nothing_untracked(tmp_path):
    repo = tmp_path / "clean"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    assert gate_receipt.untracked_digest(repo) == ""


def test_tree_referents_none_outside_a_git_repo(tmp_path):
    stray = tmp_path / "not-a-repo"
    stray.mkdir()
    assert gate_receipt.tree_referents(stray) is None


def test_tree_referents_carries_all_four_fields(tmp_path):
    repo = _repo(tmp_path)
    referents = gate_receipt.tree_referents(repo)
    assert referents is not None
    assert set(referents) == {"head", "status", "diff_digest", "untracked_digest"}
    assert referents["untracked_digest"] != ""


# ── write_receipt / run_and_write_receipt ────────────────────────────


def test_write_receipt_lands_beside_the_run_and_names_the_verdict(tmp_path):
    repo = _repo(tmp_path)
    outbox = tmp_path / "outbox"
    path = gate_receipt.write_receipt(
        outbox, repo, verdict="RED", command="make test", run_id="run-1", seconds=3.2,
    )
    assert path == outbox / gate_receipt.RECEIPT_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    # RED is recorded, not suppressed — the obligation is "ran", not "green".
    assert payload["verdict"] == "RED"
    assert payload["gate_command"] == "make test"
    assert payload["run_id"] == "run-1"
    assert payload["seconds"] == 3.2
    assert set(payload) >= {"head", "status", "diff_digest", "untracked_digest"}


def test_write_receipt_none_outside_a_git_repo(tmp_path):
    stray = tmp_path / "not-a-repo"
    stray.mkdir()
    outbox = tmp_path / "outbox"
    assert gate_receipt.write_receipt(
        outbox, stray, verdict="GREEN", command="true") is None
    assert not outbox.exists()  # no partial write on the failure path


def test_run_and_write_receipt_green_forwards_zero_exit(tmp_path, capsys):
    repo = _repo(tmp_path)
    outbox = tmp_path / "outbox"
    rc = gate_receipt.run_and_write_receipt(repo, outbox, "true", run_id="run-2")
    assert rc == 0
    payload = json.loads((outbox / gate_receipt.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert payload["verdict"] == "GREEN"
    assert payload["gate_command"] == "true"
    assert "receipt" in capsys.readouterr().out


def test_run_and_write_receipt_red_forwards_nonzero_exit(tmp_path):
    repo = _repo(tmp_path)
    outbox = tmp_path / "outbox"
    rc = gate_receipt.run_and_write_receipt(repo, outbox, "false")
    assert rc != 0
    payload = json.loads((outbox / gate_receipt.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert payload["verdict"] == "RED"


def test_run_and_write_receipt_matches_the_tree_it_ran_on(tmp_path):
    """The receipt this writer produces must be exactly what
    `hooks._gate_closeout_clause` compares against — same git output, same
    digest — for any adopter's command, not just this repo's own."""
    repo = _repo(tmp_path)
    outbox = tmp_path / "outbox"
    gate_receipt.run_and_write_receipt(repo, outbox, "true")
    payload = json.loads((outbox / gate_receipt.RECEIPT_NAME).read_text(encoding="utf-8"))

    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert payload["head"] == head
    assert payload["status"] == status
    assert payload["untracked_digest"] == gate_receipt.untracked_digest(repo)
