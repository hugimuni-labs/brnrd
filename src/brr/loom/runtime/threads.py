"""One derived view of threads, shared by the CLI and opener boundaries."""

import json
import time
from datetime import datetime

from .home import Home, thread_of
from .lifecycle import CLOSED, read_thread, main_snapshot, main_tree, readme_at
from .owners import letter_owners, thread_axes
from .project import fold, sender_threads
from .selfrepo import git


# Main changes on a landed commit, not on every router tick. Cache only its
# derived rows; liveness, owed mail, and age are always read afresh.
_MAIN = {}
_ROWS = {}


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


def _body_choice(seat: str) -> str:
    choice = ''
    for line in seat.splitlines():
        key, colon, value = line.partition(':')
        if colon and key.strip() == 'body':
            choice = value.strip().strip('"')
    return choice


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
    tip, blobs = main_tree(repo) if versioned else (None, {})
    if not versioned or _MAIN.get(repo, (None, None))[0] != tip:
        names = (list(blobs) if versioned
                 else [p.relative_to(repo).as_posix() for p in (repo / 'threads').glob('*/README.md')])
        snapshot, kept = [], {}
        for path in names:
            parts = path.split('/')
            if len(parts) != 3 or parts[2] != 'README.md':
                continue
            thread = parts[1]
            if versioned:
                # A row depends on its README and seat alone, so a commit that
                # touched neither costs this thread no git call.
                seat_blob = blobs.get(f'threads/{thread}/seat')
                key = (thread, blobs[path], seat_blob)
                row = _ROWS.get(repo, {}).get(key)
                if row is None:
                    data = readme_at(home, thread, blobs[path], tip)
                    if not data:
                        continue
                    text = git(repo, 'cat-file', 'blob', seat_blob).stdout if seat_blob else ''
                    stamp = git(repo, 'log', '-1', '--format=%ct', tip, '--', path).stdout.strip()
                    created = float(stamp or time.time())
                    date = (git(repo, 'log', '-1', '-G', '^status:', '--format=%cs', tip, '--', path).stdout.strip()
                            if data['status'] in CLOSED else '')
                    row = (data, _body_choice(text), created, date)
                kept[key] = row
                snapshot.append((thread, *row))
                continue
            data = read_thread(home, thread, ref="main")
            if not data:
                continue
            seat_path = home.thread_dir(thread) / 'seat'
            text = seat_path.read_text() if seat_path.is_file() else ''
            snapshot.append((thread, data, _body_choice(text), (repo / path).stat().st_mtime, ''))
        _MAIN[repo] = (tip, snapshot)
        _ROWS[repo] = kept
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
                     'status': data['status'], **{k: axes[thread][k] for k in ('owed-by', 'restart', 'control')}})
    for ident, fate in ownership.items():
        if fate.owner == 'operator':
            rows.append({'thread': 'operator', 'seat': '—', 'now': ident + ': ' + fate.reason,
                         'owed': 1, 'opened-by': fate.thread or '—', 'plan': '—',
                         'status': 'open',
                         'owed-by': 'operator', 'restart': 'operator note',
                         'control': 'operator: attention --note ' + ident + ' --why <reason>'})
    return rows


def render_table(rows: list[dict]) -> str:
    columns = ('thread', 'seat', 'now', 'owed', 'opened-by', 'plan', 'owed-by', 'restart', 'control')
    if any(row.get('left') for row in rows):
        columns += ('left',)
        rows = [dict(row, left=row.get('left') or '—') for row in rows]
    return '\n'.join(' · '.join(str(row[c]) for c in columns)
                     for row in [dict(zip(columns, columns)), *rows]) + '\n'


def left_by_thread(home: Home, facts: list, thread: str | None = None) -> dict:
    """Explicit CLI inventory; shared thread_table and body boundaries stay cheap."""
    from .leftovers import leftovers
    found = {}
    for strand, owner in sender_threads(facts).items():
        if thread is None or thread == owner:
            left = leftovers(home, strand)
            if left:
                found.setdefault(owner, []).extend(left)
    return found


def render_left(thread: str, items: list) -> str:
    lines = [f'Left in {thread}:']
    for item in items:
        lines.append(str(item.path))
        lines.extend(f'  {sha} {subject}' for sha, subject in item.commits)
        lines.extend(f'  edit {path!r}' for path in item.edits)
        if item.error:
            lines.append(f'  inspection failed: {item.error}')
    return '\n'.join(lines) + '\n' if items else ''


def due_table(home: Home, facts: list) -> str:
    """Waiting mail, including its sender thread after that sender has ended."""
    writers = sender_threads(facts)
    waiting = [f.letter for f in letter_owners(facts, main_snapshot(home)).values()
               if f.owner == "clock" and f.action == "wait"]
    lines = ["at · id · to · from · text"]
    for letter in sorted(waiting, key=lambda f: (f.data["at"], f.id)):
        sender = writers.get(letter.by.removeprefix("strand:"), letter.data.get("from", ""))
        first = str(letter.data.get("body") or "").split("\n", 1)[0]
        lines.append(f"{letter.data['at']} · {letter.id} · {thread_of(letter.data['to'])} · {sender} · {first}")
    return "\n".join(lines) + "\n"
