"""One external effect per letter. An uncertain attempt is never repeated."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from brr.daemon2.facts import Fact
from brr.daemon2.leases import LocalLeaseAuthority, StaleLease
from .config import loom_clock
from .connector import carries, environment
from .home import channel_parts
from .ledger import append
from .port import _front

CHANNEL = 'channel:fake'


def speak(home, router_lease, key, text, *, channel=CHANNEL, context=None):
    if not key:
        raise ValueError('speak: empty key')
    platform, _chat = channel_parts(channel, home)
    name, folder, edits = carries(home)[platform]
    fields = dict(kind='letter', id=key, to=channel, **(context or {}))
    if fields.get('edit') and not edits:
        raise ValueError(f'{name} does not carry edits')
    authority = LocalLeaseAuthority(home.root / 'ledger/leases', clock=loom_clock)
    if not authority.authorize(router_lease):
        return 'stepped-down', None, []
    safe = hashlib.sha256(key.encode()).hexdigest()
    prior = list(authority.sends.read('sends', safe))
    receipt = next((f.data['receipt'] for f in prior if f.kind == 'sent'), None)
    if receipt is None and any(f.kind == 'intended' for f in prior):
        status, receipt = 'maybe-sent', {}
    else:
        def run(_key, _gen):
            with tempfile.TemporaryDirectory(prefix='loom-letter-') as tmp:
                path = Path(tmp) / 'letter.md'
                path.write_text(_front(fields, text), encoding='utf-8')
                proc = subprocess.run([str(folder / 'out'), str(path)], env=environment(home, name),
                                      capture_output=True, text=True, timeout=60)
            if proc.returncode == 3:
                return dict(status='not-sent', reason=proc.stderr.strip(), ids=[])
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or f'out exited {proc.returncode}')
            result = json.loads(proc.stdout)
            if not isinstance(result, dict) or not isinstance(result.get('ids'), list) or any(
                    not isinstance(i, (str, int)) or isinstance(i, bool) for i in result['ids']):
                raise ValueError('out must print {"ids": [message ids]}')
            return dict(result, status='sent')
        try:
            receipt = receipt if receipt is not None else authority.effect_once(router_lease, safe, run)
            status = receipt['status']
        except StaleLease:
            return 'stepped-down', None, []
        except Exception:  # an intended effect may have reached the person
            status, receipt = 'maybe-sent', {}
    for n, ident in enumerate(receipt.get('ids', []), 1):
        append(home, Fact(kind='speech.part', by=f'loom:{home.install_id()}', id=f'speech.part:{key}#{n}',
               data=dict(key=key, part=f'{key}#{n}', channel=channel, receipt=dict(message_id=ident))))
    state = 'intended' if status == 'maybe-sent' else status
    fact = append(home, Fact(kind='speech', by=f'loom:{home.install_id()}', id=f'speech:{state}:{key}',
                  data=dict(key=key, state=state, router_gen=router_lease.gen,
                            reason=receipt.get('reason', ''))))
    return status, fact, [key] if status == 'maybe-sent' else []
