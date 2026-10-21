"""INDEX.md and CLAUDE.md: derived by the self's recipe, folded in by send-self, judged by 30-index."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from brr.loom.runtime.merge import send_to_self
from brr.loom.runtime.selfrepo import init_self, seed_root

from test_merge import commit, git, published, room, show, write

INDEX = seed_root() / "core" / "loom" / "index"


def run_index(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run([os.fspath(INDEX), *args], cwd=repo, env=env, capture_output=True, text=True)


def fold(repo: Path) -> None:
    """What send-self does at a landing: regenerate at HEAD and amend."""
    assert run_index(repo, "--write").returncode == 0
    git(repo, "add", "-A")
    git(repo, "commit", "--amend", "--no-edit")


def test_the_index_derives_rows_from_the_tree(tmp_path: Path) -> None:
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "threads/old/README.md",
          "---\nid: old\nstatus: settled\ntense: plan\n---\n# Old work, done\n")
    write(work, "threads/old/report.md", "# What shipped\n\nbody\n")
    commit(work, "old thread")
    write(work, "threads/fresh/README.md",
          "---\nid: fresh\nstatus: open\ntense: plan\nopened-by: thread:brnrd\n"
          "opened-for: [w-abc]\nwish-w-abc: \"Split it out, cleanly\"\n---\n# Fresh work\n")
    write(work, "threads/fresh/research/notes.md", "---\nkind: notes\n---\n# Sources\n\n" + "x" * 3000 + "\n")
    write(work, "memory/scars/git/rebase-dirty.md", "---\ntrigger: rebase\n---\n# Rebase on a dirty tree\n")
    write(work, "memory/scars/git/index.md", "# git scars\n")
    write(work, "people/ada/agreement.md", "# Agreement with ada\n")
    commit(work, "fresh thread")
    text = run_index(work).stdout
    assert text.startswith("# Index\n")
    assert text.index("### open") < text.index("- **[fresh](threads/fresh/README.md)** · open · w-abc Split it out, cleanly · Fresh work")
    assert "  - [research/notes.md](threads/fresh/research/notes.md) · Sources · 2 KB" in text
    assert text.index("### settled (") < text.index("- **[old](threads/old/README.md)** · settled · Old work, done")
    assert "  - [report.md](threads/old/report.md) · What shipped · <1 KB" in text
    assert text.index("[fresh]") < text.index("[brnrd]") < text.index("### settled")
    assert "- [scars/git/](memory/scars/git/index.md) · 1 scars" in text
    assert "- [scars/x.md](memory/scars/x.md) · base · <1 KB" in text
    assert "- [stance.md](memory/stance.md)" in text
    assert text.rstrip().endswith("## people\n\n- ada")
    assert "Agreement with ada" not in text
    assert run_index(work).stdout == text
    assert run_index(work, "--rev", "HEAD").stdout == text


def test_thirty_index_refuses_a_stale_or_deleted_index(tmp_path: Path) -> None:
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "threads/one/README.md", "---\nid: one\nstatus: open\ntense: plan\n---\n# One\n")
    commit(work, "one")
    write(work, "INDEX.md", "# Index\n\nstale\n")
    commit(work, "stale index")
    # A raw push: send-self would regenerate first, the gate is what judges a hand push.
    pushed = git(work, "push", "origin", "HEAD:main", check=False)
    assert pushed.returncode != 0
    assert "30-index: INDEX.md: differs from core/loom/index" in pushed.stderr
    fold(work)
    pushed = git(work, "push", "origin", "HEAD:main", check=False)
    assert pushed.returncode == 0, pushed.stderr
    assert "- **[one](threads/one/README.md)** · open · One" in show(bare, "INDEX.md")
    assert show(bare, "CLAUDE.md") == show(bare, "AGENTS.md")
    git(work, "rm", "-q", "INDEX.md", "CLAUDE.md")
    git(work, "commit", "-m", "drop them")
    pushed = git(work, "push", "origin", "HEAD:main", check=False)
    assert pushed.returncode != 0
    assert "30-index: INDEX.md: deleted" in pushed.stderr
    assert "30-index: CLAUDE.md: deleted" in pushed.stderr
    # send-self repairs the same room: the deletion is undone by regeneration.
    assert send_to_self(work).status == "merged"
    assert show(bare, "CLAUDE.md") == show(bare, "AGENTS.md")


def test_thirty_index_refuses_a_hand_edited_copy(tmp_path: Path) -> None:
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "threads/one/README.md", "---\nid: one\nstatus: open\ntense: plan\n---\n# One\n")
    commit(work, "one")
    fold(work)
    write(work, "CLAUDE.md", (work / "CLAUDE.md").read_text() + "\nhand-synced\n")
    commit(work, "edit the copy")
    pushed = git(work, "push", "origin", "HEAD:main", check=False)
    assert pushed.returncode != 0
    assert "30-index: CLAUDE.md: not a copy of AGENTS.md" in pushed.stderr


def test_send_self_folds_the_index_into_the_landing(tmp_path: Path) -> None:
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "threads/two/README.md", "---\nid: two\nstatus: open\ntense: plan\n---\n# Two\n")
    write(work, "AGENTS.md", "# A self\n\nedited by hand\n")
    commit(work, "two")
    before = git(work, "rev-list", "--count", "HEAD").stdout.strip()
    landed = send_to_self(work)
    assert landed.status == "merged", landed.stderr
    assert git(bare, "rev-list", "--count", "main").stdout.strip() == before
    assert "- **[two](threads/two/README.md)** · open · Two" in show(bare, "INDEX.md")
    assert show(bare, "CLAUDE.md") == "# A self\n\nedited by hand\n"
    assert git(bare, "log", "-1", "--format=%s", "main").stdout.strip() == "two"
    # A second room lands after main moved: its index is made after the rebase.
    other = room(bare, tmp_path / "o", "strand/o")
    write(other, "threads/three/README.md", "---\nid: three\nstatus: settled\ntense: plan\n---\n# Three\n")
    commit(other, "three")
    assert send_to_self(other).status == "merged"
    text = show(bare, "INDEX.md")
    assert text.index("[two]") < text.index("### settled") < text.index("[three]")
    # Nothing of its own to land, index already fresh: nothing to push.
    idle = room(bare, tmp_path / "i", "strand/i")
    assert send_to_self(idle).status == "merged"
    assert git(bare, "log", "-1", "--format=%s", "main").stdout.strip() == "three"


def test_init_seeds_the_index_in_the_one_seed_commit(tmp_path: Path) -> None:
    home = tmp_path / "home"
    init_self(home, person="ada")
    self_dir = home / "self"
    assert git(self_dir, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    assert (self_dir / "CLAUDE.md").read_text() == (self_dir / "AGENTS.md").read_text()
    assert "- **[brnrd](threads/brnrd/README.md)** · open" in (self_dir / "INDEX.md").read_text()
    assert "- ada" in (self_dir / "INDEX.md").read_text()
    assert "Loom-Label: taint=0" in git(self_dir, "log", "-1", "--format=%B").stdout
    assert os.access(self_dir / "core" / "loom" / "index", os.X_OK)
    assert os.access(self_dir / "core" / "immune.d" / "30-index", os.X_OK)
    assert run_index(self_dir, "--check").returncode == 0
