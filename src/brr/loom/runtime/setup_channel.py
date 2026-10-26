"""Pair a named channel by driving exactly the folder's in/out contract."""
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .adapters import resolve
from .config import load_config, read_array, seed_config, set_value
from .connector import addressed, environment, instances
from .home import Home, atomic_write, channel_parts, resolve_home
from .port import _front, parse_frontmatter
from .selfrepo import SelfError, init_self


def pair(home, kind, name, person, *, timeout=180, out=print):
    if not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', v) for v in (name, person)):
        raise ValueError('Channel and person names use letters, digits, dots, dashes or underscores')
    if name in instances(home):
        raise ValueError(f'Channel name {name!r} is already in use')
    folder = resolve(home, kind, 'connectors')
    if not (folder / 'out').is_file():
        raise ValueError(f'{kind} is an input pipe, not a messaging channel')
    env = environment(home, name)
    state = Path(env['BRNRD_STATE'])
    setup = folder / 'setup'
    if setup.is_file():
        subprocess.run([str(setup)], env=env, check=True)
    code = secrets.token_hex(4)
    # Use the normal drop directory: unrelated letters survive pairing for the
    # loom to ingest once enabled. The connector advances its usual cursor.
    drop = state / 'in'
    drop.mkdir(parents=True, exist_ok=True)
    command = folder / 'in'
    child = subprocess.Popen([str(command), str(drop)], env=env) if command.is_file() else None
    out(f'Send {code} to the bot now (waiting up to {timeout:g}s).')
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            for path in sorted(drop.glob('*.md')):
                if path.is_symlink():
                    continue
                fm = addressed(name, parse_frontmatter(path.read_text()))
                if fm.get('body', '').strip() not in (code, '/start ' + code):
                    continue
                address, chat = channel_parts(fm.get('to', ''))
                sender = fm.get('from', '')
                if address != name or sender != f'{name}:{chat}':
                    out('Pairing needs a direct message from your own account; ignored this message.')
                    continue
                with tempfile.TemporaryDirectory(prefix='pair-', dir=state) as directory:
                    reply = Path(directory) / 'reply.md'
                    reply.write_text(_front(dict(kind='letter', id='pair-' + code,
                                                to=chat, re=fm['id']), f'heard you as {chat}'))
                    result = subprocess.run([str(folder / 'out'), str(reply)], env=env,
                                            capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    raise ValueError('Pairing reply failed or may have sent; channel was not enabled')
                # A zero exit is a receipt only with the same output grammar.
                receipt = json.loads(result.stdout)
                if (not isinstance(receipt, dict) or not isinstance(receipt.get('ids'), list) or
                        any(not isinstance(i, (str, int)) or isinstance(i, bool) for i in receipt['ids'])):
                    raise ValueError('Pairing out returned no ids receipt')
                path.unlink()
                if path.with_suffix('.d').exists():
                    shutil.rmtree(path.with_suffix('.d'))
                channels = home.root / 'self/people' / person / 'channels.toml'
                previous = read_array(channels, 'channels')
                key = f'{name}:{chat}'
                if key not in previous:
                    atomic_write(channels, 'channels = ' + json.dumps([*previous, key]) + '\n')
                # Commit the config last: a failed pairing never enables input.
                seed_config(home.root)
                set_value(home.root, 'connectors', [*load_config(home.root).connectors, f'{name}={kind}'])
                out(f'heard you as {chat}; saved {key} for {person}')
                return key
            if child is not None and child.poll() is not None:
                raise ValueError(f'{kind}/in exited before hearing the code')
            time.sleep(.05)
        raise TimeoutError('No matching direct message arrived; channel was not enabled')
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(prog='brnrd setup')
    parser.add_argument('step', nargs='?', choices=['channel'], default='channel')
    parser.add_argument('--home')
    parser.add_argument('--kind')
    parser.add_argument('--name')
    parser.add_argument('--person')
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args(argv)
    # Setup creates a home; the read-only resolver normally requires one.
    target = args.home or os.environ.get("BRNRD_HOME") or Path.home() / "brnrd"
    home = Home(resolve_home(target)[0])
    bundled = Path(__file__).parent / 'connectors'
    kinds = sorted({p.name for root in (bundled, home.root / 'loom/connectors')
                    for p in root.glob('*') if (p / 'carries').is_file() and (p / 'out').is_file()})
    try:
        kind = args.kind or input(f'Channel kind ({", ".join(kinds)}): ').strip()
        name = args.name or input('Name this channel: ').strip()
        people = sorted(p.name for p in (home.root / 'self/people').glob('*') if p.is_dir())
        person = args.person or (people[0] if len(people) == 1 else input('Your person name: ').strip())
        # A letter lands only on a thread on the self's main: seed it before pairing.
        print(init_self(home.root, person=person))
        pair(home, kind, name, person, timeout=args.timeout)
    except (ValueError, OSError, TimeoutError, subprocess.SubprocessError, SelfError) as exc:
        raise SystemExit(str(exc)) from None
    return 0
