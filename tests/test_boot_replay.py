"""Boot replay harness — Slice 1 of the native boot sequence.

Purpose
-------
Drive ``build_daemon_prompt`` (and its scored variant) through a versioned
fixture and snapshot the rendered output for both the ``claude`` and ``codex``
runner renderings.  The snapshots are the acceptance test that prompt
assembly remains semantically green across refactors.

Regenerating snapshots
----------------------
Run with the ``BOOT_UPDATE_SNAPSHOTS=1`` environment variable to regenerate
all snapshot files from the production functions:

    BOOT_UPDATE_SNAPSHOTS=1 pytest tests/test_boot_replay.py -v

The snapshots live in ``tests/fixtures/boot/``:

- ``fixture_v1.json``          — versioned fixture inputs (this is the source)
- ``snapshot_claude_v1.txt``   — captured against prompt schema v1 (claude runner)
- ``snapshot_codex_v1.txt``    — captured against prompt schema v1 (codex runner)

A snapshot edited by hand will be replaced on the next regeneration run;
the diff in ``git show`` shows any change to the rendered output.

Phase outputs
-------------
The harness captures two phases per runner:

1. ``prompt``  — the full rendered daemon prompt text (from ``build_daemon_prompt``)
2. ``manifest`` — the human-readable boot source manifest (from ``format_manifest``)

Both are written to the snapshot file, separated by a ``--- phase: manifest ---``
marker, so the snapshot is a single self-contained file.

Schema version
--------------
Captured against prompt schema v1 (``bootscore.SCHEMA_VERSION == "1"``).
If the schema version advances, rename the snapshot files and update this
docstring.  The fixture version (``fixture_v1.json``) is independent —
change the fixture inputs and regenerate without bumping the schema.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "boot"
FIXTURE_FILE = FIXTURES_DIR / "fixture_v1.json"
SNAPSHOT_CLAUDE = FIXTURES_DIR / "snapshot_claude_v1.txt"
SNAPSHOT_CODEX = FIXTURES_DIR / "snapshot_codex_v1.txt"

_PHASE_SEP = "--- phase: manifest ---\n"
_UPDATE = os.environ.get("BOOT_UPDATE_SNAPSHOTS", "").strip() in ("1", "true", "yes")


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))


def _build_kwargs(fixture: dict[str, Any], runner_key: str) -> dict[str, Any]:
    """Merge shared kwargs + runner-specific overrides."""
    shared: dict[str, Any] = dict(fixture["shared"])
    runner_overrides: dict[str, Any] = fixture["runners"][runner_key]
    shared.update(runner_overrides)
    return shared


def _run_phases(
    repo_root: Path, task: str, kwargs: dict[str, Any]
) -> tuple[str, str]:
    """Run both prompt-assembly phases and return (prompt_text, manifest_text)."""
    from brr.prompts import build_daemon_prompt_with_score
    from brr.bootscore import format_manifest

    kw = dict(kwargs)
    task_val = kw.pop("task", task)
    event_id = kw.pop("event_id")
    response_path = kw.pop("response_path")

    prompt, score = build_daemon_prompt_with_score(
        task_val, event_id, response_path, repo_root, **kw
    )
    manifest = format_manifest(score)
    return prompt, manifest


def _normalize(text: str, repo_root: Path) -> str:
    """Replace the repo-root path with a stable placeholder.

    The prompt includes ``repo_root`` in the Run Context Bundle (``Execution
    root:`` line etc.).  Since ``empty_repo`` creates a fresh ``tmp_path``
    per test run, raw paths would make the snapshot non-reproducible.
    Replacing them with ``{REPO_ROOT}`` before storing / comparing makes the
    snapshot stable while still exercising all code paths.
    """
    return text.replace(str(repo_root), "{REPO_ROOT}")


def _snapshot_text(prompt: str, manifest: str, runner_key: str, repo_root: Path) -> str:
    """Compose the snapshot file content."""
    header = (
        f"# Boot snapshot — runner: {runner_key} — prompt schema v{_schema_version()}\n"
        "# Captured by the boot replay harness; regenerate with:\n"
        "#   BOOT_UPDATE_SNAPSHOTS=1 pytest tests/test_boot_replay.py -v\n"
        "# DO NOT EDIT BY HAND — regeneration is the only sanctioned path.\n"
        "\n"
    )
    normalized_prompt = _normalize(prompt, repo_root)
    return header + normalized_prompt + "\n\n" + _PHASE_SEP + "\n" + manifest


def _schema_version() -> str:
    from brr.bootscore import SCHEMA_VERSION
    return SCHEMA_VERSION


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def empty_repo(tmp_path):
    """A minimal git repo without any dominion or kb, for reproducible output."""
    from _helpers import init_git_repo
    repo = tmp_path / "repo"
    init_git_repo(repo)
    # Ensure no .brr/config that could pull in home knowledge or dominion
    (repo / ".brr").mkdir()
    (repo / ".brr" / "config").write_text("", encoding="utf-8")
    return repo


# ── Snapshot tests ────────────────────────────────────────────────────────────


class TestBootReplay:
    """Drive the production prompt builder through the versioned fixture.

    These are snapshot tests: on first run (or with BOOT_UPDATE_SNAPSHOTS=1)
    they write the snapshot; on subsequent runs they compare.  A diff means
    the prompt or manifest changed — intentional or not.
    """

    def test_claude_snapshot(self, empty_repo):
        """Claude runner rendering — captured against prompt schema v1."""
        fixture = _load_fixture()
        kwargs = _build_kwargs(fixture, "claude")
        task = kwargs.pop("task", fixture["shared"]["task"])

        prompt, manifest = _run_phases(empty_repo, task, kwargs)
        snapshot = _snapshot_text(prompt, manifest, "claude", empty_repo)

        if _UPDATE or not SNAPSHOT_CLAUDE.exists():
            SNAPSHOT_CLAUDE.write_text(snapshot, encoding="utf-8")
            pytest.skip(
                f"Snapshot written to {SNAPSHOT_CLAUDE.name}; "
                "re-run without BOOT_UPDATE_SNAPSHOTS to compare."
            )
        else:
            stored = SNAPSHOT_CLAUDE.read_text(encoding="utf-8")
            assert snapshot == stored, (
                "Claude prompt snapshot mismatch — run with "
                "BOOT_UPDATE_SNAPSHOTS=1 to regenerate, then review the diff."
            )

    def test_codex_snapshot(self, empty_repo):
        """Codex runner rendering — captured against prompt schema v1."""
        fixture = _load_fixture()
        kwargs = _build_kwargs(fixture, "codex")
        task = kwargs.pop("task", fixture["shared"]["task"])

        prompt, manifest = _run_phases(empty_repo, task, kwargs)
        snapshot = _snapshot_text(prompt, manifest, "codex", empty_repo)

        if _UPDATE or not SNAPSHOT_CODEX.exists():
            SNAPSHOT_CODEX.write_text(snapshot, encoding="utf-8")
            pytest.skip(
                f"Snapshot written to {SNAPSHOT_CODEX.name}; "
                "re-run without BOOT_UPDATE_SNAPSHOTS to compare."
            )
        else:
            stored = SNAPSHOT_CODEX.read_text(encoding="utf-8")
            assert snapshot == stored, (
                "Codex prompt snapshot mismatch — run with "
                "BOOT_UPDATE_SNAPSHOTS=1 to regenerate, then review the diff."
            )

    def test_both_snapshots_exist(self):
        """Snapshots must exist (regenerate with BOOT_UPDATE_SNAPSHOTS=1 if not)."""
        if _UPDATE:
            pytest.skip("Update mode — snapshots are being regenerated.")
        missing = [p for p in (SNAPSHOT_CLAUDE, SNAPSHOT_CODEX) if not p.exists()]
        assert not missing, (
            f"Missing snapshot(s): {[p.name for p in missing]}. "
            "Regenerate with: BOOT_UPDATE_SNAPSHOTS=1 pytest tests/test_boot_replay.py"
        )

    def test_snapshots_not_hand_edited(self):
        """Snapshots must start with the auto-generated header line."""
        if _UPDATE:
            pytest.skip("Update mode.")
        for snapshot_path in (SNAPSHOT_CLAUDE, SNAPSHOT_CODEX):
            if not snapshot_path.exists():
                continue
            content = snapshot_path.read_text(encoding="utf-8")
            assert content.startswith("# Boot snapshot"), (
                f"{snapshot_path.name} appears to have been hand-edited — "
                "the first line must be the auto-generated header. "
                "Regenerate with: BOOT_UPDATE_SNAPSHOTS=1 pytest tests/test_boot_replay.py"
            )


# ── Unit-level boot score tests ───────────────────────────────────────────────


class TestBootScore:
    """Unit tests for the BootScore IR assembled by the scored builders."""

    def test_daemon_prompt_with_score_returns_score(self, empty_repo):
        """build_daemon_prompt_with_score returns both prompt and BootScore."""
        from brr.prompts import build_daemon_prompt_with_score
        from brr.bootscore import BootScore, SCHEMA_VERSION

        prompt, score = build_daemon_prompt_with_score(
            "Test task",
            "evt-test-001",
            "/tmp/response.md",
            empty_repo,
            runner_medium="claude",
            environment="worktree",
        )
        assert isinstance(prompt, str)
        assert isinstance(score, BootScore)
        assert score.schema_version == SCHEMA_VERSION
        assert len(score.contracts) > 0

    def test_score_contracts_include_preamble_and_inject_blocks(self, empty_repo):
        """The BootScore contracts cover both preamble and inject-stack blocks."""
        from brr.prompts import build_daemon_prompt_with_score

        _, score = build_daemon_prompt_with_score(
            "Task", "evt-001", "/tmp/r.md", empty_repo,
        )
        keys = {c.block_key for c in score.contracts}
        # Preamble blocks
        assert "run-preamble" in keys
        assert "weave" in keys
        assert "daemon-substrate" in keys
        # Inject-stack blocks
        assert "identity-core" in keys
        assert "dominion" in keys
        assert "recent-activity" in keys
        assert "kb-health" in keys
        # Runtime trailer
        assert "run-context-bundle" in keys

    def test_worker_prompt_skips_inject_blocks(self, empty_repo):
        """A worker wake omits the inject-stack blocks in its score."""
        from brr.prompts import build_daemon_prompt_with_score

        _, score = build_daemon_prompt_with_score(
            "Worker task", "evt-001", "/tmp/r.md", empty_repo,
            worker=True,
        )
        keys = {c.block_key for c in score.contracts}
        # Worker preamble, not run.md
        assert "worker-preamble" in keys
        assert "run-preamble" not in keys
        # Inject stack absent for workers
        assert "identity-core" not in keys
        assert "dominion" not in keys

    def test_all_contracts_have_required_fields(self, empty_repo):
        """Every ContractEntry has non-empty block_key, label, owner, authority, location."""
        from brr.prompts import build_daemon_prompt_with_score

        _, score = build_daemon_prompt_with_score(
            "Task", "evt-001", "/tmp/r.md", empty_repo,
        )
        for c in score.contracts:
            assert c.block_key, f"block_key empty for {c}"
            assert c.label, f"label empty for {c.block_key}"
            assert c.owner, f"owner empty for {c.block_key}"
            assert c.authority, f"authority empty for {c.block_key}"
            assert c.location, f"location empty for {c.block_key}"

    def test_present_flag_accurate_for_product_templates(self, empty_repo):
        """Product templates (identity-core, run.md, etc.) show present=True when bundled."""
        from brr.prompts import build_daemon_prompt_with_score

        _, score = build_daemon_prompt_with_score(
            "Task", "evt-001", "/tmp/r.md", empty_repo,
        )
        by_key = {c.block_key: c for c in score.contracts}
        # Bundled product templates must be present
        assert by_key["identity-core"].present
        assert by_key["run-preamble"].present
        assert by_key["weave"].present
        assert by_key["daemon-substrate"].present
        # Dominion is absent in an empty repo
        assert not by_key["dominion"].present

    def test_run_prompt_with_score(self, empty_repo):
        """build_run_prompt_with_score also returns a BootScore."""
        from brr.prompts import build_run_prompt_with_score
        from brr.bootscore import BootScore

        prompt, score = build_run_prompt_with_score("Task", empty_repo)
        assert isinstance(prompt, str)
        assert isinstance(score, BootScore)
        # Non-daemon: no daemon-substrate in contracts
        keys = {c.block_key for c in score.contracts}
        assert "daemon-substrate" not in keys
        assert "run-preamble" in keys

    def test_build_boot_score_standalone(self, empty_repo):
        """build_boot_score works standalone for the CLI path."""
        from brr.prompts import build_boot_score
        from brr.bootscore import BootScore

        score = build_boot_score(empty_repo, is_daemon=True, runner_medium="codex")
        assert isinstance(score, BootScore)
        assert score.body.shell == "codex"
        assert score.host.kind == "daemon"
        assert len(score.contracts) >= 5

    def test_format_manifest_output(self, empty_repo):
        """format_manifest renders a parseable human-readable text."""
        from brr.prompts import build_boot_score
        from brr.bootscore import format_manifest

        score = build_boot_score(empty_repo, is_daemon=True, runner_medium="claude",
                                  runner_core="claude-sonnet-4-6")
        text = format_manifest(score)
        assert "brnrd boot" in text
        assert "schema v1" in text
        assert "source manifest:" in text
        assert "owner" in text
        assert "authority" in text
        assert "claude / claude-sonnet-4-6" in text

    def test_build_daemon_prompt_output_unchanged(self, empty_repo):
        """build_daemon_prompt still returns the same text it always did.

        The scored variant delegates to the same renderer — this test guards
        against the refactoring accidentally changing the rendered output.
        """
        from brr.prompts import build_daemon_prompt, build_daemon_prompt_with_score

        kwargs = dict(
            runner_medium="claude",
            environment="worktree",
        )
        plain = build_daemon_prompt(
            "Task", "evt-001", "/tmp/r.md", empty_repo, **kwargs,
        )
        scored, _ = build_daemon_prompt_with_score(
            "Task", "evt-001", "/tmp/r.md", empty_repo, **kwargs,
        )
        assert plain == scored, (
            "build_daemon_prompt and build_daemon_prompt_with_score "
            "must produce identical text."
        )

    def test_hooks_list_covers_all_abstract_phases(self, empty_repo, monkeypatch):
        """The BootScore hooks list covers all three abstract daemon phases."""
        from brr.prompts import build_boot_score
        # Clear hook env so installed=False in this test context
        monkeypatch.delenv("BRR_RUNNER", raising=False)
        monkeypatch.delenv("BRR_OUTBOX_DIR", raising=False)
        monkeypatch.delenv("BRR_PORTAL_STATE", raising=False)

        score = build_boot_score(empty_repo)
        phase_names = {h.name for h in score.hooks}
        assert "post-tool" in phase_names
        assert "stop" in phase_names
        assert "session-start" in phase_names
        for hook in score.hooks:
            assert hook.declared  # all phases are always declared
