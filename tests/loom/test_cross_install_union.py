"""Two installs that ingest one source keep one copy, the lower HLC."""
import pytest
from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import LedgerReader, append, read_facts


def source(by, hlc=None):
    return Fact(kind='source', by=by, id='source:mine:1:2', hlc=hlc,
                data={'channel': 'mine', 'chat': 1, 'message': 2, 'body': 'hi'})


def test_two_installs_ingesting_one_source_union_to_the_lower_hlc(tmp_path):
    imac, laptop = Home(tmp_path, install='aaaa'), Home(tmp_path, install='bbbb')
    first = append(imac, source('loom:imac'))
    second = append(laptop, source('loom:laptop'))
    assert first.hlc[2] == 'aaaa' and second.hlc[2] == 'bbbb'
    winner = min(first, second, key=lambda fact: fact.hlc)
    facts = read_facts(imac)
    assert [f for f in facts if f.id == 'source:mine:1:2'] == [winner]
    reader = LedgerReader()
    assert reader.read(laptop) == facts
    assert reader.ordered.duplicates == [max(first, second, key=lambda f: f.hlc)]


def test_a_later_lower_copy_displaces_the_held_one(tmp_path):
    imac, laptop = Home(tmp_path, install='aaaa'), Home(tmp_path, install='bbbb')
    held = append(imac, source('loom:imac', hlc=(2000, 0, 'aaaa')))
    reader = LedgerReader()
    assert reader.read(imac) == [held]
    # The laptop ingested first while split; its folder arrives afterwards.
    earlier = append(laptop, source('loom:laptop', hlc=(1000, 0, 'bbbb')))
    assert reader.read(imac) == read_facts(imac) == [earlier]
    assert reader.ordered.duplicates == [held]
    # Arriving again in the other order changes nothing.
    assert read_facts(laptop) == [earlier]


def test_one_install_rewriting_a_fact_is_still_a_conflict(tmp_path):
    home = Home(tmp_path, install='aaaa')
    append(home, source('loom:imac', hlc=(1000, 0, 'aaaa')))
    other = home.root / 'ledger' / 'facts' / 'aaaa' / 'hand.jsonl'
    import json
    other.write_text(json.dumps(source('loom:other', hlc=(900, 0, 'aaaa')).row()) + '\n')
    with pytest.raises(ValueError, match='conflicting fact id'):
        read_facts(home)
