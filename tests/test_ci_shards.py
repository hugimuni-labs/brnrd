"""CI's backend shards partition the suite (ci-speed, 2026-10-28).

The hook lives in the root conftest.py; ci.yml sets ``PYTEST_SHARD=k/N`` per
matrix job. A test that ran on two shards costs time; one that ran on none
is a test CI silently stopped running.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _root_conftest():
    spec = importlib.util.spec_from_file_location("root_conftest_under_test", REPO_ROOT / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("total", [1, 2, 3, 7])
def test_shards_partition_the_items(total):
    shard_items = _root_conftest().shard_items
    items = list(range(100))
    picked = [shard_items(items, f"{k}/{total}") for k in range(1, total + 1)]
    assert sorted(x for shard in picked for x in shard) == items
    assert max(map(len, picked)) - min(map(len, picked)) <= 1


@pytest.mark.parametrize("spec", ["0/3", "4/3", "3", "a/b"])
def test_a_malformed_shard_is_an_error_not_an_empty_run(spec):
    with pytest.raises(ValueError):
        _root_conftest().shard_items([1, 2, 3], spec)


def test_the_workflow_matrix_matches_its_shard_count():
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    backend = workflow["jobs"]["backend"]
    shards = backend["strategy"]["matrix"]["shard"]
    assert shards == list(range(1, len(shards) + 1))
    assert backend["env"]["PYTEST_SHARD"].endswith(f"/{len(shards)}")
    aggregator = [job for job in workflow["jobs"].values() if job.get("name") == "Backend tests"]
    assert len(aggregator) == 1 and aggregator[0]["needs"] == "backend"


def test_the_hook_selects_through_a_real_collection(tmp_path, monkeypatch):
    """Through pytest itself: shard 2/3 of nine tests collects three."""
    (tmp_path / "conftest.py").write_text((REPO_ROOT / "conftest.py").read_text())
    (tmp_path / "test_nine.py").write_text(
        "import pytest\n@pytest.mark.parametrize('i', range(9))\ndef test_n(i): pass\n"
    )
    monkeypatch.setenv("PYTEST_SHARD", "2/3")
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:xdist", "-p", "no:cacheprovider", "--collect-only", "-q", str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True,
    ).stdout
    assert "test_n[1]" in out and "test_n[4]" in out and "test_n[7]" in out
    assert "3/9 tests collected (6 deselected)" in out
