"""Every refusal a runtime verb prints ends with one valid example of the act it refused.

A refusal that names a flag gets that flag's example; otherwise the verb's own. A message
that already carries an ``e.g.`` line keeps it. The verb's catch site calls :func:`refuse`;
:class:`Parser` does the same for argparse's own errors.
"""

from __future__ import annotations

import argparse
import functools
import re
import sys

MARK = "\n  e.g. "

# (verb, flag) -> example; a flag the refusal names picks the first that matches, in order.
FLAGS: dict[str, list[tuple[str, str]]] = {
    "send": [
        ("--note", 'send --re <letter id> --to thread:<thread> --note "what you did"'),
        ("--budget", "send --to thread:<thread> --budget 2m"),
        ("--at", 'send --to thread:<thread> --at +30m "check the deploy"'),
        ("--reopen", 'send --to thread:<thread> --reopen "what reopens it"'),
        ("--chatter", 'send --re <letter id> --to thread:inbox --chatter "thanks"'),
        ("--correction", 'send --re <letter id> --to thread:inbox --correction people/<name>/page.md "noted"'),
        ("--silent", 'send --re <letter id> --to thread:inbox --silent mistake --note "a cat on the keys"'),
        ("--hold", 'send --re <letter id> --to <another channel> --hold out-of-pattern "Was that you, at the café?"'),
        ("--answered", 'send --re <letter id> --to thread:inbox --wish <w-id> --answered "the answer"'),
        ("--body", 'send --to thread:<new> --wish new:"<line>" --body claude "the work"'),
        ("--repo", 'send --to thread:<new> --wish new:"<line>" --repo brnrd "the work"'),
        ("--wish", 'send --re <letter id> --to thread:inbox --wish <w-id> "reply"'),
        ("--says", 'send --re <letter id> --to thread:inbox --wish <w-id> --says "their words" "reply"'),
        ("--ref", 'send --re <letter id> --to thread:inbox --wish <w-id> --ref pr:<url> "reply"'),
        ("--for", 'send --re <letter id> --to person:<name> --for <w-id> "the question?"'),
        ("--re", 'send --re <letter id> --to thread:inbox "reply"'),
    ],
    "wish": [
        ("--result", 'wish <w-id> --result pr:https://github.com/<owner>/<repo>/pull/<n> --line "what shipped" --deliver'),
        ("--deliver", 'wish <w-id> --result pr:<url> --line "what shipped" --deliver'),
        ("--left none", 'wish <w-id> --result pr:<url> --line "what shipped" --deliver'),
        ("--left same", 'wish <w-id> --left "first step" --left "second step"'),
        ("--left", 'wish <w-id> --left "first step" --left "second step"'),
        ("--wait", 'wish <w-id> --read "waiting: CI on #12" --wait ci:<owner>/<repo>@<sha>'),
        ("--until", 'wish <w-id> --read "waiting: his answer" --wait letter:<id> --until +1d'),
        ("--ref", "wish <w-id> --accept word --ref <letter id>"),
        ("--say", 'wish <w-id> --read "what it asks" --say <letter id>'),
        ("--parent", 'wish new --line "<line>" --parent <w-id>'),
        ("--top", "wish <w-id> --top <letter id>"),
        ("--pick", 'wish <w-id> --pick "why this one next"'),
        ("--home", 'wish <w-id> --read "what it asks"'),
        ("wish new", 'wish new --line "<line>" --thread <thread>'),
        ("closed on main", 'send --to thread:<new> --wish new:"<line>" "the work"'),
        ("no wish", "wishes"),
    ],
    "ask": [
        ("--default-at", 'ask "merge #12?" --for <w-id> --rec "yes" --default-at +1d'),
        ("--rec", 'ask "merge #12?" --for <w-id> --rec "yes"'),
    ],
    "attention": [("--note", 'attention --note <letter id> --why "what was done"')],
    "machines": [("prefer", "machines prefer <install>")],
    "face": [("nothing is proposed", "face b·w·d")],
    "publish": [("--pr", "publish <repo> --pr <n>"), ("--merged", "publish <repo> --merged <n>")],
}

VERB: dict[str, str] = {
    "send": 'send --re <letter id> --to thread:inbox "reply"',
    "settle": 'settle <thread> --why "what it became"',
    "reincarnate": 'reincarnate --why "mostly residue; the README has the rest"',
    "hold": 'hold --why "mid-way on <what>, by name"',
    "wish": 'wish <w-id> --left "next step"',
    "wishes": "wishes --all",
    "ask": 'ask "merge #12?" --for <w-id> --rec "yes"',
    "threads": "threads --left",
    "asks": "asks",
    "graph": "graph",
    "gauge": "gauge --days 7",
    "face": "face take",
    "machines": "machines",
    "attention": 'attention --note <letter id> --why "what was done"',
    "init": "init --person <name>",
    "wake": "wake --room <room> --thread <thread>",
    "lib": 'lib add <name> <url> --kind known --whose <person> --for "why"',
    "send-self": "send-self --room .",
    "publish": "publish <repo>",
    "guard": "guard merge-red '#12'",
    "resume": "resume",
    "drop": "drop",
}


def example(verb: str, message: str) -> str | None:
    """The example a refusal of ``verb`` saying ``message`` ends with."""
    for flag, shown in FLAGS.get(verb, ()):
        if re.search(re.escape(flag) + r"(?![\w-])", message):
            return shown
    return VERB.get(verb)


def with_example(verb: str, message: str) -> str:
    message = str(message).rstrip("\n")
    if MARK in message:
        return message
    shown = example(verb, message)
    return message if shown is None else f"{message}{MARK}brnrd runtime {shown}"


def refuse(verb: str, exc: object, code: int = 1, prefix: str = "") -> int:
    """Print a refusal on stderr, ending with one valid example; return the exit code."""
    sys.stderr.write(with_example(verb, f"{prefix}{exc}") + "\n")
    return code


class Parser(argparse.ArgumentParser):
    """An argparse parser whose errors end with the verb's example."""

    def __init__(self, *args, verb: str, **kwargs):
        kwargs.setdefault("prog", f"brnrd runtime {verb}")
        super().__init__(*args, **kwargs)
        self.verb = verb

    def add_subparsers(self, **kwargs):
        # A subcommand refuses with its verb's example: `lib add`'s error shows `lib add`.
        kwargs.setdefault("parser_class", functools.partial(Parser, verb=self.verb))
        return super().add_subparsers(**kwargs)

    def error(self, message):  # noqa: D401 - argparse's hook
        self.print_usage(sys.stderr)
        self.exit(2, with_example(self.verb, f"{self.prog}: error: {message}") + "\n")
