"""Recorded real drives, replayed through the production invariant check."""
import json
from pathlib import Path
import pytest
from brr.daemon2.facts import Fact
from brr.loom.runtime.check import violations

RECORDINGS = Path(__file__).parent / 'fixtures/letter-facts'

@pytest.mark.parametrize('path', sorted(RECORDINGS.glob('*.json')), ids=lambda p: p.stem)
def test_every_prefix_of_a_real_drive_has_one_owner(path):
    record = json.loads(path.read_text())
    assert record['body'].startswith(('codex/', 'grok/'))
    problems = violations([Fact.from_row(row) for row in record['facts']], record['main'])
    assert not problems, '\n'.join(f'prefix {p}: {letter}: {why}' for p, letter, why in problems)
