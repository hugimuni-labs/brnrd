"""One pure answer to who owes each accepted letter, from ledger and main."""
from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from copy import deepcopy
from brr.daemon2.facts import Fact
from .home import is_channel, notice_id
from .ledger import LedgerView, kind_facts
from .project import _projection_cache, ordered_facts, _destination, fold, reply_channels, sender_threads, is_notice


@dataclass(frozen=True)
class Ownership:
    letter: Fact
    owner: str | None
    reason: str
    thread: str | None = None
    reply_to: str = '-'
    action: str = ''
    plan_rows: tuple[int, ...] = ()

    @property
    def done(self):
        return self.owner is None


def letter_owners(facts, main: dict[str, dict]) -> dict[str, Ownership]:
    """No clock, file, lock, gauge or working checkout is read here.

    A missing observation is debt owed by the loom, never an invented end.
    Embedded successors become letters in the same causal prefix as disposition.
    """
    if isinstance(facts, LedgerView):
        if not hasattr(facts, "ownership"):
            facts.ownership = OwnerProjection()
        return facts.ownership.read(facts, main)
    cache = _projection_cache.get()
    if cache is None:
        return _letter_owners(facts, main)
    snapshot = tuple(facts)
    # Main can change without a ledger append. Include its value, so plan
    # edits and thread closures invalidate ownership as well.
    key = ("owners", tuple(map(id, snapshot)), repr(main))
    if key not in cache:
        cache[key] = (snapshot, _letter_owners(snapshot, main))
    return cache[key][1]


class OwnerInputs:
    """Indexes over the accepted prefix, shared by full and delta callers."""
    def __init__(self):
        self.offset = 0
        self.letters, self.ends, self.attention, self.dispositions = {}, {}, {}, {}
        self.positions = {}
        self.drained, self.due, self.sent = set(), set(), set()
        self.bodies = set()
        self.leases = defaultdict(list)
        self.writers = {}

    def extend(self, accepted):
        for fact in accepted[self.offset:]:
            d = fact.data
            if fact.kind == 'letter':
                self.positions.setdefault(fact.id, len(self.letters))
                self.letters[fact.id] = fact
            elif fact.kind == 'lease':
                self.writers[str(d['strand'])] = str(d['thread'])
                self.leases[d['thread']].append(fact)
            elif fact.kind == 'body.started':
                self.bodies.add((d.get('strand'), d.get('gen')))
            elif fact.kind == 'attempt.ended':
                self.ends[(d['strand'], d['gen'])] = fact
            elif fact.kind == 'port.drained':
                self.drained.add((d['strand'], d['gen']))
            elif fact.kind == 'attention' and d.get('letter'):
                self.attention[d['letter']] = d.get('why', 'legacy attention')
            elif fact.kind == 'letter.due':
                self.due.add(d.get('letter'))
            for disposition in d.get('dispositions', ()):
                self.dispositions[disposition['id']] = disposition
        self.offset = len(accepted)


class OwnerProjection:
    """Re-evaluate active debt and historical letters affected by new evidence.

    Completed rows remain queryable. Reverse dependencies include historical
    sender/reply addresses, notice pairs and opener ancestry, so completing a
    letter does not freeze its public description forever.
    """
    def __init__(self):
        self.inputs = OwnerInputs()
        self.offset = 0
        self.accepted = 0
        self.main = {}
        self.result = {}
        self.active = set()
        self.threads = defaultdict(set)
        self.senders = defaultdict(set)
        self.links = defaultdict(set)
        self.sources = defaultdict(set)
        self.indexed = set()

    def read(self, facts, main):
        state = fold(facts)
        added = state.accepted[self.accepted:]
        raw = facts[self.offset:]
        if not raw and self.main == main and self.accepted == len(state.accepted):
            return self.result
        self.inputs.extend(state.accepted)
        self.inputs.sent.update(f.data.get('key') for f in raw
                                if f.kind == 'speech' and f.data.get('state') == 'sent')
        dirty = set()
        relevant = any(f.kind != 'router.renewed' for f in raw)
        if relevant:
            dirty.update(self.active)
        for fact in added:
            if fact.kind == 'letter':
                self.indexed.add(fact.id)
                d = fact.data
                self.threads[_destination(d)].add(fact.id)
                self.senders[fact.by.removeprefix('strand:')].add(fact.id)
                self.sources[d.get('cites')].add(fact.id)
                for ref in (d.get('re'), d.get('about'), d.get('stands')):
                    if ref:
                        self.links[ref].add(fact.id)
                        self.links[fact.id].add(ref)
                dirty.add(fact.id)
        for fact in raw + added:
            d = fact.data
            dirty.update(self.sources.get(fact.id, ()))
            if fact.kind in {'lease', 'released', 'attempt.ended', 'port.drained', 'body.started'}:
                dirty.update(self.threads.get(d.get('thread'), ()))
                dirty.update(self.senders.get(d.get('strand'), ()))
            for key in ('re', 'letter', 'key'):
                if d.get(key):
                    dirty.add(d[key])
            dirty.update(str(item['id']) for item in d.get('dispositions', ()))
            if fact.kind == 'shown':
                dirty.update(d.get('ids') or ())
        if self.main != main:
            # Main edits can change any descendant's open ancestor or sender
            # settlement rule. Follow the small thread graph, not the ledger.
            changed = {t for t in self.main.keys() | main.keys() if self.main.get(t) != main.get(t)}
            for thread in self.threads:
                for tree in (self.main, main):
                    target, seen = thread, set()
                    while target in tree and target not in seen:
                        seen.add(target)
                        if target in changed:
                            dirty.update(self.threads[thread])
                            break
                        opener = str(tree[target].get('opened-by', ''))
                        target = opener.removeprefix('thread:') if opener.startswith('thread:') else None
            for sender, thread in self.inputs.writers.items():
                if thread in changed:
                    dirty.update(self.senders[sender])
        pending = list(dirty)
        while pending:
            for ident in self.links.get(pending.pop(), ()):
                if ident not in dirty:
                    dirty.add(ident)
                    pending.append(ident)
        selected = {ident for ident in dirty if ident in self.inputs.letters}
        self.result.update(_letter_owners(facts, main, self.inputs, selected))
        for ident in selected:
            if self.result[ident].done:
                self.active.discard(ident)
            else:
                self.active.add(ident)
        self.offset, self.accepted = len(facts), len(state.accepted)
        self.main = deepcopy(main)
        return self.result


def _letter_owners(facts, main, inputs=None, selected=None):
    facts = ordered_facts(facts)
    state = fold(facts)
    if inputs is None:
        inputs = OwnerInputs()
        inputs.extend(state.accepted)
        inputs.sent.update(f.data.get('key') for f in kind_facts(facts, 'speech')
                           if f.data.get('state') == 'sent')
    writers, channels = inputs.writers, reply_channels(facts)
    letters, sent = inputs.letters, inputs.sent
    dispositions, ends = inputs.dispositions, inputs.ends
    drained, attention, due = inputs.drained, inputs.attention, inputs.due
    replies = state.channel_replies
    refused = {}
    for ident, reply in replies.items():
        if reply in dispositions:
            refused[ident] = dispositions[reply].get('reason', 'send refused')
    result = {}

    def reply_address(letter, own):
        seen = set()
        while letter and letter.id not in seen:
            seen.add(letter.id)
            sender = writers.get(letter.by.removeprefix('strand:'))
            if sender and sender != own:
                return 'thread:' + sender
            if letter.id in channels:
                return channels[letter.id]
            letter = letters.get(letter.data.get('about'))
        return '-'

    candidates = letters.items() if selected is None else ((ident, letters[ident]) for ident in sorted(selected, key=inputs.positions.__getitem__))
    for ident, letter in candidates:
        d = letter.data
        thread = _destination(d)
        reply = reply_address(letter, thread)
        disposition = dispositions.get(ident, {})
        reason = disposition.get('reason', '')
        sender = writers.get(letter.by.removeprefix('strand:'))
        child = main.get(sender, {})
        def keep(owner, why, target=thread, action=''):
            rows = main.get(target, {}).get('plan_rows') or ()
            cited = tuple(i + 1 for i, (_, text) in enumerate(rows)
                          if ident in plan_citations(text))
            result[ident] = Ownership(letter, owner, why, target, reply, action, cited)
        if ident in state.handled or ident in sent or disposition.get('outcome') == 'done':
            keep(None, reason or 'reply or note recorded')
        elif d.get('notice') and is_notice(letter) and any(ident in ids for ids in state.shown.values()):
            # A passive notice informs; once a body was shown it, nobody owes it a word.
            keep(None, 'passive notice shown')
        elif disposition.get('outcome') == 'operator':
            keep('operator', reason or 'failed attempt limit', action='note')
        elif d.get("at") and ident not in due:
            keep("clock", f"due {d['at']}", action="wait")
        elif is_channel(d.get('to')):
            keep('channel', 'accepted send awaiting receipt', action='send')
        elif ident in replies and ident not in refused and replies[ident] not in sent:
            keep('channel', 'reply accepted; awaiting send receipt', action='receipt')
        elif ident in attention:
            keep('operator', attention[ident], action='note')
        elif (child.get('status') in {'settled', 'retired'}
              and child.get('opened-by') == 'thread:' + str(thread)
              and d.get('re') in letters
              and _destination(letters[d['re']].data) == sender):
            keep(None, 'worker settled by its opener')
        elif thread not in main:
            keep('loom', 'destination absent from main', action='unroutable')
        elif main[thread].get('status') in {'settled', 'retired'}:
            held = next(((f.data['strand'], f.data['gen']) for f in reversed(inputs.leases[thread])
                         if f.kind == 'lease' and f.data.get('thread') == thread
                         and ident in state.shown.get(f.data['strand'], ())), None)
            if held and (held not in ends or held not in drained):
                live = state.holder.get(thread) == held
                keep('body' if live and held not in ends else 'loom',
                     'closed reader finishing' if held not in ends else 'awaiting port drain', action='drain')
                continue
            target, seen = thread, set()
            while target in main and target not in seen:
                seen.add(target)
                if main[target].get('status') not in {'settled', 'retired'}:
                    break
                opener = str(main[target].get('opened-by', ''))
                target = opener.removeprefix('thread:') if opener.startswith('thread:') else None
            if target in main and main[target].get('status') not in {'settled', 'retired'}:
                keep('opener', 'closed destination; climb to open ancestor', target, 'climb')
            else:
                keep('operator', 'opened-by loop or no open ancestor', action='note')
        else:
            if (thread in state.holder and state.holder[thread] not in ends
                  and state.holder[thread] in inputs.bodies):
                keep('body', refused.get(ident) or 'assigned to current body')
            else:
                climbed = d.get('from') == 'loom' and ident.startswith(('loom/bounce-', 'loom/climbed-'))
                keep('opener' if climbed else 'loom',
                     refused.get(ident) or reason or ('waiting for opener' if climbed else 'waiting for start'),
                     action='start')
    return result


def active_owners(facts, main):
    table = letter_owners(facts, main)
    if isinstance(facts, LedgerView):
        projection = facts.ownership
        return [table[ident] for ident in sorted(projection.active, key=projection.inputs.positions.__getitem__)]
    return [fate for fate in table.values() if not fate.done]


def letters_for(facts, main, thread):
    """Mail a current or next reader on this thread must see."""
    return [f.letter for f in active_owners(facts, main)
            if not f.done and f.thread == thread and f.owner in {'body', 'loom', 'opener'}
            and f.action not in {'unroutable', 'climb'}
            and (f.action != 'drain' or f.owner == 'body')]


def plan_citations(text: str) -> tuple[str, ...]:
    """Only a row's explicit trailing ` · re: id, id` cites letters."""
    _, separator, cites = text.rpartition(' · re: ')
    if not separator:
        return ()
    return tuple(ident.strip() for ident in cites.split(',') if ident.strip())


def thread_axes(facts, main, ownership=None):
    """Ends and next actors are columns of the letter table, not stored states.

    A pending start's reset is already an observation in body.parked. A
    physical process disappearing cannot invent a failed/idle end here.
    """
    state = fold(facts)
    table = ownership if ownership is not None else letter_owners(facts, main)
    ends, bodies = {}, {}
    if isinstance(facts, LedgerView):
        table = {fate.letter.id: fate for fate in active_owners(facts, main)}
    origins = {ident: {_destination(fate.letter.data)} for ident, fate in table.items()}
    # Existing closure observations name their deterministic result notice.
    for fact in kind_facts(facts, "thread.closed"):
        if fact.kind == 'thread.closed' and fact.by.startswith('loom:'):
            origins.setdefault(notice_id('closed', fact.id), set()).add(fact.data['thread'])
    if isinstance(facts, LedgerView):
        ends, bodies = state.ends, state.bodies
        all_letters = facts.ownership.inputs.letters
        seeds = dict(origins)
        for ident in table:
            pending, seen = [(ident, float('inf'))], set()
            origin = set()
            while pending:
                node, upper = pending.pop()
                if (node, upper) in seen:
                    continue
                seen.add((node, upper))
                letter = all_letters.get(node)
                if letter is not None:
                    origin.add(_destination(letter.data))
                origin.update(seeds.get(node, ()))
                pending.extend((parent, edge) for edge, parent in state.origin_parents.get(node, ()) if edge < upper)
            origins[ident] = origin
    else:
        for fact in state.accepted:
            if fact.kind == 'attempt.ended':
                ends[fact.data['thread']] = fact
            elif fact.kind.startswith('body.') and fact.data.get('strand'):
                bodies[fact.data['strand']] = fact
            for disposition in fact.data.get('dispositions', ()):
                ident = disposition.get('successor', {}).get('id')
                if ident:
                    origins.setdefault(ident, set()).update(origins.get(disposition['id'], ()))
    rows = {}
    for thread, data in main.items():
        held = state.holder.get(thread)
        body = bodies.get(held[0]) if held else None
        end = ends.get(thread)
        # Include the successor that now carries a thread's original debt,
        # through every climb/bounce, even when the new reader is elsewhere.
        debts = {ident: fate for ident, fate in table.items() if not fate.done and fate.action != 'wait'
                 and (fate.thread == thread or thread in origins.get(ident, ()))}
        start_here = any(f.thread == thread and f.owner in {'loom', 'body', 'opener'}
                         and f.action not in {'climb', 'unroutable', 'drain'}
                         and not f.letter.data.get('notice') for f in debts.values())
        owners = sorted({f'{f.owner}:{f.thread}' if f.owner in {'body', 'loom', 'opener'}
                         else f.owner for f in debts.values()})
        opener = data.get('opened-by') or 'operator'
        override = opener + ' or operator' if opener != 'operator' else 'operator'
        cause = end.data['cause'] if end else 'idle'
        reset = None
        if data.get('status') in {'settled', 'retired'}:
            # A body the loop signalled stays ``stopped``. ``settled`` is the
            # thread. A body that left by itself has no stopped attempt.
            if cause != 'stopped':
                cause = 'settled'
            restart, control = 'new letter or opener answers', opener
        elif body and body.kind in {'body.pending', 'body.parked'}:
            cause = 'paced' if body.kind == 'body.parked' else 'pending'
            reset = body.data.get('until')
            restart = f'reset {reset}' if reset is not None else 'loom: gauge reading'
            control = f'{override}: threads --force {thread}' if reset is not None else 'loom'
        elif held and (not end or (end.data['strand'], end.data['gen']) != held):
            cause, restart, control = 'body', 'current body', '—'
        elif cause == 'molted':
            restart, control = 'loom: now', '—'
        elif cause == 'paced':
            restart, control = 'loom: start waiting for gauge', f'{override}: threads --force {thread}'
        elif cause == 'failed':
            retry = any(f.reason in {'first failed attempt', 'home-caused failure'} for f in debts.values())
            restart = 'loom: retry' if retry else 'author resends'
            control = 'author: note failure closed'
        else:
            restart, control = ('loom: start' if start_here else 'next letter'), '—'
        rows[thread] = {'cause': cause, 'letters': tuple(debts),
                        'owed-by': ', '.join(owners) or 'nobody',
                        'restart': restart, 'control': control, 'reset': reset}
    return rows


def unanswered_letters(facts) -> dict[str, Fact]:
    """Person letters without a delivered reply or their author's hand note.

    Handling debt and answering a person are separate projections: neither a
    resident's note nor a done disposition is evidence the person heard back.
    """
    if isinstance(facts, LedgerView):
        return facts.project('unanswered', None, _unanswered_letters)
    return _unanswered_letters(ordered_facts(facts))


def _unanswered_letters(facts) -> dict[str, Fact]:
    accepted = fold(facts).accepted
    channels = reply_channels(facts)

    def person(value):
        value = str(value or '')
        if value.startswith(('person:', 'p-')):
            return 'person:' + value.removeprefix('person:').removeprefix('p-')
        return None

    def author(letter):
        return person(letter.data.get('from')) or person(letter.by)

    asks = {f.id: f for f in accepted if f.kind == 'letter' and author(f)}
    answered = {f.data.get('re') for f in facts if f.kind == 'note'
                and f.by.startswith('person:') and f.data.get('re') in asks
                and person(f.by) == author(asks[f.data['re']])}
    # Loom notices report transport/runtime state, not an answer to the ask.
    # They can be embedded in a done disposition and have real part receipts.
    replies = {f.id: f for f in accepted if f.kind == 'letter' and not is_notice(f)
               and f.data.get('re') in asks and f.data.get('to') == channels.get(f.data['re'])}
    for f in accepted:
        if f.kind != 'speech.part' or f.data.get('key') not in replies:
            continue
        reply = replies[f.data['key']]
        if (f.data.get('channel') == reply.data['to']
                and f.data.get('receipt', {}).get('message_id') is not None):
            answered.add(reply.data['re'])
    return {ident: letter for ident, letter in asks.items() if ident not in answered}


def unanswered_by_thread(facts, main) -> dict[str, tuple[str, ...]]:
    """Every citing thread sees its distinct unanswered letters, even if closed."""
    unanswered = unanswered_letters(facts)
    return {thread: tuple(sorted({ident for _, row in data.get('plan_rows') or ()
                                  for ident in plan_citations(row) if ident in unanswered}))
            for thread, data in main.items()}
