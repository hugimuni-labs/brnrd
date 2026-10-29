"""`brnrd loom` and `brnrd runtime` answer a request for help instead of an argparse error."""

from __future__ import annotations

import pytest

from brr import cli
from brr.loom.runtime import __main__ as runtime
from brr.loom.runtime import service


def test_runtime_help_lists_every_verb_with_a_line(capsys):
    assert runtime.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: brnrd runtime <verb>")
    for verb in runtime.VERBS:
        assert verb in runtime.SUMMARY, verb
        assert f"  {verb} " in out


def test_runtime_unknown_verb_names_it_and_exits_2(capsys):
    assert runtime.main(["sned"]) == 2
    err = capsys.readouterr().err
    assert "no verb 'sned'" in err and "usage: brnrd runtime" in err


@pytest.mark.parametrize("flag", ["-h", "--help", "help"])
def test_loom_help_exits_0_and_points_at_runtime(flag, capsys):
    assert service.main([flag]) == 0
    out = capsys.readouterr().out
    assert "usage: brnrd loom install" in out and "brnrd runtime --help" in out


def test_cli_routes_loom_help_and_bare_loom(capsys):
    cli.main(["loom", "--help"])
    assert "usage: brnrd loom" in capsys.readouterr().out
    with pytest.raises(SystemExit) as bare:
        cli.main(["loom"])
    assert "usage: brnrd loom" in str(bare.value.code)


def test_top_level_help_names_loom_and_runtime():
    text = " ".join(cli.build_parser().format_help().split())
    assert "brnrd loom --help" in text and "brnrd runtime --help" in text
