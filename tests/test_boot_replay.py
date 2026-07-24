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

What the snapshot pins — and what it deliberately does not
----------------------------------------------------------
It pins **assembly**: which blocks enter, in what order, with what glue and
headers, which are present versus silent, how the computed sections render,
and where the two runner renderings differ.

It does **not** pin the product prompt *prose*.  Until 2026-07-24 it did:
about 1,150 of the two snapshots' 1,249 lines were verbatim copies of
``run.md`` / ``weave.md`` / ``register.md`` / ``daemon-substrate.md`` /
``identity-core.md``, once per runner — so brnrd's prompt text lived in the
repo three times and every prose edit had to be applied once and then
re-applied twice by regeneration.  That is a fixture carrying the payload it
exists to test the assembly of, and the cost was not the friction: it trained
the regeneration reflex, so that the one regeneration in ten that hid a real
assembly change would be waved through with the nine that didn't.

So each product body that entered verbatim is replaced by a
``{{BODY verbatim: prompts/<name>}}`` marker.  The substitution is a literal
match against the file on disk, which makes **the marker itself the
assertion**: it can only appear if that block reached the prompt
untransformed.  Let a trim or a collapse touch that block
(``prompts._tail_trim_entries``, ``dominion._collapse_markdown_to_budget`` —
both live, both applied to other blocks today) and the match fails, the raw
text lands back in the snapshot, and the diff is enormous and unmissable.

The cost-ledger figures are normalized for the same reason: they are a pure
function of those file sizes, so they churned on every prose edit while
asserting nothing the arithmetic tests do not already assert better.  Ledger
correctness is pinned by ``test_cost_ledger_measures_the_wake_not_the_disk``
(measured blocks + joins == rendered prompt bytes), which is a real
invariant rather than a frozen number.  Manifest rows for blocks whose size
does *not* depend on prompt-file prose — the kernel, the Run Context Bundle,
the work surface — keep their byte column.

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
import shutil
import tempfile
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


_BODY_FLOOR = 512
"""Minimum bytes for a prompt file to be worth eliding.

Small bundled templates (``kb-index.md``, ``kb-log.md``) are scaffolding
fragments, not prose blocks; short strings also risk matching incidentally
somewhere they are not a block body.
"""

_BODY_MARKER = "{{BODY verbatim: prompts/%s}}"


def _elide_product_bodies(
    text: str, repo_root: Path | None = None
) -> tuple[str, set[str]]:
    """Replace verbatim product prompt bodies with a marker.

    Returns the rewritten text and the set of file names actually elided —
    the caller asserts on that set, so "nothing matched" can never pass as
    "nothing to match".

    Bodies are read through :func:`prompts.effective_prompt_path`, the same
    resolver the builder uses, so a per-repo ``.brr/prompts/`` override is
    elided as the body that actually entered rather than left raw beside a
    marker for the packaged file it replaced.

    Longest first: if one template's body were ever a substring of another's,
    eliding the shorter one first would strand a fragment of the longer.
    """
    from brr import prompts

    elided: set[str] = set()
    candidates = []
    for path in sorted(prompts._PROMPTS_DIR.glob("*.md")):
        effective = prompts.effective_prompt_path(path.name, repo_root)
        if not effective.exists():
            continue
        body = effective.read_text(encoding="utf-8").strip()
        if len(body) >= _BODY_FLOOR:
            candidates.append((path.name, body))
    for name, body in sorted(candidates, key=lambda pair: -len(pair[1])):
        if body in text:
            text = text.replace(body, _BODY_MARKER % name)
            elided.add(name)
    return text, elided


def _normalize_prose_derived_sizes(text: str, elided: set[str]) -> str:
    """Blank the byte figures that are a pure function of prompt-file prose.

    Two places carry them, and only these two:

    - the manifest row of an elided block (its ``bytes`` column);
    - every figure in the ``cost ledger:`` block, since the category totals,
      the percentages, and the wake total all sum over those bodies.

    Manifest rows for computed blocks (kernel, Run Context Bundle, work
    surface) keep their real byte column — those sizes move only when the
    rendering does, which is exactly what this snapshot is for.
    """
    def _same_width(match: re.Match[str], token: str) -> str:
        """Swap a figure for a token without disturbing the column layout.

        These tables are read by eye; a placeholder that shifts every row
        makes the diff of a *real* assembly change harder to see, which is
        the one thing this file exists to keep easy.
        """
        return token.rjust(len(match.group(0)))

    for name in elided:
        text = re.sub(
            r"(?m)(?<=\s)\s*[\d,]+(?=\s+\{PACKAGE_ROOT\}/prompts/%s\s)"
            % re.escape(name),
            lambda m: _same_width(m, "[bytes]"),
            text,
        )

    def _blank_ledger(match: re.Match[str]) -> str:
        block = match.group(0)
        block = re.sub(
            r"\s*\d[\d,]*(?= B\b)", lambda m: _same_width(m, "[bytes]"), block
        )
        block = re.sub(r"\s*\d+\.\d%", lambda m: _same_width(m, "[pct]"), block)
        return block

    return re.sub(
        r"(?ms)^cost ledger:$.*?^\s*wake total\s+.*?$", _blank_ledger, text
    )


def _normalize(text: str, repo_root: Path, elided: set[str] | None = None) -> str:
    """Replace machine- and prose-dependent facts with stable placeholders.

    The prompt includes ``repo_root`` in the Run Context Bundle (``Execution
    root:`` line etc.).  Since ``empty_repo`` creates a fresh ``tmp_path``
    per test run, raw paths would make the snapshot non-reproducible.
    Replacing them with ``{REPO_ROOT}`` before storing / comparing makes the
    snapshot stable while still exercising all code paths.

    ``elided`` names the product bodies already replaced in the *prompt*
    phase; the manifest phase needs it to blank the matching byte columns.
    """
    from brr import prompts

    text = text.replace(str(repo_root), "{REPO_ROOT}")
    text = text.replace(str(prompts._PROMPTS_DIR.parent), "{PACKAGE_ROOT}")
    # mtimes describe freshness at inspection time; they are intentionally
    # live metadata and cannot make a replay fixture machine-specific.
    text = re.sub(r" \[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\]", " [mtime]", text)
    if elided:
        text = _normalize_prose_derived_sizes(text, elided)
    return text


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
        "#\n"
        "# {{BODY verbatim: prompts/<name>}} — that bundled prompt entered the\n"
        "#   wake byte-for-byte.  The marker is the assertion: it is written by a\n"
        "#   literal match against the file, so it cannot appear for a block that\n"
        "#   was trimmed, collapsed, or reflowed on the way in.  Editing the prose\n"
        "#   of a bundled prompt does not move this snapshot; changing how that\n"
        "#   prompt is *assembled* does.\n"
        "# [bytes] / [pct] — figures that are a pure function of that elided prose.\n"
        "#   The ledger's arithmetic is pinned by the unit tests, not by here.\n"
        "\n"
    )
    prompt_text, elided = _elide_product_bodies(prompt, repo_root)
    body = (
        _normalize(prompt_text, repo_root) + "\n\n" + _PHASE_SEP + "\n"
        + _normalize(manifest, repo_root, elided)
        + "\n\n--- phase: portal ---\n\n" + _normalize(portal, repo_root)
        + "\n\n--- phase: session-start hook ---\n\n" + _normalize(hook, repo_root)
    )
    return header + body


def _schema_version() -> str:
    from brr.bootscore import SCHEMA_VERSION
    return SCHEMA_VERSION


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def empty_repo():
    """A minimal git repo without any dominion or kb, for reproducible output.

    Deliberately *not* under pytest's ``tmp_path``: the manifest phase
    measures block byte sizes on the rendered (un-normalized) content, so
    the repo path's **length** is part of the snapshot. ``tmp_path`` length
    varies with the username and pytest's run counter (``pytest-of-<user>/
    pytest-<n>``) and differs again on CI — a one-character difference
    shows up as a one-byte manifest mismatch. ``mkdtemp``'s suffix is
    always 8 characters, so this path has the same length on every machine.
    """
    from _helpers import init_git_repo
    root = Path(tempfile.mkdtemp(prefix="brr-boot-replay-", dir="/tmp"))
    try:
        repo = root / "repo"
        init_git_repo(repo)
        # Ensure no .brr/config that could pull in home knowledge or dominion
        (repo / ".brr").mkdir()
        (repo / ".brr" / "config").write_text("", encoding="utf-8")
        yield repo
    finally:
        shutil.rmtree(root, ignore_errors=True)


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

    def test_snapshots_carry_markers_not_prompt_prose(self):
        """The stored snapshot names each product body; it does not copy it.

        Two ways this regresses, and both are silent without this test: the
        elision stops firing (bodies come back, the fixtures triple in size,
        every prose edit becomes a regeneration again), or it fires for
        *everything* (a body arriving transformed would be papered over).
        Checked against the stored bytes, not the freshly built text, so a
        stale fixture cannot pass either.
        """
        expected = {
            "run.md",
            "weave.md",
            "register.md",
            "daemon-substrate.md",
            "identity-core.md",
        }
        for path in (SNAPSHOT_CLAUDE, SNAPSHOT_CODEX):
            stored = path.read_text(encoding="utf-8")
            for name in expected:
                assert _BODY_MARKER % name in stored, (
                    f"{path.name} lost the marker for {name} — either the block "
                    "stopped entering the wake, or it stopped entering verbatim. "
                    "Both are real findings; neither is a regeneration."
                )

    def test_snapshots_do_not_duplicate_prompt_prose(self):
        """No bundled prompt's body may live in a fixture a second time.

        The rule the marker exists to enforce, asserted against the text
        rather than the mechanism: a sentence that appears in ``prompts/``
        must appear there *only*.  A future block added to the wake without
        going through the elision path fails here, not six prose edits later.
        """
        from brr import prompts

        # A literal floor, deliberately not ``_BODY_FLOOR``: raising that
        # constant is one of the ways the elision gets disabled, and a guard
        # that reads the same knob it is guarding cannot fire when it should.
        checked: set[str] = set()
        for path in (SNAPSHOT_CLAUDE, SNAPSHOT_CODEX):
            stored = path.read_text(encoding="utf-8")
            for template in sorted(prompts._PROMPTS_DIR.glob("*.md")):
                body = template.read_text(encoding="utf-8").strip()
                if len(body) < 512:
                    continue
                checked.add(template.name)
                assert body not in stored, (
                    f"{path.name} embeds the full text of {template.name}. "
                    "The snapshot pins assembly; the prose is already in git "
                    "once, and a second copy makes every prose edit a "
                    "three-file change."
                )
        assert {"run.md", "daemon-substrate.md", "identity-core.md"} <= checked, (
            "the templates this test is meant to cover fell below its floor — "
            "it is passing by checking nothing"
        )

    def test_a_prose_edit_does_not_move_the_snapshot(self, empty_repo, tmp_path, monkeypatch):
        """Editing a bundled prompt's wording changes no fixture byte.

        This is the property the rewrite bought, so it gets a test rather than
        a docstring.  Driven against a *copy* of the prompts directory, edited
        for real and rebuilt through the production builder — not a simulated
        one.
        """
        from brr import prompts

        pkg = tmp_path / "pkg"
        shutil.copytree(prompts._PROMPTS_DIR, pkg / "prompts")
        monkeypatch.setattr(prompts, "_PROMPTS_DIR", pkg / "prompts")

        fixture = _load_fixture()
        kwargs = _build_kwargs(fixture, "claude")
        task = kwargs.pop("task", fixture["shared"]["task"])

        def build() -> str:
            phases = _run_phases(empty_repo, task, dict(kwargs), fixture)
            return _snapshot_text(*phases, "claude", empty_repo)

        before = build()
        target = pkg / "prompts" / "daemon-substrate.md"
        target.write_text(
            target.read_text(encoding="utf-8")
            + "\n- a rule invented by this test, worth exactly zero bytes here.\n",
            encoding="utf-8",
        )
        assert build() == before, (
            "a prose-only edit to a bundled prompt moved the snapshot — the "
            "elision is no longer covering that block"
        )

    def test_a_transformed_body_is_not_elided(self):
        """The marker cannot appear for a block that arrived changed.

        The whole safety argument rests on the substitution being a literal
        match against the file, so it is asserted directly: one character of
        drift and the raw text survives into the snapshot, where the diff is
        impossible to miss.  ``prompts._tail_trim_entries`` and
        ``dominion._collapse_markdown_to_budget`` are both live and both
        rewrite block bodies today; this is the alarm for the day one of them
        starts reaching a product prompt.
        """
        from brr import prompts

        body = (prompts._PROMPTS_DIR / "daemon-substrate.md").read_text(
            encoding="utf-8"
        ).strip()
        mangled = body[:-1] + "!"

        elided_text, elided = _elide_product_bodies("head\n" + body + "\ntail")
        assert "daemon-substrate.md" in elided
        assert body not in elided_text

        kept_text, kept = _elide_product_bodies("head\n" + mangled + "\ntail")
        assert "daemon-substrate.md" not in kept
        assert mangled in kept_text

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
        # Why this body is a fact about the *body*.
        #
        # This assertion used to read ``score.attention.body_provenance ==
        # "requested from the dashboard spool rack"`` — i.e. it *pinned the bug*.
        # The runner note was landing on the kernel's ``attention:`` line, where
        # it told the wake that its attention had arrived from the spool rack
        # when the user had typed it into telegram.  The test was green and the
        # semantics were wrong, which is the only reason it survived review: a
        # test can defend a defect as faithfully as it defends a contract.
        assert score.body.provenance == "requested from the dashboard spool rack"

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

    # ── Slice 2: the action-first kernel ─────────────────────────────────────

    def test_prompt_opens_with_the_kernel_the_score_describes(self, empty_repo):
        """The block the wake reads *is* the block the score explains.

        The whole point of an inspectable middle is that inspection and delivery
        cannot drift.  If the kernel were rendered from one construction and the
        persisted score from another, ``boot-score.json`` would describe a wake
        nobody had.  One builder, checked byte-for-byte.
        """
        from brr.bootscore import format_kernel
        from brr.prompts import build_daemon_prompt_with_score

        prompt, score = build_daemon_prompt_with_score(
            "Task", "evt-001", "/tmp/r.md", empty_repo,
            runner_name="claude-fable", runner_shell="claude",
            runner_core="claude-fable-5", environment="host",
            event_body="Task", hooks_installed=True,
        )
        assert prompt.startswith(format_kernel(score)), (
            "the prompt must open with the kernel, and it must be the same "
            "kernel the BootScore renders"
        )

    def test_kernel_is_the_first_thing_read(self, empty_repo):
        """Position is the payload: nothing precedes the kernel, not even run.md.

        Slice 2's move is about *where*, not only *what*.  A kernel buried under
        30 KB of standing contract is the haystack it was built to end.
        """
        from brr.prompts import build_daemon_prompt

        prompt = build_daemon_prompt(
            "Task", "evt-001", "/tmp/r.md", empty_repo, event_body="Task",
        )
        assert prompt.splitlines()[0].startswith("brnrd boot ·")

    def test_kernel_names_the_body_not_just_the_label(self, empty_repo):
        """Requested label and issued body have diverged in production before."""
        from brr.prompts import build_daemon_prompt

        prompt = build_daemon_prompt(
            "Task", "evt-001", "/tmp/r.md", empty_repo,
            runner_medium="claude-fable (requested from the dashboard spool rack)",
            runner_name="claude-fable", runner_shell="claude",
            runner_core="claude-fable-5",
        )
        kernel = prompt.split("\n\n", 1)[0]
        assert "claude / claude-fable-5" in kernel

    def test_orientation_is_derived_from_posture_not_boilerplate(self, empty_repo):
        """Steps appear because a fact about *this* wake obliges them.

        A ``next:`` list identical in every wake would be one more constant to
        skim past — precisely the failure the kernel exists to fix.
        """
        from brr.prompts import build_boot_score

        host = build_boot_score(
            empty_repo, environment="host", pending_count=3, has_event_body=True
        )
        actions = [s.action for s in host.orientation]
        assert "branch before you edit" in actions
        assert "answer 3 queued events" in actions

        worktree = build_boot_score(
            empty_repo, environment="worktree", pending_count=0, has_event_body=True
        )
        actions = [s.action for s in worktree.orientation]
        assert "branch before you edit" not in actions   # the daemon publishes it
        assert not any(a.startswith("answer") for a in actions)  # nothing queued

    def test_worker_kernel_omits_resident_only_steps(self, empty_repo):
        """A worker never writes a card — ``worker.md`` does not grant it one."""
        from brr.prompts import build_boot_score

        score = build_boot_score(empty_repo, is_worker=True, has_event_body=True)
        actions = [s.action for s in score.orientation]
        assert not any("card" in a for a in actions)

    def test_cost_ledger_measures_the_wake_not_the_disk(self, empty_repo):
        """Bytes are what entered the prompt, and they add up to the whole bill.

        A trimmed block (log tail, dominion digest) weighs less than its file;
        a toggled-off block weighs nothing at all.  The manifest's job is to say
        which, and to reconcile: measured blocks + joins == the rendered prompt.
        """
        from brr.prompts import build_daemon_prompt_with_score

        prompt, score = build_daemon_prompt_with_score(
            "Task", "evt-001", "/tmp/r.md", empty_repo, event_body="Task",
        )
        assert score.prompt_bytes == len(prompt.encode("utf-8"))

        by_key = {c.block_key: c for c in score.contracts}
        # The kernel pays rent in its own ledger.
        assert by_key["boot-kernel"].bytes > 0
        # The bundle is measured by the only function that can weigh it.
        assert by_key["run-context-bundle"].bytes > 0
        # Absent blocks are measured-and-empty, never "unweighed".
        assert by_key["dominion"].present is False
        assert by_key["dominion"].bytes == 0

        measured = sum(c.bytes or 0 for c in score.contracts if c.present)
        # Everything unaccounted for is the "\n\n" between blocks — a handful of
        # bytes, not a missing block.
        assert 0 <= score.prompt_bytes - measured < 200

    def test_unrendered_score_reports_unweighed_not_zero(self, empty_repo):
        """``brnrd prompts show`` renders no bundle; its size is unknown, not 0.

        ``absent != unknown != none``, the rule this module has now learned three
        separate times.  A CLI inspection that printed ``0 B`` for the Run
        Context Bundle would be asserting the wake carries no runtime facts.
        """
        from brr.prompts import build_boot_score

        score = build_boot_score(empty_repo, is_daemon=True)
        by_key = {c.block_key: c for c in score.contracts}
        assert by_key["run-context-bundle"].bytes is None
        assert score.prompt_bytes is None
        # But the file-backed blocks it *can* weigh, it does.
        assert by_key["identity-core"].bytes > 0

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
