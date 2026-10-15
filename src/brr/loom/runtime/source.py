"""A connector drop becomes blobs, a durable source, then its routed letter."""

import hashlib
import mimetypes
import re
import shutil

from brr.daemon2.facts import Fact
from .channel import _sender, route_bare, thread_for_message
from .home import atomic_write, channel_parts
from .labels import is_stranger
from .ledger import append, read_facts
from .port import PortError, clean_accepts, parse_frontmatter


def _letter(home, source):
    data = source.data
    sender = data['from']
    ident = source.id.replace('source:', 'letter:', 1)
    fields = dict(id=ident, **{'from': sender}, to=data['to'], body=data['text'],
                  cites=source.id, label=data['label'])
    for key in ('clean', 'refs'):
        if key in data:
            fields[key] = data[key]
    return append(home, Fact(kind='letter', by=f'loom:{home.install_id()}', id=ident,
                             after=(source.id,), data=fields))


def ingest(home, name, fm, attachments):
    ident = fm['id']
    source_id = f'source:{name}:{ident}'
    facts = read_facts(home)
    source = next((f for f in facts if f.id == source_id), None)
    if source is None:
        platform, chat = channel_parts(fm['to'], home)
        raw_sender = fm.get('from', '')
        sender = _sender(home, platform, raw_sender.partition(':')[2]
                         if raw_sender.startswith(platform + ':') else None)
        clean = clean_accepts(home.root / 'self', fm['kind'], fm.get('refs', '').split(), fm['body'])
        reply = thread_for_message(facts, fm['to'], fm.get('re'))
        to = route_bare(facts, f'{platform}/{chat}', reply)
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
                    label=dict(taint=not clean and (sender.startswith('stranger:') or
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
        _letter(home, source)
    if source.data.get('blobs_missing'):
        from .loom import notice
        notice(home, read_facts(home), 'blobs-missing', source.id,
               f"{name}: gave up on attachment(s) {source.data['blobs_missing']} of {letter_id} after 3 pulls",
               'ask the sender to send the files again if they matter',
               thread=source.data['to'].removeprefix('thread:'))


def drain(home, name, drop):
    for path in sorted(drop.glob('*.md'), key=lambda p: (p.lstat().st_mtime_ns, p.name)):
        attachments = path.with_suffix('.d')
        try:
            fm = parse_frontmatter(path.read_text(encoding='utf-8'))
            if path.is_symlink() or fm.get('id') != path.stem or not re.fullmatch(r'[A-Za-z0-9_%:#.-]+', fm.get('id', '')) or not fm.get('kind'):
                raise ValueError('letter requires a safe id and kind')
            channel_parts(fm.get('to', ''), home)
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
        except (OSError, UnicodeError, ValueError, PortError) as exc:
            refused = drop / 'refused'
            refused.mkdir(exist_ok=True)
            path.replace(refused / path.name)
            if attachments.exists():
                attachments.replace(refused / attachments.name)
            from .loom import notice
            notice(home, read_facts(home), 'connector-reject', f'{name}:{path.stem}',
                   f'{name}: refused {path.name}: {exc}', 'repair the refused letter before resubmitting', taint=True)
            continue
        ingest(home, name, fm, files)  # ledger failures leave the drop for recovery
        path.unlink()
        if attachments.exists():
            shutil.rmtree(attachments)
