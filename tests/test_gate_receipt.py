"""The shipped gate-receipt writer (`brr.gate_receipt`).

`hooks.gate_command` arms a Stop-hook obligation that reads
`.gate-receipts.json`, but nothing under `src/brr/` ever wrote one — only
this repo's own unshipped `scripts/gate.py` did (kb/design-io-layer-trim.md,
THE OBLIGATION NOTHING CAN SATISFY). These tests drive the shipped writer
directly, then `tests/test_cli.py::test_gate_run_*` drives it through the
real `brnrd gate-run` entry point a resident actually invokes.

The file is a map keyed per tree (`gate_receipt.tree_key`, #820) — one run
can gate more than one tree (this repo's own `host` pattern: a scratch
`git worktree add`, then the checkout, both under one `BRR_OUTBOX_DIR`), and
a single-object receipt let the second, correct gate destroy the first. The
"── two trees, one outbox ──" section below drives exactly that shape.
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
    data = json.loads(path.read_text(encoding="utf-8"))
    # The file is a map keyed by tree, not the receipt itself (#820) — this
    # repo's own entry is looked up like any reader would.
    entry = data[gate_receipt.tree_key(repo)]
    # RED is recorded, not suppressed — the obligation is "ran", not "green".
    assert entry["verdict"] == "RED"
    assert entry["gate_command"] == "make test"
    assert entry["run_id"] == "run-1"
    assert entry["seconds"] == 3.2
    assert set(entry) >= {"head", "status", "diff_digest", "untracked_digest"}
    # And the public reader agrees with a hand-indexed lookup.
    assert gate_receipt.read_receipt(outbox, repo) == entry


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
    entry = gate_receipt.read_receipt(outbox, repo)
    assert entry["verdict"] == "GREEN"
    assert entry["gate_command"] == "true"
    assert "receipt" in capsys.readouterr().out


def test_run_and_write_receipt_red_forwards_nonzero_exit(tmp_path):
    repo = _repo(tmp_path)
    outbox = tmp_path / "outbox"
    rc = gate_receipt.run_and_write_receipt(repo, outbox, "false")
    assert rc != 0
    entry = gate_receipt.read_receipt(outbox, repo)
    assert entry["verdict"] == "RED"


def test_run_and_write_receipt_matches_the_tree_it_ran_on(tmp_path):
    """The receipt this writer produces must be exactly what
    `hooks._gate_closeout_clause` compares against — same git output, same
    digest — for any adopter's command, not just this repo's own."""
    repo = _repo(tmp_path)
    outbox = tmp_path / "outbox"
    gate_receipt.run_and_write_receipt(repo, outbox, "true")
    entry = gate_receipt.read_receipt(outbox, repo)

    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert entry["head"] == head
    assert entry["status"] == status
    assert entry["untracked_digest"] == gate_receipt.untracked_digest(repo)


# ── two trees, one outbox (#820) ───────────────────────────────────────


def test_two_trees_gated_under_one_outbox_both_survive(tmp_path):
    """The exact defect: one run gates a scratch worktree, then gates the
    checkout too, both writing into the same `BRR_OUTBOX_DIR`. Before #820 the
    second `write_receipt` call clobbered the first — the correct, earned
    receipt for the first tree was gone, and its only recovery was re-running
    the whole gate. Each tree now gets its own map entry."""
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    first = _repo(tmp_path / "first")
    second = _repo(tmp_path / "second")
    outbox = tmp_path / "outbox"

    gate_receipt.write_receipt(
        outbox, first, verdict="GREEN", command="cmd-a", run_id="run-1",
    )
    gate_receipt.write_receipt(
        outbox, second, verdict="RED", command="cmd-b", run_id="run-1",
    )

    first_entry = gate_receipt.read_receipt(outbox, first)
    second_entry = gate_receipt.read_receipt(outbox, second)
    assert first_entry["verdict"] == "GREEN"
    assert first_entry["gate_command"] == "cmd-a"
    assert second_entry["verdict"] == "RED"
    assert second_entry["gate_command"] == "cmd-b"
    # The map really does carry both keys, not just "whichever read wins".
    data = json.loads((outbox / gate_receipt.RECEIPT_NAME).read_text(encoding="utf-8"))
    assert set(data) == {gate_receipt.tree_key(first), gate_receipt.tree_key(second)}


def test_a_receipt_for_a_different_tree_is_neither_pass_nor_fail(tmp_path):
    """A reader asking about *its own* tree must never be answered by another
    tree's entry — the class of bug #820 names: a referent filed under the
    wrong key. `stray` was never gated; `first`'s entry existing beside it in
    the same file must not change that."""
    (tmp_path / "first").mkdir()
    (tmp_path / "stray").mkdir()
    first = _repo(tmp_path / "first")
    stray = _repo(tmp_path / "stray")
    outbox = tmp_path / "outbox"

    gate_receipt.write_receipt(outbox, first, verdict="GREEN", command="cmd-a")

    assert gate_receipt.read_receipt(outbox, stray) is None
    assert gate_receipt.read_receipt(outbox, first) is not None


def test_tree_key_is_a_digest_not_the_path(tmp_path):
    """The map key must never be the raw repo path: `.gate-receipts.json` is
    copied verbatim onto a published run node (`daemon.PRESERVED`), and that
    surface is checked to carry no absolute host path. Keying by the literal
    resolved path (the shape #820's own spec first suggested) would put every
    gated tree's filesystem layout there instead."""
    repo = _repo(tmp_path)
    key = gate_receipt.tree_key(repo)
    assert str(repo) not in key
    assert str(repo.resolve()) not in key
    # Deterministic and content-addressed: the same resolved tree, reached by
    # two different spellings of its path, must land on one key.
    assert gate_receipt.tree_key(Path(str(repo) + "/.")) == key
