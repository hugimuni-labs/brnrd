"""A wake comes from the self's own recipe, at one commit, in a clone.

Real git, in tmp_path. The forge runs the rest of the suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from brr.loom.runtime import selfrepo
from brr.loom.runtime.selfrepo import ReadmeError, SelfError, check_readme_file, parse_readme

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "readmes"
CHECKER = selfrepo.seed_root() / "core" / "immune.d" / "10-readme"

assert Path(selfrepo.__file__).resolve().is_relative_to(ROOT)

NAMED_WHY = {
    "no-frontmatter.md": "frontmatter missing",
    "unclosed.md": "frontmatter unclosed",
    "bad-line.md": "frontmatter line not key: value: not a key",
    "bad-status.md": "status nope is not one of open|live|dormant|settled|retired",
    "missing-status.md": "status missing",
    "bad-tense.md": "tense past is not one of plan|reference",
    "missing-tense.md": "tense missing",
    "reference-open.md": "tense reference requires status settled",
    "no-heading.md": "heading missing",
    "h2.md": "heading must be a single '# ' line",
    "prose-first.md": "heading must be a single '# ' line",
    "missing-id.md": "id missing",
    "empty-id.md": "id missing",
    "dup-status.md": "duplicate key: status",
    "bad-list.md": "list unclosed: limbs",
    "malformed-list.md": "list malformed: limbs",
    "for-list.md": "for must be a string",
}


def _env(**extra: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    env.update(extra)
    return env


def _run(cmd: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env or _env(), capture_output=True, text=True)


def cli(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = _env(PYTHONPATH=str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-m", "brr.loom.runtime", *args],
        cwd=ROOT,
        env=merged,
        capture_output=True,
        text=True,
    )


def commit(repo: Path, message: str, date: str | None = None) -> None:
    extra = {}
    if date:
        extra["GIT_AUTHOR_DATE"] = date
        extra["GIT_COMMITTER_DATE"] = date
    env = _env(
        GIT_AUTHOR_NAME="brnrd-loom",
        GIT_AUTHOR_EMAIL="loom@localhost",
        GIT_COMMITTER_NAME="brnrd-loom",
        GIT_COMMITTER_EMAIL="loom@localhost",
        **extra,
    )
    added = _run(["git", "add", "-A"], repo, env=env)
    assert added.returncode == 0, added.stderr
    done = _run(["git", "-c", "commit.gpgsign=false", "commit", "-m", message], repo, env=env)
    assert done.returncode == 0, done.stderr


def wake(clone: Path, thread: str, owed: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    if "LOOM_PART" not in extra:
        part = clone / ".loom-part"
        if not part.exists():
            part.write_text("loom part goes here verbatim\n", encoding="utf-8")
        extra["LOOM_PART"] = str(part)
    return _run([str(clone / "core" / "loom" / "wake"), thread, str(owed)], clone, env=_env(**extra))


def write_thread(self_dir: Path, thread: str, headline: str, note: str | None = None) -> None:
    folder = self_dir / "threads" / thread
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text(
        "---\n"
        f"id: {thread}\n"
        "status: open\n"
        "tense: plan\n"
        "---\n"
        f"# {headline}\n\n"
        "The page.\n",
        encoding="utf-8",
    )
    if note is not None:
        notes = folder / "notes"
        notes.mkdir()
        (notes / "from-inbox.md").write_text(note, encoding="utf-8")


def test_init_layout_noop_and_remote(tmp_path: Path) -> None:
    home = tmp_path / "home"
    bare = tmp_path / "bare.git"
    made = _run(["git", "init", "--bare", "-b", "main", str(bare)], tmp_path)
    assert made.returncode == 0, made.stderr

    first = cli(["init", "--home", str(home), "--person", "arseni", "--remote", str(bare)])
    assert first.returncode == 0, first.stderr
    assert first.stdout.startswith("initialized ")
    self_dir = home / "self"
    expected = [
        "core/identity.md",
        "core/loom/wake",
        "core/loom/README.md",
        "core/immune.d/10-readme",
        "memory/stance.md",
        "memory/scars/.gitkeep",
        "memory/itches/.gitkeep",
        "memory/moves/.gitkeep",
        "threads/inbox/README.md",
        "people/.gitkeep",
        "people/arseni/agreement.md",
    ]
    for rel in expected:
        assert (self_dir / rel).exists(), rel
    assert os.access(self_dir / "core" / "loom" / "wake", os.X_OK)
    log = _run(["git", "log", "--format=%an %ae", "HEAD"], self_dir)
    assert log.stdout.splitlines() == ["brnrd-loom loom@localhost"]
    count = _run(["git", "rev-list", "--count", "HEAD"], self_dir)
    assert count.stdout.strip() == "1"
    check_readme_file(self_dir / "threads" / "inbox" / "README.md")
    assert "arseni" in (self_dir / "people" / "arseni" / "agreement.md").read_text(encoding="utf-8")

    pushed = _run(["git", "rev-parse", "main"], bare)
    assert pushed.returncode == 0, pushed.stderr
    assert pushed.stdout.strip() == _run(["git", "rev-parse", "HEAD"], self_dir).stdout.strip()

    identity = self_dir / "core" / "identity.md"
    identity.write_text(identity.read_text(encoding="utf-8") + "kept\n", encoding="utf-8")
    head = _run(["git", "rev-parse", "HEAD"], self_dir).stdout
    again = cli(["init", "--home", str(home), "--person", "other"])
    assert again.returncode == 0, again.stderr
    assert again.stdout.strip() == f"self already exists: {self_dir}"
    assert "kept" in identity.read_text(encoding="utf-8")
    assert not (self_dir / "people" / "other").exists()
    assert _run(["git", "rev-parse", "HEAD"], self_dir).stdout == head

    with pytest.raises(SelfError, match="single path segment"):
        selfrepo.init_self(tmp_path / "escaped", person="../out")


def test_wake_carries_the_self_and_skips_hearth(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert cli(["init", "--home", str(home), "--person", "arseni"]).returncode == 0
    self_dir = home / "self"
    write_thread(self_dir, "w-1", "The w-1 headline", "unique-note-9c1 is the whole note\n")
    hearth = self_dir / "people" / "sam" / "hearth"
    hearth.mkdir(parents=True)
    (hearth / "secret.md").write_text("HEARTH-SECRET-should-not-appear\n", encoding="utf-8")
    (self_dir / "people" / "sam" / "agreement.md").write_text("sam agrees in public\n", encoding="utf-8")
    commit(self_dir, "add w-1")
    clone = selfrepo.room(home, "s1")
    owed = tmp_path / "owed.json"
    owed.write_text(json.dumps([
        {"id": "p-arseni/ab12", "from": "person:arseni", "body": "owed-body-line-one\nowed-body-line-two\n"},
    ]), encoding="utf-8")
    result = wake(clone, "w-1", owed)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    identity = (clone / "core" / "identity.md").read_text(encoding="utf-8")
    stance = (clone / "memory" / "stance.md").read_text(encoding="utf-8")
    readme = (clone / "threads" / "w-1" / "README.md").read_text(encoding="utf-8")
    assert identity in out
    assert stance in out
    assert readme in out
    assert "unique-note-9c1 is the whole note" in out
    assert "owed-body-line-one\nowed-body-line-two" in out
    assert "p-arseni/ab12" in out
    assert "person:arseni" in out
    assert "loom part goes here verbatim" in out
    # First line of the file, which for a README is the frontmatter marker.
    assert "threads/inbox/README.md · --- · " in out
    assert "people/sam/agreement.md · sam agrees in public · " in out
    assert "HEARTH-SECRET-should-not-appear" not in out
    assert "people/sam/hearth/" not in out
    assert "threads/w-1/notes/" not in out.split("## Tree\n", 1)[1]


def test_the_self_decides(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert cli(["init", "--home", str(home)]).returncode == 0
    self_dir = home / "self"
    write_thread(self_dir, "w-1", "Headline")
    commit(self_dir, "thread")
    first = selfrepo.room(home, "old")
    identity = self_dir / "core" / "identity.md"
    identity.write_text(identity.read_text(encoding="utf-8") + "UNIQUE-LINE-7f3a\n", encoding="utf-8")
    commit(self_dir, "rewrite identity")
    fresh = selfrepo.room(home, "fresh")
    owed = tmp_path / "owed.json"
    owed.write_text("[]", encoding="utf-8")
    new = wake(fresh, "w-1", owed)
    old = wake(first, "w-1", owed)
    assert new.returncode == 0, new.stderr
    assert "UNIQUE-LINE-7f3a" in new.stdout
    assert "UNIQUE-LINE-7f3a" not in old.stdout

    recipe = self_dir / "core" / "loom" / "wake"
    recipe.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
    recipe.chmod(0o755)
    commit(self_dir, "replace the recipe")
    custom = selfrepo.room(home, "custom")
    via_cli = cli(["wake", "--room", str(custom), "--thread", "w-1", "--owed", str(owed)])
    assert via_cli.returncode == 0, via_cli.stderr
    assert via_cli.stdout.strip() == "custom"
    seed_line = "I am a placeholder."
    assert seed_line not in via_cli.stdout


def test_budget_cuts_the_tree_and_refuses_a_fat_identity(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert cli(["init", "--home", str(home)]).returncode == 0
    self_dir = home / "self"
    write_thread(self_dir, "w-1", "Headline")
    for index, token in enumerate(("OLDEST-AAAA", "MID-BBBB", "MID-CCCC", "MID-DDDD", "MID-EEEE", "NEWEST-FFFF")):
        scar = self_dir / "memory" / "scars" / f"f{index}.md"
        scar.write_text(token + " " + ("x" * 80) + "\n", encoding="utf-8")
        commit(self_dir, f"scar {index}", date=f"2026-12-01 00:0{index}:00 +0000")
    clone = selfrepo.room(home, "s1")
    owed = tmp_path / "owed.json"
    owed.write_text("[]", encoding="utf-8")
    full = wake(clone, "w-1", owed, LOOM_BUDGET_BYTES="100000")
    assert full.returncode == 0, full.stderr
    tree = full.stdout.split("## Tree\n", 1)[1]
    assert "omitted" not in tree
    lines = [line for line in tree.splitlines() if line.startswith("memory/scars/")]
    assert [line.split(" · ", 1)[0] for line in lines] == [
        "memory/scars/f5.md",
        "memory/scars/f4.md",
        "memory/scars/f3.md",
        "memory/scars/f2.md",
        "memory/scars/f1.md",
        "memory/scars/f0.md",
    ]
    all_lines = [line for line in tree.splitlines() if " · " in line]
    head, _, _rest = full.stdout.partition("## Tree\n")
    keep = "".join(f"{line}\n" for line in all_lines[:2])
    limit = len((head + "## Tree\n" + keep).encode("utf-8"))
    cut = wake(clone, "w-1", owed, LOOM_BUDGET_BYTES=str(limit))
    assert cut.returncode == 0, cut.stderr
    omitted = len(all_lines) - 2
    assert cut.stdout.rstrip().endswith(f"omitted {omitted} entries; each is one read away")
    assert "NEWEST-FFFF" in cut.stdout
    assert "MID-EEEE" in cut.stdout
    assert "OLDEST-AAAA" not in cut.stdout
    assert head in cut.stdout

    refused = wake(clone, "w-1", owed, LOOM_BUDGET_BYTES="30")
    assert refused.returncode == 3
    assert refused.stdout == ""
    assert "over budget" in refused.stderr
    assert "core/identity.md" in refused.stderr
    assert "none of these is cut" in refused.stderr

    broken = wake(clone, "w-1", owed, LOOM_PART="")
    # empty string is "unset" in the recipe only when the variable is missing;
    # a set-but-empty value is not a file.
    assert broken.returncode == 1
    assert "LOOM_PART" in broken.stderr


def test_room_is_a_clone_and_reused(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert cli(["init", "--home", str(home)]).returncode == 0
    source_head = _run(["git", "rev-parse", "HEAD"], home / "self").stdout.strip()
    made = selfrepo.room(home, "s1")
    again = selfrepo.room(home, "s1")
    assert again == made
    branch = _run(["git", "branch", "--show-current"], made)
    assert branch.stdout.strip() == "strand/s1"
    common = _run(["git", "rev-parse", "--git-common-dir"], made).stdout.strip()
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = (made / common_path).resolve()
    assert common_path != (home / "self" / ".git").resolve()
    alternates = made / ".git" / "objects" / "info" / "alternates"
    assert alternates.is_file()
    assert str((home / "self" / ".git" / "objects").resolve()) in alternates.read_text(encoding="utf-8")
    marker = made / "core" / "identity.md"
    marker.write_text(marker.read_text(encoding="utf-8") + "LOCAL-ONLY\n", encoding="utf-8")
    assert selfrepo.room(home, "s1") == made
    assert "LOCAL-ONLY" in marker.read_text(encoding="utf-8")
    assert _run(["git", "rev-parse", "HEAD"], home / "self").stdout.strip() == source_head
    with pytest.raises(SelfError, match="single path segment"):
        selfrepo.room(home, "a/b")


def test_readme_fixtures_agree() -> None:
    syntax = _run(["sh", "-n", str(CHECKER)], ROOT)
    assert syntax.returncode == 0, syntax.stderr
    dash = _run(["dash", "-n", str(CHECKER)], ROOT)
    assert dash.returncode == 0, dash.stderr

    ok = sorted((FIXTURES / "ok").glob("*.md"))
    bad = sorted((FIXTURES / "bad").glob("*.md"))
    assert len(ok) >= 5
    assert len(bad) >= 10
    for path in ok:
        parsed = parse_readme(path.read_text(encoding="utf-8"))
        assert parsed["id"]
        assert parsed["headline"]
        checked = _run([str(CHECKER), "--file", str(path)], ROOT)
        assert checked.returncode == 0, f"{path.name}: {checked.stderr}"
        assert checked.stderr == ""
    for path in bad:
        with pytest.raises(ReadmeError) as caught:
            parse_readme(path.read_text(encoding="utf-8"))
        checked = _run([str(CHECKER), "--file", str(path)], ROOT)
        assert checked.returncode == 1, path.name
        assert checked.stderr == f"{path}: {caught.value.why}\n"
        if path.name in NAMED_WHY:
            assert caught.value.why == NAMED_WHY[path.name]

    full = parse_readme((FIXTURES / "ok" / "full.md").read_text(encoding="utf-8"))
    assert full["id"] == "w-130"
    assert full["status"] == "open"
    assert full["tense"] == "plan"
    assert full["limbs"] == []
    assert full["blocked-on"] == ""
    assert full["headline"] == "What this thread is for"
    quoted = parse_readme((FIXTURES / "ok" / "quoted-limbs.md").read_text(encoding="utf-8"))
    assert quoted["limbs"] == ["send", "recall"]
    assert quoted["status"] == "dormant"
    held = parse_readme((FIXTURES / "ok" / "for-name.md").read_text(encoding="utf-8"))
    assert held["for"] == "brnrd"
    assert "for" not in parse_readme((FIXTURES / "ok" / "minimal.md").read_text(encoding="utf-8"))


def test_range_check_and_folder_rule(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert cli(["init", "--home", str(home)]).returncode == 0
    self_dir = home / "self"
    zeros = "0" * 40
    boot = _run([str(CHECKER), zeros, "HEAD"], self_dir)
    assert boot.returncode == 0, boot.stderr

    bad = self_dir / "threads" / "w-bad"
    bad.mkdir(parents=True)
    (bad / "README.md").write_text(
        "---\nid: w-bad\nstatus: nope\ntense: plan\n---\n# Nope\n",
        encoding="utf-8",
    )
    commit(self_dir, "bad readme")
    refused = _run([str(CHECKER), "HEAD~1", "HEAD"], self_dir)
    assert refused.returncode == 1
    assert refused.stderr == (
        "threads/w-bad/README.md: status nope is not one of open|live|dormant|settled|retired\n"
    )
    with pytest.raises(ReadmeError, match="status nope"):
        check_readme_file(bad / "README.md")

    mismatch = self_dir / "threads" / "w-1"
    mismatch.mkdir()
    (mismatch / "README.md").write_text(
        "---\nid: other\nstatus: open\ntense: plan\n---\n# Mismatch\n",
        encoding="utf-8",
    )
    file_check = _run([str(CHECKER), "--file", str(mismatch / "README.md")], self_dir)
    assert file_check.returncode == 1
    assert file_check.stderr.strip().endswith("id other is not folder w-1")
    with pytest.raises(ReadmeError, match="id other is not folder w-1"):
        check_readme_file(mismatch / "README.md")
    # The text parser cannot see the folder, and does not pretend to.
    assert parse_readme((mismatch / "README.md").read_text(encoding="utf-8"))["id"] == "other"
    commit(self_dir, "id does not match the folder")

    scar = self_dir / "memory" / "scars" / "x.md"
    scar.write_text("a scar\n", encoding="utf-8")
    commit(self_dir, "unrelated")
    quiet = _run([str(CHECKER), "HEAD~1", "HEAD"], self_dir)
    assert quiet.returncode == 0, quiet.stderr
    everything = _run([str(CHECKER), zeros, "HEAD"], self_dir)
    assert everything.returncode == 1
    assert "threads/w-bad/README.md:" in everything.stderr
    assert "threads/w-1/README.md:" in everything.stderr


def test_missing_loom_part_and_bad_owed_are_loud(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert cli(["init", "--home", str(home)]).returncode == 0
    clone = selfrepo.room(home, "s1")
    owed = tmp_path / "owed.json"
    owed.write_text("[]", encoding="utf-8")
    recipe = clone / "core" / "loom" / "wake"
    missing = _run([str(recipe), "inbox", str(owed)], clone, env=_env())
    assert missing.returncode == 1
    assert "LOOM_PART is not set" in missing.stderr
    owed.write_text('{"id": "nope"}', encoding="utf-8")
    part = tmp_path / "part"
    part.write_text("part\n", encoding="utf-8")
    bad = _run([str(recipe), "inbox", str(owed)], clone, env=_env(LOOM_PART=str(part)))
    assert bad.returncode == 1
    assert "expected a list" in bad.stderr
    (tmp_path / "empty.json").write_text("[]", encoding="utf-8")
    missing_thread = _run(
        [str(recipe), "no-such", str(tmp_path / "empty.json")],
        clone,
        env=_env(LOOM_PART=str(part)),
    )
    assert missing_thread.returncode == 1
    assert "threads/no-such/README.md" in missing_thread.stderr
    bad_room = cli(["wake", "--room", str(tmp_path), "--thread", "inbox"])
    assert bad_room.returncode == 1
    assert "no self clone" in bad_room.stderr
