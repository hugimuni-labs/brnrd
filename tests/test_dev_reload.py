"""Tests for the developer reload watcher."""

from pathlib import Path

from brr import dev_reload
from brr.dev_reload import DevReloadWatcher


def test_watcher_detects_python_and_markdown_changes(tmp_path):
    package_dir = tmp_path / "src" / "brr"
    package_dir.mkdir(parents=True)
    module = package_dir / "daemon.py"
    docs = package_dir / "docs" / "execution-map.md"
    docs.parent.mkdir()
    module.write_text("old\n", encoding="utf-8")
    docs.write_text("old\n", encoding="utf-8")

    watcher = DevReloadWatcher(package_dir)
    assert watcher.changed() is False

    module.write_text("new module contents\n", encoding="utf-8")
    assert watcher.changed() is True
    assert watcher.changed() is False

    docs.write_text("new docs contents\n", encoding="utf-8")
    assert watcher.changed() is True


def test_watcher_detects_package_data_and_extra_paths(tmp_path):
    package_dir = tmp_path / "src" / "brr"
    package_dir.mkdir(parents=True)
    dockerfile = package_dir / "Dockerfile"
    pyproject = tmp_path / "pyproject.toml"
    dockerfile.write_text("FROM python:3.12\n", encoding="utf-8")
    pyproject.write_text("[project]\nname='brr'\n", encoding="utf-8")

    watcher = DevReloadWatcher(package_dir, extra_paths=[pyproject])
    assert watcher.changed() is False

    dockerfile.write_text("FROM python:3.13\n", encoding="utf-8")
    assert watcher.changed() is True

    pyproject.write_text(
        "[project]\nname='brr'\nversion='0.2'\n",
        encoding="utf-8",
    )
    assert watcher.changed() is True


def test_watcher_ignores_unloaded_file_types(tmp_path):
    package_dir = tmp_path / "src" / "brr"
    package_dir.mkdir(parents=True)
    (package_dir / "daemon.py").write_text("old\n", encoding="utf-8")

    watcher = DevReloadWatcher(package_dir)
    (package_dir / "scratch.tmp").write_text("ignored\n", encoding="utf-8")

    assert watcher.changed() is False


def test_for_repo_includes_source_layout_pyproject(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    package_dir = repo / "src" / "brr"
    package_dir.mkdir(parents=True)
    package_file = package_dir / "dev_reload.py"
    package_file.write_text("old\n", encoding="utf-8")
    pyproject = repo / "pyproject.toml"
    pyproject.write_text("[project]\nname='brr'\n", encoding="utf-8")

    monkeypatch.setattr(dev_reload, "__file__", str(package_file))
    watcher = DevReloadWatcher.for_repo(repo)
    assert watcher.changed() is False

    pyproject.write_text(
        "[project]\nname='brr'\nversion='0.2'\n",
        encoding="utf-8",
    )
    assert watcher.changed() is True


# ── Image staleness: the fingerprint a spawn's boot is honest about ───────────
#
# Regression cover for the 2026-07-13 false negative. A resident edits boot
# *code*, spawns a weak core to floor-test the change, and the child renders the
# *pre-edit* kernel — because the daemon assembles that child's whole prompt in
# its own (now superseded) process image, and the re-exec that would refresh it
# is waiting on the very resident doing the spawning. Silent, and read as a
# verdict on the new boot. These pin the tell.


def test_image_is_stale_false_without_a_captured_fingerprint():
    """An ad-hoc run is a fresh interpreter by construction — never stale."""
    dev_reload._IMAGE_FINGERPRINT = None
    assert dev_reload.image_is_stale() is False


def test_image_is_stale_after_package_code_changes(tmp_path, monkeypatch):
    pkg = tmp_path / "brr"
    pkg.mkdir()
    (pkg / "bootscore.py").write_text("KERNEL = 1\n", encoding="utf-8")
    monkeypatch.setattr(dev_reload, "__file__", str(pkg / "dev_reload.py"))

    dev_reload.capture_image_fingerprint()
    assert dev_reload.image_is_stale() is False

    (pkg / "bootscore.py").write_text("KERNEL = 2  # the fix\n", encoding="utf-8")
    assert dev_reload.image_is_stale() is True

    dev_reload._IMAGE_FINGERPRINT = None


def test_markdown_edits_do_not_make_the_image_stale(tmp_path, monkeypatch):
    """The distinction the whole fix rests on.

    ``prompts.py`` ``read_text()``s ``*.md`` on every assembly, so prose edits
    reach the next wake this daemon assembles whether or not it has re-execed.
    Reporting them as staleness would cry wolf on the most common edit in the
    repo — and a drift line that cries wolf trains the reader to skim the line
    that was meant to save it.
    """
    pkg = tmp_path / "brr"
    (pkg / "prompts").mkdir(parents=True)
    (pkg / "bootscore.py").write_text("KERNEL = 1\n", encoding="utf-8")
    (pkg / "prompts" / "run.md").write_text("orient.\n", encoding="utf-8")
    monkeypatch.setattr(dev_reload, "__file__", str(pkg / "dev_reload.py"))

    dev_reload.capture_image_fingerprint()
    (pkg / "prompts" / "run.md").write_text("orient, then act.\n", encoding="utf-8")

    assert dev_reload.image_is_stale() is False

    dev_reload._IMAGE_FINGERPRINT = None


def test_edit_then_revert_is_not_stale(tmp_path, monkeypatch):
    """Content-hashed, not mtime-stamped — the image is what it *imported*.

    Under an mtime fingerprint a resident who edits a file and reverts it would
    be warned for the rest of the daemon's life, on a wake whose image matches
    the checkout byte for byte. A drift line that cries wolf is worse than none:
    it trains the reader to skim the exact line that exists to save them.
    """
    pkg = tmp_path / "brr"
    pkg.mkdir()
    original = "KERNEL = 1\n"
    (pkg / "bootscore.py").write_text(original, encoding="utf-8")
    monkeypatch.setattr(dev_reload, "__file__", str(pkg / "dev_reload.py"))

    dev_reload.capture_image_fingerprint()
    (pkg / "bootscore.py").write_text("KERNEL = 2\n", encoding="utf-8")
    assert dev_reload.image_is_stale() is True

    (pkg / "bootscore.py").write_text(original, encoding="utf-8")
    assert dev_reload.image_is_stale() is False

    dev_reload._IMAGE_FINGERPRINT = None


# ── Changed-path tracking for the pre-exec breadcrumb (#421) ────────────────


def test_last_changed_is_empty_before_any_change_is_detected(tmp_path):
    """A freshly-constructed watcher has no last_changed entries."""
    pkg = tmp_path / "brr"
    pkg.mkdir()
    (pkg / "daemon.py").write_text("v1\n", encoding="utf-8")

    watcher = DevReloadWatcher(pkg)
    assert watcher.last_changed == []


def test_changed_records_which_files_differed(tmp_path):
    """last_changed names the files that actually changed, not the full set."""
    pkg = tmp_path / "brr"
    pkg.mkdir()
    (pkg / "daemon.py").write_text("v1\n", encoding="utf-8")
    (pkg / "runner.py").write_text("v1\n", encoding="utf-8")

    watcher = DevReloadWatcher(pkg)
    assert watcher.changed() is False

    (pkg / "daemon.py").write_text("v2\n", encoding="utf-8")
    assert watcher.changed() is True

    changed = watcher.last_changed
    assert any("daemon.py" in k for k in changed), f"daemon.py missing from {changed}"
    assert all("runner.py" not in k for k in changed), (
        f"unmodified runner.py should not appear in {changed}"
    )


def test_last_changed_clears_after_no_change(tmp_path):
    """After a quiet poll cycle last_changed reflects the new (empty) diff."""
    pkg = tmp_path / "brr"
    pkg.mkdir()
    (pkg / "daemon.py").write_text("v1\n", encoding="utf-8")

    watcher = DevReloadWatcher(pkg)
    (pkg / "daemon.py").write_text("v2\n", encoding="utf-8")
    assert watcher.changed() is True
    assert watcher.last_changed  # non-empty

    # Second call: no new changes — snapshot advances, last_changed resets.
    assert watcher.changed() is False
    assert watcher.last_changed == []


def test_added_and_deleted_files_appear_in_last_changed(tmp_path):
    """Additions and removals are both changes that should appear in last_changed."""
    pkg = tmp_path / "brr"
    pkg.mkdir()
    (pkg / "keeper.py").write_text("stays\n", encoding="utf-8")
    new_file = pkg / "fresh.py"

    watcher = DevReloadWatcher(pkg)

    new_file.write_text("new\n", encoding="utf-8")
    assert watcher.changed() is True
    assert any("fresh.py" in k for k in watcher.last_changed)

    new_file.unlink()
    assert watcher.changed() is True
    assert any("fresh.py" in k for k in watcher.last_changed)


# ── Pre-exec breadcrumb (#421) ──────────────────────────────────────────────


def test_breadcrumb_names_the_event_as_dev_reload():
    """The breadcrumb text must say "dev-reload" so it's distinguishable from a crash."""
    bc = dev_reload.format_dev_reload_breadcrumb(["package/daemon.py"])
    assert "dev-reload" in bc


def test_breadcrumb_shows_short_path_not_prefix(tmp_path):
    """The display key strips "package/" so the breadcrumb reads as a filename."""
    bc = dev_reload.format_dev_reload_breadcrumb(["package/daemon.py", "package/runner.py"])
    assert "daemon.py" in bc
    assert "runner.py" in bc
    # The internal prefix is not shown verbatim.
    assert "package/daemon.py" not in bc


def test_breadcrumb_is_bounded_to_max_files():
    """When many files change the breadcrumb shows at most _MAX_BREADCRUMB_FILES paths."""
    import re

    files = [f"package/mod{i}.py" for i in range(20)]
    bc = dev_reload.format_dev_reload_breadcrumb(files)
    # The "+N more" tail must appear.
    assert "+15 more" in bc
    # Exactly _MAX_BREADCRUMB_FILES file names appear in the visible portion.
    shown = re.findall(r'mod\d+\.py', bc)
    assert len(shown) == dev_reload._MAX_BREADCRUMB_FILES


def test_breadcrumb_deduplicates_repeated_keys():
    """Duplicate paths from multiple watcher firings should appear only once."""
    files = ["package/daemon.py", "package/daemon.py", "package/runner.py"]
    bc = dev_reload.format_dev_reload_breadcrumb(files)
    # "daemon.py" appears exactly once.
    assert bc.count("daemon.py") == 1


def test_breadcrumb_handles_empty_changed_list():
    """An empty changed list (unlikely but defensive) doesn't crash."""
    bc = dev_reload.format_dev_reload_breadcrumb([])
    assert "dev-reload" in bc
    assert "changed:" not in bc
