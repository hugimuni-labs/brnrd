"""Incremental reads retain the uncached ledger's ordering and failure semantics."""
import json

import pytest
from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.daemon2.facts import union
from brr.loom.runtime.ledger import LedgerReader, read_facts as shared_read_facts, read_file


def read_facts(home):
    """The uncached fold every reader must agree with: no shared reader, every file parsed."""
    paths = sorted(home.facts_dir().parent.glob("*/*.jsonl"))
    return union(*(read_file(path) for path in paths)) if paths else []


def row(ident, **kwargs):
    return (json.dumps(Fact(kind='note', by='person:test', id=ident,
                           data={}, **kwargs).row()) + '\n').encode()


def test_reader_tracks_tails_rewrites_replacements_and_removal(tmp_path):
    home = Home(tmp_path)
    folder = home.facts_dir()
    path = folder / 'loom.jsonl'
    reader = LedgerReader()
    first, second = row('one'), row('two')
    path.write_bytes(first + second[:20])
    assert reader.read(home) == read_facts(home)
    # Returned lists belong to the caller.
    reader.read(home).clear()
    with path.open('ab') as handle:
        handle.write(second[20:])
    assert reader.read(home) == read_facts(home)
    path.write_bytes(first)  # truncate
    assert reader.read(home) == read_facts(home)
    path.write_bytes(first.replace(b'one', b'new'))  # same size rewrite
    assert reader.read(home) == read_facts(home)
    replacement = folder / 'replacement'
    replacement.write_bytes(second)
    replacement.replace(path)
    assert reader.read(home) == read_facts(home)
    other = folder / 'hand.jsonl'
    other.write_bytes(first)
    assert reader.read(home) == read_facts(home)
    path.unlink()
    assert reader.read(home) == read_facts(home)
    other.unlink()
    assert reader.read(home) == []


def test_reader_does_not_hide_conflicts_or_cache_a_failed_parse(tmp_path):
    home = Home(tmp_path)
    path = home.facts_dir() / 'loom.jsonl'
    reader = LedgerReader()
    path.write_bytes(row('one'))
    reader.read(home)
    with path.open('ab') as handle:
        handle.write(b'broken\n')
    with pytest.raises(ValueError):
        reader.read(home)
    path.write_bytes(row('one'))
    assert reader.read(home) == read_facts(home)
    (path.parent / 'hand.jsonl').write_bytes(row('one', at='2000-01-01T00:00:00+00:00'))
    with pytest.raises(ValueError, match='conflicting fact'):
        reader.read(home)


def test_reader_parses_only_complete_appended_rows(tmp_path, monkeypatch):
    from brr.loom.runtime import ledger
    home = Home(tmp_path)
    path = home.facts_dir() / 'loom.jsonl'
    reader = LedgerReader()
    path.write_bytes(row('one'))
    reader.read(home)
    parsed = []
    real = ledger.json.loads

    def counted(raw):
        parsed.append(raw)
        return real(raw)

    monkeypatch.setattr(ledger.json, 'loads', counted)
    next_row = row('two')
    with path.open('ab') as handle:
        handle.write(next_row[:10])
    assert len(reader.read(home)) == 1
    assert parsed == []
    with path.open('ab') as handle:
        handle.write(next_row[10:])
    assert [f.id for f in reader.read(home)] == ['one', 'two']
    assert len(parsed) == 1


def test_projection_cache_tracks_appends_and_does_not_leak(tmp_path):
    from brr.loom.runtime.project import fold, projection_cache
    facts = [Fact(kind='note', by='person:test', id='one', data={'re': 'a'})]
    cache = {}
    with projection_cache(cache):
        assert fold(facts).handled == {'a'}
        facts.append(Fact(kind='note', by='person:test', id='two', data={'re': 'b'}))
        assert fold(facts).handled == {'a', 'b'}
    with projection_cache(cache):
        assert fold(list(facts)).handled == {'a', 'b'}
        assert fold(facts[:1]).handled == {'a'}
    assert fold([]).handled == set()


def test_reader_reorders_when_a_causal_predecessor_arrives_late(tmp_path):
    home = Home(tmp_path)
    folder = home.facts_dir()
    (folder / 'loom.jsonl').write_bytes(row(
        'successor', hlc=(1, 0, 'abcd'), after=('predecessor',)))
    reader = LedgerReader()
    assert [f.id for f in reader.read(home)] == ['successor']
    (folder / 'hand.jsonl').write_bytes(row('predecessor', hlc=(2, 0, 'abcd')))
    assert [f.id for f in reader.read(home)] == ['predecessor', 'successor']
    assert reader.read(home) == read_facts(home)


def test_a_view_handed_out_never_changes_when_a_late_fact_reorders(tmp_path):
    """A walker on another thread holds the view refresh returned (the board's
    bead press). A late earlier fact used to rebuild that object in place, so the
    walker saw an emptied list under a full index and cached a partial answer."""
    home = Home(tmp_path)
    folder = home.facts_dir()
    (folder / 'loom.jsonl').write_bytes(row('late', hlc=(5, 0, 'abcd')))
    reader = LedgerReader()
    held = reader.refresh(home)
    built = held.project('ids', (), lambda view: [f.id for f in view])
    (folder / 'hand.jsonl').write_bytes(row('early', hlc=(2, 0, 'abcd')))
    current = reader.refresh(home)
    assert [f.id for f in current] == ['early', 'late']
    assert [f.id for f in held] == ['late']
    assert [f.id for f in held.by_kind['note']] == ['late']
    assert held.project('ids', (), lambda view: [f.id for f in view]) is built
    assert current == read_facts(home)


def test_a_second_read_facts_copies_pointers_and_folds_nothing(tmp_path):
    """read_facts shares the process's fold: a second call parses no fact, and
    allocates one list of pointers, not a refold (#2541's shape, 2026-10-30)."""
    import gc
    import tracemalloc
    home = Home(tmp_path)
    home.facts_dir().joinpath('loom.jsonl').write_bytes(b''.join(row(f'f{i}') for i in range(5000)))
    first = shared_read_facts(home)
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.take_snapshot()
        second = shared_read_facts(home)
        grown = sum(stat.size_diff for stat in tracemalloc.take_snapshot().compare_to(before, 'filename'))
    finally:
        tracemalloc.stop()
    assert second == first and second is not first
    assert all(a is b for a, b in zip(first, second))  # the same Fact objects, none parsed again
    assert grown < 8 * len(first) + 64_000  # the list's pointers and the scan's stats, nothing per fact


def test_a_stream_rewritten_longer_in_place_is_read_again_not_as_an_append(tmp_path):
    """The hand.jsonl interface: a person rewrites the file, same inode, longer."""
    home = Home(tmp_path)
    path = home.facts_dir() / 'hand.jsonl'
    path.write_bytes(row('one') + row('two'))
    reader = LedgerReader()
    assert [f.id for f in reader.read(home)] == ['one', 'two']
    path.write_bytes(row('one-renamed') + row('two'))
    assert [f.id for f in reader.read(home)] == [f.id for f in read_facts(home)]
    with path.open('ab') as handle:
        handle.write(row('three'))
    assert [f.id for f in reader.read(home)] == [f.id for f in read_facts(home)]
