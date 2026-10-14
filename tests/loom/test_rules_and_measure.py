"""The self's rules (trigger / wake) and a goal thread's measure, through the real callers.

The wake recipe runs as a subprocess in a temp self; the gate runs as
``core/immune <old> <new>`` in a temp repo. Real git, in tmp_path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "src" / "brr" / "loom" / "runtime" / "seed"
_PINNED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR")
ZEROS = "0" * 40


def _env(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _PINNED}
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@localhost",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@localhost",
    )
    env.update(extra)
    return env


def run(cmd: list[str], cwd: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=_env(**extra), capture_output=True, text=True)


def git(repo: Path, *args: str) -> str:
    proc = run(["git", *args], repo)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def write(root: Path, rel: str, text: str, *, exe: bool = False) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if exe:
        path.chmod(0o755)


def commit(repo: Path, message: str = "c") -> str:
    git(repo, "add", "-A")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def a_self(tmp: Path, readme: str | None = None) -> Path:
    root = tmp / "self"
    shutil.copytree(SEED, root)
    for rel in ("core/loom/wake", "core/immune", "core/immune.d/10-readme",
                "core/immune.d/20-labels", "core/immune.d/30-rules",
                "core/immune.d/50-core-notice"):
        (root / rel).chmod(0o755)
    if readme is not None:
        write(root, "threads/inbox/README.md", readme)
    # 20-labels lets only an enrolled committer push unlabeled: enroll the test's.
    listing = root / "core/loom/people-commit"
    listing.write_text(listing.read_text(encoding="utf-8") + "t@localhost\n", encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    commit(root, "seed")
    return root


def rule(root: Path, ident: str, text: str, when: str | None = None, extra: str = "") -> None:
    head = f"---\nid: {ident}\n" + (f"when: {when}\n" if when is not None else "") + extra + "---\n"
    write(root, f"memory/rules/{ident}.md", head + text + "\n")


def wake(root: Path, letters: list[dict] | None = None, **extra: str) -> subprocess.CompletedProcess[str]:
    owed = root.parent / "owed.json"
    owed.write_text(json.dumps(letters or []), encoding="utf-8")
    part = root.parent / "part"
    part.write_text("loom part\n", encoding="utf-8")
    extra.setdefault("LOOM_PART", str(part))
    return run([str(root / "core/loom/wake"), "inbox", str(owed)], root, **extra)


def immune(root: Path, old: str, new: str, check: str | None = None) -> subprocess.CompletedProcess[str]:
    if check is None:
        return run([str(root / "core/immune"), old, new], root)
    return run([str(root / "core/immune.d" / check), old, new], root)


GOAL = (
    "---\nid: inbox\nstatus: open\ntense: goal\nmeasure: {measure}\n---\n"
    "# Close the gap\n\nThe goal.\n"
)


# --- slice 1: rules -------------------------------------------------------------


def test_trigger_matches_whole_word_and_is_named(tmp_path: Path) -> None:
    root = a_self(tmp_path)
    rule(root, "no-migrations", "Never touch `migrations/`.", when="[migration, \"schema change\"]")
    rule(root, "squash", "Squash merge.", when="[merge]")
    commit(root)

    letter = {"id": "l1", "from": "arseni", "body": "Plan the Schema\n  Change for tomorrow."}
    out = wake(root, [letter])
    assert out.returncode == 0, out.stderr
    assert "## Rules\n### no-migrations · when: schema change\nNever touch `migrations/`.\n" in out.stdout
    # "merge" is not a whole word anywhere ("emerge" would not count either).
    assert "### squash" not in out.stdout
    # An unrendered rule stays one line in the tree; a rendered one does not.
    assert "memory/rules/squash.md · Squash merge." in out.stdout
    assert "memory/rules/no-migrations.md ·" not in out.stdout

    emerge = wake(root, [{"id": "l2", "from": "a", "body": "the plan will emerge"}])
    assert "### squash" not in emerge.stdout
    assert "## Rules" not in emerge.stdout


def test_trigger_reads_the_thread_readme(tmp_path: Path) -> None:
    root = a_self(tmp_path, "---\nid: inbox\nstatus: open\ntense: plan\n---\n# Inbox\n\nWe MERGE here.\n")
    rule(root, "squash", "Squash merge.", when="[merge]")
    commit(root)
    out = wake(root)
    assert out.returncode == 0, out.stderr
    assert "### squash · when: merge\nSquash merge.\n" in out.stdout


def test_wake_rule_is_always_present(tmp_path: Path) -> None:
    root = a_self(tmp_path)
    rule(root, "subjects", "Imperative commit subjects.")
    commit(root)
    out = wake(root)
    assert out.returncode == 0, out.stderr
    assert "## Rules\n### subjects · wake\nImperative commit subjects.\n" in out.stdout
    # Rules sit after the thread, before the tree and the loom part.
    order = [out.stdout.index(mark) for mark in ("## threads/inbox/README.md", "## Rules", "## Tree", "## Loom")]
    assert order == sorted(order)


def test_wake_rules_over_budget_exit_3(tmp_path: Path) -> None:
    root = a_self(tmp_path)
    rule(root, "huge", "x" * 5000)
    commit(root)
    out = wake(root, LOOM_BUDGET_BYTES="3000")
    assert out.returncode == 3
    assert "Rules " in out.stderr and "none of these is cut" in out.stderr


def test_cut_order_tree_first_then_matched_rules(tmp_path: Path) -> None:
    root = a_self(tmp_path)
    for index in range(30):
        write(root, f"memory/scars/s{index:02}.md", f"scar {index}\n")
    rule(root, "a-first", "A " + "a" * 300, when="[deploy]")
    rule(root, "b-second", "B " + "b" * 300, when="[deploy]")
    commit(root)
    letter = [{"id": "l", "from": "p", "body": "deploy it"}]
    full = wake(root, letter)
    assert full.returncode == 0
    base = len(full.stdout.encode()) - len(full.stdout.split("## Tree\n", 1)[1].split("## Loom", 1)[0].encode())
    base -= len("## Tree\n")

    # Room for both rules but not the tree: the tree goes, both rules stay.
    tight = wake(root, letter, LOOM_BUDGET_BYTES=str(base + 20))
    assert tight.returncode == 0, tight.stderr
    assert "### a-first" in tight.stdout and "### b-second" in tight.stdout
    assert "omitted" in tight.stdout and "left out" not in tight.stdout

    # Room for one rule: the last id is cut and named.
    tighter = wake(root, letter, LOOM_BUDGET_BYTES=str(base - 250))
    assert tighter.returncode == 0, tighter.stderr
    assert "### a-first" in tighter.stdout and "### b-second" not in tighter.stdout
    assert "left out 1 matched rules: b-second; each is one read away\n" in tighter.stdout


def test_gate_refuses_check_unknown_keys_and_bad_ids(tmp_path: Path) -> None:
    root = a_self(tmp_path)
    old = git(root, "rev-parse", "HEAD")
    rule(root, "guarded", "Never touch migrations/.", extra="check: paths-untouched migrations/\n")
    rule(root, "odd", "Odd.", extra="keep: wake\n")
    rule(root, "empty-when", "Nothing.", when="[]")
    write(root, "memory/rules/wrong.md", "---\nid: other\n---\nText.\n")
    write(root, "memory/rules/notes.txt", "stray\n")
    rule(root, "fine", "Fine.", when="[deploy]")
    new = commit(root)
    out = immune(root, old, new)
    assert out.returncode == 1
    err = out.stderr
    assert "30-rules: memory/rules/guarded.md: check: not supported yet" in err
    assert "30-rules: memory/rules/odd.md: unknown key: keep (keys: id, when)" in err
    assert "30-rules: memory/rules/empty-when.md: when is empty; drop it for a wake rule" in err
    assert "30-rules: memory/rules/wrong.md: id other is not file stem wrong" in err
    assert "30-rules: memory/rules/notes.txt: not a rule file" in err
    assert "fine.md" not in err

    # The first push judges every rule on the tip.
    first = immune(root, ZEROS, new, "30-rules")
    assert first.returncode == 1 and "guarded.md" in first.stderr

    # The wake never guesses past a rule the gate would refuse.
    refused = wake(root)
    assert refused.returncode == 1
    assert "memory/rules/empty-when.md" in refused.stderr or "notes.txt" in refused.stderr


def test_gate_passes_valid_rules(tmp_path: Path) -> None:
    root = a_self(tmp_path)
    old = git(root, "rev-parse", "HEAD")
    rule(root, "squash", "Squash merge.", when="[merge, \"pull request\"]")
    rule(root, "voice", "Exact and dry.")
    new = commit(root)
    out = immune(root, old, new, "30-rules")
    assert out.returncode == 0, out.stderr


# --- slice 2: a goal thread's measure -------------------------------------------


def test_goal_without_measure_is_refused(tmp_path: Path) -> None:
    root = a_self(tmp_path)
    old = git(root, "rev-parse", "HEAD")
    write(root, "threads/inbox/README.md", "---\nid: inbox\nstatus: open\ntense: goal\n---\n# Goal\n")
    new = commit(root)
    out = immune(root, old, new)
    assert out.returncode == 1
    assert "10-readme: threads/inbox/README.md: tense goal requires measure" in out.stderr


def test_measure_must_be_committed_executable(tmp_path: Path) -> None:
    root = a_self(tmp_path)
    old = git(root, "rev-parse", "HEAD")
    write(root, "threads/inbox/README.md", GOAL.format(measure="threads/inbox/measure"))
    new = commit(root)
    missing = immune(root, old, new, "10-readme")
    assert missing.returncode == 1
    assert "measure threads/inbox/measure missing at" in missing.stderr

    write(root, "threads/inbox/measure", "#!/bin/sh\necho 3 of 5\n")
    plain = commit(root)
    refused = immune(root, old, plain, "10-readme")
    assert "measure threads/inbox/measure is not executable (mode 100644, want 100755)" in refused.stderr

    (root / "threads/inbox/measure").chmod(0o755)
    good = commit(root)
    assert immune(root, old, good, "10-readme").returncode == 0

    # Breaking the measure later, without touching the README, is judged too.
    (root / "threads/inbox/measure").unlink()
    gone = commit(root)
    later = immune(root, good, gone, "10-readme")
    assert later.returncode == 1
    assert "threads/inbox/README.md: measure threads/inbox/measure missing" in later.stderr


def test_any_thread_may_declare_a_measure(tmp_path: Path) -> None:
    root = a_self(tmp_path, "---\nid: inbox\nstatus: open\ntense: plan\nmeasure: ../up\n---\n# Inbox\n")
    out = immune(root, ZEROS, git(root, "rev-parse", "HEAD"), "10-readme")
    assert out.returncode == 1
    assert "measure is not a path inside the self: ../up" in out.stderr


def test_measure_output_is_the_distance(tmp_path: Path) -> None:
    root = a_self(tmp_path, GOAL.format(measure="threads/inbox/measure"))
    write(root, "threads/inbox/measure", "#!/bin/sh\npwd\necho 'target 10 · now 7'\n", exe=True)
    commit(root)
    rule(root, "subjects", "Imperative commit subjects.")
    commit(root)
    out = wake(root)
    assert out.returncode == 0, out.stderr
    assert f"## Distance\n{root.resolve()}\ntarget 10 · now 7\n" in out.stdout
    order = [out.stdout.index(mark) for mark in
             ("## memory/stance.md", "## Distance", "## threads/inbox/README.md", "## Rules")]
    assert order == sorted(order)


def test_measure_output_is_capped(tmp_path: Path) -> None:
    root = a_self(tmp_path, GOAL.format(measure="m"))
    write(root, "m", "#!/bin/sh\nhead -c 5000 /dev/zero | tr '\\0' x\n", exe=True)
    commit(root)
    out = wake(root)
    assert out.returncode == 0
    distance = out.stdout.split("## Distance\n", 1)[1].split("## threads", 1)[0]
    assert "x" * 1024 + "\n(cut at 1024 of 5000 bytes)\n" == distance


def test_failing_measure_renders_and_the_wake_goes_on(tmp_path: Path) -> None:
    root = a_self(tmp_path, GOAL.format(measure="m"))
    write(root, "m", "#!/bin/sh\necho partial\necho 'no api key' >&2\nexit 4\n", exe=True)
    commit(root)
    out = wake(root)
    assert out.returncode == 0, out.stderr
    assert "## Distance\nmeasure failed: m: exit 4\nno api key\n" in out.stdout

    write(root, "m", "#!/bin/sh\nsleep 30 &\nsleep 30\n", exe=True)
    slow = wake(root, LOOM_MEASURE_SECONDS="0.5")
    assert slow.returncode == 0
    assert "## Distance\nmeasure failed: m: timed out after 0.5s\n" in slow.stdout

    (root / "m").unlink()
    gone = wake(root)
    assert gone.returncode == 0
    assert "## Distance\nmeasure failed: m: no such file\n" in gone.stdout
