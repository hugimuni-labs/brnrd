"""One derived view of threads, shared by the CLI and opener boundaries."""

import json
import time
from datetime import datetime

from .adapters import body_label
from .home import Home, thread_of
from .ledger import LedgerView
from .lifecycle import CLOSED, read_thread, main_snapshot, main_tree, readme_at
from .owners import active_owners, letter_owners, thread_axes, unanswered_by_thread
from .project import cached_projection, fold, sender_threads
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


def _table_inputs(facts, main):
    """Ledger/main-only inputs; physical liveness, gauges and time stay outside."""
    from .loom import pending_bodies
    state, writers = fold(facts), sender_threads(facts)
    ownership = letter_owners(facts, main)
    unanswered = unanswered_by_thread(facts, main)
    axes = thread_axes(facts, main, ownership)
    owed, activity, boundary = {}, {}, {}
    debts = active_owners(facts, main) if isinstance(facts, LedgerView) else ownership.values()
    for fate in debts:
        if not fate.done and fate.owner in {'body', 'loom', 'opener'}:
            owed.setdefault(fate.thread, []).append(fate.letter)
    if isinstance(facts, LedgerView):
        return state, ownership, axes, owed, state.activity, state.boundary, state.bodies, unanswered
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
    return state, ownership, axes, owed, activity, boundary, pending_bodies(state.accepted), unanswered


def thread_table(home: Home, facts: list) -> list[dict]:
    """Read main and the ledger; never ask a body to declare its state.

    A recent boundary is a shown fact or the jack's existing state-file mtime.
    There is no explicit 'waiting' fact: a live body with nothing owed, or a
    stop block, is the nearest derivable holding value.
    """
    from .loom import body_alive
    repo = home.root / 'self'
    versioned = (repo / '.git').exists()
    # One read of main feeds both the axes and the rows: a thread opened
    # between two reads would have a row and no axes.
    tip, blobs = main_tree(repo) if versioned else (None, {})
    main = main_snapshot(home, (tip, blobs) if versioned else None)
    state, ownership, axes, owed, activity, boundary, pending, unanswered = cached_projection(
        facts, "thread-table", repr(main), lambda rows: _table_inputs(rows, main))
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
    now, rows = time.time(), []
    from .wishes import plan_word, told as fold_wishes
    told = fold_wishes(facts)
    # A gauge depends on its shell alone: read it once per table, not per row.
    tanks = {}
    from .receipts import receipts
    shipped = receipts(facts, home.install_id())
    for thread, data, choice, created, date in _MAIN[repo][1]:
        why = '—'
        if opened_for(data):
            why = '; '.join(opened_for_line(ident, told.get(ident)) for ident in opened_for(data))
        count = len(owed.get(thread, ()))
        strand = state.holder.get(thread, ('', 0))[0]
        latest = pending.get(strand)
        shell = choice.partition('/')[0]
        seat = choice or '—'
        if latest and latest.data.get('shell'):
            shell = latest.data['shell']
            seat = body_label(shell, latest.data.get('core', ''))
        last = activity.get(thread, created)
        if data['status'] in CLOSED:
            count = len(axes[thread]['letters'])
            if axes[thread]['cause'] == 'stopped':
                value = 'stopped'
            elif latest and latest.kind == 'body.unidentified':
                value = f"unidentified {latest.data.get('pid')}"
            else:
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
                if shell not in tanks:
                    tanks[shell] = fuel(home, shell) if shell else ''
                tank = tanks[shell]
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
        rows.append({'thread': thread, 'seat': seat, 'now': value, 'owed': count, 'unanswered': len(unanswered.get(thread, ())),
                     'opened-by': data.get('opened-by') or '—', 'opened-for': why, 'plan': plan_word(told, thread),
                     'status': data['status'], **{k: axes[thread][k] for k in ('owed-by', 'restart', 'control')},
                     'receipts': '; '.join(r.line() for r in shipped.get(thread, ())) or '—'})
    for ident, fate in ownership.items():
        if fate.owner == 'operator':
            rows.append({'thread': 'operator', 'seat': '—', 'now': ident + ': ' + fate.reason,
                         'owed': 1, 'unanswered': 0, 'opened-by': fate.thread or '—', 'opened-for': '—',
                         'plan': '—',
                         'status': 'open',
                         'owed-by': 'operator', 'restart': 'operator note',
                         'control': 'operator: attention --note ' + ident + ' --why <reason>'})
    return rows


def opened_for(data: dict) -> list[str]:
    """``opened-for`` as a list, also when a hand edit wrote one bare id."""
    value = data.get('opened-for') or []
    return [value] if isinstance(value, str) else list(value)


def opened_for_line(ident: str, wish) -> str:
    """A wish a thread was opened for: its id, state and line, or that it is gone."""
    if wish is None:
        return f'{ident} (no such wish)'
    line = ' '.join(wish.line.split()).replace(' · ', ' / ')
    return f"{ident} {wish.state}: {line if len(line) <= 60 else line[:59] + '…'}"


def render_table(rows: list[dict]) -> str:
    columns = ('thread', 'seat', 'now', 'owed', 'unanswered', 'opened-by', 'opened-for', 'plan', 'owed-by', 'restart', 'control')
    if any(row.get('receipts', '—') != '—' for row in rows):
        columns += ('receipts',)
        rows = [dict(row, receipts=row.get('receipts') or '—') for row in rows]
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
