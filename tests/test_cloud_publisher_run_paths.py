"""What a run touched — asked of git, not of argv.

THE MEASUREMENT (maintainer, 2026-08-28, on a map of a 142-boundary run that
had drawn exactly one file leaf): "the map rendered is whaaaaa, compared to
the actual edits and reads you have made so far over this run. Which likely
means a path assembly issue. What if we track on daemon the run path as a
part of a run card or a run file."

He was right, and the cause is structural rather than a bug in any one hop.
The room grew terrain from paths spotted in ``edge.detail`` — argv — which
loses what a run touched three separate ways:

- a heredoc names no file. ``python3 - <<'PY'`` writes whatever its body
  writes, and the body is stripped at the wire deliberately. Measured on that
  run: 67 of 142 boundaries named no path at all.
- a relative path belongs to *that boundary's* cwd, which the room never
  joined it against — so ``src/lib/x.ts`` typed from ``src/frontend`` lands
  under the wrong chamber, or under none.
- the room sees ``_CROSSINGS_MAX`` crossings plus one cursor. Eight
  boundaries out of a hundred and forty-two.

Git answers all three exactly and needs no parsing at all. These pin that.
"""

import subprocess

from brr.gates.cloud_publisher import _run_paths


def _repo(tmp_path):
    """A tree with a fork point, on a work branch — the shape a run has."""
    tree = tmp_path / "tree"
    (tree / ".brr").mkdir(parents=True)
    run = ["git", "-C", str(tree)]
    subprocess.run([*run, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*run, "config", "user.email", "t@t"], check=True)
    subprocess.run([*run, "config", "user.name", "t"], check=True)
    (tree / "seed.txt").write_text("seed\n")
    subprocess.run([*run, "add", "-A"], check=True)
    subprocess.run([*run, "commit", "-qm", "seed"], check=True)
    subprocess.run([*run, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    subprocess.run([*run, "switch", "-q", "-c", "brr/work"], check=True)
    return tree


def test_a_committed_edit_is_attested_terrain(tmp_path):
    """The case argv loses hardest: a file written inside a heredoc body and
    then committed is named nowhere on any command line."""
    tree = _repo(tmp_path)
    (tree / "src").mkdir()
    (tree / "src" / "a.ts").write_text("x\n")
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "w"], check=True)
    assert _run_paths(tree / ".brr", {}) == ["src/a.ts"]


def test_an_uncommitted_edit_and_an_untracked_file_are_both_terrain(tmp_path):
    """``git diff <base>`` compares the *working tree*, so committed and
    uncommitted come in one call. Untracked is invisible to it by definition
    and needs the second — a new file is exactly the terrain a map is for."""
    tree = _repo(tmp_path)
    (tree / "seed.txt").write_text("edited\n")  # tracked, uncommitted
    (tree / "fresh.ts").write_text("new\n")  # untracked
    assert sorted(_run_paths(tree / ".brr", {})) == ["fresh.ts", "seed.txt"]


def test_the_runtime_dir_is_machinery_never_terrain(tmp_path):
    """#1664's rule, kept: a hidden runtime directory minted fake chambers
    once already, out of the account path's own ``brnrd`` segment."""
    tree = _repo(tmp_path)
    (tree / ".brr" / "outbox").mkdir(parents=True, exist_ok=True)
    (tree / ".brr" / "outbox" / "note.md").write_text("x\n")
    (tree / "real.ts").write_text("x\n")
    assert _run_paths(tree / ".brr", {}) == ["real.ts"]


def test_a_tree_git_will_not_answer_for_yields_nothing_not_a_guess(tmp_path):
    """A missing answer is a real answer. A room that draws nothing beats a
    room that draws a guess — the whole lesson of the fake-chamber round."""
    bare = tmp_path / "not-a-repo"
    (bare / ".brr").mkdir(parents=True)
    assert _run_paths(bare / ".brr", {}) == []


def test_the_list_is_bounded(tmp_path):
    """A run that edits more than the cap has already said what its shape
    is; the tail adds rows, not terrain."""
    from brr.gates.cloud_publisher import _RUN_PATHS_MAX

    tree = _repo(tmp_path)
    for i in range(_RUN_PATHS_MAX + 20):
        (tree / f"f{i:03d}.ts").write_text("x\n")
    assert len(_run_paths(tree / ".brr", {})) == _RUN_PATHS_MAX


def test_the_room_payload_carries_the_paths(tmp_path):
    """`_boundary_row`'s lesson, applied: the field has to reach the payload
    a client actually reads, not only the helper that computes it."""
    from brr.gates.cloud_publisher import _room_payload

    tree = _repo(tmp_path)
    (tree / "src").mkdir()
    (tree / "src" / "a.ts").write_text("x\n")
    room = _room_payload(tree / ".brr", {"worktree_path": str(tree)})
    assert room is not None
    assert room["paths"] == ["src/a.ts"]
    assert room["branch"] == "brr/work"
