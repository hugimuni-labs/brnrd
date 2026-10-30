"""The production delta callers must equal a fresh full causal replay."""
import copy
import json
from pathlib import Path

import pytest

from brr.daemon2.facts import Fact, union
from brr.loom.runtime.bodies import state_of
from brr.loom.runtime.home import Home
from datetime import datetime, timezone

from brr.loom.runtime.faces import triggers
from brr.loom.runtime.ledger import LedgerReader, LedgerView, append, kind_facts, read_facts
from brr.loom.runtime.owners import letter_owners, letters_for, thread_axes, unanswered_letters
from brr.loom.runtime.port import letter_attachments, letter_replies
from brr.loom.runtime.project import fold, reply_channels, sender_threads
from brr.loom.runtime.threads import _table_inputs


RECORDINGS = Path(__file__).parent / 'fixtures/letter-facts'


def equivalent(view, raw, main, root):
    ordered = union(raw)
    # Copy before the next resume: the live view deliberately carries state.
    assert copy.deepcopy(fold(view)) == fold(ordered)
    assert letter_owners(view, main) == letter_owners(ordered, main)
    assert thread_axes(view, main) == thread_axes(ordered, main)
    assert _table_inputs(view, main)[3:] == _table_inputs(ordered, main)[3:]
    assert sender_threads(view) == sender_threads(ordered)
    assert reply_channels(view) == reply_channels(ordered)
    assert letter_attachments(root, view) == letter_attachments(root, ordered)
    assert letter_replies(view) == letter_replies(ordered)
    for thread in main:
        assert letters_for(view, main, thread) == letters_for(ordered, main, thread)
    assert unanswered_letters(view) == unanswered_letters(ordered)
    # Asked twice: the second answer comes from what the view carried.
    for _ in range(2):
        assert kind_facts(view, 'shown', 'letter', 'note') == kind_facts(ordered, 'shown', 'letter', 'note')
    now = datetime(2100, 1, 1, tzinfo=timezone.utc)
    for thread, (strand, gen) in fold(ordered).holder.items():
        assert triggers(view, strand, gen, thread, now) == triggers(ordered, strand, gen, thread, now)
    for strand, gen in fold(ordered).holder.values():
        for alive in (True, False):
            assert state_of(view, strand, gen, alive=alive) == state_of(ordered, strand, gen, alive=alive)


@pytest.mark.parametrize('path', sorted(RECORDINGS.glob('*.json')), ids=lambda p: p.stem)
@pytest.mark.parametrize('batch', [1, 3, 17])
def test_recorded_drives_all_batches_and_main_changes(path, batch, tmp_path):
    record = json.loads(path.read_text())
    rows = [Fact.from_row(row) for row in record['facts']]
    view, seen = LedgerView(), []
    main = copy.deepcopy(record['main'])
    for offset in range(0, len(rows), batch):
        incoming = rows[offset:offset + batch]
        view.extend(incoming)
        seen.extend(incoming)
        equivalent(view, seen, main, tmp_path)
    # Re-evaluate completed history too when plan rows or sender status change.
    for thread in main:
        main[thread] = dict(main[thread], status='settled', plan_rows=[(False, 'review · re: ' + rows[-1].id)])
        equivalent(view, seen, main, tmp_path)


def f(kind, n, data, by='loom:abcd', after=()):
    return Fact(kind, by, data, id=data.get('id', f'f:{n}'), hlc=(n, 0, 'abcd'), after=after)


def test_late_predecessor_failover_receipts_dispositions_and_notices(tmp_path):
    main = {'inbox': {'status': 'open'}, 'work': {'status': 'open', 'opened-by': 'thread:inbox'}}
    rows = [
        f('router', 1, {'gen': 1}),
        f('lease', 2, {'thread': 'work', 'strand': 's-abcd-first', 'gen': 1, 'router_gen': 1}),
        f('letter', 3, {'id': 'in', 'to': 'thread:work', 'from': 'p-a'}, by='person:a'),
        f('body.started', 4, {'strand': 's-abcd-first', 'gen': 1}),
        f('letter', 5, {'id': 'reply', 'to': 'channel:telegram', 'from': 's-abcd-first', 'gen': 1, 're': 'in'}, by='strand:s-abcd-first'),
        f('speech', 6, {'key': 'reply', 'state': 'sent'}),
        f('letter.disposed', 7, {'dispositions': [{'id': 'in', 'outcome': 'done', 'successor': {'id': 'notice', 'to': 'thread:inbox', 'from': 'loom', 'stands': 'in'}}]}),
        f('router', 8, {'gen': 2}),
        f('lease', 9, {'thread': 'work', 'strand': 's-abcd-second', 'gen': 2, 'router_gen': 2}),
        f('note', 10, {'re': 'notice'}, by='person:a'),
        f('note', 11, {'re': 'in', 'gen': 1}, by='strand:s-abcd-first'),
    ]
    view, seen = LedgerView(), []
    for row in rows:
        view.append(row)
        seen.append(row)
        equivalent(view, seen, main, tmp_path)
    # A missing predecessor arrives with a higher priority: naive delta sorting
    # would keep the old lease ahead of it and accept the wrong generation.
    late = f('router', 20, {'gen': 3})
    successor = f('lease', 12, {'thread': 'work', 'strand': 's-abcd-third', 'gen': 3, 'router_gen': 3}, after=(late.id,))
    for row in (successor, late):
        view.append(row)
        seen.append(row)
        equivalent(view, seen, main, tmp_path)
    assert view.reset
    assert fold(view).holder['work'] == ('s-abcd-third', 3)


def test_reader_and_locked_writer_refresh_external_suffixes_and_partial_tails(tmp_path, monkeypatch):
    from brr.loom.runtime import ledger
    home = Home(tmp_path)
    reader = LedgerReader()
    first = append(home, f('router', 1, {'gen': 1}))
    view = reader.refresh(home)
    assert reader.refresh(home) is view
    equivalent(view, [first], {}, tmp_path)
    append(home, f('router.renewed', 2, {'gen': 1}))
    assert reader.refresh(home) is view
    assert view == read_facts(home)
    # The warm writer must not parse its existing history for ID/HLC checks.
    parsed = []
    real = ledger.Fact.from_row
    monkeypatch.setattr(ledger.Fact, 'from_row', lambda row: (parsed.append(row), real(row))[1])
    append(home, f('router.renewed', 3, {'gen': 1}))
    assert len(parsed) <= 1
    path = home.facts_dir() / 'loom.jsonl'
    with path.open('ab') as out:
        out.write(b'{partial')
    append(home, f('router.renewed', 4, {'gen': 1}))
    assert reader.refresh(home) == read_facts(home)
    # A stream that appears, or one replaced under the same size, is still read
    # once its writer marks the ledger changed, as append and the shuttle do.
    other = home.facts_dir() / 's-abcd-other.jsonl'
    other.write_text(json.dumps(f('router.renewed', 5, {'gen': 1}).row()) + '\n')
    ledger.mark_changed(home.facts_dir().parent)
    assert reader.refresh(home) == read_facts(home)
    other.write_text(json.dumps(f('router.renewed', 6, {'gen': 1}).row()) + '\n')
    ledger.mark_changed(home.facts_dir().parent)
    assert [x.id for x in reader.refresh(home)] == [x.id for x in read_facts(home)]
    other.unlink()
    ledger.mark_changed(home.facts_dir().parent)
    assert reader.refresh(home) == read_facts(home)


def test_an_unchanged_ledger_costs_one_stat_and_an_unmarked_write_waits_for_the_full_scan(tmp_path, monkeypatch):
    from brr.loom.runtime import ledger
    home = Home(tmp_path)
    reader = LedgerReader()
    append(home, f('router', 1, {'gen': 1}))
    reader.refresh(home)
    stats = []
    real = ledger.os.stat
    monkeypatch.setattr(ledger.os, 'stat', lambda p, *a, **k: (stats.append(str(p)), real(p, *a, **k))[1])
    reader.refresh(home)
    assert [p for p in stats if p.endswith('.jsonl')] == []
    # A writer from before the marker appends without touching it.
    clock = [ledger.time.monotonic()]
    monkeypatch.setattr(ledger.time, 'monotonic', lambda: clock[0])
    reader.refresh(home)
    with (home.facts_dir() / 'loom.jsonl').open('a') as out:
        out.write(json.dumps(f('router.renewed', 2, {'gen': 1}).row()) + '\n')
    assert len(reader.refresh(home)) == 1
    clock[0] += ledger.FULL_EVERY
    assert reader.refresh(home) == read_facts(home)
    assert len(read_facts(home)) == 2


def test_renewal_delta_never_iterates_history_after_warmup(tmp_path, monkeypatch):
    from brr.loom.runtime import project
    rows = [f('router.renewed', n, {'gen': 0}) for n in range(16000)]
    view = LedgerView(rows)
    fold(view)
    letter_owners(view, {})
    letter_attachments(tmp_path, view)
    visits = []
    real = project.Reducer.apply
    monkeypatch.setattr(project.Reducer, 'apply', lambda self, rows: (visits.extend(rows), real(self, rows))[1])
    # Ordinary calls and one new renewal must not enumerate the old view.
    monkeypatch.setattr(LedgerView, '__iter__', lambda self: (_ for _ in ()).throw(AssertionError('history walk')))
    fold(view)
    letter_owners(view, {})
    view.append(f('router.renewed', 20000, {'gen': 0}))
    fold(view)
    letter_owners(view, {})
    letter_attachments(tmp_path, view)
    assert [r.id for r in visits] == ['f:20000']


def test_notice_order_and_disposition_are_not_transitive_closure(tmp_path):
    main = {'inbox': {'status': 'open'}}
    rows = [
        f('letter', 1, {'id': 'a', 'to': 'thread:inbox', 'from': 'loom', 'stands': 'b'}),
        f('letter', 2, {'id': 'b', 'to': 'thread:inbox', 'from': 'loom', 'stands': 'c'}),
        f('note', 3, {'re': 'c'}, by='person:a'),
        f('letter.disposed', 4, {'dispositions': [{'id': 'a', 'outcome': 'done'}]}),
        f('shown', 5, {'strand': 's-abcd-first', 'gen': 1, 'ids': ['a']}),
        f('note', 6, {'re': 'b'}, by='person:a'),
    ]
    view, seen = LedgerView(), []
    for row in rows:
        view.append(row)
        seen.append(row)
        equivalent(view, seen, main, tmp_path)
        if row.id == 'f:3':
            assert fold(view).handled == {'b', 'c'}
    assert fold(view).handled == {'a', 'b', 'c'}


def test_writer_reloads_external_clock_conflicts_and_replacements(tmp_path, monkeypatch):
    from brr.loom.runtime import ledger
    home = Home(tmp_path, install='abcd')
    append(home, f('router', 1, {'gen': 1}))
    path = home.facts_dir() / 'loom.jsonl'
    external = f('router.renewed', 2, {'gen': 1})
    external = Fact(external.kind, external.by, external.data, id=external.id, hlc=(9999999999999, 8, 'abcd'))
    with path.open('a') as out:
        out.write(json.dumps(external.row()) + '\n')
    stored = append(home, Fact('router.renewed', 'loom:abcd', {'gen': 1}, id='local'))
    assert stored.hlc == (9999999999999, 9, 'abcd')
    assert append(home, Fact('router.renewed', 'loom:abcd', {'gen': 1}, id='local')) == stored
    conflict = Fact('router', 'loom:abcd', {'gen': 3}, id='local')
    with pytest.raises(ledger.LedgerConflict):
        append(home, conflict)
    # Same-size rewrite and inode replacement discard the writer's old index.
    replacement = path.with_suffix('.new')
    replacement.write_text(json.dumps(f('router', 20, {'gen': 3}).row()) + '\n')
    replacement.replace(path)
    assert append(home, conflict).data == {'gen': 3}


def test_late_source_blob_and_reply_target_update_only_affected_letters(tmp_path, monkeypatch):
    source = f('source', 3, {'channel': 'channel:telegram', 're': 'native', 'blobs': ['sha']}, after=('f:4',))
    blob = f('blob', 4, {'sha': 'sha', 'mime': 'text/plain', 'size': 2})
    rows = [f('letter', 1, {'id': 'in', 'to': 'thread:inbox', 'from': 'p-a', 'cites': source.id, 're': 'target'}, by='person:a'), source, blob,
            f('letter', 5, {'id': 'target', 'to': 'thread:inbox', 'from': 'p-a', 'body': 'earlier'}, by='person:a')]
    view, seen = LedgerView(), []
    main = {'inbox': {'status': 'open'}}
    for row in rows:
        view.append(row)
        seen.append(row)
        equivalent(view, seen, main, tmp_path)
    assert 'replying to your target: earlier' == letter_replies(view)['in']
    assert 'text/plain' in letter_attachments(tmp_path, view)['in'][0]


def test_production_held_ticks_and_renewal_do_not_walk_old_ledger(tmp_path, monkeypatch):
    import fcntl
    from brr.loom.runtime import loom, usage
    from brr.loom.runtime.router import Router
    from test_tick_cost import _home

    home = Home(_home(tmp_path))
    router = Router(home)
    loom.tick_once(home, {}, 'fake', 'haiku', router)
    router.clock = lambda: router.lease.until - router.config.router_ttl
    strand = f's-{home.install_id()}-held'
    append(home, Fact('lease', f'loom:{home.install_id()}', {
        'thread': 'w-1', 'strand': strand, 'gen': 1, 'router_gen': router.lease.gen}))
    room = home.room(strand)
    (room / 'port/in').mkdir(parents=True)
    monkeypatch.setattr(usage, 'reading', lambda *a, **k: {'windows': []})
    with (room / '.body').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        loom.tick_once(home, {}, 'fake', 'haiku', router)
        view = router.reader.refresh(home)
        with monkeypatch.context() as guard:
            guard.setattr(LedgerView, '__iter__', lambda self: (_ for _ in ()).throw(AssertionError('historical tick scan')))
            loom.tick_once(home, {}, 'fake', 'haiku', router)
            append(home, Fact('router.renewed', f'loom:{home.install_id()}', {'gen': router.lease.gen}))
            loom.tick_once(home, {}, 'fake', 'haiku', router)
        assert router.reader.refresh(home) is view
        equivalent(view, read_facts(home), {'w-1': {'status': 'open'}}, home.root)


def test_sender_reassignment_moves_historical_activity(tmp_path):
    main = {'one': {'status': 'open'}, 'two': {'status': 'open'}}
    rows = [
        f('lease', 1, {'thread': 'one', 'strand': 's-abcd-first', 'gen': 1}),
        f('body.started', 2, {'strand': 's-abcd-first', 'gen': 1}),
        f('shown', 3, {'strand': 's-abcd-first', 'gen': 1, 'ids': []}),
        f('lease', 4, {'thread': 'two', 'strand': 's-abcd-first', 'gen': 2}),
    ]
    view, seen = LedgerView(), []
    for row in rows:
        view.append(row)
        seen.append(row)
        equivalent(view, seen, main, tmp_path)


@pytest.mark.parametrize('shared_source', [False, True])
def test_later_reply_and_source_citation_do_not_change_old_letter_projections(tmp_path, shared_source):
    """Inbound replies/citations are not inputs to the older letter's context.

    Each production projection reads the letter's own source and, for reply
    context, its own re target. A later letter pointing back cannot change
    those inputs. Actual source/blob/target arrivals remain reverse-indexed.
    """
    main = {'inbox': {'status': 'open'}}
    blob = f('blob', 1, {'sha': 'old', 'mime': 'text/plain', 'size': 2})
    source = f('source', 2, {'channel': 'channel:telegram/old', 're': 'native-parent', 'blobs': ['old']}, after=(blob.id,))
    parent = f('letter', 3, {'id': 'parent', 'to': 'thread:inbox', 'from': 'p-a', 'body': 'parent body'}, by='person:a')
    old = f('letter', 4, {'id': 'old', 'to': 'thread:inbox', 'from': 'p-a', 'cites': source.id, 're': parent.id}, by='person:a')
    seen = [blob, source, parent, old]
    view = LedgerView(seen)
    equivalent(view, seen, main, tmp_path)
    expected = (reply_channels(view)[old.id], copy.deepcopy(letter_attachments(tmp_path, view)[old.id]), letter_replies(view)[old.id])
    later_blob = f('blob', 5, {'sha': 'new', 'mime': 'image/png', 'size': 3})
    later_source = f('source', 6, {'channel': 'channel:telegram/new', 're': 'native-old', 'blobs': ['new']}, after=(later_blob.id,))
    later = f('letter', 7, {'id': 'later', 'to': 'thread:inbox', 'from': 'p-a', 're': old.id,
                           'cites': source.id if shared_source else later_source.id}, by='person:a')
    for row in (later_blob, later_source, later):
        view.append(row)
        seen.append(row)
        equivalent(view, seen, main, tmp_path)
        assert (reply_channels(view)[old.id], letter_attachments(tmp_path, view)[old.id], letter_replies(view)[old.id]) == expected
    assert letter_replies(view)[later.id].startswith('replying to your old:')
    assert letter_attachments(tmp_path, view)[later.id] == (expected[1] if shared_source else [f'attachment: image/png · 3 bytes · {tmp_path.resolve() / "blobs/new"}'])


def test_local_append_repo_pairs_equal_the_next_production_refresh(tmp_path):
    home = Home(tmp_path, install='abcd')
    reader = LedgerReader()
    view = reader.refresh(home)
    data = {'thread': 'work', 'strand': 's-abcd-first', 'gen': 1,
            'repos': [('widget', 'https://example.invalid/widget.git')]}
    stored = append(home, Fact('lease', 'loom:abcd', data))
    view.append(stored)
    assert stored == read_facts(home)[0]
    assert reader.refresh(home) is view
    assert view == read_facts(home)
    assert isinstance(data['repos'][0], tuple)  # callers retain their input
    assert isinstance(stored.data['repos'][0], list)
