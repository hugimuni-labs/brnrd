"""Room contents held nowhere else, inspected only by an explicit request.

Reachability is publish's comparison. This inventory also reads dirty paths
and the copies held by openers and molt successors; it never runs on a tick.
An inspection failure is non-empty, so a future sweep cannot treat it as safe.
"""

from dataclasses import dataclass
from pathlib import Path

from .adapters import repos, seat_text
from .ledger import read_facts
from .project import fold, sender_threads
from .publish import _opened, _uncovered
from .selfrepo import SelfError, git


@dataclass(frozen=True)
class Leftover:
    path: Path
    commits: tuple[tuple[str, str], ...] = ()
    edits: tuple[str, ...] = ()
    error: str = ''

    @property
    def count(self) -> int:
        return len(self.commits) + len(self.edits)


def _edits(checkout: Path) -> tuple[str, ...]:
    # -z is literal, including whitespace and newlines; a rename is one edit.
    entries = iter(git(checkout, 'status', '--porcelain=v1', '-z',
                       '--untracked-files=all').stdout.split('\x00'))
    paths = []
    for entry in entries:
        if not entry:
            continue
        paths.append(entry[3:])
        if 'R' in entry[:2] or 'C' in entry[:2]:
            paths[-1] += ' <- ' + next(entries)
    return tuple(paths)


def leftovers(home, strand: str) -> tuple[Leftover, ...]:
    """Commits and edits that only ``home.room(strand)`` still holds.

    Self is compared with today's main, projects with their declared source
    (refreshed on request, never their mutable origin). Copies in an opener
    or a descendant room cover commits, never another room's dirty edits.
    Missing rooms are empty; failed inspections are explicit retained items.
    """
    room = home.room(strand).resolve()
    if not room.is_dir():
        return ()
    facts = read_facts(home)
    writers = sender_threads(facts)
    thread = writers.get(strand)
    parents = {str(f.data['strand']): str(f.data.get('parent', ''))
               for f in fold(facts).accepted if f.kind == 'lease'}

    def descendant(other):
        seen = set()
        while other and other not in seen:
            seen.add(other)
            other = parents.get(other)
            if other == strand:
                return True
        return False

    declared = dict(repos(seat_text(home, thread))) if thread else {}
    result = []
    for checkout in sorted(room.iterdir()):
        if not (checkout / '.git').exists():
            continue
        edits = ()
        try:
            edits = _edits(checkout)
            if checkout.name == 'self':
                # Local main is authoritative; the room's main is a birth snapshot.
                git(checkout, 'fetch', '--no-tags', '--no-write-fetch-head', '--',
                    str((home.root / 'self').resolve()), '+refs/heads/main:refs/leftovers/main')
                covered = ['refs/leftovers/main']
            else:
                source = declared.get(checkout.name)
                if source is None:
                    raise SelfError('no declared source for this checkout')
                git(checkout, 'fetch', '--prune', '--no-tags', '--no-write-fetch-head', '--',
                    source, '+refs/heads/*:refs/leftovers/source/*')
                covered = ['--glob=refs/leftovers/source/*']
            commits = _uncovered(checkout, ['--branches'], covered)
            held = set()
            for other, owner in writers.items():
                if other == strand:
                    continue
                copied = (checkout.name != 'self' and thread and _opened(home, owner, thread)
                          and dict(repos(seat_text(home, owner))).get(checkout.name) == declared.get(checkout.name))
                if not descendant(other) and not copied:
                    continue
                peer = home.room(other) / checkout.name
                if (peer / '.git').exists():
                    held.update(sha for sha, _ in _uncovered(peer, ['--branches'], []))
            commits = tuple((sha, subject) for sha, subject in commits if sha not in held)
            if commits or edits:
                result.append(Leftover(checkout, commits, edits))
        except (SelfError, OSError, ValueError) as exc:
            result.append(Leftover(checkout, edits=edits, error=str(exc)))
    return tuple(result)
