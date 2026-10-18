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
              route_bare(facts, f'{platform}/{chat}', binding[1] if binding else None))
        if not home.routable(to.removeprefix('thread:')):
            to = 'thread:inbox'
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
        if fm.get('missing'):
            data['blobs_missing'] = [int(n) for n in fm['missing'].split()]
        if clean:
            data.update(clean=fm['kind'], refs=fm['refs'].split())
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
        for key in ('clean', 'refs'):
            if key in data:
                fields[key] = data[key]
        append(home, Fact(kind='letter', by=f'loom:{home.install_id()}', id=letter_id,
                          after=(source.id,), data=fields))
    if source.data.get('blobs_missing'):
        from .loom import notice
        notice(home, read_facts(home), 'blobs-missing', source.id,
               f"{name}: gave up on attachment(s) {source.data['blobs_missing']} of {letter_id} after 3 pulls",
               'ask the sender to send the files again if they matter',
               thread=source.data['to'].removeprefix('thread:'))


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


def route_bare(facts, chat, reply_thread=None):
    """A reply's thread, else the last confirmed speaker, regardless of lease."""
    try:
        _platform, chat_id = channel_parts(f'channel:{chat}')
    except ValueError:
        return 'thread:inbox'
    if chat_id is None:
        return 'thread:inbox'
    if reply_thread:
        return f'thread:{reply_thread}'
    spoken = [thread for data, _letter, thread in _speech(facts, f'channel:{chat}', 'speech')
              if data.get('state') == 'sent']
    return f'thread:{spoken[-1]}' if spoken else 'thread:inbox'
