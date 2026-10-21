"""45-scars: a changed scar under memory/scars/<domain>/ without a trigger: line or a date in its head refuses."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from brr.loom.runtime.merge import install_pre_receive
from brr.loom.runtime.selfrepo import init_self, seed_root

from test_merge import commit, copy_seed, git, published, room, show, write

SCARS = seed_root() / "core" / "immune.d" / "45-scars"
INDEX = seed_root() / "core" / "loom" / "index"


def run_scars(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run([os.fspath(SCARS), *args], cwd=repo, env=env, capture_output=True, text=True)


def fold(work: Path) -> None:
    """What send-self does at a landing: regenerate the indexes at HEAD and amend, so 30-index is quiet."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    assert subprocess.run([os.fspath(INDEX), "--write"], cwd=work, env=env).returncode == 0
    git(work, "add", "-A")
    git(work, "commit", "--amend", "--no-edit")


def push(work: Path) -> subprocess.CompletedProcess[str]:
    fold(work)
    return git(work, "push", "origin", "HEAD:main", check=False)


def test_scars_of_every_accepted_shape_pass(tmp_path: Path) -> None:
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "memory/scars/git/rebase-dirty.md",
          "# Rebase on a dirty tree\ntrigger: rebase, dirty tree, stash\n\n2026-10-02, mine. body\n")
    write(work, "memory/scars/git/force-push.md",
          "---\ntrigger: force push, --force-with-lease\n---\n# A force push after a failed rebase closes the PR\n\n"
          "Seen 2026-10-19. body\n")
    write(work, "memory/scars/shell/blank.md",
          "# The blank variable\n\n\n\nA substitution that resolves to nothing.\n\nOne.\nTwo.\nThree.\nFour.\nFive.\n"
          "Six.\n\nTrigger: rm -rf, ${VAR}, unset\n\nOn 2026-08-11 it ate a tree.\n")  # trigger is the 9th non-blank line
    write(work, "memory/scars/legacy.md", "# A file directly under memory/scars/ is the old layout: not judged\n")
    commit(work, "scars that carry their form")
    judged = run_scars(work, "0" * 40, "HEAD")
    assert judged.returncode == 0, judged.stderr
    pushed = push(work)
    assert pushed.returncode == 0, pushed.stderr
    assert "- [force-push.md](force-push.md)" in show(bare, "memory/scars/git/index.md")


def test_a_scar_without_trigger_or_date_refuses(tmp_path: Path) -> None:
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "memory/scars/git/no-trigger.md", "# A scar with no trigger\n\n2026-10-20: it happened.\n")
    write(work, "memory/scars/git/no-date.md", "# A scar with no date\ntrigger: rebase\n\nIt happened once.\n")
    write(work, "memory/scars/git/late.md",
          "# A trigger past the head\n\n" + "".join(f"line {n}\n" for n in range(1, 10))
          + "trigger: too late, this is the eleventh non-blank line\n2026-10-20\n")
    commit(work, "scars without their form")
    pushed = push(work)
    assert pushed.returncode != 0
    assert "45-scars: memory/scars/git/no-trigger.md: no trigger: line in its first 10 lines" in pushed.stderr
    assert "45-scars: memory/scars/git/no-date.md: no YYYY-MM-DD date in its first 10 lines" in pushed.stderr
    assert "45-scars: memory/scars/git/late.md: no trigger: line in its first 10 lines" in pushed.stderr
    assert "45-scars: memory/scars/git/late.md: no YYYY-MM-DD date" in pushed.stderr
    assert pushed.stderr.count(" 45-scars:") == 4
    # The same verdict without a push, on the working file.
    judged = run_scars(work, "--file", "memory/scars/git/no-trigger.md")
    assert judged.returncode == 1
    assert "memory/scars/git/no-trigger.md: no trigger: line" in judged.stderr
    write(work, "memory/scars/git/no-trigger.md", "# A scar with no trigger, now with one\ntrigger: rebase\n\n2026-10-20: it happened.\n")
    write(work, "memory/scars/git/no-date.md", "# A scar with no date, now dated\ntrigger: rebase\n\n2026-10-20: it happened once.\n")
    write(work, "memory/scars/git/late.md", "# A trigger in the head\ntrigger: in time\n2026-10-20\n")
    assert run_scars(work, "--file", "memory/scars/git/no-trigger.md").returncode == 0
    commit(work, "repaired")
    pushed = push(work)
    assert pushed.returncode == 0, pushed.stderr


def test_the_folder_index_is_not_judged(tmp_path: Path) -> None:
    """The derived index.md has neither trigger nor date of its own; it is 30-index's to judge."""
    bare = published(tmp_path)
    work = room(bare, tmp_path / "w", "strand/w")
    write(work, "memory/scars/git/one.md", "# One\ntrigger: one\n2026-10-20\n")
    commit(work, "one")
    pushed = push(work)
    assert pushed.returncode == 0, pushed.stderr
    text = show(bare, "memory/scars/git/index.md")
    assert text.startswith("# Scars: git (1)\n")
    assert run_scars(work, "--file", "memory/scars/git/index.md").returncode == 1  # judged alone, it has no form
    assert run_scars(work, bare and git(bare, "rev-parse", "main~1").stdout.strip(), "HEAD").returncode == 0


def test_only_changed_scars_are_judged(tmp_path: Path) -> None:
    """A formless scar that landed before the check does not refuse an unrelated change; its own edit does."""
    bare = tmp_path / "self.git"
    seed = tmp_path / "seed"
    git(None, "init", "--bare", "-b", "main", os.fspath(bare))
    git(None, "init", "-b", "main", os.fspath(seed))
    copy_seed(seed)
    (seed / "core" / "immune.d" / "45-scars").unlink()
    write(seed, "threads/brnrd/README.md", "---\nid: brnrd\nstatus: open\ntense: plan\n---\n# Brnrd\n")
    write(seed, "memory/scars/git/old.md", "# Old, without trigger or date\n")
    commit(seed, "a self from before the check")
    fold(seed)
    git(seed, "remote", "add", "origin", os.fspath(bare))
    install_pre_receive(bare)
    assert git(seed, "push", "origin", "HEAD:main", check=False).returncode == 0
    work = room(bare, tmp_path / "w", "strand/w")
    (work / "core" / "immune.d" / "45-scars").write_bytes(SCARS.read_bytes())
    (work / "core" / "immune.d" / "45-scars").chmod(0o755)
    commit(work, "add the check")
    pushed = push(work)
    assert pushed.returncode == 0, pushed.stderr
    write(work, "memory/scars/git/fresh.md", "# Fresh\ntrigger: fresh\n2026-10-21\n")
    commit(work, "unrelated")
    pushed = push(work)
    assert pushed.returncode == 0, pushed.stderr
    write(work, "memory/scars/git/old.md", "# Old, without trigger or date, touched\n")
    commit(work, "touch the old scar")
    pushed = push(work)
    assert pushed.returncode != 0
    assert "45-scars: memory/scars/git/old.md: no trigger: line in its first 10 lines" in pushed.stderr
    assert pushed.stderr.count(" 45-scars:") == 2


def test_init_seeds_the_scars_check_executable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    init_self(home, person="ada")
    self_dir = home / "self"
    assert os.access(self_dir / "core" / "immune.d" / "45-scars", os.X_OK)
    assert run_scars(self_dir, "0" * 40, "HEAD").returncode == 0
