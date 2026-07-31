"""``scripts/stamp_build_info.py`` — the one writer of ``build_info.txt``.

The 2026-07-30 shadow-deploy incident: two inline stampers existed (Upsun
build hook, backend Dockerfile), the three-line honesty fix reached one of
them, and every container image shipped a two-line stamp that
``version_info.build_info()`` correctly refused to read a commit from —
``/v1/stats/version`` answered ``commit: null`` for an image whose build arg
carried the exact sha. These tests pin the repaired shape: one script, both
deploy surfaces calling it, and the stamp actually round-tripping through
the reader.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from brnrd import version_info

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "stamp_build_info.py"


def _load():
    spec = importlib.util.spec_from_file_location("stamp_build_info_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_arg_commit_round_trips_through_the_reader(tmp_path, monkeypatch):
    """A CI-built image (BRNRD_BUILD_COMMIT set) must report its commit."""
    monkeypatch.setenv("BRNRD_BUILD_COMMIT", "a" * 40)
    dest = tmp_path / "build_info.txt"

    _load().stamp(dest)

    lines = dest.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3, "the stamp is three lines: identity, built_at, source"
    assert lines[2] == "git"

    monkeypatch.setattr(version_info, "_BUILD_INFO_PATH", dest)
    info = version_info.build_info()
    assert info["commit"] == "a" * 40
    assert info["built_at"] == lines[1]


def test_gitless_tree_resolves_to_platform_tree_id(tmp_path, monkeypatch):
    """The Upsun exported tree: no build arg, no .git — tree id, marked ``tree``."""
    monkeypatch.delenv("BRNRD_BUILD_COMMIT", raising=False)
    monkeypatch.setenv("PLATFORM_TREE_ID", "tree-id-value")
    monkeypatch.chdir(tmp_path)  # git rev-parse fails outside any repo
    dest = tmp_path / "build_info.txt"

    _load().stamp(dest)

    value, _built_at, source = dest.read_text(encoding="utf-8").splitlines()
    assert (value, source) == ("tree-id-value", "tree")

    monkeypatch.setattr(version_info, "_BUILD_INFO_PATH", dest)
    assert version_info.build_info()["commit"] is None, (
        "a tree id must never be reported as a commit — that is the exact "
        "dishonesty the third line exists to end"
    )


def test_unknown_identity_stays_absent_not_fabricated(tmp_path, monkeypatch):
    monkeypatch.delenv("BRNRD_BUILD_COMMIT", raising=False)
    monkeypatch.delenv("PLATFORM_TREE_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    dest = tmp_path / "build_info.txt"

    _load().stamp(dest)

    lines = dest.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ""

    monkeypatch.setattr(version_info, "_BUILD_INFO_PATH", dest)
    info = version_info.build_info()
    assert info["commit"] is None
    assert info["built_at"] is not None


def test_both_deploy_surfaces_call_the_one_stamper():
    """Neither deploy surface may grow its own inline stamp again.

    The wiring is asserted on both sides: each surface invokes the script,
    and neither writes ``build_info.txt`` by hand anymore.
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    upsun = (REPO_ROOT / ".upsun" / "config.yaml").read_text(encoding="utf-8")

    assert "COPY scripts/stamp_build_info.py" in dockerfile
    assert "python scripts/stamp_build_info.py" in dockerfile
    assert "python scripts/stamp_build_info.py" in upsun

    # The resurrection tell is an inline *writer*, not a mention: comments may
    # (and do) name build_info.txt, but only a revived inline stamper would
    # bring ``write_text`` back into a deploy surface.
    for name, text in (("Dockerfile", dockerfile), (".upsun/config.yaml", upsun)):
        assert "write_text" not in text, (
            f"{name} writes a file from inline python again — the incident "
            f"this guards against is two build_info stampers drifting apart"
        )


def test_publish_workflow_still_passes_the_build_commit():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "publish-container.yml"
    ).read_text(encoding="utf-8")
    assert "BRNRD_BUILD_COMMIT=${{ github.sha }}" in workflow


def test_publish_workflow_deploy_tail_is_guarded_and_loud():
    """The deploy tail rolls out only with a target, and never skips silently.

    This assertion used to read ``assert "/deploy" in workflow`` — and that
    string was the bug. Chaining ``POST /deploy`` after ``PATCH`` is the
    documented cause of the ``409 transient_state`` that failed both of
    #894's live runs, so the guard was pinning the defect as expected and
    going green over it. Matching source text cannot tell a call that is
    required from one that must not exist; what this test actually cares
    about is that a rollout *happens*, that its failure is red, and that an
    absent target is visible. The rollout itself is behaviour-tested in
    ``tests/test_scw_rollout.py``.
    """
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "publish-container.yml"
    ).read_text(encoding="utf-8")
    assert "SCW_CONTAINER_ID" in workflow
    # a rollout is attempted at all, and by the one script that owns it
    assert "scripts/scw_rollout.py" in workflow
    # rollout failure is a red step, not a green headline (#892's rule)
    assert "::error::container deploy failed" in (
        REPO_ROOT / "scripts" / "scw_rollout.py"
    ).read_text(encoding="utf-8")
    # and an absent target is a visible notice, not a silent skip (#891's rule)
    assert "::notice::Image mirrored but not deployed" in workflow
