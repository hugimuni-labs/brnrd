"""A letter's progress, derived from receipts; platform feedback is never evidence."""
import hashlib
from datetime import datetime, timezone

from brr.daemon2.facts import Fact
from .dispositions import is_person
from .ledger import append
from .project import is_notice
from .wishes import read_says


def timestamp(fact):
    return datetime.fromisoformat(fact.at).timestamp()


def steps(facts):
    """Stable rows with event time, including letters injected without a connector."""
    letters = {str(f.data.get('id', f.id)): f for f in facts
               if f.kind == 'letter' and (is_person(f) or f.data.get('cites', '').startswith('source:'))}
    sources = {f.id: f for f in facts if f.kind == 'source'}
    starts = {(f.data.get('strand'), f.data.get('gen')): f for f in facts if f.kind == 'body.started'}
    rows = {}

    def add(ident, step, fact, **extra):
        if ident not in letters:
            return
        at = extra.pop('at', timestamp(fact))
        key = (ident, step, extra.get('strand'), extra.get('gen'))
        row = dict(letter=ident, step=step, at=at, **extra)
        if key not in rows or at < rows[key]['at']:
            rows[key] = row

    for ident, letter in letters.items():
        add(ident, 'admitted', letter)
    for fact in facts:
        d = fact.data
        if fact.kind == 'wish':
            for say in read_says(d.get('says')):
                add(say.letter, 'stamped', fact)
        elif fact.kind in {'letter', 'note'}:
            for ident in d.get('chatter') or ():
                add(ident, 'stamped', fact)
            if d.get('triage') and d.get('re'):
                add(d['re'], 'stamped', fact)
            if (fact.kind == 'letter' and fact.by.startswith('strand:') and not is_notice(fact)
                    and d.get('body', '').strip() and d.get('re')):
                add(d['re'], 'first-word', fact, at=float(d.get('sent_at', timestamp(fact))))
        elif fact.kind == 'shown':
            start = starts.get((d.get('strand'), d.get('gen')))
            if start:
                for ident in d.get('ids') or ():
                    add(ident, 'booted', fact, at=max(timestamp(start), timestamp(fact)),
                        strand=d['strand'], gen=d['gen'])
    for row in rows.values():
        letter = letters[row['letter']]
        source = sources.get(letter.data.get('cites'))
        if source:
            row.update(channel=source.data['channel'], native=source.data['origin'].split(':', 1)[1],
                       chat=source.data['chat'])
    return sorted(rows.values(), key=lambda r: (r['at'], r['letter'], r['step']))


def observe(home, facts):
    known = {f.id for f in facts if f.kind == 'letter.presence'}
    for row in steps(facts):
        key = [row['letter'], row['step'], row.get('strand'), row.get('gen')]
        ident = 'letter.presence:' + hashlib.sha256(repr(key).encode()).hexdigest()[:32]
        if ident not in known:
            facts.append(append(home, Fact(kind='letter.presence', by=f'loom:{home.install_id()}',
                id=ident, at=datetime.fromtimestamp(row['at'], timezone.utc).isoformat(), data=row)))
            known.add(ident)
    return facts


def first_words(facts, ids):
    letters = {str(f.data.get('id', f.id)): f for f in facts if is_person(f)
               and str(f.data.get('id', f.id)) in ids}
    result = dict.fromkeys(letters)
    if not letters:
        return result
    # The same derivation works before the observer's next tick, e.g. at body end.
    rows = [f.data for f in facts if f.kind == 'letter.presence']
    recorded = {(r['letter'], r['step']) for r in rows}
    rows += [r for r in steps(facts) if (r['letter'], r['step']) not in recorded]
    for row in rows:
        ident = row['letter']
        if row['step'] == 'first-word' and ident in letters:
            result[ident] = max(0, round(row['at'] - timestamp(letters[ident])))
    return result
