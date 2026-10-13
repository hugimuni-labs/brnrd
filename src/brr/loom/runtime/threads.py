"""One derived view of threads, shared by the CLI and opener boundaries."""

import json
import time
from datetime import datetime, timezone

from .home import Home, thread_of
from .lifecycle import CLOSED, read_thread, main_snapshot
from .owners import letter_owners, thread_axes
from .timers import column as when_column
from .project import fold, sender_threads
from .selfrepo import git


# Main changes on a landed commit, not on every router tick. Cache only its
# derived rows; liveness, owed mail, and age are always read afresh.
_MAIN = {}


def _at(fact) -> float:
    return datetime.fromisoformat(fact.at).timestamp()


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        return f"{seconds // 86400}d"
    if seconds >= 3600:
        return f"{seconds // 3600}h"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def thread_table(home: Home, facts: list) -> list[dict]:
    """Read main and the ledger; never ask a body to declare its state.

    A recent boundary is a shown fact or the jack's existing state-file mtime.
    There is no explicit 'waiting' fact: a live body with nothing owed, or a
    stop block, is the nearest derivable holding value.
    """
    from .loom import body_alive, pending_bodies
    state, writers = fold(facts), sender_threads(facts)
    main = main_snapshot(home)
    ownership = letter_owners(facts, main)
    axes = thread_axes(facts, main, ownership)
    owed, activity, boundary = {}, {}, {}
    for fate in ownership.values():
        if not fate.done and fate.owner in {'body', 'loom', 'opener'}:
            owed.setdefault(fate.thread, []).append(fate.letter)
    for f in state.accepted:
        strand = f.data.get('strand') or f.by.removeprefix('strand:')
        thread = writers.get(strand)
        if f.kind == 'letter':
            try:
                thread = thread_of(f.data.get('to'))
            except ValueError:
                pass
        if thread:
            activity[thread] = max(activity.get(thread, 0), _at(f))
        if f.kind == 'shown':
            boundary[strand] = max(boundary.get(strand, 0), _at(f))
    repo = home.root / 'self'
    versioned = (repo / '.git').exists()
    tip = git(repo, 'rev-parse', 'main').stdout.strip() if versioned else None
    if not versioned or _MAIN.get(repo, (None, None))[0] != tip:
        names = (git(repo, 'ls-tree', '-r', '--name-only', 'main', '--', 'threads').stdout.splitlines()
                 if versioned else [p.relative_to(repo).as_posix() for p in (repo / 'threads').glob('*/README.md')])
        snapshot = []
        for path in names:
            parts = path.split('/')
            if len(parts) != 3 or parts[2] != 'README.md':
                continue
            thread = parts[1]
            data = read_thread(home, thread, ref=tip or "main")
            if not data:
                continue
            if versioned:
                text = git(repo, 'show', f'{tip}:threads/{thread}/seat', check=False).stdout
                stamp = git(repo, 'log', '-1', '--format=%ct', tip, '--', path).stdout.strip()
                created = float(stamp or time.time())
                date = (git(repo, 'log', '-1', '-G', '^status:', '--format=%cs', tip, '--', path).stdout.strip()
                        if data['status'] in CLOSED else '')
            else:
                seat_path = home.thread_dir(thread) / 'seat'
                text = seat_path.read_text() if seat_path.is_file() else ''
                created, date = (repo / path).stat().st_mtime, ''
            choice = ''
            for line in text.splitlines():
                key, colon, value = line.partition(':')
                if colon and key.strip() == 'body':
                    choice = value.strip().strip('"')
            snapshot.append((thread, data, choice, created, date))
        _MAIN[repo] = (tip, snapshot)
    pending, now, rows = pending_bodies(state.accepted), time.time(), []
    for thread, data, choice, created, date in _MAIN[repo][1]:
        count = len(owed.get(thread, ()))
        strand = state.holder.get(thread, ('', 0))[0]
        latest = pending.get(strand)
        shell = choice.partition('/')[0]
        seat = choice or '—'
        if latest and latest.data.get('shell'):
            shell = latest.data['shell']
            seat = f"{shell}/{latest.data.get('core', '')}"
        last = activity.get(thread, created)
        if data['status'] in CLOSED:
            count = len(axes[thread]['letters'])
            value = 'settled' + (f' {date}' if date else '')
        elif latest and latest.kind in {'body.parked', 'body.pending'}:
            value = axes[thread]['cause']
        elif strand and body_alive(home.room(strand)):
            path = home.room(strand) / 'port/jack-state.json'
            try:
                jack = json.loads(path.read_text())
                recent = max(boundary.get(strand, 0), path.stat().st_mtime)
            except (OSError, ValueError):
                jack, recent = {}, boundary.get(strand, 0)
            if not count or jack.get('stop_blocks'):
                value = 'holding'
                from .usage import fuel
                tank = fuel(home, shell) if shell else ''
                if tank:
                    value += ' ' + tank.removeprefix(f'fuel {shell} ')
            elif now - recent <= 180:
                value = 'working'
            else:
                value = 'idle ' + _age(now - max(last, recent))
        else:
            count = len(axes[thread]['letters'])
            cause = axes[thread]['cause']
            value = 'idle ' + _age(now - last) if cause == 'idle' else cause
        rows.append({'thread': thread, 'seat': seat, 'now': value, 'owed': count,
                     'opened-by': data.get('opened-by') or '—', 'plan': data['plan'],
                     'when': when_column(data.get('when'), thread, facts,
                                         datetime.now(timezone.utc)),
                     'status': data['status'], **{k: axes[thread][k] for k in ('owed-by', 'restart', 'control')}})
    for ident, fate in ownership.items():
        if fate.owner == 'operator':
            rows.append({'thread': 'operator', 'seat': '—', 'now': ident + ': ' + fate.reason,
                         'owed': 1, 'opened-by': fate.thread or '—', 'plan': '—', 'when': '—',
                         'status': 'open',
                         'owed-by': 'operator', 'restart': 'operator note',
                         'control': 'operator: attention --note ' + ident + ' --why <reason>'})
    return rows


def render_table(rows: list[dict]) -> str:
    columns = ('thread', 'seat', 'now', 'owed', 'opened-by', 'plan', 'when', 'owed-by', 'restart', 'control')
    return '\n'.join(' · '.join(str(row[c]) for c in columns)
                     for row in [dict(zip(columns, columns)), *rows]) + '\n'
