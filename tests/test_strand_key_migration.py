"""Records outlive the image that wrote them — the `worker` → `strand` cut.

The rename made ``strand`` canonical everywhere the daemon *asks* whether a
run is a strand-stack child. But the answer is read off records that were
written by an earlier image and are sitting on disk right now: every event
file in ``.brr/inbox/`` that a ``spawn:``/``respawn:`` ever produced carries
``worker: True`` in its frontmatter, and ``Run.meta`` is rebuilt from it.

The window is not hypothetical. ``dev_reload`` re-execs the daemon **at
quiescence** — precisely the moment a queued-but-not-yet-dispatched respawn
is waiting — and ``_reconcile_orphaned_spawn_dispatches`` exists because
spawn events survive restarts by design. An adopter restarting a daemon with
a non-empty queue is the same shape without the dev watcher.

Read as *not a strand*, such a child wakes with the resident's contract:

- the user thread's correspondence and pending events (#574 — a strand once
  read a sibling's leaked spec as a directive and shipped the wrong issue),
- closeout obligations it was never granted a chat seam to satisfy (#779),
- **no ``GIT_DIR``/``GIT_WORK_TREE`` pin** (#703 — the incident put 262
  insertions of a child's deliverable onto the maintainer's own ``main``,
  twice, while its own branch published empty),
- mirror cards, live-menu authority, and the right to nest further spawns.

Every one of those is a silent widening: nothing raises, nothing is refused,
and the run finalizes ``done``. So the compatibility is a *read* concern, and
the pinned property is structural rather than a list of call sites —
``_is_strand`` is the only legal way to ask.

The frontmatter shim in ``_queue_respawn_request`` is a different document
with a different audience: it answers a **resident** who typed the old
spelling by hand, and tells them to migrate. It does nothing for a record the
daemon itself stamped.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from brr import daemon
from brr.run import Run

from _helpers import init_git_repo


LEGACY = {"worker": True}
CANONICAL = {"strand": True}


# ── the read helper answers both spellings ──────────────────────────


@pytest.mark.parametrize(
    "record, expected",
    [
        (CANONICAL, True),
        (LEGACY, True),
        ({"strand": True, "worker": True}, True),
        ({}, False),
        (None, False),
        ({"strand": False, "worker": False}, False),
        # A resident-stack run's meta carries neither key, and the absent
        # case must never be guessed into a strand: the failure direction
        # matters. False-negative widens a child's authority; false-positive
        # silently starves the resident of its own thread.
        ({"root_kind": "repo"}, False),
    ],
)
def test_is_strand_reads_both_spellings(record, expected):
    assert daemon._is_strand(record) is expected


# ── the behavioural half: the pin the legacy record would have lost ──


@pytest.fixture
def trees(tmp_path):
    host = tmp_path / "host"
    init_git_repo(host)
    (host / "README.md").write_text("host\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=host, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "host: base"], cwd=host, check=True, capture_output=True
    )
    run_root = tmp_path / "wt" / "run-legacy-child"
    run_root.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "-b", "brr/legacy", str(run_root), "main"],
        cwd=host,
        check=True,
        capture_output=True,
    )
    return host, run_root


def test_legacy_meta_still_gets_the_git_pin(trees):
    """#703's containment must not lapse for a record written pre-rename.

    Driven through ``_child_git_pin`` — the caller the defect would have
    reached — not through the helper, because the helper agreeing with
    itself proves nothing about the call site.
    """
    host, run_root = trees
    legacy = Run(
        id="run-legacy-child", event_id="evt-legacy", body="spec",
        env="worktree", meta=dict(LEGACY),
    )
    canonical = Run(
        id="run-legacy-child", event_id="evt-legacy", body="spec",
        env="worktree", meta=dict(CANONICAL),
    )
    pin = daemon._child_git_pin(legacy, run_root)
    assert pin == daemon._child_git_pin(canonical, run_root)
    assert set(pin) == {"GIT_DIR", "GIT_WORK_TREE"}
    assert pin["GIT_WORK_TREE"] == str(run_root)
    assert pin["GIT_DIR"] != str(host / ".git")


def test_legacy_meta_still_refuses_a_nested_spawn(tmp_path):
    """A strand may not spawn (one level, by design) — in either spelling."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    legacy = Run(
        id="run-legacy-child", event_id="evt-legacy", body="spec",
        env="worktree", meta=dict(LEGACY),
    )
    accepted = daemon._queue_spawn_request(
        lambda *a, **k: None,
        legacy,
        tmp_path / "inbox",
        "evt-legacy",
        {"spawn": "true", "shell": "claude", "core": "sonnet"},
        "nested work",
        outbox_dir=outbox,
    )
    assert accepted is False
    notices = "\n".join(
        p.read_text(encoding="utf-8") for p in outbox.rglob("*") if p.is_file()
    )
    assert "spawn refused" in notices


# ── the structural half: `_is_strand` is the only legal way to ask ───


#: Functions allowed to name a spelling directly, each for a stated reason.
DIRECT_READ_EXEMPT = {
    # The helper itself — it *is* the compatibility read.
    "_is_strand",
    # The outbox frontmatter shim: a different document with a different
    # audience (a resident who typed the key by hand), and the one place the
    # legacy spelling should argue back instead of silently working.
    "_queue_respawn_request",
}


def _direct_strand_reads() -> dict[str, set[str]]:
    """``<enclosing function> -> {key}`` for every literal ``.get("strand"|"worker")``.

    Parsed from the source text, because the property being pinned is a
    property of the text: an introspecting test would pass on a call site
    that reads the raw key and happens to be exercised with a canonical
    record.
    """
    source = Path(daemon.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    found: dict[str, set[str]] = {}
    seen_any_get = False
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "get":
                continue
            seen_any_get = True
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            key = node.args[0].value
            if key in ("strand", "worker"):
                found.setdefault(fn.name, set()).add(key)
    # Sanity: a rename or a moved module would make the walk find nothing and
    # this module would go green over an empty set — the no-op failure mode a
    # structural guard is most prone to.
    assert seen_any_get, (
        f"AST walk of {daemon.__file__} found no `.get(...)` calls at all — "
        "the parse missed the module, so a green result here proves nothing"
    )
    assert DIRECT_READ_EXEMPT & set(found), (
        "the exempt functions read neither spelling directly — either they "
        "were renamed or the walk is not reaching them; a green result here "
        "would prove nothing"
    )
    return found


def test_no_call_site_reads_the_strand_key_directly():
    offenders = {
        name: sorted(keys)
        for name, keys in _direct_strand_reads().items()
        if name not in DIRECT_READ_EXEMPT
    }
    assert not offenders, (
        "these functions ask whether a run is a strand by reading the key "
        f"directly: {offenders}. Use `_is_strand(...)` — a record written "
        "before the rename says `worker`, and reading only `strand` silently "
        "hands that child the resident's contract (#574 / #703 / #779)."
    )


def test_writers_stamp_only_the_canonical_key():
    """Compatibility is read-only: nothing new may be written as ``worker``."""
    source = Path(daemon.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    written: set[str] = set()
    for node in ast.walk(tree):
        # `meta["worker"] = ...`
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value in ("strand", "worker")
                ):
                    written.add(target.slice.value)
        # `{"worker": True}`
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value in ("strand", "worker"):
                    written.add(key.value)
    assert "strand" in written, (
        "no literal `strand` write found in daemon.py — the walk missed the "
        "dispatch path, so this assertion proves nothing"
    )
    assert "worker" not in written, (
        "daemon.py writes the legacy `worker` key. The old spelling is "
        "read-only compatibility for records already on disk; stamping it "
        "afresh extends the migration window forever."
    )


# ── the notice must not indict a strand for the dispatcher's field ───
#
# Same contract as `_is_strand`, one surface over: `report:` is stamped onto
# the child's meta from the *parent's* frontmatter, and the notice that
# renders a mismatch names only the child's run id.


def test_prose_report_declaration_names_the_dispatcher_and_the_ambiguity():
    note = "\n".join(daemon._unstattable_report_note("the PR body is the report"))
    assert "dispatcher's declaration" in note
    # The ambiguity is named, not resolved: a path may legally contain a
    # space, so the line may not assert "that is not a path".
    assert "if" in note and "meant as prose" in note
    assert "not a path" not in note
    assert "stat" in note


def test_a_plausible_path_gets_the_ownership_line_and_no_prose_guess():
    note = "\n".join(
        daemon._unstattable_report_note("/tmp/brr-wt-x/report.md")
    )
    assert "dispatcher's declaration" in note
    # Nothing here is evidence of prose — the strand plausibly just never
    # wrote the file, and saying otherwise would be the confident branch.
    assert "meant as prose" not in note


def test_a_path_with_a_space_is_not_called_prose():
    """The whitespace heuristic must not fire on a legal path (#800)."""
    note = "\n".join(
        daemon._unstattable_report_note("/tmp/my runs/report.md")
    )
    assert "meant as prose" not in note
