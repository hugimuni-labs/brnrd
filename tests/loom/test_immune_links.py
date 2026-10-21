"""40-links: a changed markdown file with a dead relative link, a link into people/, or a wiki link refuses."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from brr.loom.runtime.merge import install_pre_receive
from brr.loom.runtime.selfrepo import init_self, seed_root

from test_merge import commit, copy_seed, git, published, room, show, write

LINKS = seed_root() / "core" / "immune.d" / "40-links"
INDEX = seed_root() / "core" / "loom" / "index"


def run_links(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run([os.fspath(LINKS), *args], cwd=repo, env=env, capture_output=True, text=True)


def fold(work: Path) -> None:
    """What send-self does at a landing: regenerate the index at HEAD and amend, so 30-index is quiet."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    assert subprocess.run([os.fspath(INDEX), "--write"], cwd=work, env=env).returncode == 0
    git(work, "add", "-A")
    git(work, "commit", "--amend", "--no-edit")


def push(work: Path) -> subprocess.CompletedProcess[str]:
    fold(work)
    return git(work, "push", "origin", "HEAD:main", check=False)


def test_living_links_of_every_shape_pass(tmp_path: Path) -> None:
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "threads/one/README.md", "---\nid: one\nstatus: open\ntense: plan\n---\n# One\n")
    write(work, "threads/one/research/facts.md", "# Facts\n")
    write(work, "threads/one/café.md", "# Café\n")
    write(work, "people/ada/agreement.md", "# Agreement with ada\n")
    write(work, "people/ada/notes.md", "# Notes\n\n[the agreement](agreement.md) · [folder](../ada/)\n")
    write(work, "threads/one/design.md", "\n".join([
        "# Design",
        "",
        "[readme](README.md) [facts](research/facts.md#links) [folder](research/) [folder too](research)",
        "[root](../../AGENTS.md) [accent](caf%C3%A9.md) [spaced](<research/facts.md>) [titled](README.md \"One\")",
        "![no image here](https://example.test/x.png) [web](https://example.test/) [mail](mailto:ada@example.test)",
        "[anchor](#plan) [empty]() `[[code]]` `[inline](nope.md)`",
        "```",
        "[fenced](nope.md) [[fenced]]",
        "```",
        "~~~",
        "[tilde](nope.md)",
        "~~~",
        "    [indented](README.md) is read as prose, not code: a list's continuation line looks the same",
        "",
    ]))
    commit(work, "links that resolve")
    assert run_links(work, "0" * 40, "HEAD").returncode == 0, run_links(work, "0" * 40, "HEAD").stderr
    pushed = push(work)
    assert pushed.returncode == 0, pushed.stderr


def test_a_dead_relative_link_refuses_the_push(tmp_path: Path) -> None:
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "threads/one/README.md", "---\nid: one\nstatus: open\ntense: plan\n---\n# One\n")
    commit(work, "one")
    assert push(work).returncode == 0
    write(work, "threads/one/notes.md",
          "# Notes\n\n[gone](research/missing.md) [above](../../outside.md) [absolute](/threads/one/README.md)\n")
    commit(work, "dead links")
    pushed = push(work)
    assert pushed.returncode != 0
    assert "40-links: threads/one/notes.md: dead link: research/missing.md" in pushed.stderr
    assert "40-links: threads/one/notes.md: dead link: ../../outside.md" in pushed.stderr
    assert "40-links: threads/one/notes.md: dead link: /threads/one/README.md" in pushed.stderr
    # The same verdict without a push, on the working file.
    judged = run_links(work, "--file", "threads/one/notes.md")
    assert judged.returncode == 1
    assert "threads/one/notes.md: dead link: research/missing.md" in judged.stderr
    write(work, "threads/one/notes.md", "# Notes\n\n[here](README.md)\n")
    commit(work, "repaired")
    pushed = push(work)
    assert pushed.returncode == 0, pushed.stderr
    assert "[here](README.md)" in show(bare, "threads/one/notes.md")


def test_a_link_into_people_from_outside_refuses(tmp_path: Path) -> None:
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "people/ada/agreement.md", "# Agreement with ada\n")
    write(work, "threads/one/README.md",
          "---\nid: one\nstatus: open\ntense: plan\n---\n# One\n\n"
          "Merges per [the agreement](../../people/ada/agreement.md#merges); see [people](../../people/).\n")
    commit(work, "one")
    pushed = push(work)
    assert pushed.returncode != 0
    assert "40-links: threads/one/README.md: links into people/: ../../people/ada/agreement.md#merges" in pushed.stderr
    assert "40-links: threads/one/README.md: links into people/: ../../people/" in pushed.stderr
    assert pushed.stderr.count(" 40-links:") == 2


def test_a_wiki_link_refuses_but_a_literal_does_not(tmp_path: Path) -> None:
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "threads/one/README.md", "---\nid: one\nstatus: open\ntense: plan\n---\n# One\n")
    write(work, "threads/one/notes.md", "# Notes\n\nsee [[design]] and `calls == [[\"mock\"]]` and [[ ]]\n")
    commit(work, "wiki")
    pushed = push(work)
    assert pushed.returncode != 0
    assert "40-links: threads/one/notes.md: wiki link: [[design]]" in pushed.stderr
    assert pushed.stderr.count(" 40-links:") == 1


def test_only_changed_markdown_is_judged(tmp_path: Path) -> None:
    """A dead link that landed before the check does not refuse an unrelated change; its own edit does."""
    bare = tmp_path / "self.git"
    seed = tmp_path / "seed"
    git(None, "init", "--bare", "-b", "main", os.fspath(bare))
    git(None, "init", "-b", "main", os.fspath(seed))
    copy_seed(seed)
    (seed / "core" / "immune.d" / "40-links").unlink()
    write(seed, "threads/brnrd/README.md", "---\nid: brnrd\nstatus: open\ntense: plan\n---\n# Brnrd\n")
    write(seed, "threads/brnrd/old.md", "# Old\n\n[gone](missing.md)\n")
    write(seed, "threads/brnrd/notes.py", "# not markdown: [gone](missing.md)\n")
    commit(seed, "a self from before the check")
    git(seed, "remote", "add", "origin", os.fspath(bare))
    install_pre_receive(bare)
    assert git(seed, "push", "origin", "HEAD:main", check=False).returncode == 0
    work = room(bare, tmp_path / "w", "strand/w")
    (work / "core" / "immune.d" / "40-links").write_bytes(LINKS.read_bytes())
    (work / "core" / "immune.d" / "40-links").chmod(0o755)
    commit(work, "add the check")
    pushed = git(work, "push", "origin", "HEAD:main", check=False)
    assert pushed.returncode == 0, pushed.stderr
    write(work, "threads/brnrd/fresh.md", "# Fresh\n\n[there](README.md)\n")
    commit(work, "unrelated")
    pushed = git(work, "push", "origin", "HEAD:main", check=False)
    assert pushed.returncode == 0, pushed.stderr
    write(work, "threads/brnrd/old.md", "# Old\n\n[gone](missing.md) touched\n")
    commit(work, "touch the old file")
    pushed = git(work, "push", "origin", "HEAD:main", check=False)
    assert pushed.returncode != 0
    assert "40-links: threads/brnrd/old.md: dead link: missing.md" in pushed.stderr
    assert pushed.stderr.count(" 40-links:") == 1


def test_init_seeds_the_links_check_executable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    init_self(home, person="ada")
    self_dir = home / "self"
    assert os.access(self_dir / "core" / "immune.d" / "40-links", os.X_OK)
    assert run_links(self_dir, "0" * 40, "HEAD").returncode == 0


def test_file_judges_a_tracked_file_against_the_head_tree(tmp_path: Path) -> None:
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "threads/one/README.md", "---\nid: one\nstatus: open\ntense: plan\n---\n# One\n")
    commit(work, "one")
    # The target exists on disk but not in HEAD's tree, so the link reads dead.
    write(work, "threads/one/README.md", "---\nid: one\nstatus: open\ntense: plan\n---\n# One\n\n[notes](notes.md)\n")
    write(work, "threads/one/notes.md", "# Notes\n")
    judged = run_links(work, "--file", "threads/one/README.md")
    assert judged.returncode == 1
    assert "threads/one/README.md: dead link: notes.md" in judged.stderr
    # An untracked file is refused, not judged.
    judged = run_links(work, "--file", "threads/one/notes.md")
    assert judged.returncode == 1
    assert "threads/one/notes.md: not a tracked file" in judged.stderr
    commit(work, "notes")
    assert run_links(work, "--file", "threads/one/README.md").returncode == 0
