"""Replicated channel declarations; credentials and local fences never travel.

Declarations are immutable claims. Competing owners remain a conflict, even
when one claim sorts later: an offline bot owner cannot be revoked by a fact.
This is client policy, not enrollment or authorization at the cloud relay.
"""
import hashlib
import json
import re
import subprocess

from brr.daemon2.facts import Fact
from .adapters import resolve
from .config import load_config
from .ledger import append, read_facts


class OwnershipError(ValueError):
    pass


class Unavailable(OwnershipError):
    """No effect was attempted; keep the original drop or draft for recovery."""


def allowed(home, name, authorize=None):
    """One predicate at every client effect/admission boundary.

    Shared callers must supply the router's live shared and local fence;
    a projected leader or a local lease alone cannot stand in for it.
    Check it after resolving local identity too, which may take time.
    """
    if authorize is not None and not authorize():
        return False
    if problem(home, name):
        return False
    if authorize is None:
        return not load_config(home.root).lease_remote
    return bool(authorize())


def require(home, name, authorize=None):
    if not allowed(home, name, authorize):
        raise Unavailable(f'{name}: connector ownership or current lease authority is unavailable')


def claims(home):
    return [f.data for f in read_facts(home) if f.kind == 'connector.declared']


def binding(home, name, kind):
    from .connector import environment
    folder = resolve(home, kind, 'connectors')
    path = folder / 'scope'
    scope = path.read_text().strip() if path.is_file() else ''
    if scope not in {'global', 'machine'}:
        raise OwnershipError(f'{name}: shared connectors require {folder}/scope containing global or machine')
    data = dict(name=name, kind=kind, scope=scope,
                owner=home.install_id() if scope == 'machine' else None)
    identity = folder / 'identity'
    if identity.is_file():
        # The adapter emits a public transport identity, never a token or hash
        # of a secret. Suppress adapter diagnostics at this replicated boundary.
        try:
            proc = subprocess.run([str(identity)], env=environment(home, name),
                                  capture_output=True, text=True, timeout=10)
            value = proc.stdout.strip()
            if proc.returncode or not re.fullmatch(r'[A-Za-z0-9._:-]{1,160}', value):
                raise ValueError()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise OwnershipError(f'{name}: cannot read public transport identity; repair local credentials and run brnrd runtime machines connectors migrate') from None
        data['identity'] = value
    return data


def conflict(data, rows):
    same = [r for r in rows if r.get('name') == data['name']]
    if any(r != data for r in same):
        return f"{data['name']}: conflicting connector declaration; the existing owner must acknowledge a transfer, or rotate/revoke the old credential before recovery"
    identity = data.get('identity')
    if identity and any(r.get('identity') == identity and r != data for r in rows):
        return f"{data['name']}: this transport is already declared on another channel or machine; copied credentials cannot enroll it again"
    return None


def migrate(home):
    """Idempotently declare today's bindings before shared traffic is enabled.

    All local entries are inspected, including on followers. Independent,
    concurrent nominations may conflict after shuttle import; neither wins.
    """
    if not load_config(home.root).lease_remote:
        return
    from .connector import instances
    errors = []
    rows = claims(home)
    for name, kind in instances(home).items():
        try:
            data = binding(home, name, kind)
            if why := conflict(data, rows):
                raise OwnershipError(why)
            if data not in rows:
                digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
                append(home, Fact(kind='connector.declared', by=f'loom:{home.install_id()}',
                                 id=f'connector.declared:{home.install_id()}:{digest}', data=data))
                rows.append(data)
        except OwnershipError as exc:
            errors.append(str(exc))
    if errors:
        raise OwnershipError('; '.join(errors))


def problem(home, name):
    """Current local binding must exactly match its sole replicated claim."""
    if not load_config(home.root).lease_remote:
        return None
    from .connector import instances
    kind = instances(home).get(name)
    if kind is None:
        return f'{name}: connector is not enabled locally'
    try:
        data = binding(home, name, kind)
    except OwnershipError as exc:
        return str(exc)
    rows = claims(home)
    if why := conflict(data, rows):
        return why
    if data not in rows:
        return f'{name}: no shared connector declaration; run brnrd runtime machines connectors migrate'
    return None
