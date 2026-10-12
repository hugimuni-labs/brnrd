"""One pure answer to who owes each accepted letter, from ledger and main."""
from __future__ import annotations

from dataclasses import dataclass
from brr.daemon2.facts import Fact, union
from .home import is_channel
from .project import _destination, fold, reply_channels, sender_threads


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
    facts = union(list(facts))
    state = fold(facts)
    writers = sender_threads(facts)
    channels = reply_channels(facts)
    letters = {f.id: f for f in state.accepted if f.kind == 'letter'}
    sent = {f.data.get('key') for f in facts if f.kind == 'speech' and f.data.get('state') == 'sent'}
    dispositions, ends, drained, attention = {}, {}, set(), {}
    for fact in state.accepted:
        d = fact.data
        if fact.kind == 'attempt.ended':
            ends[(d['strand'], d['gen'])] = fact
        elif fact.kind == 'port.drained':
            drained.add((d['strand'], d['gen']))
        elif fact.kind == 'attention' and d.get('letter'):
            attention[d['letter']] = d.get('why', 'legacy attention')
        for disposition in d.get('dispositions', ()):
            dispositions[disposition['id']] = disposition
    # An accepted, lease-valid reply remains identifiable after its writer leaves.
    replies = dict(state.channel_replies)
    refused = {}
    for f in letters.values():
        if is_channel(f.data.get('to')) and f.data.get('re'):
            if f.id in dispositions and replies.get(f.data['re']) == f.id:
                refused[f.data['re']] = dispositions[f.id].get('reason', 'send refused')
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

    for ident, letter in letters.items():
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
        elif disposition.get('outcome') == 'operator':
            keep('operator', reason or 'failed attempt limit', action='note')
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
            held = next(((f.data['strand'], f.data['gen']) for f in reversed(state.accepted)
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
                  and any(f.kind == 'body.started' and (f.data.get('strand'), f.data.get('gen')) == state.holder[thread]
                          for f in state.accepted)):
                keep('body', refused.get(ident) or 'assigned to current body')
            else:
                climbed = d.get('from') == 'loom' and ident.startswith(('loom/bounce-', 'loom/climbed-'))
                keep('opener' if climbed else 'loom',
                     refused.get(ident) or reason or ('waiting for opener' if climbed else 'waiting for start'),
                     action='start')
    return result


def letters_for(facts, main, thread):
    """Mail a current or next reader on this thread must see."""
    return [f.letter for f in letter_owners(facts, main).values()
            if not f.done and f.thread == thread and f.owner in {'body', 'loom', 'opener'}
            and f.action not in {'unroutable', 'climb'}
            and (f.action != 'drain' or f.owner == 'body')]


def plan_citations(text: str) -> tuple[str, ...]:
    """Only a row's explicit trailing ` · re: id, id` cites letters."""
    _, separator, cites = text.rpartition(' · re: ')
    if not separator:
        return ()
    return tuple(ident.strip() for ident in cites.split(',') if ident.strip())
