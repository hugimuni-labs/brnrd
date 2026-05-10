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
