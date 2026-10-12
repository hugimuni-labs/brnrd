"""Ownership regressions through ports, process exits and the operator CLI."""
import json
from pathlib import Path

from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.owners import letter_owners
from brr.loom.runtime.verbs_loom import _attention
from _step import Loom, wait_until, write_thread


def test_two_failed_starts_are_visible_to_operator_and_a_note_retires_them(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Over-budget wake', 'answer-fast', wait='0')
    monkeypatch.setenv('LOOM_BUDGET_BYTES', '1')
    ident = inject_letter(root, to='thread:work', body='Please answer')
    loom = Loom(root)
    loom.start()
    try:
        def table():
            return letter_owners(loom.facts(), main_snapshot(Home(root)))
        wait_until(lambda: table()[ident].owner == 'operator', 10, loom.dump)
        ends = [f for f in loom.facts() if f.kind == 'attempt.ended']
        assert len(ends) == 2
        assert all(f.data['cause'] == 'failed' and ident in f.data['ids'] for f in ends)
        assert ends[0].data['dispositions'][0]['outcome'] == 'retry'
        assert ends[1].data['dispositions'][0]['outcome'] == 'operator'
        assert _attention(['--home', str(root)]) == 0
        assert ident in capsys.readouterr().out
        assert _attention(['--home', str(root), '--note', ident, '--why', 'repaired by hand']) == 0
        assert table()[ident].done
    finally:
        loom.halt()
