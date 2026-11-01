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
from .selfrepo import IDENTITY_NOTE, SelfError, init_self, restore_self


def _door(folder, env, code=None):
    """An optional door's ``{name, link}``: the bot, and a link that sends the code by itself.

    Two optional keys: ``code``, what to type into that bot when it is not
    ours (a shared bot knows only the code its server minted), and ``qr``,
    false where a picture of the link is not offered. With no ``code`` the
    door binds the chat where the connector's ``bound`` reads it back.
    """
    if not (folder / 'door').is_file():
        return None
    try:
        # Its stderr is kept: a broken door falls back to the plain code, never a traceback.
        result = subprocess.run([str(folder / 'door'), *([code] if code else [])], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=30)
        door = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not (isinstance(door, dict) and isinstance(door.get('name'), str)
            and isinstance(door.get('link'), str)):
        return None
    if not isinstance(door.get('code'), str) or not door['code']:
        if not code:
            return None
        door['code'] = code
    return door


def _bound(folder, env):
    """The chats a connector's server already binds to this person, or None when it keeps none.

    A ``bound`` executable prints ``[{"from", "platform", "who"}]``, ``from``
    the sender as ``in`` writes it. Such a chat needs no code: the server
    verified who it is when it was bound.
    """
    if not (folder / 'bound').is_file():
        return None
    result = subprocess.run([str(folder / 'bound')], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=30)
    try:
        rows = json.loads(result.stdout) if result.returncode == 0 else None
    except ValueError:
        rows = None
    if not (isinstance(rows, list) and all(isinstance(r, dict) and isinstance(r.get('from'), str)
                                           and r['from'] for r in rows)):
        why = (result.stderr.strip().splitlines() or [f'bound exited {result.returncode}'])[-1]
        raise ValueError(f'Could not read which chats are bound: {why}')
    return rows


def _enroll(home, kind, name, person, keys):
    """Save ``keys`` as ``person``'s and enable the channel; the config is written last."""
    channels = home.root / 'self/people' / person / 'channels.toml'
    previous = read_array(channels, 'channels')
    added = [key for key in keys if key not in previous]
    if added:
        atomic_write(channels, 'channels = ' + json.dumps([*previous, *added]) + '\n')
    # Commit the config last: a failed pairing never enables input.
    seed_config(home.root)
    enabled = load_config(home.root).connectors
    if f'{name}={kind}' not in enabled:  # a door bound again is still one connector
        set_value(home.root, 'connectors', [*enabled, f'{name}={kind}'])


#: Telegram's reason for not writing (its error description, lowercased) → what the person does.
NOT_SENT = {
    'blocked by the user': "the bot is blocked in your Telegram: open its chat and press Restart (or Unblock)",
    "can't initiate conversation": "open the bot's chat in Telegram and press Start, so it may write to you",
    'user is deactivated': 'that Telegram account is deactivated; pair from the account you use',
}


def _what_to_do(why, bot):
    """One sentence on making the bot able to write, from Telegram's own reason."""
    low = why.lower()
    for said, do in NOT_SENT.items():
        if said in low:
            return do
    if '403' in why:
        return (f"Telegram would not let {bot or 'the bot'} write to you: open its chat, press Start "
                "(Restart if you stopped it), then write to it once more")
    return ''


#: How a platform reads in a sentence.
PLATFORMS = {'telegram': 'Telegram', 'whatsapp': 'WhatsApp', 'web': 'In your browser'}


def _bind(home, kind, name, person, folder, env, chats, *, timeout, out, qr, links):
    """The account path: a chat already bound is used as it is; else a link binds one, read back.

    No code reaches the loom here: the server binds the chat on the
    person's tap and ``bound`` lists it. The first letter is the proof.
    The account's own web chat (platform ``loom``) is the approver's and
    is saved too, but it is not a chat to write from a phone: the link is
    still offered when it is the only one.
    """
    web = [chat for chat in chats if chat.get('platform') == 'loom']
    chats = [chat for chat in chats if chat.get('platform') != 'loom']
    if not chats:
        door = _door(folder, env)
        if door is None:
            raise ValueError('brnrd.dev gave no link to bind a chat; nothing was enabled. Run brnrd setup again.')
        shown = door.get('qr', True) is not False
        picture = qr_code(door['link']) if shown and (sys.stdout.isatty() if qr is None else qr) else ''
        also = ", or scan the code below with your phone's camera" if picture else ''
        # The same code binds on either app: the link fills it in, typed it is the whole message.
        whatsapp = (f"open {door['whatsapp']} and send the text it fills in" if door.get('whatsapp') else
                    f"text {door['code']} by itself to brnrd's WhatsApp number")
        out(f"No chat is bound to your brnrd account yet. Bind one, or both:\n"
            f"  Telegram: open {door['link']} where you use the app and press Start{also}, "
            f"or open {door['name']} yourself and send the code {door['code']}.\n"
            f"  WhatsApp: {whatsapp}.\n"
            f"Waiting up to {timeout:g}s.")
        if picture:
            out(picture)
        deadline = time.monotonic() + timeout
        while not chats:
            if time.monotonic() >= deadline:
                raise TimeoutError('No chat was bound in time; channel was not enabled')
            time.sleep(BOUND_POLL_S)
            chats = [chat for chat in _bound(folder, env) if chat.get('platform') != 'loom']
    keys = [f"{name}:{chat['from']}" for chat in [*chats, *web]]
    _enroll(home, kind, name, person, keys)
    for chat in web:
        out(f"your brnrd.dev chat ({chat.get('who') or chat['from']}) is saved for {person}")
    for chat in chats:
        if chat.get('platform') == 'web':
            out(f"your page is {chat['link']}; saved it for {person}")
            continue
        where = PLATFORMS.get(chat.get('platform'), chat.get('platform') or kind)
        out(f"your {where} chat is bound to your brnrd account (as {chat.get('who') or chat['from']}); "
            f"saved it for {person}")
    for chat in [*chats, *web]:
        if chat.get('link'):
            links.append((PLATFORMS.get(chat.get('platform'), 'brnrd.dev' if chat.get('platform') == 'loom'
                                        else chat.get('platform') or kind), chat['link']))
    return keys[0]


#: How often the unbound path asks the server whether the tap has bound a chat.
BOUND_POLL_S = 2.0


def _instructions(door, code, timeout, scan=False):
    """Say which way pairing runs: the person writes first; nothing arrives before."""
    tail = (f"The bot does not write first; it answers 'Paired ✓'. "
            f'Waiting up to {timeout:g}s.')
    if door:
        also = ", or scan the code below with your phone's camera" if scan else ''
        return (f"Open {door['link']} where you use the app and press Start{also}: that sends "
                f"your code. Or open {door['name']} yourself and send the code {door['code']}. " + tail)
    return (f'Open the chat with your bot yourself (press Start if the app asks) '
            f'and send the code {code}. ' + tail)


def _answer(folder, env, state, chat, answers, ident, text):
    """Say ``text`` in ``chat`` through the connector's out; its completed process."""
    with tempfile.TemporaryDirectory(prefix='pair-', dir=state) as directory:
        reply = Path(directory) / 'reply.md'
        reply.write_text(_front(dict(kind='letter', id=ident, to=chat, re=answers), text))
        return subprocess.run([str(folder / 'out'), str(reply)], env=env,
                              capture_output=True, text=True, timeout=60)


#: Letters a person reads off a screen and types without a second look: no 0/O, 1/I/L.
ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'


def pairing_code():
    """A code to type, never hex: six such letters, ``K7Q-M9X``."""
    picked = ''.join(secrets.choice(ALPHABET) for _ in range(6))
    return f'{picked[:3]}-{picked[3:]}'


def _says_code(body, code):
    """Is ``body`` the code, typed or sent by Start? Case, spaces and dashes are forgiven."""
    plain = re.sub(r'[^A-Z0-9]', '', body.upper())
    want = re.sub(r'[^A-Z0-9]', '', code.upper())
    return plain in (want, 'START' + want)


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


def pair(home, kind, name, person, *, timeout=180, out=print, qr=None, env=None, links=None):
    """Pair ``name`` and enrol it for ``person``; returns the chat's key.

    ``links`` gathers ``(where, link)`` for each chat now enabled, for
    setup's last step to show.
    """
    links = [] if links is None else links
    if not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', v) for v in (name, person)):
        raise ValueError('Channel and person names use letters, digits, dots, dashes or underscores')
    if instances(home).get(name, kind) != kind:
        raise ValueError(f'Channel name {name!r} is already in use')
    folder = resolve(home, kind, 'connectors')
    if not (folder / 'out').is_file():
        raise ValueError(f'{kind} is an input pipe, not a messaging channel')
    extra, env = env or {}, environment(home, name)
    env.update(extra)
    state = Path(env['BRNRD_STATE'])
    setup = folder / 'setup'
    if setup.is_file():
        sys.stdout.flush()  # the connector writes to the same terminal, after what is already said
        if subprocess.run([str(setup)], env=env).returncode != 0:
            raise ValueError('Nothing was enabled; run brnrd again when you have it.')
    chats = _bound(folder, env)
    if chats is not None:
        return _bind(home, kind, name, person, folder, env, chats, timeout=timeout, out=out, qr=qr, links=links)
    code = pairing_code()
    # Use the normal drop directory: unrelated letters survive pairing for the
    # loom to ingest once enabled. The connector advances its usual cursor.
    drop = state / 'in'
    drop.mkdir(parents=True, exist_ok=True)
    command = folder / 'in'
    child = subprocess.Popen([str(command), str(drop)], env=env) if command.is_file() else None

    def stop():
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    door = _door(folder, env, code)
    shown = door and door.get('qr', True) is not False
    picture = qr_code(door['link']) if shown and (sys.stdout.isatty() if qr is None else qr) else ''
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
                body = fm.get('body', '').strip()
                address, chat = channel_parts(fm.get('to', ''))
                direct = address == name and fm.get('from', '') == f'{name}:{chat}'
                if body == '/start' and direct and fm.get('id'):
                    # A plain Start pairs nothing: several homes may be
                    # waiting at once, and only the code says which one.
                    # Ask for it in the chat, the next step there.
                    asked = _answer(folder, env, state, chat, fm['id'], 'ask-' + code,
                                    'Send me the code your terminal shows, and this chat is paired.')
                    path.unlink()
                    if path.with_suffix('.d').exists():
                        shutil.rmtree(path.with_suffix('.d'))
                    out(f"Start was pressed without the code; send {door['code'] if door else code} in that chat."
                        + ('' if asked.returncode == 0 else ' (The chat could not be told.)'))
                    continue
                if not _says_code(body, code):
                    continue
                if not direct:
                    out('Pairing needs a direct message from your own account; ignored this message.')
                    continue
                result = _answer(folder, env, state, chat, fm['id'], 'pair-' + code,
                                 'Paired ✓ This chat now reaches brnrd; write here any time.')
                if result.returncode != 0:
                    # The heard code is the proof: it came from this chat, so
                    # the channel is enabled either way. Only the answer
                    # failed; say why (the connector's last stderr line is the
                    # server's own reason) and go on.
                    why = (result.stderr.strip().splitlines() or [f'{kind}/out exited {result.returncode}'])[-1]
                    sent = 'was not sent' if result.returncode == 3 else 'may or may not have been sent'
                    do = _what_to_do(why, door and door['name'])
                    out(f"Your code arrived, so this chat is paired, but the 'Paired ✓' answer {sent}: {why}"
                        + (f". To fix it, {do}." if do else ''))
                else:
                    # A zero exit is a receipt only with the same output grammar.
                    receipt = json.loads(result.stdout)
                    if (not isinstance(receipt, dict) or not isinstance(receipt.get('ids'), list) or
                            any(not isinstance(i, (str, int)) or isinstance(i, bool) for i in receipt['ids'])):
                        raise ValueError('Pairing out returned no ids receipt')
                path.unlink()
                if path.with_suffix('.d').exists():
                    shutil.rmtree(path.with_suffix('.d'))
                key = f'{name}:{chat}'
                # Stop reading the bot first: once enabled, the loom's own in
                # reads it, and two readers make Telegram refuse one (409).
                stop()
                _enroll(home, kind, name, person, [key])
                if door:
                    links.append((PLATFORMS.get(kind, kind), door['link'].partition('?')[0]))
                out(f'heard you as {chat}; saved {key} for {person}')
                return key
            if child is not None and child.poll() is not None:
                raise ValueError(f'{kind}/in exited before hearing the code')
            time.sleep(.05)
        raise TimeoutError('No matching direct message arrived; channel was not enabled')
    finally:
        stop()
        # The code said twice (the start link, then typed) leaves a second
        # copy; once the loom polls this channel it would be a stray letter.
        for path in drop.glob('*.md'):
            if not path.is_symlink() and _says_code(parse_frontmatter(path.read_text()).get('body', ''), code):
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
    'relay': 'brnrd cloud: Telegram or WhatsApp through brnrd\'s own bot, and a chat on brnrd.dev',
    'telegram': 'Your own Telegram bot (two minutes, in the Telegram app)',
    'web': 'A page on this computer, in your browser (nothing to sign in to)',
}
#: What a first setup starts with ticked: brnrd cloud, and the page, which needs nothing.
START = ('relay', 'web')
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


def door_states(home):
    """``{name: (kind, live, what)}`` for every enabled connector, read from the connector, not the config.

    ``live`` is True when it still reaches the person (the account still
    binds a chat; the bot still answers), False when it went dark (a chat
    unbound on brnrd.dev, a revoked token), None when it cannot say.
    """
    found = {}
    for name, kind in instances(home).items():
        try:
            folder = resolve(home, kind, 'connectors')
        except (ValueError, OSError):
            found[name] = (kind, False, 'its connector is not installed')
            continue
        env = environment(home, name)
        if (folder / 'bound').is_file():
            try:
                chats = [c for c in _bound(folder, env) if c.get('platform') != 'loom']
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                found[name] = (kind, None, str(exc))
                continue
            where = ', '.join(sorted({PLATFORMS.get(c.get('platform'), c.get('platform') or '?') for c in chats}))
            said = (f"at {chats[0]['link']}" if chats and chats[0].get('platform') == 'web' else
                    f'{where} bound' if chats else 'no chat bound any more')
            found[name] = (kind, bool(chats), said)
        elif (folder / 'door').is_file():
            door = _door(folder, env, 'status')
            found[name] = (kind, door is not None,
                           f"{door['name']} answers" if door else 'the bot does not answer (token revoked?)')
        else:
            found[name] = (kind, None, '')
    return found


def _choose_doors(home, kinds):
    """Which doors to connect, one or several: brnrd needs at least one to reach the person.

    A first setup starts on brnrd cloud and the page on this computer. Run again, a door that
    still reaches the person starts off (it needs nothing), and one that
    went dark starts on, so Enter mends it.
    """
    offered = [k for k in CHOICES if k in kinds] + [k for k in kinds if k not in CHOICES and k not in HIDDEN]
    states = {}
    for _name, (kind, live, what) in door_states(home).items():
        states.setdefault(kind, (live, what))
    rows = []
    for kind in offered:
        live, what = states.get(kind, (None, ''))
        note = '' if kind not in states else style.dim(' · connected' if live else f' · {what or "not reaching you"}')
        rows.append((kind, CHOICES.get(kind, kind) + note, ''))
    start = {k for k in offered if k in states and states[k][0] is not True}
    if not states:
        start = {k for k in START if k in offered} or {offered[0]}
    _step('How should brnrd reach you? Pick one or more')
    while True:
        picked = checklist(rows, start=start)
        if picked or any(live for live, _ in states.values()):
            return [k for k in offered if k in picked]
        print(f"  {style.cross()} brnrd needs at least one way to reach you; switch one on.")


def _label(kind):
    return CHOICES.get(kind, kind).split(':')[0].split(' (')[0].split(',')[0]


def _walk_doors(home, doors, kinds, connect):
    """Connect each ticked door in turn; ``(paired, missed)``. Any one can be skipped, none is a dead end.

    Ctrl-C at a door asks once: go on, back to the list, or stop. A door that
    fails or is skipped is missed; the others still reach the person. The page
    on this computer comes last and needs nothing, so with it ticked setup
    always ends connected; switched off and nothing else connected, the list
    comes back once, with one line, then setup stops.
    """
    paired, done, tried, again = [], set(), [], True
    queue = sorted(doors, key=lambda k: k == 'web')
    while queue or (not done and again and 'web' in kinds and 'web' not in tried):
        if not queue:
            again = False
            print(f"  {style.dot()} No door connected yet; pick again (the page on this computer needs nothing).")
            queue = sorted(_choose_doors(home, kinds), key=lambda k: k == 'web')
            continue
        kind = queue.pop(0)
        tried.append(kind)
        _step(f"Connect {_label(kind)}")
        print(f"  {style.dim('Ctrl-C skips this one')}")
        try:
            paired.append(connect(kind))
            done.add(kind)
        except (ValueError, TimeoutError) as exc:
            print(f"  {style.cross()} {exc}")
        except KeyboardInterrupt:
            print()
            answer = _ask('Skipped. Enter goes on, b goes back to the list, q stops setup:').lower()
            if answer.startswith('q'):
                raise
            if answer.startswith('b'):
                queue = sorted((k for k in _choose_doors(home, kinds) if k not in done), key=lambda k: k == 'web')
    if not done:
        raise ValueError('No door was connected, so brnrd cannot reach you yet; run brnrd setup again.')
    missed = [k for k in dict.fromkeys(tried) if k not in done]
    if done == {'web'} and missed:
        print(f"  {style.dot()} brnrd reaches you on the page for now; add more: brnrd setup channel")
    return paired, missed


def _channel_name(home, kind):
    """The channel's name: the one this kind already has (a door bound again), else the kind, numbered when taken."""
    taken = instances(home)
    mine = next((name for name, k in taken.items() if k == kind), None)
    return mine or next(n for n in (kind, *(f'{kind}-{i}' for i in range(2, 100))) if n not in taken)


SELF_REPO = 'brnrd-self'
APP_URL = 'https://github.com/apps/brnrd-dev/installations/new'
#: What brnrd asks later for an offer setup left off, in the person's chat; one line each, never twice.
LATER = {
    'repo': "Back up my memory to a private GitHub repo? (setup left it off)",
    'app': "Install the brnrd-dev GitHub App, so brnrd.dev can reach your repos? (setup left it off)",
}


def _gh_login():
    """``(login, '')`` for the account gh is signed in as here, else ``(None, why not)``."""
    from brr import home_link
    if not home_link.gh_available():
        return None, 'needs the GitHub CLI (gh): https://cli.github.com'
    login = home_link.detect_identity()
    return (login, '') if login else (None, 'needs gh signed in: run gh auth login, then brnrd setup channel')


def offers(home, kind, login=None, why='', repo=SELF_REPO):
    """What setup switches on unless the person says no: ``(key, label, why not)`` rows.

    A row with a reason cannot be picked and starts off. The repo row goes once
    the self has an origin; the App row only on a cloud path
    that still signs in, where the approve page is still ahead.
    """
    from . import gates
    from .selfrepo import git
    rows = []
    if not git(home.root / 'self', 'remote', 'get-url', 'origin', check=False).stdout.strip():
        where = f' ({login}/{repo})' if login else ''
        rows.append(('repo', f"Back up brnrd's memory to a private GitHub repo{where}", why))
    if kind == 'relay' and gates.state_dir(home.root, load_config(home.root)) is None:
        rows.append(('app', 'Let brnrd.dev reach your GitHub repos (the brnrd-dev GitHub App)', ''))
    return rows


def _row(row, on, at=None, number=None):
    _, label, why = row
    box = style.dim('[ ]') if why else (f"[{style.check()}]" if on else '[ ]')
    lead = f"{number}." if number is not None else (style.accent('›') if at else ' ')
    return f"    {lead} {box} {style.dim(label) if why else label}" + (f" {style.dim('· ' + why)}" if why else '')


def checklist(rows, *, keys=None, start=None):
    """Every pickable row starts on, or those in ``start``: Enter confirms, arrows move, space switches one.

    ``keys`` stands in for a terminal (tests); without a terminal it reads one
    line instead: Enter keeps them, numbers switch those. Returns the keys on.
    """
    on = [not why and (start is None or key in start) for key, _, why in rows]
    if not rows:
        return set()
    tty = keys is not None or sys.stdin.isatty()
    if all(why for _, _, why in rows):  # nothing to pick: say why, ask nothing
        for row in rows:
            print(_row(row, False))
        return set()
    if not tty:
        for i, row in enumerate(rows):
            print(_row(row, on[i], number=i + 1))
        for token in _ask('Enter to confirm, or the numbers to switch:').replace(',', ' ').split():
            if token.isdigit() and 1 <= int(token) <= len(rows) and not rows[int(token) - 1][2]:
                on[int(token) - 1] = not on[int(token) - 1]
        return {row[0] for row, picked in zip(rows, on) if picked}
    print(f"  {style.dim('Enter confirms · ↑ ↓ move · space switches one off or on')}")
    at = 0

    def draw(again):
        if again:
            sys.stdout.write(f"\x1b[{len(rows)}A")
        for i, row in enumerate(rows):
            sys.stdout.write("\x1b[2K" + _row(row, on[i], at=i == at) + "\n")
        sys.stdout.flush()

    draw(False)
    restore = None
    if keys is None:
        import termios
        import tty as tty_mode
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        restore = lambda: termios.tcsetattr(fd, termios.TCSADRAIN, saved)  # noqa: E731
        tty_mode.setcbreak(fd)
        keys = iter(lambda: os.read(fd, 3).decode(errors='ignore'), None)
    try:
        for key in keys:
            if key in ('\r', '\n', ''):
                break
            if key in ('\x1b[A', 'k'):
                at = (at - 1) % len(rows)
            elif key in ('\x1b[B', 'j'):
                at = (at + 1) % len(rows)
            elif key == ' ' and not rows[at][2]:
                on[at] = not on[at]
            draw(True)
    finally:
        if restore:
            restore()
    return {row[0] for row, picked in zip(rows, on) if picked}


def _gh_git(*args):
    """``git`` with gh's credential and no prompt: a repo the person can read, never a password asked."""
    return subprocess.run(['git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
                           *args], capture_output=True, text=True, timeout=60,
                          env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})


def _repo_url(login, name):
    return f'https://github.com/{login}/{name}.git'


def _holds_a_self(url):
    """Has ``url`` a ``main``? An empty repo is adoptable; one with history is a self already."""
    found = _gh_git('ls-remote', '--heads', url, 'main')
    if found.returncode != 0:
        raise ValueError(f"could not read {url.removesuffix('.git')}: "
                         + (found.stderr.strip().splitlines() or ['git ls-remote failed'])[-1])
    return bool(found.stdout.strip())


def backup_repo(home, login, *, ask=None):
    """The backup repo's name, after offering to restore brnrd's memory from an earlier install's.

    A private ``login/brnrd-self`` with history is that memory, and it is
    the person's: restoring is the default. Declined, setup never seeds
    over it and picks the next free name (``brnrd-self-2``, ...), so the
    mirror never meets a history it diverged from. A home with a self
    already, or no gh login, asks nothing.
    """
    from brr import home_link
    if (home.root / 'self/.git').exists() or not login:
        return SELF_REPO
    ask = ask or _ask
    asked = False
    for name in (SELF_REPO, *(f'{SELF_REPO}-{i}' for i in range(2, 20))):
        found = home_link._repo_view(login, name)
        if not found:
            return name
        if str(found.get('visibility', '')).upper() != 'PRIVATE':
            continue  # never brnrd's memory, and never pushed to
        url = _repo_url(login, name)
        try:
            holds = _holds_a_self(url)
        except ValueError as exc:
            # Unread is not empty: a new name is safe, a push over a memory is not.
            print(f"  {style.dot()} {exc}; this install backs up to a new repo")
            continue
        if not holds:
            return name  # private and empty: adopted as it is
        if not asked:
            asked = True
            _step('brnrd remembers you')
            print(f"  {style.dot()} {login}/{name} holds brnrd's memory from an earlier install.")
            if ask('Restore it here? (yes/no)', 'yes').lower() in ('y', 'yes'):
                sha = restore_self(home.root, url)
                _ok(f"memory restored from {login}/{name} ({sha})")
                return name
            print(f"  {style.dot()} kept as it is; this install backs up to a new repo")
    raise ValueError(f'{login} has {SELF_REPO} up to -19 taken; back up by hand: git remote add origin <url>')


def back_up_self(home, login, repo=SELF_REPO):
    """A private ``login/<repo>`` as the self's origin, pushed once; the loom's mirror keeps it current.

    An existing repo of that name is adopted only when GitHub calls it private
    (the self holds people's pages) and it is empty: one with history is
    another install's memory, never pushed over.
    """
    from brr import home_link
    from .selfrepo import git
    found = home_link._repo_view(login, repo)
    url = _repo_url(login, repo)
    if found and str(found.get('visibility', '')).upper() != 'PRIVATE':
        raise ValueError(f'{login}/{repo} exists and is not private; brnrd never pushes its memory there')
    if found and _holds_a_self(url):
        raise ValueError(f'{login}/{repo} already holds a self; brnrd never pushes over it')
    if not found:
        home_link._repo_create(login, repo)
    git(home.root / 'self', 'remote', 'add', 'origin', url)
    pushed = git(home.root / 'self', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
                 'push', '-u', 'origin', 'main', check=False)
    return url, pushed.returncode == 0


def _take_offers(home, picked, rows, login, repo=SELF_REPO):
    """Do the picked offers that need no page; say what each did. Returns the keys left off."""
    for key, label, _why in rows:
        if key not in picked:
            continue
        if key == 'repo':
            try:
                url, pushed = back_up_self(home, login, repo)
            except (ValueError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
                print(f"  {style.cross()} backup not set up: {exc}")
                picked = picked - {'repo'}
                continue
            _ok(f"memory backs up to {url.removesuffix('.git')}" + ('' if pushed else ' (first push waits for the loom)'))
        elif key == 'app':
            _ok(f"install the GitHub App: {APP_URL}")
            if sys.stdout.isatty():
                import webbrowser
                webbrowser.open(APP_URL)
    return [key for key, _, _ in rows if key not in picked]


ONBOARDING_WISH = 'brnrd knows the person it works for'


def onboarding_asks(home, also=()):
    """``core/onboarding.md``'s list, as asks on the home thread: what brnrd
    asks a new person, carried like any ask and answered in chat.

    Each ``- `` line is one ask under one wish, in the file's order. The list
    is the self's to edit; ``also`` adds lines after it (what setup left
    off). A line already asked is never asked twice. Returns
    the ask ids written. The loom writes them as the resident's own: the
    person said nothing yet, and a person's word is only a letter they sent.
    """
    from .ledger import append, read_facts
    from .verbs_wishes import wish_fact
    from .wishes import wishes
    listed = home.root / 'self/core/onboarding.md'
    lines = [line[2:].strip() for line in listed.read_text().splitlines()
             if line.startswith('- ') and line[2:].strip()] if listed.is_file() else []
    lines += [line for line in also if line not in lines]
    if not lines:
        return []
    by, thread = f'loom:{home.install_id()}', home.main_thread()
    facts = read_facts(home)
    found = wishes(facts)
    parent = next((w.id for w in found.values() if (w.thread, w.line) == (thread, ONBOARDING_WISH)), None)
    if parent is None:
        # The resident's own wish, the one the asks unblock: nobody said it,
        # and it unblocks nothing above it (wish_fact would ask for a parent).
        from brr.daemon2.facts import Fact
        from .home import mint
        parent = 'w-' + mint(12)
        append(home, Fact(kind='wish', by=by, id=parent, data={
            'wish': parent, 'says': [], 'from': 'resident', 'line': ONBOARDING_WISH, 'thread': thread}))
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


SLOW = object()
FUEL_TIMEOUT = 3.0


def _read_fuel(root, shells, read, timeout=FUEL_TIMEOUT):
    """Every shell's fuel read at once, ``{shell: windows | None | SLOW}``:
    the menu waits for the slowest up to ``timeout``, never their sum. A
    reader still out is SLOW; its daemon thread never holds setup's exit."""
    import threading
    found, threads = {}, []
    for shell in shells:
        def one(shell=shell):
            try:
                found[shell] = read(root, shell)
            except Exception:
                found[shell] = None
        thread = threading.Thread(target=one, daemon=True)
        thread.start()
        threads.append(thread)
    end = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, end - time.monotonic()))
    return {shell: found.get(shell, SLOW) for shell in shells}


def _choose_body(home):
    """The harnesses brnrd thinks with, best first: every one setup knows, found or not, with its fuel.

    Found ones are numbered in the person's order (``bodies.order``), else
    ``bodies/order``'s. Enter keeps it; typed numbers are the new order, and
    one left out is switched off. The first is ``model.default``; the loom
    falls down the list when it is missing or out of fuel.
    """
    from .adapters import _preferences, body_label, body_order, default_order, parse_body, set_order
    from .service import _body_choice, _defaults, _windows, _write_body
    _step('Which AI should brnrd think with? Best first')
    seed_config(home.root)
    current = _body_choice(home.root)
    floor = load_config(home.root).hold_floor_pct
    ranked = [shell for shell, _core in _defaults()]
    chosen = _preferences(home)[1].get("bodies.order")
    mine = [s for s in body_order(home) if s in ranked] if chosen else []
    known = [*mine, *(s for s in ranked if s not in mine)]
    if current and parse_body(current)[0] not in known:
        known.append(parse_body(current)[0])
    off = {s for s in known if chosen and s in ranked and s not in mine}
    if current and parse_body(current)[0] in known and parse_body(current)[0] not in off:
        known.remove(parse_body(current)[0])
        known.insert(0, parse_body(current)[0])
    installed = [shell for shell in known if shutil.which(shell)]
    if installed:
        print(f"    {style.dim('· checking ' + ', '.join(installed) + ' …')}", flush=True)
    readings = _read_fuel(home.root, installed, _windows)
    found, fueled = [], []
    for shell in known:
        if not shutil.which(shell):
            print(f"    {style.dim('·')} {shell}: {style.dim('not installed')}")
            continue
        windows = readings.get(shell)
        if windows is SLOW:
            fuel, windows = style.dim("found, couldn't check its fuel"), None
        elif windows is None:
            fuel = style.dim('found, fuel unknown')
        else:
            fuel = ' · '.join(f"{w['name']} {w['left_pct']:g}%" for w in windows) + ' left'
            if any(w['left_pct'] <= floor for w in windows):
                fuel += style.dim(' (low: it waits until this refills)')
        found.append(shell)
        fueled.append(windows is not None and not any(w['left_pct'] <= floor for w in windows))
        print(f"    {len(found)}. {shell}: {fuel}" + (style.dim(' (off)') if shell in off else ''))
    if not found:
        # Like the daemon-era runner rung: say it, keep going; pairing does not need it.
        print(f"  {style.cross()} No AI harness is on PATH: install Claude Code "
              '(https://claude.com/claude-code) or Codex, then run brnrd setup again')
        return
    # Enter keeps the order, the one already set first, else the first with fuel; the ones off stay off.
    keep = [i for i, shell in enumerate(found) if shell not in off]
    if not current or parse_body(current)[0] not in found:
        first = next((i for i in keep if fueled[i]), keep[0] if keep else 0)
        keep = [first, *(i for i in keep if i != first)]
    picked = keep or [0]
    if len(found) > 1:
        while True:
            answer = _ask('Enter keeps this order; or type the numbers best first (one left out is off):',
                          ' '.join(str(i + 1) for i in picked))
            tokens = answer.replace(',', ' ').split()
            if tokens and all(t.isdigit() and 1 <= int(t) <= len(found) for t in tokens):
                break
            print(f"  {style.dot()} Type numbers from the list, e.g. 2 1.")
        picked = list(dict.fromkeys(int(t) - 1 for t in tokens))
    order = [found[i] for i in picked]
    head = order[0]
    choice = current if current and parse_body(current)[0] == head else body_label(head, '')
    if choice != current:
        _write_body(home.root, choice)
    # Ranked harnesses not installed here stay on the list, after the found ones: installed later, they just work.
    rest = [s for s in default_order() if s not in found and s not in off]
    set_order(home, [s for s in [*order, *rest] if s in ranked] or [head])
    _ok(f"{choice} (kept)" if choice == current else choice)
    if len(order) > 1:
        print(f"  {style.dim('then ' + ', '.join(order[1:]) + ' when ' + head + ' is out of fuel or missing')}")
    print(f"  {style.dim('Change it any time: brnrd setup, or bodies.order in loom/preferences.toml.')}")


def _status(home):
    """What a set-up home has, said without asking anything; None when it has no channel yet."""
    from .service import _body_choice, _lock_held
    config = load_config(home.root)
    if not config.connectors:
        return None
    _step('brnrd is set up here')
    try:
        body = _body_choice(home.root)
    except (ValueError, OSError):
        body = None
    if body:
        _ok(f"thinks with {body}")
    else:
        print(f"  {style.cross()} no AI chosen yet: run brnrd setup channel")
    dark = False
    for name, (_kind, live, what) in door_states(home).items():
        if live is False:
            dark = True
            print(f"  {style.cross()} talks over {name}, but {what}: brnrd setup channel connects it again")
        else:
            _ok(f"talks over {name}" + (f" ({what})" if what else ''))
    if _lock_held(home.root):
        _ok('running')
    else:
        print(f"  {style.dot()} not running: start it with `brnrd loom install`")
    if not dark:
        print(f"  {style.dim('Add or mend a door: brnrd setup channel. Change the AI: brnrd setup channel.')}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='brnrd setup')
    parser.add_argument('step', nargs='?', choices=['channel'])
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
    if args.step is None and not args.kind and _status(home) is not None:
        return 0
    try:
        _choose_body(home)
        doors = [args.kind] if args.kind else _choose_doors(home, kinds)
        if not doors:
            _ok('every door still reaches you; nothing to connect')
            return 0
        if not args.kind:
            for kind in doors:
                _ok(_label(kind))
        login, why = _gh_login()
        # Before any seed: an earlier install's memory is restored, never seeded over.
        repo = backup_repo(home, login)
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
        rows = offers(home, 'relay' if 'relay' in doors else doors[0], login, why, repo)
        picked = set()
        if rows:
            _step('brnrd will also')
            picked = checklist(rows)
        left = _take_offers(home, picked, rows, login, repo)
        onboarding_asks(home, also=[LATER[key] for key in left])
        links = []

        def connect(kind):
            return pair(home, kind, args.name or _channel_name(home, kind), person, timeout=args.timeout, links=links)
        if args.kind:  # one door named on the command line: it connects or setup says why
            _step(f"Connect {_label(args.kind)}")
            paired, missed = [connect(args.kind)], []
        else:
            paired, missed = _walk_doors(home, doors, kinds, connect)
        if missed:
            print(f"  {style.dot()} brnrd setup channel connects the rest later.")
        if any(paired) and args.wait > 0:
            _step('Say hello: send brnrd any message in ' + ('one of your chats' if len(links) > 1 else 'that chat'))
            for where, link in links:
                print(f"    {where}: {style.accent(link)}")
            first_letter(home, person, timeout=args.wait)
    except (ValueError, OSError, TimeoutError, subprocess.SubprocessError, SelfError) as exc:
        raise SystemExit(str(exc)) from None
    except KeyboardInterrupt:
        print()
        raise SystemExit('Stopped; nothing past the last ✓ was changed.') from None
    return 0
