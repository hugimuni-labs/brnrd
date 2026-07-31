"""The close-keyword predicate, as a Python owner with two consumers (#839).

Three jobs here, in order of how much they load-bear:

1. **The hook did not move.** `hook_script_body()` renders the exact literal
   that shipped with #657, and `_RUN_ID_HOOK_SCRIPT` is that fragment plus the
   shebang/marker/trailer wrapper. `tests/test_gitops.py` is the behavioural
   floor (70 tests, unmodified); this is the byte-level one.
2. **The two dialects agree.** `grep -E` reads POSIX ERE, `re` does not, so the
   patterns are stored in the shell dialect and mechanically expanded for
   Python. If that expansion were approximate the PR-body channel would refuse
   a different set of lines than the commit channel — the exact drift the
   one-owner shape exists to make impossible. Driven side by side over a
   corpus, per line, per pattern.
3. **The table the ticket asked for.** The four shapes #839's comments
   enumerate as real, independently confirmed closures all caught; the two
   this repo writes constantly, clean.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from brr import closekeyword, gitops


GREP = shutil.which("grep")


# ── 1. the hook did not move ──────────────────────────────────────────────


def test_hook_script_body_is_a_prefix_of_the_installed_hook():
    """The rendered fragment is what the installed hook actually carries."""
    assert closekeyword.hook_script_body() in gitops._RUN_ID_HOOK_SCRIPT


def test_installed_hook_is_the_fragment_plus_its_wrapper():
    """Nothing else in the hook mentions the predicate.

    Split the script at the fragment; what remains is the shebang, the marker,
    the `BRR_RUN_ID` guard, the newline guard and the trailer call — no stray
    second copy of a pattern or a remedy hiding elsewhere in the string.
    """
    head, _, tail = gitops._RUN_ID_HOOK_SCRIPT.partition(
        closekeyword.hook_script_body()
    )
    for pattern in (
        closekeyword.ANY,
        closekeyword.LINESTART,
        closekeyword.CLEAN,
        closekeyword.COLONCLOSE,
    ):
        assert pattern not in head and pattern not in tail
    assert "interpret-trailers" in tail
    assert head.startswith("#!/bin/sh\n")


def test_every_rule_reaches_the_hook_with_its_remedies():
    body = closekeyword.hook_script_body()
    for rule in closekeyword.RULES:
        assert f"commit-msg: {rule.headline}" in body
        for remedy in rule.remedies:
            assert remedy in body
        for detail in rule.details:
            assert detail in body


def test_hook_carries_no_pr_body_wording():
    """The PR-body channel's extra lines must not leak into the commit hook.

    The two channels share a predicate, not a voice: the hook has a bypass and
    no quoting note, and its existing tests assert its stderr byte for byte.
    """
    body = closekeyword.hook_script_body()
    assert "pr-body" not in body
    for line in closekeyword.PR_BODY.quoting:
        assert line not in body


def test_sh_single_quoting_stays_trivially_safe():
    """No apostrophes anywhere ⇒ `'...'` is exact and needs no escaping."""
    for pattern in (
        closekeyword.ANY,
        closekeyword.LINESTART,
        closekeyword.CLEAN,
        closekeyword.COLONCLOSE,
    ):
        assert "'" not in pattern
    for rule in closekeyword.RULES:
        assert "'" not in rule.headline
        assert all("'" not in d for d in rule.details)
        assert all("'" not in r for r in rule.remedies)


def test_sq_refuses_an_unquotable_string():
    with pytest.raises(ValueError):
        closekeyword._sq("it's not quotable")


# ── 2. the two dialects agree ─────────────────────────────────────────────


# Every shape the predicate's own argument names, plus the neighbours that
# probe its edges: the ":" allowance, the knowingly-accepted residual, the
# "disclosed" word-boundary case, and the #NNN placeholder the remedies print.
_CORPUS = (
    "Closes #413 §7 S13.",
    "Closes #413, not really",
    "Closes #413, #414, and #415",
    "Closes #413. Also this fixes #414",
    "This does not close #477.",
    "Fix #533: split config and closes #534",
    "Fix #533: split config and disclosed #534",
    "Partially closes #413",
    "  Closes #413.",
    "Closes #413",
    "Closes #413, #414",
    "Closes #413, #414, #415",
    "Fix #900: subject",
    "Closes #413: partially",
    "Part of #413 — scoped reference",
    "Closes #NNN.",
    "CLOSES #413 and more",
    "resolved #7 for the daemon path only",
    "nothing to see here",
    "",
    "#749 was shut by a keyword with a tail on line 30",
)


def _grep(pattern: str, line: str) -> bool:
    return subprocess.run(
        [GREP, "-qiE", pattern], input=line, text=True,
    ).returncode == 0


@pytest.mark.skipif(GREP is None, reason="grep -E unavailable")
@pytest.mark.parametrize("line", _CORPUS)
def test_python_and_grep_read_every_pattern_the_same(line):
    for name in ("ANY", "LINESTART", "CLEAN", "COLONCLOSE"):
        ere = getattr(closekeyword, name)
        expected = _grep(ere, line)
        actual = closekeyword.compile_pattern(ere).search(line) is not None
        assert actual is expected, f"{name} disagrees on {line!r}"


def test_to_python_leaves_no_posix_class_behind():
    for name in ("ANY", "LINESTART", "CLEAN", "COLONCLOSE"):
        assert "[:" not in closekeyword._to_python(getattr(closekeyword, name))


def test_to_python_refuses_an_unknown_posix_class():
    with pytest.raises(ValueError):
        closekeyword._to_python("[[:upper:]]+")


# ── 3. the table the ticket asked for ─────────────────────────────────────


@pytest.mark.parametrize(
    "line,rule",
    [
        # The four independently confirmed closures #839's comments enumerate.
        ("This does not close #477.", "position"),
        ("Closes #413 §7 S13.", "tail"),
        ("Closes #413, not really", "tail"),
        ("Fix #533: split config and closes #534", "colon-close"),
    ],
)
def test_confirmed_closures_are_caught_with_the_right_rule(line, rule):
    findings = closekeyword.check(line, channel="pr-body")
    assert [f.rule for f in findings] == [rule]


@pytest.mark.parametrize(
    "line",
    [
        "Fix #900: subject",
        "Closes #413, #414",
        "Closes #413.",
        "Closes #413",
        "  Closes #413.",
        "Part of #413 — the scoped reference form",
        # Knowingly accepted residual (#657): the ":" allowance cannot tell a
        # subject from a qualifier. Pinned so a future widening has to argue
        # with this line rather than silently flip it.
        "Closes #413: partially",
        # The remedies' own placeholder — the quoting answer #839 needs.
        "Closes #NNN.",
        "Closes #NNN move 5 (the ticket stays open for moves 1-4).",
        # Keyword and ref not adjacent: the other quoting answer.
        "#749 was shut by a close keyword carrying a tail",
        # Word boundary: "disclosed" is not "closed".
        "Fix #533: split config and disclosed #534",
    ],
)
def test_clean_lines_stay_clean(line):
    assert closekeyword.check(line, channel="pr-body") == []


def test_the_749_body_line_is_refused_with_its_line_number():
    """#838's body, verbatim — the failure this whole ticket is about."""
    body = (
        "## What this does\n"
        "\n"
        "Ships move 5 of the schedule rework.\n"
        "\n"
        "Closes #749 move 5 (the ticket stays open for moves 1-4).\n"
    )
    findings = closekeyword.check(body, channel="pr-body")
    assert len(findings) == 1
    assert findings[0].rule == "tail"
    assert findings[0].line_number == 5
    assert findings[0].line.startswith("Closes #749 move 5")


def test_check_reports_every_bad_line_not_just_the_first():
    """The hook exits on the first; a PR body is edited whole, so report all."""
    body = "Closes #1 (partly)\nok\nThis fixes #2 as well.\nCloses #3, #4\n"
    findings = closekeyword.check(body, channel="pr-body")
    assert [(f.line_number, f.rule) for f in findings] == [
        (1, "tail"), (3, "position"),
    ]


def test_check_is_pure_on_the_repos_own_docs_shape():
    """A body may name refs freely — only keyword+ref adjacency is a close."""
    assert closekeyword.check("See #839, #749 and #838 for context.") == []


def test_render_names_the_channel_and_offers_the_quoting_escape():
    findings = closekeyword.check("Closes #749 move 5 (scoped).", channel="pr-body")
    out = closekeyword.render(findings, channel="pr-body")
    assert out.startswith("pr-body:1: close keyword with a tail")
    assert "Mask the digits" in out
    # No `--no-verify` to offer on this channel, and inventing one would be a
    # bypass for a guard whose whole point is that nothing else covers it.
    assert "Bypass" not in out


def test_render_commit_msg_channel_keeps_the_hooks_bypass():
    findings = closekeyword.check("Closes #749 move 5.", channel="commit-msg")
    out = closekeyword.render(findings, channel="commit-msg")
    assert "Bypass: git commit --no-verify" in out
    assert "Mask the digits" not in out


def test_unknown_channel_is_an_error_not_a_silent_default():
    with pytest.raises(ValueError):
        closekeyword.check("Closes #1.", channel="issue-comment")
