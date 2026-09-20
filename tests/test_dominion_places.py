"""Filesystem-level coverage for move 2(a)'s derived dominion rooms."""

from __future__ import annotations

import os
from pathlib import Path

from brr import dominion


def _repo(label: str, path: Path) -> dict[str, str]:
    return {"kind": "repo", "label": label, "path": str(path)}


def _prepare(home: Path, *labels: str) -> list[dict[str, str]]:
    repos = []
    for label in labels:
        checkout = home.parent / label.replace("/", "-")
        checkout.mkdir(parents=True)
        (home / "knowledge" / "repos" / label.replace("/", "__")).mkdir(
            parents=True, exist_ok=True)
        repos.append(_repo(label, checkout))
    (home / "knowledge" / "global").mkdir(parents=True, exist_ok=True)
    return repos


def test_mount_places_creates_two_rooms_with_exact_link_targets(tmp_path):
    home = tmp_path / "home"
    repos = _prepare(home, "hugimuni-labs/brnrd", "hugimuni-labs/hugimuni")

    report = dominion.mount_places(
        home, [{"kind": "home", "label": "home", "path": str(home)}, *repos], apply=True)

    brnrd = home / "dominion" / "places" / "brnrd"
    hugimuni = home / "dominion" / "places" / "hugimuni"
    assert os.readlink(brnrd / "repo") == repos[0]["path"]
    assert os.readlink(hugimuni / "repo") == repos[1]["path"]
    assert os.readlink(brnrd / "kb") == "../../../knowledge/repos/hugimuni-labs__brnrd"
    assert os.readlink(hugimuni / "kb") == "../../../knowledge/repos/hugimuni-labs__hugimuni"
    assert os.readlink(home / "dominion" / "kb") == "../knowledge/global"
    assert not report.refusal


def test_mount_places_second_apply_is_idempotent(tmp_path):
    home = tmp_path / "home"
    repos = _prepare(home, "hugimuni-labs/brnrd")
    dominion.mount_places(home, repos, apply=True)
    target = os.readlink(home / "dominion" / "places" / "brnrd" / "repo")

    report = dominion.mount_places(home, repos, apply=True)

    assert not report.changed
    assert "dominion/places/brnrd/repo" in report.unchanged
    assert os.readlink(home / "dominion" / "places" / "brnrd" / "repo") == target


def test_mount_places_dry_run_describes_the_pass_without_writing(tmp_path):
    home = tmp_path / "home"
    repos = _prepare(home, "hugimuni-labs/brnrd")

    report = dominion.mount_places(home, repos, apply=False)

    assert "dominion/places/brnrd/repo" in report.created
    assert not (home / "dominion").exists()
    assert not (home / ".gitignore").exists()


def test_mount_places_repairs_link_but_leaves_real_file(tmp_path):
    home = tmp_path / "home"
    repos = _prepare(home, "hugimuni-labs/brnrd")
    dominion.mount_places(home, repos, apply=True)
    room = home / "dominion" / "places" / "brnrd"
    repo_link = room / "repo"
    repo_link.unlink()
    repo_link.symlink_to("/wrong")
    kb_link = room / "kb"
    kb_link.unlink()
    kb_link.write_text("do not replace\n", encoding="utf-8")

    report = dominion.mount_places(home, repos, apply=True)

    assert os.readlink(repo_link) == repos[0]["path"]
    assert kb_link.read_text(encoding="utf-8") == "do not replace\n"
    assert "dominion/places/brnrd/repo" in report.repaired
    assert "dominion/places/brnrd/kb" in report.blocked


def test_mount_places_never_creates_or_touches_notes(tmp_path):
    home = tmp_path / "home"
    repos = _prepare(home, "hugimuni-labs/brnrd", "hugimuni-labs/hugimuni")
    kept = home / "dominion" / "places" / "brnrd"
    kept.mkdir(parents=True)
    notes = kept / "notes.md"
    notes.write_text("resident territory\n", encoding="utf-8")

    dominion.mount_places(home, repos, apply=True)

    assert notes.read_text(encoding="utf-8") == "resident territory\n"
    assert not (home / "dominion" / "places" / "hugimuni" / "notes.md").exists()


def test_mount_places_refuses_duplicate_short_names_before_writing(tmp_path):
    home = tmp_path / "home"
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()

    report = dominion.mount_places(
        home, [_repo("one/brnrd", one), _repo("two/brnrd", two)], apply=True)

    assert report.refusal
    assert not (home / "dominion").exists()
    assert not (home / ".gitignore").exists()


def test_mount_places_links_missing_kb_without_creating_it(tmp_path):
    home = tmp_path / "home"
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    report = dominion.mount_places(home, [_repo("hugimuni-labs/brnrd", checkout)], apply=True)

    kb = home / "dominion" / "places" / "brnrd" / "kb"
    assert kb.is_symlink()
    assert not kb.exists()
    assert "dominion/places/brnrd/kb" in report.dangling
    assert not (home / "knowledge" / "repos" / "hugimuni-labs__brnrd").exists()
