"""Done is true: the loom reads what a delivery's result names before the person sees it.

A ``--deliver`` is the body's word; this module turns it into the forge's. A
pull request result is delivered only when GitHub says it merged, at the sha
GitHub names; a ``ci:`` result only when every check at its commit is green; a
``file:`` result only when the path is in the self's ``main`` as landed. When
the body's line says how many tests passed and the pull request's CI reported
a count, the two must agree. A result that fails is held: the wish stays
undelivered and the fact, one line, is its left (``verbs_wishes.cmd_wish``).

Beside it, a fact for the reviewer, never a refusal: a pull request whose diff
changes tests and code together carries one line naming the assertions it
removed or loosened and the tests it skips (``weakened``), in its body above
the footer and in the delivery letter.

Trust design section 10 (threads/inbox/research/trust-design.md on the self).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

#: Result kinds that name a path in the self.
FILES = frozenset({"file", "doc", "box"})

_PULL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
_SHORT = re.compile(r"(?:([\w.-]+/[\w.-]+)|[\w.-]+)?#(\d+)")
_RUN = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/actions/runs/(\d+)")
_COMMIT = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/commit/([0-9a-f]{7,40})")
_AT = re.compile(r"([\w.-]+/[\w.-]+)@([0-9a-f]{7,40})")
#: "4012 passed", "4,012 tests passed": what a body writes of its run.
_PASSED = re.compile(r"(\d[\d,]*)\s+(?:tests?\s+)?passed\b")
#: The line ``weakened`` writes, so a later pass replaces it in the body.
WEAKENED = "Tests weakened beside code"
_WEAKENED_LINE = re.compile(rf"^{WEAKENED}\b.*\n?", re.M)


@dataclass
class Checked:
    """What the forge said of one result: ``ok``, and the fact in one line."""
    ok: bool
    line: str
    weakened: str = ""


def forge_for(home):
    """The forge with the home's token, or the machine's gh login (``update.GitHubChecks``)."""
    from .config import load_config
    from .update import GitHubChecks
    return Forge(GitHubChecks(load_config(home.root).forge_token))


class Forge:
    """The few GitHub reads a delivery needs, and the one edit its line makes."""

    def __init__(self, checks):
        self.checks = checks

    def _call(self, path: str, *, accept: str = "application/vnd.github+json",
              method: str = "GET", body: dict | None = None):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.checks._token()}", "Accept": accept})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        return raw.decode("utf-8", "replace") if accept.endswith("diff") else json.loads(raw or b"{}")

    def pull(self, repo: str, number: int) -> dict:
        return self._call(f"{repo}/pulls/{number}")

    def diff(self, repo: str, number: int) -> str:
        return self._call(f"{repo}/pulls/{number}", accept="application/vnd.github.diff")

    def run(self, repo: str, ident: int) -> dict:
        return self._call(f"{repo}/actions/runs/{ident}")

    def ci(self, repo: str, sha: str) -> str:
        return self.checks.verdict(f"https://github.com/{repo}.git", sha)

    def passed(self, repo: str, sha: str) -> int | None:
        """The tests CI says passed at ``sha``: the sum of its ``tests`` notices
        (ci.yml writes one per shard), or ``None`` when no check reported one.
        A rerun leaves its old check run at the sha too; only the newest of
        each name counts, so a rerun shard is not summed twice."""
        latest: dict[str, dict] = {}
        for run in self.checks._runs(repo, sha):
            name = str(run.get("name"))
            if name not in latest or int(run.get("id") or 0) > int(latest[name].get("id") or 0):
                latest[name] = run
        total, seen = 0, False
        for run in latest.values():
            for note in self._call(f"{repo}/check-runs/{run['id']}/annotations?per_page=100") or ():
                found = _PASSED.search(str(note.get("message") or ""))
                if note.get("title") == "tests" and found:
                    total, seen = total + int(found.group(1).replace(",", "")), True
        return total if seen else None

    def set_body(self, repo: str, number: int, text: str) -> None:
        self._call(f"{repo}/pulls/{number}", method="PATCH", body={"body": text})


def pull_of(ref: str, kind: str, facts) -> tuple[str, int] | None:
    """``(owner/repo, number)`` a result names: its URL, ``owner/repo#N``, or a bare
    ``#N``/``N`` for a ``pr`` result, read the way a park reads it (``verbs_wishes._repo``)."""
    if (found := _PULL.search(ref)):
        return found.group(1), int(found.group(2))
    if kind != "pr":
        return None
    ref = ref.strip()
    found = _SHORT.fullmatch(ref if not ref.isdigit() else "#" + ref)
    if not found:
        return None
    number = int(found.group(2))
    if found.group(1):
        return found.group(1), number
    from .verbs_wishes import _repo
    repo = _repo(facts, number)
    return (repo, number) if repo else None


def check(home, facts, kind: str, ref: str, line: str, forge=None) -> Checked | None:
    """The forge's word on a result about to be delivered, or ``None`` when the ref
    names nothing the loom reads (a letter, a commit, a link off GitHub)."""
    pull = pull_of(ref, kind, facts)
    if pull is None and kind == "pr":
        return Checked(False, f"pr:{ref} names no pull request on GitHub: give owner/repo#N or its URL")
    if pull is None and kind == "ci":
        return _ci(ref, forge or forge_for(home))
    if pull is None and kind in FILES:
        return _file(home, ref)
    if pull is None:
        return None
    return _pull(home, *pull, line, forge or forge_for(home))


def _unread(what: str, exc: Exception) -> Checked:
    said = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else str(exc) or type(exc).__name__
    return Checked(False, f"the forge could not be read for {what} ({said}): nothing is delivered on the body's word")


def _pull(home, repo: str, number: int, line: str, forge) -> Checked:
    name = f"{repo}#{number}"
    try:
        body = forge.pull(repo, number)
    except Exception as exc:  # noqa: BLE001 - an unread forge holds the delivery, in words
        return _unread(name, exc)
    if not body.get("merged_at"):
        state = "closed unmerged" if body.get("state") == "closed" else "open"
        return Checked(False, f"{name} is {state}, not merged: park on pr:{name} and deliver once it merges")
    sha = str(body.get("merge_commit_sha") or "")
    head = str((body.get("head") or {}).get("sha") or "")
    said = f"{name} merged at {sha[:8]}"
    claimed = _PASSED.search(line)
    if claimed and head:
        try:
            reported = forge.passed(repo, head)
        except Exception as exc:  # noqa: BLE001
            return _unread(f"{name}'s test count", exc)
        wrote = int(claimed.group(1).replace(",", ""))
        if reported is not None and reported != wrote:
            return Checked(False, f"the line says {wrote} passed; CI on {name} at {head[:8]} reports "
                                  f"{reported} passed: deliver with CI's number")
        if reported is not None:
            said += f", {wrote} passed as CI says"
    return Checked(True, said, mark(home, repo, number, forge, body=body))


def _ci(ref: str, forge) -> Checked:
    found = _RUN.search(ref) or _COMMIT.search(ref) or _AT.fullmatch(ref.strip())
    if not found:
        return Checked(False, f"ci:{ref} names no commit or run on GitHub: give owner/repo@sha or its URL")
    repo = found.group(1)
    try:
        sha = str(forge.run(repo, int(found.group(2))).get("head_sha") or "") if found.re is _RUN else found.group(2)
        verdict = forge.ci(repo, sha)
    except Exception as exc:  # noqa: BLE001
        return _unread(f"CI on {ref}", exc)
    if verdict != "green":
        return Checked(False, f"CI on {repo}@{sha[:8]} is {verdict}, not green: deliver once it is")
    return Checked(True, f"CI green on {repo}@{sha[:8]}")


def _file(home, ref: str) -> Checked:
    """A path in the self: in ``main`` as landed. An absolute path is on this machine."""
    path = ref.strip()
    if path.startswith(("/", "~")):
        here = Path(path).expanduser()
        return Checked(True, f"{path} is on this machine") if here.exists() else \
            Checked(False, f"{path} does not exist on this machine")
    path = path.removeprefix("self/").removeprefix("./")
    self_dir = home.root / "self"
    if not (self_dir / ".git").exists():
        return Checked(True, f"{path} is in the self") if (self_dir / path).exists() else \
            Checked(False, f"{path} is not in the self")
    from .selfrepo import git
    sha = git(self_dir, "rev-parse", "main", check=False).stdout.strip()
    if git(self_dir, "cat-file", "-e", f"main:{path}", check=False).returncode != 0:
        return Checked(False, f"{path} is not on the self's main at {sha[:8]}: land it (send-self), then deliver")
    return Checked(True, f"{path} is on the self's main at {sha[:8]}")


def mark(home, repo: str, number: int, forge=None, body: dict | None = None) -> str:
    """Put the ``weakened`` line on pull request ``number``'s body, replacing an older
    one; drop it when the diff no longer weakens. The line, or ``""``. A forge that
    refuses the read or the edit costs the line on the PR, never the delivery."""
    forge = forge or forge_for(home)
    try:
        said = weakened(forge.diff(repo, number))
    except Exception:  # noqa: BLE001 - the line is a courtesy to the reviewer
        return ""
    try:
        body = body if body is not None else forge.pull(repo, number)
        old = str(body.get("body") or "")
        new = with_line(old, said)
        if new != old:
            forge.set_body(repo, number, new)
    except Exception:  # noqa: BLE001 - the delivery still carries it
        pass
    return said


def with_line(text: str, line: str) -> str:
    """``text`` with ``line`` as its only weakened line, above the resident's footer
    (``publish.sign``) or the brnrd marker, else at the end."""
    from .comments import _MARKED
    text = _WEAKENED_LINE.sub("", text)
    if not line:
        return text
    anchor = re.search(r"^Opened by resident brnrd\b", text, re.M) or next(iter(_MARKED.finditer(text)), None)
    if anchor is None:
        return text.rstrip() + ("\n\n" if text.strip() else "") + line + "\n"
    at = anchor.start()
    return text[:at].rstrip() + ("\n\n" if text[:at].strip() else "") + line + "\n\n" + text[at:]


# ---------------------------------------------------------------------------
# What a diff weakened

_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|e2e)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$|"
                        r"(^|/)conftest\.py$")
_PROSE = re.compile(r"\.(md|rst|txt)$|(^|/)docs/|(^|/)LICENSE")
_ASSERT = re.compile(r"^\s*(assert\b|self\.assert\w*\(|expect\(|with pytest\.raises\b|pytest\.raises\()")
_SKIP = re.compile(r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail)\(|@unittest\.skip|"
                   r"\b(it|test|describe)\.(skip|todo)\(|^\s*x(it|describe)\(")
_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)|^\s*(?:it|test)\(\s*['\"]([^'\"]+)")
#: What an ``==`` assertion becomes when it is loosened rather than rewritten.
_LOOSE = re.compile(r"\sin\s|>=|<=|\bapprox\(|\.startswith\(|\.endswith\(|\bany\(|is not None|"
                    r"^\s*assert\s+(True|\w+)\s*(#.*)?$|toBeTruthy|toContain\(|toMatch\(")
_SHOWN = 4


def _files(diff: str):
    """``(path, hunks)``, each hunk a list of ``(sign, text)`` for its changed lines."""
    path, hunks = None, []
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            if path is not None:
                yield path, hunks
            path, hunks = raw.rpartition(" b/")[2], []
        elif raw.startswith("+++ ") or raw.startswith("--- "):
            continue
        elif raw.startswith("@@"):
            hunks.append([])
        elif hunks and raw[:1] in "+- ":
            hunks[-1].append((raw[0], raw[1:]))
    if path is not None:
        yield path, hunks


def weakened(diff: str) -> str:
    """One line naming what a diff that changes tests and code together takes out of
    its tests: asserts removed, ``==`` loosened, tests skipped or deleted. ``""`` when
    nothing is, or when the diff touches only tests or only code."""
    files = list(_files(diff))
    code = any(not _TEST_PATH.search(p) and not _PROSE.search(p) for p, _ in files)
    if not code or not any(_TEST_PATH.search(p) for p, _ in files):
        return ""
    tests = [(p, hunks) for p, hunks in files if _TEST_PATH.search(p)]
    # A line moved, from one hunk or test file to another, is not taken out.
    added_all = {t.strip() for _, hunks in tests for hunk in hunks for s, t in hunk if s == "+"}
    removed_all = {t.strip() for _, hunks in tests for hunk in hunks for s, t in hunk if s == "-"}
    found: list[str] = []
    for path, hunks in tests:
        for hunk in hunks:
            added = [t.strip() for s, t in hunk if s == "+"]
            loose = [t for t in added if _ASSERT.match(t) and _LOOSE.search(t) and "==" not in t]
            # A test deleted whole is one entry: the asserts in its run of removed lines fold in.
            deleting, folded = None, 0
            for sign, text in hunk:
                line = text.strip()
                if sign != "-" and deleting is not None:
                    found[deleting] += f" ({folded} asserts)" if folded > 1 else " (1 assert)" if folded else ""
                    deleting, folded = None, 0
                if sign == "-" and (name := _TEST_DEF.match(line)) and line not in added_all:
                    if deleting is not None:
                        found[deleting] += f" ({folded} asserts)" if folded > 1 else " (1 assert)" if folded else ""
                    found.append(f"{path}\0deletes {name.group(1) or name.group(2)}")
                    deleting, folded = len(found) - 1, 0
                elif sign == "-" and _ASSERT.match(line) and line not in added_all:
                    if deleting is not None:
                        folded += 1
                    elif "==" in line and loose:
                        found.append(f"{path}\0loosens `{_clip(line)}` to `{_clip(loose.pop(0))}`")
                    else:
                        found.append(f"{path}\0drops `{_clip(line)}`")
                elif sign == "+" and _SKIP.search(line) and line not in removed_all:
                    found.append(f"{path}\0skips with `{_clip(line)}`")
            if deleting is not None:
                found[deleting] += f" ({folded} asserts)" if folded > 1 else " (1 assert)" if folded else ""
    if not found:
        return ""
    more = len(found) - _SHOWN
    shown: dict[str, list[str]] = {}
    for entry in found[:_SHOWN]:
        path, _, what = entry.partition("\0")
        shown.setdefault(path, []).append(what)
    return (f"{WEAKENED} (a fact for the reviewer): "
            + "; ".join(f"{path} {', '.join(whats)}" for path, whats in shown.items())
            + (f"; +{more} more" if more > 0 else ""))


def _clip(text: str, n: int = 60) -> str:
    text = " ".join(text.split()).replace("`", "'")
    return text if len(text) <= n else text[: n - 1] + "…"
