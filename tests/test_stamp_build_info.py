"""``scripts/stamp_build_info.py`` — the one writer of ``build_info.txt``.

The 2026-07-30 shadow-deploy incident: two inline stampers existed (the PaaS
build hook, the backend Dockerfile), the three-line honesty fix reached one of
them, and every container image shipped a two-line stamp that
``version_info.build_info()`` correctly refused to read a commit from —
``/v1/stats/version`` answered ``commit: null`` for an image whose build arg
carried the exact sha. These tests pin the repaired shape: one script, every
deploy surface calling it (since 2026-07-31 the container image is the only
one), and the stamp actually round-tripping through the reader.
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


def test_gitless_build_resolves_to_an_empty_identity(tmp_path, monkeypatch):
    """No build arg and no ``.git``: nothing is claimed, on either line.

    Until 2026-07-31 this rung fell through to the PaaS-exported tree id and
    stamped it with source ``tree``; that host is gone and so is the rung. The
    replacement is an empty identity with an empty source — the returned pair
    is asserted directly rather than inferred from the setup, so a build tree
    that turns out to *be* a repo fails here instead of passing by accident.
    """
    monkeypatch.delenv("BRNRD_BUILD_COMMIT", raising=False)
    monkeypatch.chdir(tmp_path)  # git rev-parse fails outside any repo
    module = _load()

    assert module.resolve_identity() == ("", "")

    dest = tmp_path / "build_info.txt"
    module.stamp(dest)
    value, _built_at, source = dest.read_text(encoding="utf-8").splitlines()
    assert (value, source) == ("", "")

    monkeypatch.setattr(version_info, "_BUILD_INFO_PATH", dest)
    assert version_info.build_info()["commit"] is None, (
        "an unidentified build must never report a commit — that is the exact "
        "dishonesty the third line exists to end"
    )


def test_unknown_identity_stays_absent_not_fabricated(tmp_path, monkeypatch):
    monkeypatch.delenv("BRNRD_BUILD_COMMIT", raising=False)
    monkeypatch.chdir(tmp_path)
    dest = tmp_path / "build_info.txt"

    _load().stamp(dest)

    lines = dest.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ""

    monkeypatch.setattr(version_info, "_BUILD_INFO_PATH", dest)
    info = version_info.build_info()
    assert info["commit"] is None
    assert info["built_at"] is not None


def test_the_container_image_calls_the_one_stamper():
    """The only deploy surface left must call the script, not restamp inline.

    Load-bearing rather than tidy: an image built without this step ships a
    ``build_info.txt`` that is absent or stale, and ``/v1/stats/version``
    answers ``commit: null`` — the 2026-07-30 incident exactly. The PaaS build
    hook that used to be the second caller was deleted with ``.upsun/`` on
    2026-07-31, so there is one surface to check and one to keep honest.
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY scripts/stamp_build_info.py" in dockerfile
    assert "python scripts/stamp_build_info.py" in dockerfile

    # The resurrection tell is an inline *writer*, not a mention: comments may
    # (and do) name build_info.txt, but only a revived inline stamper would
    # bring ``write_text`` back into a deploy surface.
    assert "write_text" not in dockerfile, (
        "the Dockerfile writes a file from inline python again — the incident "
        "this guards against is two build_info stampers drifting apart"
    )


def test_publish_workflow_still_passes_the_build_commit():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "publish-container.yml"
    ).read_text(encoding="utf-8")
    assert "BRNRD_BUILD_COMMIT=${{ github.sha }}" in workflow


def test_publish_workflow_deploy_tail_is_guarded_and_loud():
    """The deploy tail rolls out only with a target, and never skips silently."""
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "publish-container.yml"
    ).read_text(encoding="utf-8")
    assert "SCW_CONTAINER_ID" in workflow
    assert "/deploy" in workflow
    # rollout failure is a red step, not a green headline (#892's rule)
    assert "::error::container deploy failed" in workflow
    # and an absent target is a visible notice, not a silent skip (#891's rule)
    assert "::notice::Image mirrored but not deployed" in workflow
