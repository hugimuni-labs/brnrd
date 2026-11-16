"""Limbs (threads/limbs): a page per service, checked at load, and the stand-in made from it."""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import limbs
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append
from test_guards import _repo

REPO = Path(__file__).resolve().parents[2]


def test_every_product_page_loads_and_passes_its_examples():
    pages = limbs.products()
    assert [p.name for p in pages] == ["github"]
    github = pages[0]
    assert github.command == "gh"
    assert set(github.table) == set(limbs.ROWS)
    assert len(github.examples) >= len(github.rules)


def test_the_generated_readme_is_current():
    assert (REPO / "limbs" / "README.md").read_text() == limbs.readme(limbs.products()), (
        "limbs/README.md is stale: run `brnrd limbs --write`")


def test_a_wheel_carries_the_same_pages():
    link = REPO / "src" / "brr" / "loom" / "runtime" / "limb_pages"
    assert link.is_symlink() and link.resolve() == (REPO / "limbs").resolve()


PAGE = """# Thing
way: command `thing`

| row   | what        | how     |
|-------|-------------|---------|
| ask   | ship        | his word |
| never | burn        | refused |

```limb
never  thing burn
  say: no burning
ask publish  thing ship  {tag}
  skip: -m --message
  target: {tag}
e.g. never  thing burn now
e.g. ask    thing ship -m v1 v2
e.g. do     thing look
```
"""


def test_a_good_page_parses():
    page = limbs.parse("thing", PAGE)
    row, rule = limbs.decide(page.rules, ["thing", "ship", "-m", "v1", "v2"])
    assert row == "ask" and limbs.match(rule, ["thing", "ship", "-m", "v1", "v2"]) == {"tag": "v2"}


@pytest.mark.parametrize("change,error", [
    (("e.g. do     thing look", "e.g. do     thing burn"), r"e\.g\. do thing burn lands in never"),
    (("e.g. never  thing burn now\n", ""), "no e.g. line is decided by: never  thing burn"),
    (("| never | burn        | refused |\n", ""), "a never rule, but the table has no never row"),
    (("never  thing burn\n  say: no burning\n", ""), "the table has a never row and the small print no never rule"),
    (("  say: no burning\n", ""), r"a never rule says what it refuses"),
    (("ask publish", "ask ship-it"), "an ask rule names its class first"),
    (("  target: {tag}", "  target: {other}"), r"uses \{other\}, which the rule does not capture"),
    (("e.g. do     thing look", "e.g. maybe  thing look"), r"an example is 'e\.g\. <row> <command>'"),
    (("ask publish  thing ship", "maybe  thing ship"), "a line starts with look, do, ask, never or e.g."),
    (("```limb", "```"), "one ```limb block"),
    (("  skip: -m --message", "  skip -m"), "is not one of say:"),
])
def test_a_page_that_disagrees_with_itself_fails_at_load(change, error):
    with pytest.raises(limbs.LimbError, match=error):
        limbs.parse("thing", PAGE.replace(*change))


GITHUB = limbs.product("github")


@pytest.mark.parametrize("argv,row", [
    # A value flag's value is not the positional: the shell text read these flag by flag.
    (["gh", "repo", "delete", "--yes"], "never"),
    (["gh", "api", "-X", "delete", "repos/o/r"], "never"),
    (["gh", "api", "-f", "a=b", "--method", "DELETE", "repos/o/r"], "never"),
    (["gh", "api", "-H", "Accept: x", "-XDELETE", "/repos/o/r/"], "never"),
    # A DELETE below a repository is not deleting it.
    (["gh", "api", "--method=DELETE", "repos/o/r/issues/comments/1"], "do"),
    (["gh", "api", "-X", "GET", "repos/o/r"], "do"),
    (["gh", "api", "repos/o/r", "-F", "x=1"], "do"),
    (["gh", "api", "repos/o/r/pulls/7/merge", "-X", "PUT"], "ask"),
    (["gh", "release", "create", "--target", "main", "--notes-file", "n.md", "v1.2.3"], "ask"),
    (["gh", "pr", "view", "--json", "title"], "look"),
    (["gh", "pr", "merged"], "do"),
])
def test_github_rows(argv, row):
    assert limbs.decide(GITHUB.rules, argv)[0] == row


# What a naive word match misses: the value of a flag that takes one is never the
# PR or the tag, so a merge or a release cannot slip past its ask onto the wrong target.
@pytest.mark.parametrize("argv,found", [
    (["gh", "pr", "merge", "--subject", "1234", "2610"], {"pr": "2610", "repo": ""}),
    (["gh", "pr", "merge", "-b", "fixes 99", "-R", "o/r", "2610"], {"pr": "2610", "repo": "o/r"}),
    (["gh", "pr", "merge", "--repo=o/r", "--admin", "2610"], {"pr": "2610", "repo": "o/r"}),
    (["gh", "pr", "merge", "-Ro/r", "2610"], {"pr": "2610", "repo": "o/r"}),
    (["gh", "pr", "merge", "--match-head-commit", "abc", "--squash"], {"pr": "", "repo": ""}),
    (["gh", "pr", "merge", "https://github.com/o/r/pull/2610/files"], {"pr": "2610", "repo": ""}),
    (["gh", "release", "create", "--notes", "v0.1.0 notes", "-t", "v9", "v0.9.1"], {"tag": "v0.9.1"}),
    (["gh", "release", "create", "-R", "o/r", "--discussion-category", "x", "v0.9.1"], {"tag": "v0.9.1"}),
    (["gh", "api", "-H", "x", "/repos/o/r/pulls/5/merge?y=1"], {"repo": "o/r", "pr": "5"}),
])
def test_the_ask_rules_read_flags_that_take_a_value(argv, found):
    row, rule = limbs.decide(GITHUB.rules, argv)
    assert row == "ask"
    assert limbs.match(rule, argv) == found


def _tool(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.mark.parametrize("argv,checks,asked", [
    (["pr", "merge", "--subject", "1234", "2610"], ["gh pr checks 2610"], "guard merge-red #2610"),
    (["pr", "merge", "-R", "o/r", "--squash"], ["gh pr view -R o/r --json number -q .number",
                                                 "gh pr checks 77 -R o/r"], "guard merge-red #77"),
    (["api", "-X", "PUT", "repos/o/r/pulls/5/merge"], ["gh pr checks 5 -R o/r"], "guard merge-red #5"),
    (["release", "create", "--target", "main"], [], "guard publish ? --why a release is public"),
])
def test_the_stand_in_asks_with_the_right_target(tmp_path, argv, checks, asked):
    log = tmp_path / "log"
    gh = _tool(tmp_path / "gh", f'echo "gh $*" >> {log}\n[ "$1 $2" = "pr view" ] && echo 77\n'
                                '[ "$1 $2" = "pr checks" ] && exit 1\nexit 0\n')
    brnrd = _tool(tmp_path / "brnrd", f'echo "brnrd $*" >> {log}\nexit 1\n')
    shim = _tool(tmp_path / "shim", limbs.refuse_shell(GITHUB)
                 + limbs.gate_shell(GITHUB).replace("{gh}", str(gh)).replace("{brnrd}", str(brnrd))
                 + f'echo "ran $*" >> {log}\n')
    proc = subprocess.run([str(shim), *argv], capture_output=True, text=True)
    lines = log.read_text().splitlines()
    assert proc.returncode == 1
    assert [line for line in lines if line.startswith("gh ")] == checks
    assert [line for line in lines if line.startswith("brnrd ")][0].startswith("brnrd runtime " + asked)


def test_the_stand_in_ignores_a_planted_module_in_the_working_directory(tmp_path):
    (tmp_path / "re.py").write_text("raise SystemExit('planted')\n")
    shim = _tool(tmp_path / "shim", limbs.refuse_shell(GITHUB) + 'echo "row=$_brnrd_limb"\n')
    proc = subprocess.run([str(shim), "pr", "view"], capture_output=True, text=True, cwd=tmp_path,
                          env={**os.environ, "PYTHONPATH": str(tmp_path)})
    assert (proc.returncode, proc.stdout) == (0, "row=look\n"), proc.stderr


# --- a person's own page ----------------------------------------------------

OWN_NEVER_MERGE = """# GitHub

| row   | what         | how     |
|-------|--------------|---------|
| never | merge a PR{cite} | refused |

```limb
never  gh pr merge
  say: arseni merges by hand
e.g. never  gh pr merge 1
```
"""

OWN_NO_NEVER = """# GitHub

| row   | what     | how     |
|-------|----------|---------|
| never | nothing{cite}  | refused |

```limb
never  gh frobnicate
  say: never
e.g. never  gh frobnicate
```
"""


def test_a_persons_row_adds_to_the_products_and_only_tightens():
    own = limbs.parse("github", OWN_NEVER_MERGE.format(cite=""), source="person")
    page, notes = limbs.overlay(GITHUB, own, lambda letter: False)
    assert notes == []
    assert page.source == "person" and page.own_rows == ("never",)
    assert limbs.decide(page.rules, ["gh", "pr", "merge", "1"])[0] == "never"
    assert limbs.decide(page.rules, ["gh", "repo", "delete", "o/r"])[0] == "never"
    assert limbs.decide(page.rules, ["gh", "release", "create", "v1"])[0] == "ask"


def test_a_loosening_row_without_the_owners_letter_only_adds():
    own = limbs.parse("github", OWN_NO_NEVER.format(cite=" letter:L9"), source="person")
    page, notes = limbs.overlay(GITHUB, own, lambda letter: False)
    assert limbs.decide(page.rules, ["gh", "repo", "delete", "o/r"])[0] == "never"
    assert notes and "letter:L9" in notes[0] and "add to the product's row" in notes[0]


def test_a_row_citing_the_owners_letter_replaces_the_products():
    own = limbs.parse("github", OWN_NO_NEVER.format(cite=" letter:L1"), source="person")
    page, notes = limbs.overlay(GITHUB, own, lambda letter: letter == "L1")
    assert limbs.decide(page.rules, ["gh", "repo", "delete", "o/r"])[0] == "do"
    assert limbs.decide(page.rules, ["gh", "frobnicate"])[0] == "never"
    assert notes and "replaces the product's on letter:L1" in notes[0]
    assert "letter:" not in limbs.table([page])


def _letter(ident, body, sender="person:arseni"):
    return Fact(kind="letter", by="loom:aaaa", id=ident,
                data={"id": ident, "body": body, "from": sender, "to": "thread:inbox"})


@pytest.mark.parametrize("letter,loosened", [
    (_letter("L1", "yes, let the github hand delete repos"), True),
    (_letter("L1", "yes, delete repos"), False),                       # does not name the service
    (_letter("L1", "let github delete repos", sender="person:sasha"), False),  # not the owner
    (_letter("L1", "let github delete repos", sender="s-aaaa-ta"), False),     # a strand is never the owner
])
def test_the_owners_letter_is_read_from_the_ledger_and_the_page_from_main(tmp_path, letter, loosened):
    _repo(tmp_path / "self", {"limbs/github.md": OWN_NO_NEVER.format(cite=" letter:L1"),
                              "people/arseni/agreement.md": "a\n", "README.md": "self\n"})
    home = Home(tmp_path, install="aaaa")
    append(home, letter)
    page, _notes = limbs.effective(home, "github")
    assert page.source == "person"
    assert (limbs.decide(page.rules, ["gh", "repo", "delete", "o/r"])[0] == "do") is loosened


def test_a_page_only_in_the_working_tree_counts_for_nothing(tmp_path):
    _repo(tmp_path / "self", {"README.md": "self\n"})
    (tmp_path / "self" / "limbs").mkdir()
    (tmp_path / "self" / "limbs" / "github.md").write_text(OWN_NEVER_MERGE.format(cite=""))
    page, notes = limbs.effective(Home(tmp_path, install="aaaa"), "github")
    assert page is GITHUB or page.source == "product"
    assert notes == []


def test_a_broken_persons_page_leaves_the_products_and_says_so(tmp_path):
    _repo(tmp_path / "self", {"limbs/github.md": "# GitHub\nno table\n", "README.md": "self\n"})
    page, notes = limbs.effective(Home(tmp_path, install="aaaa"), "github")
    assert page.source == "product"
    assert notes and "the product's page stands" in notes[0]


def test_the_body_stand_in_carries_the_persons_rows(tmp_path, monkeypatch):
    from brr.loom.runtime.loom import _env
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    called = tmp_path / "called"
    _tool(bin_dir / "gh", f'touch "{called}"\n')
    monkeypatch.setenv("PATH", str(bin_dir) + ":/usr/bin:/bin")
    home = tmp_path / "home"
    (home / "loom").mkdir(parents=True)
    (tmp_path / "forge.token").write_text("t\n")
    (home / "loom" / "config.toml").write_text(f'forge_token = "file: {tmp_path / "forge.token"}"\n')
    _repo(home / "self", {"limbs/github.md": OWN_NEVER_MERGE.format(cite=""), "README.md": "self\n"})
    room = home / "rooms" / "s1"
    (room / "port").mkdir(parents=True)
    proc = subprocess.run(["gh", "pr", "merge", "1"], env=_env(room), capture_output=True, text=True, timeout=10)
    assert proc.returncode == 1 and "arseni merges by hand" in proc.stderr
    assert not called.exists()


def test_brnrd_limbs_prints_the_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BRNRD_HOME", str(tmp_path / "nohome"))
    monkeypatch.delenv("BRNRD_ROOM", raising=False)
    from brr.cli import main
    main(["limbs"])
    assert capsys.readouterr().out == limbs.table(limbs.products())
    assert sys.modules["brr.loom.runtime.limbs"] is limbs
