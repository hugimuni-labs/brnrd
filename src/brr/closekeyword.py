"""The close-keyword predicate — one owner, two channels.

GitHub closes an issue from a **closing keyword + ref** on two surfaces with
equal authority: a commit message, and a **pull-request body**.  Before #839
this predicate existed only as POSIX-ERE fragments embedded in the
``commit-msg`` hook string in :mod:`brr.gitops`, so only the first surface was
covered.  #749 died of exactly the gap: PR #838's body carried
``Closes #749 move 5 (the ticket stays open for moves 1-4).`` — GitHub matched
the head of that line, discarded the clause written specifically to prevent the
close, and shut three unshipped moves off the open list.

So this module is the predicate's single home.  Two consumers read the same
table:

- :func:`hook_script_body` renders the ``sh`` fragment
  :data:`brr.gitops._RUN_ID_HOOK_SCRIPT` is built from.  The hook is
  **byte-frozen**: the fragment this renders is character-for-character the
  literal that shipped, and ``tests/test_gitops.py`` is its parity floor.
- :func:`check` is a pure Python checker — text in, findings out — wired to
  ``gate: forge`` PR bodies at the outbox drain
  (:func:`brr.daemon._deliver_out_of_bound`) and exposed as ``brnrd
  close-check`` for the hand-``gh`` path that no chokepoint can reach.

**Two dialects, one source.**  ``grep -E`` speaks POSIX ERE (``[[:digit:]]``);
:mod:`re` does not.  The patterns below are stored in the dialect the *shell*
needs — the one that has to survive Python source escaping, ``sh`` single
quoting and ``grep`` — and :func:`_to_python` mechanically expands the three
character classes they use into their literal ranges for :mod:`re`.  The
expansion is a closed substitution table over classes that only ever appear
inside bracket expressions, so it is exact rather than approximate;
``tests/test_closekeyword.py`` drives both dialects over a corpus and asserts
they agree line for line.  That is why there is no "do the two copies agree?"
question to ask: there is one copy, and one derivation.

The predicate's own argument — why *position* and not a word list, why the
``:`` allowance is load-bearing, why the residual ``Closes #413: partially``
is knowingly accepted — lives with the patterns in :data:`RULES` and in
:mod:`brr.gitops`'s hook comments.  Nothing here changes it; #839 was never
about the rule being wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# --------------------------------------------------------------------------
# The patterns, in the dialect the shell needs.
#
# POSIX sh / POSIX ERE — ``grep -E``, no ``\b``, explicit character classes.
# "[.]?" rather than "\.?": same POSIX ERE meaning, no backslash to survive
# Python source escaping *and* sh single-quoting on the way to grep.  GitHub's
# own closing-keyword scanner does not care about narrative framing or
# position within the text — it matches "keyword #NNN" wherever it occurs — so
# every shape below is a real, independently confirmed close, not a
# readability nit.
#
# #653 first tried a closed word list of leading qualifiers (partially,
# mostly, ...).  Dropped: a real commit surfaced mid-run — `85ed4735`,
# "This does not close #477." — that GitHub's timeline confirms closed #477
# two seconds after push, and no finite word list anticipates "does not" the
# way a position rule does for free.  The second real closure is sharper
# still: GitHub's timeline credits `aef7fa11` — the commit that *shipped*
# #652 — for re-closing #413, because its own body quotes `Closes #413 §7
# S13.` as a documentation example of what the guard refuses.  The same push
# also carried `c91d3866`, whose subject narrates "...that actually closed
# #413..."; that one changed nothing only because the issue was already closed
# by then.  Neither is a leading qualifier.  Position, not vocabulary, is the
# discriminator.
#
# ANY: keyword + #NNN anywhere in the line (word-bounded so it doesn't fire
# inside a longer word like "disclosed").  A line that doesn't match this at
# all needs no further check.
ANY = (
    "(^|[^[:alnum:]_])(close[sd]?|fix(es|ed)?|resolve[sd]?)"
    "[[:space:]]+#[[:digit:]]+"
)
# LINESTART: the same pair, anchored to the start of the line (leading
# whitespace allowed) — the one position GitHub-closing intent can be stated
# unambiguously.
LINESTART = (
    "^[[:space:]]*(close[sd]?|fix(es|ed)?|resolve[sd]?)"
    "[[:space:]]+#[[:digit:]]+"
)
# CLEAN: what a line-start close is *allowed* to look like, whole — a positive
# rule (#657), replacing #652's negative _BRR_TRAILING.  The negative shape
# asked "is the character after the ref disqualifying?" and exempted "," to let
# a genuine multi-close through.  That carve-out exempted the entire rest of
# the line with it: `Closes #413, not really` and `Closes #413, #414, and #415`
# both passed while GitHub closed #413 anyway.  A "." bought the same exemption
# a different way — the negative rule wanted whitespace right after the digits,
# so `Closes #413. Also this fixes #414` passed on both counts, the second
# close shielded by the first one being well-formed.  Fourth face of the same
# class, and — like the third — the hole was in the carve-out, not in the rule.
#
# So: once a line has cleared the position gate, the only legal continuation
# after the ref is a repeatable `, #NNN` list, then either end of line
# (optional "." and trailing space) or ":" — the subject separator.  Anything
# else is prose, and prose after a close keyword is what this guard exists to
# catch.
#
# The ":" allowance is load-bearing and it is *not* a return to vocabulary.
# Measured over this repo's last 300 commit messages, driving the installed
# hook:
#
#     shipped (negative _BRR_TRAILING)   10 refusal lines / 5 commits
#     strict  (no ":" allowed)           18 refusal lines / 13 commits
#     this rule (":" allowed)            10 refusal lines / 5 commits
#
# The 8 extra strict refusals are all `Fix #NNN: <subject>` — this repo's
# standard fix-commit subject, where the close is intended, the effect matches
# the intent, and nothing on the line narrows it.  Refusing those would make
# the guard fire on nearly every fix commit, and a guard that cries wolf stops
# being read (#623).  (The window slides: re-driven at #839's branch point the
# same rule reads 9 lines / 4 commits, because two of the original refusals
# have aged out of the last 300.  What must not move is the *delta* across a
# refactor — #839's own measurement is before-and-after on one window, and it
# came out 9/4 both times.)
#
# Residual, knowingly accepted: `Closes #413: partially` passes.  It is the
# price of the ":" allowance — the guard cannot tell a subject from a qualifier
# once a colon is legal.  Not covered.  Do not read the rule as covering it.
CLEAN = (
    "^[[:space:]]*(close[sd]?|fix(es|ed)?|resolve[sd]?)[[:space:]]+"
    "#[[:digit:]]+(,[[:space:]]*#[[:digit:]]+)*([.]?[[:space:]]*$|:)"
)
# COLONCLOSE: the one hole the ":" allowance opens that is not a qualifier but
# a *second close*.  `Fix #533: split config and closes #534` clears CLEAN at
# the colon and shuts #534 as well — which is the #413 accident's own shape, an
# unintended close riding a well-formed one.  The colon exempts a *subject*; it
# must not exempt another close.  Driven over this repo's last 300 commits: 10
# refusal lines / 5 commits with this branch on, unchanged.  Zero new refusals
# — it reaches only a shape the repo has never written.  Two alternatives after
# the colon so a keyword can sit flush against it (`:closes #2`) or anywhere
# later, and neither fires inside a longer word ("disclosed").
COLONCLOSE = (
    "^[[:space:]]*(close[sd]?|fix(es|ed)?|resolve[sd]?)[[:space:]]+"
    "#[[:digit:]]+(,[[:space:]]*#[[:digit:]]+)*:([[:space:]]*|.*[^[:alnum:]_])"
    "(close[sd]?|fix(es|ed)?|resolve[sd]?)[[:space:]]+#[[:digit:]]+"
)


# The three POSIX character classes these patterns use, expanded to the
# literal ranges :mod:`re` understands.  Every occurrence sits inside a
# bracket expression (``[[:space:]]``, ``[^[:alnum:]_]``), so substituting the
# class token for its contents is exact, not an approximation.  ASCII ranges
# are spelled out rather than borrowing ``\w``/``\s``, which are Unicode-wide
# in Python and would silently widen the predicate.
_POSIX_CLASSES = {
    "[:alnum:]": "a-zA-Z0-9",
    "[:digit:]": "0-9",
    "[:space:]": " \\t\\n\\x0b\\f\\r",
}


def _to_python(ere: str) -> str:
    """Expand POSIX character classes so :mod:`re` reads the same pattern."""
    out = ere
    for token, expansion in _POSIX_CLASSES.items():
        out = out.replace(token, expansion)
    left = re.search(r"\[:[a-z]+:\]", out)
    if left is not None:  # pragma: no cover - guards a future pattern edit
        raise ValueError(f"unexpanded POSIX class {left.group(0)!r} in {ere!r}")
    return out


def compile_pattern(ere: str) -> re.Pattern[str]:
    """Compile a shell-dialect pattern for Python, case-insensitively.

    ``grep -qiE`` is what the hook runs, so ``IGNORECASE`` here is parity, not
    a choice.
    """
    return re.compile(_to_python(ere), re.IGNORECASE)


@dataclass(frozen=True)
class Rule:
    """One refusal branch: its test, its diagnosis, its remedies.

    *var* / *negate* describe how the hook's ``if``-cascade tests this rule —
    ``negate`` meaning the branch fires when the pattern does **not** match.
    The Python checker evaluates the identical cascade in the identical order,
    so the two cannot diverge on which rule claims a line.
    """

    name: str
    var: str
    pattern: str
    negate: bool
    headline: str
    details: tuple[str, ...]
    remedies: tuple[str, ...]

    def fires(self, line: str) -> bool:
        matched = compile_pattern(self.pattern).search(line) is not None
        return (not matched) if self.negate else matched


# Both/every remedy in each rule, deliberately.  These branches are reached by
# authors with opposite intents — "Partially closes #NNN" wants a reference,
# "This closes #NNN" wants the close, "Closes #NNN and #MMM" wants both,
# "Fix #NNN - subject" wants the close plus a title — and an author shown only
# the scoped form is steered into silently *not* closing what they meant to
# close.  A guard satisfiable only by abandoning the intent is not
# satisfiable; that is the whole argument this predicate rests on.
RULES: tuple[Rule, ...] = (
    Rule(
        name="position",
        var="_BRR_LINESTART",
        pattern=LINESTART,
        negate=True,
        headline=(
            "close keyword not at the start of a line "
            "(GitHub still closes on it there)."
        ),
        details=("A close keyword only closes at the start of a line.",),
        remedies=(
            "Closes #NNN.       (at the start of a line — closes it)",
            "Part of #NNN ...   (scoped reference — does not close)",
        ),
    ),
    Rule(
        name="tail",
        var="_BRR_CLEAN",
        pattern=CLEAN,
        negate=True,
        headline=(
            "close keyword with a tail "
            "(GitHub ignores the tail and closes the issue)."
        ),
        details=(
            'After the ref, only more refs may follow: ", #MMM", '
            'then end of line or ": subject".',
        ),
        remedies=(
            "Closes #NNN.            (bare close — no tail)",
            'Closes #NNN, #MMM       (real multi-close — commas, never "and")',
            "Fix #NNN: subject       (close plus a subject, after the colon)",
            "Part of #NNN ...        (scoped reference — does not close)",
        ),
    ),
    Rule(
        name="colon-close",
        var="_BRR_COLONCLOSE",
        pattern=COLONCLOSE,
        negate=False,
        headline="a second close keyword rides the subject after the colon.",
        details=("The colon may introduce a subject, never another close.",),
        remedies=(
            "Closes #NNN, #MMM       (close both, on the ref list)",
            'Fix #NNN: subject       (then "Closes #MMM." on its own line)',
        ),
    ),
)


@dataclass(frozen=True)
class Channel:
    """A surface GitHub closes from, and how a refusal is worded there.

    *quoting* is #839's own design point.  The commit hook refused the first
    draft of the commit message announcing it — correctly — because that draft
    quoted the offending line.  A PR body is *where* a run would naturally
    show the example, so a widened check needs an answer, and the shipped
    predicate already contains it: ``#NNN`` is not ``#[[:digit:]]+``, so the
    placeholder form the remedies have been printing all along is quotable
    verbatim; and ``ANY`` requires the keyword and the ref to be adjacent, so
    naming the ref away from the keyword is quotable too.  No rule change, no
    bypass, no code-fence exemption — GitHub does not document ignoring
    closing keywords inside fenced blocks, and #839 is a ticket about the cost
    of assuming a channel's behaviour.
    """

    label: str
    bypass: str | None
    quoting: tuple[str, ...] = ()


COMMIT_MSG = Channel(label="commit-msg", bypass="git commit --no-verify")
PR_BODY = Channel(
    label="pr-body",
    bypass=None,
    quoting=(
        'Quoting a bad line as the example? Mask the digits — "Closes #NNN"',
        "never closes, and a ref away from its keyword (#749) never closes.",
    ),
)
CHANNELS = {c.label: c for c in (COMMIT_MSG, PR_BODY)}


@dataclass(frozen=True)
class Finding:
    """One refused line: where, which rule, and what to say about it."""

    rule: str
    line_number: int
    line: str
    headline: str
    details: tuple[str, ...]
    remedies: tuple[str, ...]


def check(text: str, *, channel: str = PR_BODY.label) -> list[Finding]:
    """Return every close-keyword refusal in *text*. Pure — no I/O, no git.

    Unlike the hook, which ``exit 1``s on the first offending line, this
    reports them all: a PR body is edited as a whole, and a refusal that names
    one of three bad lines costs three round trips.
    """
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}")
    any_pattern = compile_pattern(ANY)
    findings: list[Finding] = []
    for number, line in enumerate(text.split("\n"), start=1):
        if any_pattern.search(line) is None:
            continue
        for rule in RULES:
            if not rule.fires(line):
                continue
            findings.append(
                Finding(
                    rule=rule.name,
                    line_number=number,
                    line=line,
                    headline=rule.headline,
                    details=rule.details,
                    remedies=rule.remedies,
                )
            )
            break
    return findings


@dataclass(frozen=True)
class CloseRef:
    """A close reference extracted from a line: the ref number and its line."""

    ref: str  # e.g., "1433"
    line_number: int


def extract_close_refs(text: str, *, channel: str = PR_BODY.label) -> list[CloseRef]:
    """Extract all close refs that will close issues in *text*.

    Returns refs found in lines with valid close-keyword syntax (lines that
    don't fire any of the RULES). Each ref is extracted from a close keyword
    line that passes validation.
    """
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}")

    # First, check which lines have syntax errors
    findings = check(text, channel=channel)
    bad_lines = {f.line_number for f in findings}

    # Now extract refs from valid lines (lines with close keywords but no errors)
    any_pattern = compile_pattern(ANY)
    linestart_pattern = compile_pattern(LINESTART)
    # Pattern to extract all #NNN refs
    ref_pattern = re.compile(r"#(\d+)", re.IGNORECASE)

    refs: list[CloseRef] = []
    for number, line in enumerate(text.split("\n"), start=1):
        # Skip lines with syntax errors
        if number in bad_lines:
            continue
        # Only process lines that have a close keyword at all
        if any_pattern.search(line) is None:
            continue
        # Only process lines with valid syntax (matching LINESTART)
        if linestart_pattern.search(line) is None:
            continue

        # Extract all refs from this valid close-keyword line
        for match in ref_pattern.finditer(line):
            ref_num = match.group(1)
            refs.append(CloseRef(ref=ref_num, line_number=number))

    return refs


def render(findings: list[Finding], *, channel: str = PR_BODY.label) -> str:
    """Render *findings* in the hook's own diagnosis shape."""
    chan = CHANNELS[channel]
    out: list[str] = []
    for finding in findings:
        out.append(f"{chan.label}:{finding.line_number}: {finding.headline}")
        out.append(f"  Offending line: {finding.line}")
        out.extend(f"  {d}" for d in finding.details)
        out.append("  Use instead:")
        out.extend(f"    {r}" for r in finding.remedies)
        out.extend(f"  {q}" for q in chan.quoting)
        if chan.bypass:
            out.append(f"  Bypass: {chan.bypass}")
    return "\n".join(out)


def _sq(value: str) -> str:
    """Single-quote *value* for ``sh``, refusing anything unquotable.

    The whole one-owner shape rests on this staying trivially true: none of
    the patterns or messages contains an apostrophe, so ``'...'`` is exact and
    no escaping dance is needed.  Should a future edit introduce one, this
    raises at import rather than shipping a hook whose quoting silently ate
    half a pattern.
    """
    if "'" in value:
        raise ValueError(f"not safe to single-quote for sh: {value!r}")
    return f"'{value}'"


def _printf(text: str) -> str:
    """One ``printf ... >&2`` line of the hook, at the hook's indentation."""
    return f"        printf {_sq(text + chr(92) + 'n')} >&2\n"


def hook_script_body(*, indent: str = "  ") -> str:
    """Render the close-keyword section of the ``commit-msg`` hook.

    Byte-frozen against the literal that shipped with #657: this renders the
    pattern assignments and the ``while``-loop cascade exactly as they were
    written by hand, so :mod:`brr.gitops` interpolating it is a refactor with
    no behavioural surface at all.  ``tests/test_gitops.py`` — unmodified — is
    the proof, and ``tests/test_closekeyword.py`` pins the byte-identity
    directly.
    """
    lines = [
        f"{indent}_BRR_ANY={_sq(ANY)}\n",
        f"{indent}_BRR_LINESTART={_sq(LINESTART)}\n",
        f"{indent}_BRR_CLEAN={_sq(CLEAN)}\n",
        f"{indent}_BRR_COLONCLOSE={_sq(COLONCLOSE)}\n",
        f"{indent}while IFS= read -r _brr_ln; do\n",
        '    if echo "$_brr_ln" | grep -qiE "${_BRR_ANY}"; then\n',
    ]
    for position, rule in enumerate(RULES):
        keyword = "if" if position == 0 else "elif"
        bang = "! " if rule.negate else ""
        lines.append(
            f'      {keyword} {bang}echo "$_brr_ln" '
            f'| grep -qiE "${{{rule.var}}}"; then\n'
        )
        lines.append(_printf(f"{COMMIT_MSG.label}: {rule.headline}"))
        lines.append(
            "        printf '  Offending line: %s\\n' \"$_brr_ln\" >&2\n"
        )
        lines.extend(_printf(f"  {d}") for d in rule.details)
        lines.append(_printf("  Use instead:"))
        lines.extend(_printf(f"    {r}") for r in rule.remedies)
        if COMMIT_MSG.bypass:
            lines.append(_printf(f"  Bypass: {COMMIT_MSG.bypass}"))
        lines.append("        exit 1\n")
    lines.append("      fi\n")
    lines.append("    fi\n")
    lines.append(f'{indent}done < "$1"\n')
    return "".join(lines)
