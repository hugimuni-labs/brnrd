"""Pair a named channel by driving exactly the folder's in/out contract."""
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from brr import style

from .adapters import resolve
from .config import load_config, read_array, seed_config, set_value
from .connector import addressed, environment, instances
from .home import Home, atomic_write, channel_parts, resolve_home
from .port import _front, parse_frontmatter
from .selfrepo import IDENTITY_NOTE, SelfError, init_self


def _door(folder, env, code):
    """An optional door's ``{name, link}``: the bot, and a link that sends the code by itself."""
    if not (folder / 'door').is_file():
        return None
    try:
        result = subprocess.run([str(folder / 'door'), code], env=env, stdout=subprocess.PIPE,
                                text=True, timeout=30)
        door = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not (isinstance(door, dict) and isinstance(door.get('name'), str)
            and isinstance(door.get('link'), str)):
        return None
    return door


def _instructions(door, code, timeout, scan=False):
    """Say which way pairing runs: the person writes first; nothing arrives before."""
    tail = (f"The bot does not write first; it answers 'Paired ✓'. "
            f'Waiting up to {timeout:g}s.')
    if door:
        also = ", or scan the code below with your phone's camera" if scan else ''
        return (f"Open {door['link']} where you use the app and press Start{also}: that sends "
                f"your code. Or open {door['name']} yourself and send the code {code}. " + tail)
    return (f'Open the chat with your bot yourself (press Start if the app asks) '
            f'and send the code {code}. ' + tail)


def qr_code(link):
    """``link`` as a QR code for a terminal, or "" when segno is missing.

    Each module is painted black or white, not left to the terminal's own
    colours: a light theme would otherwise invert the code.
    """
    try:
        import segno
    except ImportError:
        return ''
    rows = segno.make(link, error='l').matrix_iter(border=2)
    return '\n'.join(''.join('\x1b[40m  ' if dark else '\x1b[47m  ' for dark in row) + '\x1b[0m'
                     for row in rows)


def pair(home, kind, name, person, *, timeout=180, out=print, qr=None):
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
        sys.stdout.flush()  # the connector writes to the same terminal, after what is already said
        if subprocess.run([str(setup)], env=env).returncode != 0:
            raise ValueError('Nothing was enabled; run brnrd again when you have it.')
    code = secrets.token_hex(4)
    # Use the normal drop directory: unrelated letters survive pairing for the
    # loom to ingest once enabled. The connector advances its usual cursor.
    drop = state / 'in'
    drop.mkdir(parents=True, exist_ok=True)
    command = folder / 'in'
    child = subprocess.Popen([str(command), str(drop)], env=env) if command.is_file() else None
    door = _door(folder, env, code)
    picture = qr_code(door['link']) if door and (sys.stdout.isatty() if qr is None else qr) else ''
    out(_instructions(door, code, timeout, scan=bool(picture)))
    if picture:
        out(picture)
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
                                                to=chat, re=fm['id']), 'Paired ✓ This chat now reaches brnrd; write here any time.'))
                    result = subprocess.run([str(folder / 'out'), str(reply)], env=env,
                                            capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    # The heard code is the proof: it came from this chat, so
                    # the channel is enabled either way. Only the answer
                    # failed; say why (the connector's last stderr line is the
                    # server's own reason) and go on.
                    why = (result.stderr.strip().splitlines() or [f'{kind}/out exited {result.returncode}'])[-1]
                    sent = 'was not sent' if result.returncode == 3 else 'may or may not have been sent'
                    out(f"Your code arrived, so this chat is paired, but the 'Paired ✓' answer {sent}: {why}")
                else:
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
        # The code said twice (the start link, then typed) leaves a second
        # copy; once the loom polls this channel it would be a stray letter.
        for path in drop.glob('*.md'):
            if not path.is_symlink() and parse_frontmatter(path.read_text()).get('body', '').strip() in (code, '/start ' + code):
                path.unlink()
                if path.with_suffix('.d').exists():
                    shutil.rmtree(path.with_suffix('.d'))


def first_letter(home, person, *, timeout=600, out=print, running=None, sleep=time.sleep):
    """Wait until a letter from ``person`` is shown to a body: the proof the chat reaches brnrd.

    Returns the strand that was shown it. Pairing proves the bot can talk;
    only a body reading the next letter proves the loom hears that chat.
    """
    from .ledger import read_facts
    from .service import _lock_held
    sender = f'person:{person}'
    before = {f.id for f in read_facts(home) if f.kind == 'letter'}
    running = running or (lambda: _lock_held(home.root))
    told = landed = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        facts = read_facts(home)
        new = {str(f.data.get('id', f.id)) for f in facts
               if f.kind == 'letter' and f.id not in before and f.data.get('from') == sender}
        if new and not landed:
            out(f"  {style.check()} brnrd got your message")
            landed = True
        for fact in facts:
            if fact.kind == 'shown' and new & set(fact.data.get('ids') or ()):
                strand = fact.data.get('strand')
                out(f"  {style.check()} brnrd is reading it now ({strand}); the answer comes in that chat")
                return strand
        if not landed and not told and not running():
            out(f"  {style.dot()} brnrd isn't running here yet: start it with `brnrd loom install`, "
                "and your message will go through.")
            told = True
        sleep(.5)
    if landed:
        raise TimeoutError('Your message reached brnrd, but nothing has started reading it yet; '
                           'see `brnrd loom status`.')
    raise TimeoutError("No message from you reached brnrd; the chat is paired, so once "
                       "`brnrd loom status` shows it running, write again.")


#: What a person picks from, in plain words; a kind not named here shows by its own name.
#: ``fake`` is a test pipe: reachable with ``--kind fake``, never offered.
CHOICES = {
    'telegram': 'Telegram, with a bot you make yourself (two minutes, in the Telegram app)',
    'relay': 'brnrd cloud, with your brnrd.dev login',
}
HIDDEN = {'fake'}


def _step(label):
    print(f"\n{style.bold('→')} {style.bold(label)}")


def _ok(text):
    print(f"  {style.check()} {text}")


def _ask(question, default=''):
    hint = f" {style.dim('(Enter for ' + default + ')')}" if default else ''
    try:
        return input(f"  {style.qmark()} {style.bold(question)}{hint} ").strip() or default
    except EOFError:
        raise ValueError('No answer: input closed; nothing was changed') from None


def _choose_kind(kinds):
    offered = [k for k in CHOICES if k in kinds] + [k for k in kinds if k not in CHOICES and k not in HIDDEN]
    _step('How do you want to talk to brnrd?')
    for number, kind in enumerate(offered, 1):
        print(f"    {number}. {CHOICES.get(kind, kind)}")
    while True:
        answer = _ask(f'Choose 1-{len(offered)}:', '1')
        if answer in kinds:
            return answer
        if answer.isdigit() and 1 <= int(answer) <= len(offered):
            return offered[int(answer) - 1]
        print(f"  {style.dot()} Type a number from the list.")


def _channel_name(home, kind):
    """The channel's name is the kind, numbered when that is taken: nobody should have to name a pipe."""
    taken = set(instances(home))
    return next(n for n in (kind, *(f'{kind}-{i}' for i in range(2, 100))) if n not in taken)


ONBOARDING_WISH = 'brnrd knows the person it works for'


def onboarding_asks(home, person):
    """``core/onboarding.md``'s list, as asks on the home thread: what brnrd
    asks a new person, carried like any ask and answered in chat.

    Each ``- `` line is one ask under one wish, in the file's order. The list
    is the self's to edit; a line already asked is never asked twice. Returns
    the ask ids written. Signed by the person running setup: the list is
    theirs, put in at their own terminal.
    """
    from .ledger import append, read_facts
    from .verbs_wishes import wish_fact
    from .wishes import wishes
    listed = home.root / 'self/core/onboarding.md'
    lines = [line[2:].strip() for line in listed.read_text().splitlines()
             if line.startswith('- ') and line[2:].strip()] if listed.is_file() else []
    if not lines:
        return []
    by, thread = f'person:{person}', home.main_thread()
    facts = read_facts(home)
    found = wishes(facts)
    parent = next((w.id for w in found.values() if (w.thread, w.line) == (thread, ONBOARDING_WISH)), None)
    if parent is None:
        # Nobody said it: a derived wish, the one the asks unblock.
        fact = append(home, wish_fact(facts, by=by, ident='new', line=ONBOARDING_WISH,
                                      thread=thread, sender='person'))
        parent = fact.data['wish']
        facts = read_facts(home)
        found = wishes(facts)
    asked = {w.line for w in found.values() if w.parent == parent and w.sender == 'resident'}
    written = []
    for line in lines:
        if line in asked:
            continue
        fact = append(home, wish_fact(facts, by=by, ident='new', line=line, thread=thread,
                                      sender='resident', parent=parent))
        facts = read_facts(home)
        written.append(fact.data['wish'])
    return written


def _person_id(name):
    """A people/ folder name from the OS login."""
    slug = re.sub(r'[^a-z0-9._-]+', '-', name.strip().lower()).strip('-._')
    return slug or 'me'


def _choose_body(home):
    """The harness brnrd thinks with: every one setup knows, found or not, with its fuel; asked when two are found."""
    from .adapters import body_label, parse_body
    from .service import _body_choice, _defaults, _windows, _write_body
    _step('Which AI should brnrd think with?')
    seed_config(home.root)
    current = _body_choice(home.root)
    floor = load_config(home.root).hold_floor_pct
    found, offered = [], []
    known = _defaults()
    if current and parse_body(current)[0] not in {shell for shell, _core in known}:
        known.append((parse_body(current)[0], ''))
    for shell, core in known:
        if not shutil.which(shell):
            print(f"    {style.dim('·')} {shell}: {style.dim('not installed')}")
            continue
        windows = _windows(home.root, shell)
        if windows is None:
            fuel = style.dim('found, fuel unknown')
        else:
            fuel = ' · '.join(f"{w['name']} {w['left_pct']:g}%" for w in windows) + ' left'
            if any(w['left_pct'] <= floor for w in windows):
                fuel += style.dim(' (low: it waits until this refills)')
        label = current if current and parse_body(current)[0] == shell else body_label(shell, core)
        found.append(label)
        offered.append(windows is not None and not any(w['left_pct'] <= floor for w in windows))
        print(f"    {len(found)}. {shell}: {fuel}")
    if not found:
        # Like the daemon-era runner rung: say it, keep going; pairing does not need it.
        print(f"  {style.cross()} Neither claude nor codex is on PATH: install Claude Code "
              '(https://claude.com/claude-code) or Codex, then run brnrd setup again')
        return
    # Enter keeps the one already set, else the first with fuel.
    pick = next((i for i, label in enumerate(found) if label == current), None)
    if pick is None:
        pick = next((i for i, ok in enumerate(offered) if ok), 0)
    if len(found) > 1:
        while True:
            answer = _ask(f'Choose 1-{len(found)}:', str(pick + 1))
            if answer.isdigit() and 1 <= int(answer) <= len(found):
                break
            print(f"  {style.dot()} Type a number from the list.")
        pick = int(answer) - 1
    choice = found[pick]
    if choice != current:
        _write_body(home.root, choice)
    _ok(f"{choice} (kept)" if choice == current else choice)
    print(f"  {style.dim('Change it any time: brnrd setup, or model.default in loom/preferences.toml.')}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog='brnrd setup')
    parser.add_argument('step', nargs='?', choices=['channel'], default='channel')
    parser.add_argument('--home')
    parser.add_argument('--kind')
    parser.add_argument('--name')
    parser.add_argument('--person')
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--wait', type=float, default=600,
                        help='seconds to wait for your first message to reach brnrd; 0 skips')
    args = parser.parse_args(argv)
    # Setup creates a home; the read-only resolver normally requires one.
    target = args.home or os.environ.get("BRNRD_HOME") or Path.home() / "brnrd"
    home = Home(resolve_home(target)[0])
    bundled = Path(__file__).parent / 'connectors'
    kinds = sorted({p.name for root in (bundled, home.root / 'loom/connectors')
                    for p in root.glob('*') if (p / 'carries').is_file() and (p / 'out').is_file()})
    from brr import __version__
    print(f"{style.bold('brnrd')} {style.dim(__version__)} {style.dim('· guided setup')}")
    print(f"{style.dim('home:')} {style.accent(str(home.root))}")
    try:
        _choose_body(home)
        kind = args.kind or _choose_kind(kinds)
        if not args.kind:
            _ok(CHOICES.get(kind, kind).split(',')[0])
        name = args.name or _channel_name(home, kind)
        people = sorted(p.name for p in (home.root / 'self/people').glob('*') if p.is_dir())
        if args.person or len(people) == 1:
            person = args.person or people[0]
        else:
            # An internal key, never asked: brnrd asks the name itself in its
            # first conversation (core/onboarding.md, seeded as asks).
            import getpass
            person = _person_id(getpass.getuser())
        # A letter lands only on a thread on the self's main: seed it before pairing.
        if init_self(home.root, person=person).startswith('initialized '):
            _ok(IDENTITY_NOTE)
        onboarding_asks(home, person)
        _step(f'Connect {CHOICES.get(kind, kind).split(",")[0]}')
        paired = pair(home, kind, name, person, timeout=args.timeout)
        if paired and args.wait > 0:
            _step('Say hello: send brnrd any message in that chat')
            first_letter(home, person, timeout=args.wait)
    except (ValueError, OSError, TimeoutError, subprocess.SubprocessError, SelfError) as exc:
        raise SystemExit(str(exc)) from None
    except KeyboardInterrupt:
        print()
        raise SystemExit('Stopped; nothing past the last ✓ was changed.') from None
    return 0
