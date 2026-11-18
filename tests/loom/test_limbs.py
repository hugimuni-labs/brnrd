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
    assert [p.name for p in pages] == ["github", "npm"]
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
unlisted: do

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


def test_a_product_page_states_its_default_for_the_unlisted():
    with pytest.raises(limbs.LimbError, match="no 'unlisted:' line"):
        limbs.parse("thing", PAGE.replace("unlisted: do\n", ""))
    with pytest.raises(limbs.LimbError, match="is look, do, never, or ask <class>"):
        limbs.parse("thing", PAGE.replace("unlisted: do", "unlisted: ask"))
    with pytest.raises(limbs.LimbError, match="the table has no look row"):
        limbs.parse("thing", PAGE.replace("unlisted: do", "unlisted: look"))
    assert GITHUB.unlisted == "do"


def test_a_page_whose_unlisted_is_ask_or_never_catches_what_it_does_not_list():
    """A bank's page: what the small print does not name is the danger, so it asks."""
    asking = limbs.parse("thing", PAGE.replace("unlisted: do", "unlisted: ask publish")
                         .replace("e.g. do     thing look", "e.g. ask    thing look"))
    row, rule = limbs.decide(asking.rules, ["thing", "look"], asking.unlisted)
    assert (row, rule.cls) == ("ask", "publish")
    assert limbs.decide(asking.rules, ["thing", "burn"], asking.unlisted)[0] == "never"
    refusing = limbs.parse("thing", PAGE.replace("unlisted: do", "unlisted: never")
                           .replace("e.g. do     thing look", "e.g. never  thing look"))
    assert limbs.decide(refusing.rules, ["thing", "look"], refusing.unlisted)[0] == "never"
    assert limbs.decide(refusing.rules, ["thing", "ship", "v1"], refusing.unlisted)[0] == "ask"


def test_the_stand_in_carries_the_pages_unlisted(tmp_path):
    asking = limbs.parse("thing", PAGE.replace("unlisted: do", "unlisted: ask publish")
                         .replace("e.g. do     thing look", "e.g. ask    thing look"))
    log = tmp_path / "log"
    brnrd = _tool(tmp_path / "brnrd", f'echo "brnrd $*" >> {log}\nexit 1\n')
    shim = _tool(tmp_path / "shim", limbs.refuse_shell(asking)
                 + limbs.gate_shell(asking).replace("{thing}", "/bin/true").replace("{brnrd}", str(brnrd))
                 + f'echo "ran $*" >> {log}\n')
    proc = subprocess.run([str(shim), "look", "at it"], capture_output=True, text=True)
    assert proc.returncode == 1
    assert log.read_text().splitlines() == [
        "brnrd runtime guard publish thing look 'at it' --why the page does not list it"]
    refusing = limbs.replace(asking, unlisted="never")
    shim = _tool(tmp_path / "never", limbs.refuse_shell(refusing) + 'echo ran\n')
    proc = subprocess.run([str(shim), "look"], capture_output=True, text=True)
    assert proc.returncode == 1 and proc.stdout == "" and "does not list this call" in proc.stderr


def test_a_persons_unlisted_only_tightens():
    own = limbs.parse("github", OWN_NEVER_MERGE.format(cite="").replace("# GitHub\n", "# GitHub\nunlisted: never\n"),
                      source="person")
    page, notes = limbs.overlay(GITHUB, own, lambda letter: False)
    assert page.unlisted == "never" and "unlisted" in page.own_rows and notes == []
    assert limbs.decide(page.rules, ["gh", "frobnicate"], page.unlisted)[0] == "never"
    strict = limbs.replace(GITHUB, unlisted="never")
    page, notes = limbs.overlay(strict, limbs.replace(own, unlisted="do"), lambda letter: False)
    assert page.unlisted == "never" and "does not tighten" in notes[0]
    silent = limbs.parse("github", OWN_NEVER_MERGE.format(cite=""), source="person")
    assert silent.unlisted == ""
    page, notes = limbs.overlay(strict, silent, lambda letter: False)
    assert page.unlisted == "never" and notes == []


def test_the_stand_ins_classes_are_the_guards():
    from brr.loom.runtime import grants
    assert set(limbs.CLASSES) == set(grants.CLASSES) == set(grants.VERBS)


# The calls inbox ran through limbs.decide at #2736's head (bg0cf), all `do` there.
@pytest.mark.parametrize("argv,row,cls,target", [
    (["gh", "repo", "edit", "--visibility", "public", "--accept-visibility-change-consequences"],
     "ask", "visibility", ""),
    (["gh", "repo", "edit", "o/r", "--visibility=private"], "ask", "visibility", "o/r"),
    (["gh", "api", "-X", "PATCH", "repos/o/r", "-f", "visibility=public"], "ask", "visibility", "o/r"),
    (["gh", "api", "--method", "PATCH", "/repos/o/r", "--field", "private=false"], "ask", "visibility", "o/r"),
    (["gh", "secret", "set", "NPM_TOKEN", "--body", "x"], "ask", "secret", "NPM_TOKEN"),
    (["gh", "secret", "set", "-e", "prod", "NPM_TOKEN"], "ask", "secret", "NPM_TOKEN"),
    (["gh", "secret", "delete", "-R", "o/r", "NPM_TOKEN"], "ask", "secret", "NPM_TOKEN"),
    (["gh", "api", "-X", "PUT", "repos/o/r/environments/prod/secrets/K", "-f", "key_id=1"], "ask", "secret", "K"),
    (["gh", "api", "-XDELETE", "orgs/o/actions/secrets/K"], "ask", "secret", "K"),
    (["gh", "api", "-X", "PUT", "repos/o/r/collaborators/someone", "-f", "permission=admin"],
     "ask", "access", "someone"),
    (["gh", "api", "-X", "DELETE", "/repos/o/r/collaborators/someone"], "ask", "access", "someone"),
    (["gh", "release", "delete", "-R", "o/r", "v0.9.1", "--yes"], "ask", "forge-delete", "v0.9.1"),
    (["gh", "api", "-X", "DELETE", "repos/o/r/releases/123"], "ask", "forge-delete", "123"),
    (["gh", "workflow", "run", "-R", "o/r", "deploy.yml", "-f", "env=prod"], "ask", "run", "deploy.yml"),
    (["gh", "api", "repos/o/r/actions/workflows/9/dispatches", "-f", "ref=main"], "ask", "run", "9"),
    # Read at e0294bba by inbox (irdre): these landed in do.
    (["gh", "api", "-X", "PATCH", "repos/o/r", "--input", "body.json"], "ask", "visibility", "o/r"),
    (["gh", "api", "--method=PATCH", "repos/o/r", "--input", "-"], "ask", "visibility", "o/r"),
    (["gh", "api", "-X", "POST", "repos/o/r/dispatches", "-f", "event_type=deploy"], "ask", "run", "o/r"),
    (["gh", "secret", "set", "-f", ".env"], "ask", "secret", ".env"),
    (["gh", "secret", "set", "--env-file=prod.env", "-R", "o/r"], "ask", "secret", "prod.env"),
    (["gh", "auth", "token"], "never", "", ""),
    (["gh", "auth", "status", "--show-token"], "never", "", ""),
    (["gh", "auth", "status", "-h", "github.com", "-t"], "never", "", ""),
])
def test_the_acts_that_were_unlisted_now_ask_or_stop(argv, row, cls, target):
    got, rule = limbs.decide(GITHUB.rules, argv, GITHUB.unlisted)
    assert (got, rule.cls) == (row, cls)
    if row == "ask":
        assert limbs._sub(rule.details["target"], limbs.match(rule, argv)) == target


@pytest.mark.parametrize("argv,row", [
    (["gh", "secret", "list"], "look"),
    (["gh", "api", "repos/o/r/actions/secrets/K"], "look"),
    (["gh", "api", "repos/o/r/collaborators/someone"], "look"),
    (["gh", "auth", "status"], "look"),
    (["gh", "release", "delete-asset", "v1", "a.tgz"], "do"),
    (["gh", "api", "-X", "PATCH", "repos/o/r", "-f", "description=x"], "do"),
    (["gh", "api", "-X", "PATCH", "repos/o/r/issues/1", "--input", "body.json"], "do"),
    (["gh", "api", "-X", "PATCH", "repos/o/r/issues/1", "-f", "visibility=x"], "do"),
])
def test_their_neighbours_stay_where_they_were(argv, row):
    assert limbs.decide(GITHUB.rules, argv, GITHUB.unlisted)[0] == row


def test_the_stand_in_refuses_gh_auth_token_before_the_key(tmp_path):
    shim = _tool(tmp_path / "shim", limbs.refuse_shell(GITHUB) + 'echo "key fetched"\n')
    proc = subprocess.run([str(shim), "auth", "token"], capture_output=True, text=True)
    assert proc.returncode == 1 and proc.stdout == "" and "does not hand out the forge token" in proc.stderr


def test_the_stand_in_asks_for_a_visibility_change_naming_the_repository(tmp_path):
    log = tmp_path / "log"
    gh = _tool(tmp_path / "gh", f'echo "gh $*" >> {log}\n[ "$1 $2" = "repo view" ] && echo o/r\nexit 0\n')
    brnrd = _tool(tmp_path / "brnrd", f'echo "brnrd $*" >> {log}\nexit 1\n')
    shim = _tool(tmp_path / "shim", limbs.refuse_shell(GITHUB)
                 + limbs.gate_shell(GITHUB).replace("{gh}", str(gh)).replace("{brnrd}", str(brnrd))
                 + f'echo "ran $*" >> {log}\n')
    proc = subprocess.run([str(shim), "repo", "edit", "--visibility", "public"], capture_output=True, text=True)
    assert proc.returncode == 1
    assert log.read_text().splitlines() == [
        "gh repo view --json nameWithOwner -q .nameWithOwner",
        "brnrd runtime guard visibility o/r --why it changes who can see the repository"]


@pytest.mark.parametrize("argv,row", [
    # A value flag's value is not the positional: the shell text read these flag by flag.
    (["gh", "repo", "delete", "--yes"], "never"),
    (["gh", "api", "-X", "delete", "repos/o/r"], "never"),
    (["gh", "api", "-f", "a=b", "--method", "DELETE", "repos/o/r"], "never"),
    (["gh", "api", "-H", "Accept: x", "-XDELETE", "/repos/o/r/"], "never"),
    # gh takes a full address, GitHub's or an Enterprise host's.
    (["gh", "api", "-X", "DELETE", "https://api.github.com/repos/o/r"], "never"),
    (["gh", "api", "-X", "DELETE", "https://ghe.example/api/v3/repos/o/r/"], "never"),
    (["gh", "api", "-X", "DELETE", "https://api.github.com/repos/o/r/issues/comments/1"], "do"),
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
    (["gh", "pr", "merge", "https://github.com/o/r/pull/2610/files"], {"pr": "2610", "repo": "o/r"}),
    (["gh", "release", "create", "--notes", "v0.1.0 notes", "-t", "v9", "v0.9.1"], {"tag": "v0.9.1"}),
    (["gh", "release", "create", "-R", "o/r", "--discussion-category", "x", "v0.9.1"], {"tag": "v0.9.1"}),
    (["gh", "api", "-H", "x", "/repos/o/r/pulls/5/merge?y=1"], {"repo": "o/r", "pr": "5"}),
    (["gh", "pr", "merge", "https://github.com/o/other/pull/5"], {"pr": "5", "repo": "o/other"}),
    (["gh", "pr", "merge", "-R", "o/r", "https://github.com/o/other/pull/5"], {"pr": "5", "repo": "o/other"}),
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
    # No tag: the target is the call itself, never "?" (a letter with a question mark named that).
    (["release", "create", "--target", "main"], [], "guard publish gh release create --target main --why"),
    # A link to another repository's PR reads that repository's checks, not this one's.
    (["pr", "merge", "https://github.com/o/other/pull/5"], ["gh pr checks 5 -R o/other"], "guard merge-red #5"),
    (["pr", "merge", "-R", "o/r", "https://github.com/o/other/pull/5/"], ["gh pr checks 5 -R o/other"],
     "guard merge-red #5"),
    (["api", "-X", "PUT", "https://api.github.com/repos/o/r/pulls/5/merge"], ["gh pr checks 5 -R o/r"],
     "guard merge-red #5"),
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


OTHER_COMMAND = """# Thing
unlisted: do

| row  | what | how    |
|------|------|--------|
| look | peek | passes |

```limb
look  other peek
e.g. look  other peek
```
"""


def test_a_persons_page_for_another_command_is_refused_whole():
    """A product page with no look row: a person's look rule for another command must not become
    the stand-in's command and so make every never and ask rule miss."""
    base = limbs.parse("thing", PAGE)
    own = limbs.parse("thing", OTHER_COMMAND, source="person")
    page, notes = limbs.overlay(base, own, lambda letter: False)
    assert page is base
    assert notes and "for `other`" in notes[0] and "the product's page stands" in notes[0]
    assert limbs.decide(page.rules, ["thing", "burn", "now"])[0] == "never"


def test_the_stand_in_takes_its_command_from_the_page_and_fails_closed_on_a_stray_rule(tmp_path):
    base = limbs.parse("thing", PAGE)
    shim = _tool(tmp_path / "shim", limbs.refuse_shell(base) + 'echo "row=$_brnrd_limb"\n')
    proc = subprocess.run([str(shim), "burn", "now"], capture_output=True, text=True)
    assert proc.returncode == 1 and "no burning" in proc.stderr
    mixed = limbs.replace(base, rules=(limbs.parse("thing", OTHER_COMMAND).rules[0], *base.rules))
    stray = _tool(tmp_path / "stray", limbs.refuse_shell(mixed).replace("--command other", "--command thing")
                  + 'echo "row=$_brnrd_limb"\n')
    proc = subprocess.run([str(stray), "look"], capture_output=True, text=True)
    assert proc.returncode == 1 and "carries rules for other" in proc.stderr


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


def test_a_target_with_no_word_is_named_by_no_letter():
    """Before e0294bba's fix the stand-in asked with "?" when a call had no target, and
    a letter saying "secret" and ending in a question mark granted it."""
    from brr.loom.runtime import grants
    assert not grants.names("could you set the secret?", "?")
    assert not grants.names("anything - really", "-")
    assert grants.names("set the secret from .env please", ".env")
    assert grants.names("ok to merge #2610", "#2610")


# --- npm (threads/limbs): look for view, ask for publish, never for unpublish ---

NPM = limbs.product("npm")


@pytest.mark.parametrize("argv,row", [
    # npm takes any unique start of a command, and options before it.
    (["npm", "unpubl", "x"], "never"),
    (["npm", "unpu", "x"], "never"),
    (["npm", "-w", "pkg", "unpublish"], "never"),
    (["npm", "pu"], "ask"),
    (["npm", "publ", "--tag", "next"], "ask"),
    (["npm", "--registry=https://registry.npmjs.org", "publish"], "ask"),
    (["npm", "publish", "-ws"], "ask"),
    (["npm", "-w", "pkg", "pu"], "ask"),
    (["npm", "-C", "pkg", "pub"], "ask"),
    (["npm", "--tag", "next", "pub"], "ask"),
    # A dry run asks too: npm reads its flag's value, and `--dry-run false` uploads.
    (["npm", "publish", "--dry-run"], "ask"),
    (["npm", "publish", "--dry-run", "false"], "ask"),
    (["npm", "publish", "--dry-run=false"], "ask"),
    (["npm", "publish", "--dry-run", "--no-dry-run"], "ask"),
    (["npm", "-w", "pkg", "publish", "--dry-run"], "ask"),
    # Neighbours: not a registry write, an option's value never read as the command.
    (["npm", "-g", "install", "puppeteer"], "do"),
    (["npm", "--prefix", "public-site", "run", "build"], "do"),
    (["npm", "--prefix", "unpacked", "run", "build"], "do"),
    (["npm", "-w", "publisher", "test"], "do"),
    (["npm", "pack", "--dry-run"], "do"),
    (["npm", "un", "left-pad"], "do"),
    (["npm", "update"], "do"),
    (["npm", "-g", "install", "typescript"], "do"),
    (["npm", "info", "brnrd"], "look"),
])
def test_npm_rows(argv, row):
    assert limbs.decide(NPM.rules, argv, NPM.unlisted)[0] == row


def _fake_npm(tmp_path, log):
    return _tool(tmp_path / "npm", f'echo "npm $*" >> {log}\n'
                 'if [ "$1 $2" = "pkg get" ]; then\n'
                 '  case "$3 $5" in "name ") echo \'"brnrd"\' ;; "version ") echo \'"0.9.1"\' ;;\n'
                 '    "name sub") echo \'"@o/sub"\' ;; "version sub") echo \'"1.0.0"\' ;; esac\nfi\nexit 0\n')


def _npm_shim(tmp_path, npm, brnrd, log):
    return _tool(tmp_path / "shim", limbs.refuse_shell(NPM)
                 + limbs.gate_shell(NPM).replace("{npm}", str(npm)).replace("{brnrd}", str(brnrd))
                 + f'echo "ran $*" >> {log}\n')


@pytest.mark.parametrize("argv,asked", [
    (["publish", "--access", "public"], "guard publish brnrd@0.9.1 --why"),
    (["pub", "sub", "--tag", "next"], "guard publish @o/sub@1.0.0 --why"),
    # Named by workspace or by an option first: the call itself, which a letter must quote.
    (["publish", "-w", "sub"], "guard publish npm publish -w sub --why"),
    (["--prefix", "sub", "publish"], "guard publish npm --prefix sub publish --why"),
])
def test_the_npm_stand_in_asks_naming_the_package_and_version(tmp_path, argv, asked):
    log = tmp_path / "log"
    npm = _fake_npm(tmp_path, log)
    brnrd = _tool(tmp_path / "brnrd", f'echo "brnrd $*" >> {log}\nexit 1\n')
    proc = subprocess.run([str(_npm_shim(tmp_path, npm, brnrd, log)), *argv], capture_output=True, text=True)
    lines = log.read_text().splitlines()
    assert proc.returncode == 1
    assert not any(line.startswith("ran ") for line in lines)
    assert [line for line in lines if line.startswith("brnrd ")][0].startswith("brnrd runtime " + asked)


def test_the_npm_stand_in_refuses_unpublish_without_running_npm(tmp_path):
    log = tmp_path / "log"
    npm = _fake_npm(tmp_path, log)
    brnrd = _tool(tmp_path / "brnrd", f'echo "brnrd $*" >> {log}\nexit 0\n')
    proc = subprocess.run([str(_npm_shim(tmp_path, npm, brnrd, log)), "unpub", "brnrd@0.9.1"],
                          capture_output=True, text=True)
    assert proc.returncode == 1 and "refuses to unpublish" in proc.stderr
    assert not log.exists()


def test_the_npm_stand_in_passes_a_build_and_a_pack(tmp_path):
    log = tmp_path / "log"
    npm = _fake_npm(tmp_path, log)
    brnrd = _tool(tmp_path / "brnrd", f'echo "brnrd $*" >> {log}\nexit 1\n')
    shim = _npm_shim(tmp_path, npm, brnrd, log)
    for argv in (["run", "build"], ["--prefix", "src/frontend", "ci"], ["pack", "--dry-run"]):
        assert subprocess.run([str(shim), *argv]).returncode == 0
    assert log.read_text().splitlines() == ["ran run build", "ran --prefix src/frontend ci", "ran pack --dry-run"]


def test_the_body_gets_an_npm_stand_in_without_a_forge_token(tmp_path, monkeypatch):
    from brr.loom.runtime.loom import _env
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    called = tmp_path / "called"
    _tool(bin_dir / "npm", f'echo "$*" >> "{called}"\n')
    monkeypatch.setenv("PATH", str(bin_dir) + ":/usr/bin:/bin")
    home = tmp_path / "home"
    (home / "loom").mkdir(parents=True)
    _repo(home / "self", {"README.md": "self\n"})
    room = home / "rooms" / "s1"
    (room / "port").mkdir(parents=True)
    env = _env(room)
    proc = subprocess.run(["npm", "unpublish", "x"], env=env, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 1 and "refuses to unpublish" in proc.stderr
    assert not called.exists()
    assert subprocess.run(["npm", "run", "build"], env=env, timeout=10).returncode == 0
    assert called.read_text() == "run build\n"
    # Written again at the next body, the stand-in finds the real npm, never itself.
    env = _env(room)
    assert subprocess.run(["npm", "ci"], env=env, timeout=10).returncode == 0
    assert called.read_text() == "run build\nci\n"
