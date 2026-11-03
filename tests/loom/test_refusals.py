"""Every refusal a runtime verb prints ends with one valid example of the act."""

from __future__ import annotations

import pytest

from brr.loom.runtime import __main__ as cli
from brr.loom.runtime.refusals import MARK, VERB, with_example

MACHINERY = {"loom", "jack", "merge-driver", "forge-token", "reap", "where", "config"}
VERBS = sorted(set(cli.VERBS) - MACHINERY)


def test_every_body_verb_has_an_example():
    assert set(VERBS) <= set(VERB)


@pytest.mark.parametrize("verb", VERBS)
def test_a_parse_error_ends_with_the_verbs_example(verb, capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main([verb, "--no-such-flag"])
    assert exit.value.code == 2
    last = capsys.readouterr().err.rstrip("\n").splitlines()[-1]
    assert last.startswith(f"  e.g. brnrd runtime {VERB[verb].split()[0]}")


def test_the_flag_a_refusal_names_picks_the_example():
    said = with_example("wish", "--result requires kind:ref and --line")
    assert said.endswith('--result pr:https://github.com/<owner>/<repo>/pull/<n> --line "what shipped" --deliver')
    assert with_example("send", "send: --note requires --re").endswith('--note "what you did"')
    # --left none is a delivery, not a restatement.
    assert "--deliver" in with_example("wish", "--left none: nothing left is a delivery")


def test_a_message_with_its_own_example_keeps_it():
    said = with_example("send", f"send: no thread x{MARK}brnrd runtime threads")
    assert said.count(MARK) == 1


def test_wish_result_without_line_shows_how(capsys):
    from brr.loom.runtime import verbs_wishes
    code = verbs_wishes.refuse("wish", ValueError("--result requires kind:ref and --line"))
    assert code == 1
    err = capsys.readouterr().err
    assert err.splitlines() == [
        "--result requires kind:ref and --line",
        '  e.g. brnrd runtime wish <w-id> --result pr:https://github.com/<owner>/<repo>/pull/<n> '
        '--line "what shipped" --deliver',
    ]
