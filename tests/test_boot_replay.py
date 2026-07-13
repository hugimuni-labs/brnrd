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
import re
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
    repo_root: Path, task: str, kwargs: dict[str, Any], fixture: dict[str, Any]
) -> tuple[str, str, str, str]:
    """Run the production prompt, portal, and hook phases from one fixture."""
    from brr.prompts import build_daemon_prompt_with_score
    from brr.bootscore import format_manifest
    from brr import hooks

    kw = dict(kwargs)
    task_val = kw.pop("task", task)
    event_id = kw.pop("event_id")
    response_path = kw.pop("response_path")

    prompt, score = build_daemon_prompt_with_score(
        task_val, event_id, response_path, repo_root, **kw
    )
    manifest = format_manifest(score)
    portal = fixture["portal_state"]
    portal_text = hooks.format_delta(portal, seed=True) or ""
    portal_path = repo_root / ".brr" / "portal-state.json"
    portal_path.write_text(json.dumps(portal), encoding="utf-8")
    hook_payload, hook_code = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", {
        "BRR_RUN_ID": "run-fixture-0001",
        "BRR_EVENT_ID": event_id,
        "BRR_RUNNER": "claude",
        "BRR_OUTBOX_DIR": str(repo_root / ".brr"),
        "BRR_PORTAL_STATE": str(portal_path),
    })
    assert hook_code == 0
    return prompt, manifest, portal_text, json.dumps(hook_payload, sort_keys=True)


def _normalize(text: str, repo_root: Path) -> str:
    """Replace the repo-root path with a stable placeholder.

    The prompt includes ``repo_root`` in the Run Context Bundle (``Execution
    root:`` line etc.).  Since ``empty_repo`` creates a fresh ``tmp_path``
    per test run, raw paths would make the snapshot non-reproducible.
    Replacing them with ``{REPO_ROOT}`` before storing / comparing makes the
    snapshot stable while still exercising all code paths.
    """
    from brr import prompts

    text = text.replace(str(repo_root), "{REPO_ROOT}")
    text = text.replace(str(prompts._PROMPTS_DIR.parent), "{PACKAGE_ROOT}")
    # mtimes describe freshness at inspection time; they are intentionally
    # live metadata and cannot make a replay fixture machine-specific.
    return re.sub(r" \[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\]", " [mtime]", text)


def _snapshot_text(
    prompt: str, manifest: str, portal: str, hook: str, runner_key: str, repo_root: Path
) -> str:
    """Compose the snapshot file content.

    Every section is normalized, not just the prompt.  The manifest is the one
    section that actually carries absolute paths and file mtimes, and it was
    previously concatenated raw — so the stored snapshot embedded the
    generating machine's home directory and checkout times, and the test could
    only pass on the machine that wrote it.  ``test_snapshots_are_machine_
    independent`` now holds this shut.
    """
    header = (
        f"# Boot snapshot — runner: {runner_key} — prompt schema v{_schema_version()}\n"
        "# Captured by the boot replay harness; regenerate with:\n"
        "#   BOOT_UPDATE_SNAPSHOTS=1 pytest tests/test_boot_replay.py -v\n"
        "# DO NOT EDIT BY HAND — regeneration is the only sanctioned path.\n"
        "\n"
    )
    body = (
        _normalize(prompt, repo_root) + "\n\n" + _PHASE_SEP + "\n"
        + _normalize(manifest, repo_root)
        + "\n\n--- phase: portal ---\n\n" + _normalize(portal, repo_root)
        + "\n\n--- phase: session-start hook ---\n\n" + _normalize(hook, repo_root)
    )
    return header + body


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

        prompt, manifest, portal, hook = _run_phases(empty_repo, task, kwargs, fixture)
        snapshot = _snapshot_text(prompt, manifest, portal, hook, "claude", empty_repo)

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

        prompt, manifest, portal, hook = _run_phases(empty_repo, task, kwargs, fixture)
        snapshot = _snapshot_text(prompt, manifest, portal, hook, "codex", empty_repo)

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

    def test_snapshots_are_machine_independent(self):
        """A stored snapshot must contain no fact only this machine knows.

        The bug: ``_snapshot_text`` normalized the prompt but concatenated the
        *manifest* raw — and the manifest is the only section carrying absolute
        paths and file mtimes.  The snapshots therefore embedded the generating
        machine's home directory, and the suite was green only where it was
        written.  Checked directly against the stored bytes, so no future
        composition change can reintroduce it quietly.
        """
        for path in (SNAPSHOT_CLAUDE, SNAPSHOT_CODEX):
            stored = path.read_text(encoding="utf-8")
            assert "/home/" not in stored, f"{path.name} embeds a home directory"
            assert "/Users/" not in stored, f"{path.name} embeds a home directory"
            assert not re.search(r"\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\]", stored), (
                f"{path.name} embeds a raw file mtime — checkout time is not "
                "content, and differs on every clone"
            )
            assert "{PACKAGE_ROOT}" in stored  # the placeholder is actually used

    def test_ad_hoc_score_omits_daemon_substrate(self, empty_repo):
        """A non-daemon score carries the preamble but not the substrate block."""
        from brr.prompts import build_boot_score

        score = build_boot_score(empty_repo, is_daemon=False, task_text="Task")
        keys = {c.block_key for c in score.contracts}
        assert "daemon-substrate" not in keys
        assert "run-preamble" in keys

    def test_build_boot_score_standalone(self, empty_repo):
        """build_boot_score works standalone for the CLI path."""
        from brr.prompts import build_boot_score
        from brr.bootscore import BootScore

        score = build_boot_score(empty_repo, is_daemon=True, runner_shell="codex")
        assert isinstance(score, BootScore)
        assert score.body.shell == "codex"
        assert score.host.kind == "daemon"
        assert len(score.contracts) >= 5

    def test_scored_daemon_prompt_names_the_body_it_runs_in(self, empty_repo):
        """The daemon's own path must resolve Shell+Core, not just a label.

        Regression: the daemon passed only its *display label*
        ("claude-fable (requested from the dashboard spool rack)") as the
        shell and never passed the core at all — so every wake persisted a
        boot-score.json reading ``"core": null`` while ``run.md``, written into
        the same directory in the same second, named the core exactly. The
        artifact built to make boot honest could not say what body it was in.
        """
        from brr.prompts import build_daemon_prompt_with_score

        _, score = build_daemon_prompt_with_score(
            "Task", "evt-001", "/tmp/r.md", empty_repo,
            runner_medium="claude-fable (requested from the dashboard spool rack)",
            runner_name="claude-fable",
            runner_shell="claude",
            runner_core="claude-fable-5",
            body_provenance="requested from the dashboard spool rack",
            environment="host",
        )
        assert score.body.name == "claude-fable"
        assert score.body.shell == "claude"
        assert score.body.core == "claude-fable-5"
        assert score.attention.body_provenance == (
            "requested from the dashboard spool rack"
        )

    def test_format_manifest_output(self, empty_repo):
        """format_manifest renders a parseable human-readable text."""
        from brr.prompts import build_boot_score
        from brr.bootscore import format_manifest

        score = build_boot_score(empty_repo, is_daemon=True, runner_shell="claude",
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

    def test_hooks_list_covers_all_abstract_phases(self, empty_repo):
        """The BootScore hooks list covers all three abstract daemon phases.

        No env-clearing needed: the score is a pure function of its arguments.
        """
        from brr.prompts import build_boot_score

        score = build_boot_score(empty_repo)
        phase_names = {h.name for h in score.hooks}
        assert "post-tool" in phase_names
        assert "stop" in phase_names
        assert "session-start" in phase_names
        for hook in score.hooks:
            assert hook.declared  # all phases are always declared

    def test_unknown_hook_state_is_not_a_denial(self, empty_repo):
        """No hook facts supplied ⇒ ``installed`` is None (unknown), not False.

        The bug this pins: an env-var probe reported "not-installed" whenever
        it was asked outside a wake — i.e. in the operator's terminal, the only
        place ``prompts show`` is ever run — while the hooks were firing.
        ``absent != unknown != off``.
        """
        from brr.prompts import build_boot_score

        score = build_boot_score(empty_repo)
        assert all(h.installed is None for h in score.hooks)
        assert score.body.tier is None  # not "Tier 2", not "Tier 1" — unknown

    def test_hook_stamps_are_per_phase(self, empty_repo):
        """A phase that fired never lends its timestamp to a phase that didn't."""
        from brr.prompts import build_boot_score

        score = build_boot_score(
            empty_repo,
            hooks_installed=True,
            hook_stamps={"post-tool": "2026-07-13T19:00:00Z"},
        )
        by_name = {h.name: h for h in score.hooks}
        assert by_name["post-tool"].last_fired == "2026-07-13T19:00:00Z"
        assert by_name["stop"].last_fired is None
        assert by_name["session-start"].last_fired is None
        assert all(h.installed is True for h in score.hooks)

    def test_hook_stamps_round_trip_through_hooks_module(self, tmp_path):
        """The key the score reads is the key the hooks module writes.

        Pins the seam that was broken on merge: ``last_fired`` was read from a
        key no writer in brnrd ever wrote, so every hook reported "unknown"
        forever.  This fails if the two sides drift apart again.
        """
        import json

        from brr import hooks as hooks_mod
        from brr.prompts import read_hook_stamps

        state = {}
        hooks_mod._record_fired(state, hooks_mod.PHASE_POST_TOOL)
        (tmp_path / hooks_mod.HOOK_STATE_NAME).write_text(
            json.dumps(state), encoding="utf-8"
        )

        stamps = read_hook_stamps(tmp_path)
        assert hooks_mod.PHASE_POST_TOOL in stamps
        assert stamps[hooks_mod.PHASE_POST_TOOL].endswith("Z")
        assert hooks_mod.PHASE_STOP not in stamps

    def test_boot_score_json_carries_attention_and_posture(self, empty_repo):
        """``to_dict`` serializes what the text view shows — no silent drops."""
        from brr.bootscore import to_dict
        from brr.prompts import build_boot_score

        score = build_boot_score(
            empty_repo, event_ids=("evt-1",), branch="brr/x", pending_count=2
        )
        payload = to_dict(score)
        assert payload["attention"]["event_ids"] == ("evt-1",)
        assert payload["posture"]["branch"] == "brr/x"
        assert payload["posture"]["pending_count"] == 2
        assert payload["hooks"] and payload["contracts"]
