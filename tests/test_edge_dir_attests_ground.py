"""The daemon attests terrain it can `stat`; the browser can only pattern-match.

Measured on run-260828-1518-mwnd: 328 of its 511 boundaries ran from the repo
root and 183 from `src/frontend`. A room that grows terrain only from the cwd
therefore sees almost nothing — a resident runs
`sed -n … src/frontend/src/lib/quota.ts` from wherever it is standing.

The room used to close that gap client-side, by mining paths out of the detail
string. That is how `0.4/0.3/0.2` (a version or opacity ramp), `origin/brr/…`
(a git ref) and `pull/1671` (a URL fragment) all became rooms on the island:
**the browser can only ask what a token looks like.** Every fix for it named
one more shape and met the next shape nobody had listed.

The daemon can ask what a token *is*. These pin that the question is a `stat`
and not a pattern.
"""

from pathlib import Path

import pytest

from brr.gates.cloud_publisher import _detail_dir, _edge_dir


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "src" / "frontend" / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "brr").mkdir(parents=True)
    (tmp_path / "src" / "frontend" / "src" / "lib" / "quota.ts").write_text("x")
    return tmp_path


def test_a_detail_names_real_ground_when_the_cwd_is_the_root(tree):
    got = _edge_dir(str(tree), {}, tree / ".brr", "sed -n '1,180p' src/frontend/src/lib/quota.ts")
    assert got == "src/frontend/src/lib", "the file leaf drops; chambers are directories"


def test_the_shapes_that_kept_minting_fake_chambers_are_not_directories(tree):
    # None of these needs to be listed anywhere. They fail because they do
    # not exist, which is a property rather than a blocklist — and a
    # blocklist is what every previous attempt needed.
    for detail in (
        "stroke-opacity 0.4/0.3/0.2 across the ramp",
        "git push origin/brr/the-fuel-you-can-read",
        "gh api repos/hugimuni-labs/brnrd/pulls/1671/comments",
        "cat ~/.local/state/brnrd/accounts/acc_x/home/knowledge/index.md",
        "curl https://example.com/a/b/c",
    ):
        assert _detail_dir(detail, tree) is None, detail


def test_an_explicit_cwd_still_wins_over_the_detail(tree):
    # The detail is only consulted when the cwd says nothing — the cwd is
    # the stronger attestation and must not be second-guessed.
    got = _edge_dir(str(tree / "src" / "brr"), {}, tree / ".brr", "Edit src/frontend/src/lib/x.ts")
    assert got == "src/brr"


def test_the_deepest_real_directory_wins(tree):
    # A boundary naming both `src` and `src/frontend/src/lib` happened at the
    # more specific place. Resolving to the shallowest would stall the island
    # one level below the root forever.
    got = _detail_dir("mv src/frontend/src/lib/a.ts src/brr/", tree)
    assert got == "src/frontend/src/lib"


def test_the_tree_root_itself_is_not_a_chamber(tree):
    # `.` is where the actor already is. Attesting it as terrain would make
    # every boundary "arrive" somewhere it never left.
    assert _detail_dir("ls ./", tree) is None
    assert _edge_dir(str(tree), {}, tree / ".brr", "ls ./") == "."


def test_a_path_escaping_the_tree_is_not_its_terrain(tree):
    # `.resolve()` follows `..` and symlinks, so containment is re-checked
    # against the resolved path rather than trusted from the joined string.
    outside = tree.parent / "elsewhere"
    outside.mkdir()
    assert _detail_dir("cat ../elsewhere/x", tree) is None


def test_no_detail_leaves_the_root_reading_as_the_root(tree):
    assert _edge_dir(str(tree), {}, tree / ".brr", None) == "."
    assert _edge_dir(str(tree), {}, tree / ".brr", "") == "."


def test_a_cwd_outside_the_tree_still_degrades_to_its_basename(tree):
    # Unchanged behaviour: the wire must never carry a host path.
    got = _edge_dir("/var/tmp/somewhere", {}, tree / ".brr", "ls")
    assert got == "somewhere"
    assert "/" not in got
