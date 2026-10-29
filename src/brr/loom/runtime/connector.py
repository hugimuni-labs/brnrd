"""Enabled folders declare channels and exchange the port's letter files."""

import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from brr.daemon2.facts import Fact, union
from .adapters import resolve
from .config import config_path, load_config, read_array
from .home import atomic_write, channel_parts, mint
from .labels import guard_off, is_stranger
from .ledger import append, read_facts
from .port import clean_accepts, parse_frontmatter
from .project import fold, sender_threads
from .refs import link, miss_words
from .wishes import wishes


def instances(home):
    cfg = load_config(home.root)
    return dict(v.split('=', 1) if '=' in v else (v, v) for v in cfg.connectors)


def carries(home):
    found = {}
    for name, kind in instances(home).items():
        folder = resolve(home, kind, "connectors")
        flags = (folder / "carries").read_text().split()
        if flags not in ([], ["edit"]):
            raise ValueError(f"{folder}/carries: expected only an optional edit line")
        found[name] = (name, folder, bool(flags))
    return found


def environment(home, name):
    state = home.root / "loom/connectors" / name / "state"
    state.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in {"GIT_DIR", "GIT_WORK_TREE"}}
    src = str(Path(__file__).resolve().parents[3])
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env.update(BRNRD_HOME=str(home.root.resolve()), BRNRD_STATE=str(state.resolve()),
               BRNRD_CONFIG=str(config_path(home.root).resolve()),
               PYTHONPATH=src + os.pathsep + env.get("PYTHONPATH", ""))
    return env


class Inputs:
    """One supervised input per enabled folder. A directory input is passive."""
    def __init__(self, home):
        carries(home)  # validate the complete declaration before starting any child
        self.home, self.children, self.failures = home, {}, {}
        self.names = instances(home)

    def step(self):
        # Read at every step: a channel paired while the loom runs is heard
        # without a restart, and one taken away stops being polled.
        names = instances(self.home)
        if names != self.names:
            carries(self.home)
            for name in [n for n in self.children if self.names.get(n) != names.get(n)]:
                self._stop(self.children.pop(name)[0])
            self.names = names
        for name, kind in self.names.items():
            folder = resolve(self.home, kind, "connectors")
            drop = self.home.root / "loom/connectors" / name / "state/in"
            drop.mkdir(parents=True, exist_ok=True)
            command = folder / "in"
            child, due, delay = self.children.get(name, (None, 0, 1))
            if command.is_file() and (child is None or child.poll() is not None):
                if child is not None:
                    due, child = time.monotonic() + delay, None
                    delay = min(delay * 2, 60)
                if time.monotonic() >= due:
                    try:
                        child = subprocess.Popen([str(command), str(drop.resolve())],
                                                 env=environment(self.home, name))
                    except OSError:
                        due, delay = time.monotonic() + delay, min(delay * 2, 60)
                self.children[name] = (child, due, delay)
            drain(self.home, name, drop, self.failures)

    def close(self):
        for child, _due, _delay in self.children.values():
            self._stop(child)
        self.children.clear()

    @staticmethod
    def _stop(child):
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def addressed(name, fm):
    """The one inbound boundary: native ids become instance addresses."""
    fields = dict(fm, to=f"channel:{name}/{fm['to']}")
    channel_parts(fields['to'])
    if fm.get('from'):
        fields['from'] = f"{name}:{fm['from']}"
    return fields


def ingest(home, name, fm, attachments):
    ident = fm['id']
    source_id = f'source:{name}:{ident}'
    facts = read_facts(home)
    source = next((f for f in facts if f.id == source_id), None)
    binding = reply_for_message(facts, source.data['channel'] if source else fm['to'],
                                source.data.get('re') if source else fm.get('re'))
    if source is None:
        platform, chat = channel_parts(fm['to'], home)
        raw_sender = fm.get('from', '')
        sender = _sender(home, platform, raw_sender.partition(':')[2]
                         if raw_sender.startswith(platform + ':') else None)
        clean = clean_accepts(home.root / 'self', fm['kind'], fm.get('refs', '').split(), fm['body'])
        # A connector may name the thread a letter was written on; else route it.
        to = (f"thread:{fm['thread']}" if fm.get('thread') else
              route_bare(facts, f'{platform}/{chat}', binding[1] if binding else None,
                         home.main_thread()))
        # A bare yes in reply to a delivery letter is the agree it asked for.
        if not fm.get('verdict') and binding and sender.startswith('person:'):
            fm = dict(fm, **_agree(facts, binding[0], fm['body']))
        # A verdict belongs to the thread that owns its wish, wherever it was given.
        owner = wishes(facts).get(fm.get('wish')) if fm.get('verdict') and sender.startswith('person:') else None
        if owner is not None and home.routable(owner.thread):
            to = f'thread:{owner.thread}'
        if not home.routable(to.removeprefix('thread:')):
            to = f'thread:{home.main_thread()}'
        blobs, blob_ids = [], []
        for index, path in enumerate(attachments):
            payload = path.read_bytes()
            sha = hashlib.sha256(payload).hexdigest()
            dest = home.root / 'blobs' / sha
            if not dest.exists():
                atomic_write(dest, payload, sync_directory=True)
            blob_id = f'blob:{name}:{ident}#{path.name.split(".")[0]}'
            append(home, Fact(kind='blob', by=f'loom:{home.install_id()}', id=blob_id,
                   data=dict(sha=sha, mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                             size=len(payload), origin=blob_id.removeprefix('blob:'))))
            blobs.append(sha)
            blob_ids.append(blob_id)
        data = dict(origin=f'{name}:{ident}', platform=platform, chat=chat, channel=fm['to'],
                    **{'from': sender}, text=fm['body'], blobs=blobs, to=to,
                    label=dict(taint=not clean and not guard_off(home.root / 'self') and (sender.startswith('stranger:') or
                               is_stranger(sender, home.root / 'self')), audience=['self']))
        if fm.get('re'):
            data['re'] = fm['re']
        for key in ('wake', 'wish', 'verdict'):
            if fm.get(key):
                data[key] = fm[key]
        if fm.get('missing'):
            data['blobs_missing'] = [int(n) for n in fm['missing'].split()]
        if clean:
            data.update(clean=fm['kind'], refs=fm['refs'].split())
        # A ref that names nothing never refuses his letter: it lands without that link.
        from .lifecycle import main_snapshot
        links, misses = link(facts, main_snapshot(home), fm['body'])
        if links:
            data['links'] = links
        if misses:
            data['refs_missing'] = misses
        source = append(home, Fact(kind='source', by=f'loom:{home.install_id()}',
                                   id=source_id, after=tuple(blob_ids), data=data))
    letter_id = source_id.replace('source:', 'letter:', 1)
    if not any(f.id == letter_id for f in facts):
        data = source.data
        sender = data['from']
        fields = dict(id=letter_id, **{'from': sender}, to=data['to'], body=data['text'],
                      cites=source.id, label=data['label'])
        if binding:
            fields['re'] = str(binding[0].data.get('id') or binding[0].id)
        for key in ('clean', 'refs', 'links', 'wake', 'wish', 'verdict'):
            if key in data:
                fields[key] = data[key]
        append(home, Fact(kind='letter', by=f'loom:{home.install_id()}', id=letter_id,
                          after=(source.id,), data=fields))
    if source.data.get('refs_missing'):
        from .loom import notice
        facts = read_facts(home)
        letter = next(f for f in facts if f.id == letter_id)
        words = miss_words(source.data['refs_missing'])
        notice(home, facts, 'ref-unknown', letter_id, f"{letter_id} from {source.data['from']}: {words}",
               'nothing to look for: those ids do not exist',
               thread=source.data['to'].removeprefix('thread:'), passive=True)
        if source.data['from'].startswith('person:'):
            notice(home, facts, 'ref-told', letter_id, f'Your letter landed; {words}.',
                   'Check the id and send the ref again if it matters.',
                   about=letter, channel=source.data['channel'], passive=True)
    # A person's agree on a delivered wish is its acceptance; a correct is the letter.
    verdict_id = f'wish.verdict:{letter_id}'
    if (source.data.get('verdict') == 'agree' and source.data['from'].startswith('person:')
            and not any(f.id == verdict_id for f in facts)):
        wish = wishes(facts).get(source.data.get('wish'))
        if wish is not None and wish.state in {'delivered', 'expired'}:
            append(home, Fact(kind='wish.verdict', by=source.data['from'], id=verdict_id,
                              data={'wish': wish.id, 'as': 'accepted', 'evidence': 'word', 'ref': letter_id}))
    if source.data.get('verdict') and source.data['from'].startswith('person:'):
        heard(home, letter_id, source.data)
    if source.data.get('blobs_missing'):
        from .loom import notice
        notice(home, read_facts(home), 'blobs-missing', source.id,
               f"{name}: gave up on attachment(s) {source.data['blobs_missing']} of {letter_id} after 3 pulls",
               'ask the sender to send the files again if they matter',
               thread=source.data['to'].removeprefix('thread:'))


def heard(home, letter_id, data):
    """A person's verdict is news to its wish's thread and to the inbox; only a correct asks for work.

    An agree is settled by the loom as it lands; each thread it reaches gets a
    passive notice, shown once and owed by nobody. A correct stays owed on the
    wish's thread, and the inbox, the center, is told of it the same way.
    """
    from .loom import notice
    facts = read_facts(home)
    found = wishes(facts).get(data.get('wish'))
    line = f' "{found.line}"' if found is not None and found.line else ''
    thread = data['to'].removeprefix('thread:')
    words = f"verdict: {data['verdict']} on {data['wish']}{line} from {data['from']} ({letter_id})"
    # A correct is itself the letter owed on its thread; that thread needs no notice of it.
    targets = {home.main_thread()} | ({thread} if data['verdict'] == 'agree' else set())
    if data['verdict'] == 'agree':
        if not any(f.kind == 'note' and f.data.get('re') == letter_id for f in facts):
            append(home, Fact(kind='note', by=f'loom:{home.install_id()}', id=f'note:verdict:{letter_id}',
                              data={'re': letter_id, 'body': 'an agree is a notice: no reply owed'}))
    else:
        targets.discard(thread)
    can = 'nothing owed' if data['verdict'] == 'agree' else f'nothing owed here; the letter is owed on thread {thread}'
    # A settled thread may still hold delivered wishes (silence archives a
    # thread, never accepts a wish). A letter to a closed thread climbs to its
    # open ancestor (owners); with none, a correct would wait on nobody, so
    # the inbox owes it instead.
    orphan = data['verdict'] == 'correct' and not _open_ancestor(home, thread)
    if orphan:
        targets.discard(thread)
        can = (f'thread {thread} is settled: reopen it with a letter to thread:{thread} '
               'that carries the correction, or answer the person here')
    for target in sorted(targets):
        notice(home, read_facts(home), f'verdict-{target}', letter_id, words, can, thread=target,
               passive=not orphan)


def _open_ancestor(home, thread):
    """``thread`` itself when open, else the nearest open opener thread, else None."""
    from .lifecycle import CLOSED, read_thread
    seen = set()
    while thread and thread not in seen:
        seen.add(thread)
        data = read_thread(home, thread)
        if not data or data.get('status') not in CLOSED:
            return thread if data else None
        opener = str(data.get('opened-by', ''))
        thread = opener.removeprefix('thread:') if opener.startswith('thread:') else None
    return None


def drain(home, name, drop, failures=None):
    failures = failures if failures is not None else {}
    for path in sorted(drop.glob('*.md'), key=lambda p: (p.lstat().st_mtime_ns, p.name)):
        attachments = path.with_suffix('.d')
        valid = False
        try:
            fm = addressed(name, parse_frontmatter(path.read_text(encoding='utf-8')))
            if path.is_symlink() or fm.get('id') != path.stem or not re.fullmatch(r'[A-Za-z0-9_%:#.-]+', fm.get('id', '')) or not fm.get('kind'):
                raise ValueError('letter requires a safe id and kind')
            channel_parts(fm['to'], home)
            if fm['kind'] != 'letter' and not clean_accepts(
                    home.root / 'self', fm['kind'], fm.get('refs', '').split(), fm['body']):
                raise ValueError('kind must be letter or a clean letter with valid refs')
            if fm.get('missing') and not all(n.isdecimal() for n in fm['missing'].split()):
                raise ValueError('missing must contain attachment numbers')
            files = sorted(attachments.iterdir()) if attachments.exists() else []
            for file in files:
                if not file.is_file() or file.is_symlink():
                    raise ValueError('attachment must be a regular file')
                file.read_bytes()
            valid = True
            ingest(home, name, fm, files)
        except Exception as exc:
            if valid:
                failures[path] = failures.get(path, 0) + 1
                if failures[path] < 3:
                    raise  # leave the original drop for recovery
                exc = f'ingest failed 3 times: {type(exc).__name__}: {exc}'
            refused = drop / 'refused'
            refused.mkdir(exist_ok=True)
            target = refused / path.name
            if target.exists() or target.is_symlink():
                target = refused / f'{path.stem}-{mint(5)}.md'
            if attachments.exists():
                attachments.replace(target.with_suffix('.d'))
            if path in failures:
                atomic_write(target.with_suffix('.failures'), str(failures[path]))
            path.replace(target)
            from .loom import notice
            notice(home, read_facts(home), 'connector-reject', f'{name}:{path.stem}',
                   f'{name}: refused {path.name}: {exc}', 'repair the refused letter before resubmitting', taint=True)
        else:
            path.unlink()
            if attachments.exists():
                shutil.rmtree(attachments)
        failures.pop(path, None)


def _sender(home, instance, user_id):
    # Only a connector's authenticated identity enters the person lookup.
    key = f"{instance}:{user_id}"
    matches = [p.parent.name for p in sorted((home.root / 'self/people').glob('*/channels.toml'))
               if user_id not in (None, '') and key in read_array(p, 'channels')]
    return f'person:{matches[0]}' if len(matches) == 1 else f'stranger:{instance}:{"" if user_id is None else user_id}'


def person_dm(home, channel):
    """Only a unique enrolled person's direct chat may be spoken to."""
    try:
        instance, chat = channel_parts(channel, home)
    except ValueError:
        return None
    sender = _sender(home, instance, chat)
    return sender if chat is not None and sender.startswith('person:') else None


def _speech(facts, channel, kind):
    """Join trusted receipts to the strand which actually drafted the letter."""
    ordered = union(facts)
    letters = {str(f.data.get('id') or f.id): f for f in fold(ordered).accepted if f.kind == 'letter'}
    threads = sender_threads(ordered)
    for fact in ordered:
        if fact.kind != kind or not fact.by.startswith('loom:'):
            continue
        letter = letters.get(str(fact.data.get('key')))
        if letter is None or not letter.by.startswith('strand:') or letter.data.get('to') != channel:
            continue
        thread = threads.get(letter.by.removeprefix('strand:'))
        if thread:
            yield fact.data, letter, thread


AGREE = frozenset({'ok', 'okay', 'yes', 'y', 'accept', 'accepted', 'agree', 'lgtm', 'good', 'done', '+1', '👍', '✅'})


def _agree(facts, letter, body) -> dict:
    """``{verdict, wish}`` when ``body`` is a bare yes to a letter that delivered one wish."""
    if body.strip().strip('.!').lower() not in AGREE:
        return {}
    found = wishes(facts)
    delivered = [i for i in letter.data.get('links') or () if i in found and found[i].state in {'delivered', 'expired'}]
    return {'verdict': 'agree', 'wish': delivered[0]} if len(delivered) == 1 else {}


def reply_for_message(facts, channel, message_id):
    """The outbound letter and its thread, bound by a trusted same-chat receipt."""
    if message_id not in (None, ''):
        for data, letter, thread in _speech(facts, channel, 'speech.part'):
            if data.get('channel') == channel and str((data.get('receipt') or {}).get('message_id')) == str(message_id):
                return letter, thread
    return None


def thread_for_message(facts, channel, message_id):
    binding = reply_for_message(facts, channel, message_id)
    return binding[1] if binding else None


def route_bare(facts, chat, reply_thread=None, fallback='inbox'):
    """A reply's thread, else the home thread: never the last thread that spoke.

    His rule (5474, 5482): any thread may write him, but only a reply-to carries
    his answer back there; every other letter of his is the inbox's to place.
    """
    try:
        _platform, chat_id = channel_parts(f'channel:{chat}')
    except ValueError:
        return f'thread:{fallback}'
    if chat_id is None or not reply_thread:
        return f'thread:{fallback}'
    return f'thread:{reply_thread}'
