"""The cloud client's wire operations, shared by the two folder executables."""

import json
import os
from pathlib import Path
from urllib.parse import quote
from brr.gates import cloud
from brr.loom.runtime import gates
from brr.loom.runtime.config import load_config
from brr.loom.runtime.home import atomic_write


def settings():
    root = Path(os.environ['BRNRD_CONFIG']).parent.parent
    config = load_config(root)
    return config, Path(os.environ['BRNRD_STATE']), gates.state_dir(root, config)


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def message_id(platform, chat, ident):
    return f'{platform}:{quote(str(chat), safe="")}:{quote(str(ident), safe="")}'


class RelayClient:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)

    def _state(self):
        state = cloud._load_state_from_dir(self.state_dir)
        if not state.get('brnrd_url') or not state.get('token'):
            raise RuntimeError('relay: cloud state has no URL or token')
        return state

    def pull(self, cursor):
        state = self._state()
        return cloud._request(state['brnrd_url'], 'GET', '/v1/daemons/inbox', token=state['token'],
                              params=dict(since=cursor, wait=cloud._POLL_WAIT_S),
                              timeout=cloud._POLL_TIMEOUT_S)

    def send(self, payload):
        state = self._state()
        if payload.get('event_id'):
            payload = dict(payload, status='done')
            path = '/v1/daemons/responses'
        else:
            path = '/v1/daemons/messages'
        return cloud._request(state['brnrd_url'], 'POST', path, token=state['token'], json=payload, retry=False)

    def typing(self, chats):
        state = self._state()
        return cloud._request(state['brnrd_url'], 'POST', '/v1/daemons/loom/typing',
                              token=state['token'], json=chats, retry=False, timeout=5)

    def download_attachment(self, event_id, index, dest):
        state = self._state()
        return cloud._download_attachment(state['brnrd_url'], state['token'], event_id, index, dest)


def typing_targets(root, *, alive=None):
    """Only live rooms owing a person letter. Replies clear before the next loom tick.

    No ledger fold, model, or message body leaves the machine. A dead or
    stopped room drops out even when its last boundary is still on disk.
    """
    from brr.loom.runtime.port import parse_boundary, answered
    if alive is None:
        from brr.loom.runtime.loom import body_alive
        alive = body_alive
    chats = set()
    for path in (Path(root) / 'rooms').glob('*/port/in/boundary.md'):
        room = path.parents[2]
        if not alive(room):
            continue
        try:
            boundary = parse_boundary(path.read_text())
            done = answered(room)
            for letter in boundary.letters:
                if letter.id not in boundary.first_words or letter.id in done:
                    continue
                # Port addresses are cloud/telegram:<chat>[:<topic>]; the
                # existing relay target parser takes cloud:telegram:<chat>.
                target = letter.reply.removeprefix('channel:')
                if target.startswith(('cloud/telegram:', 'relay/telegram:')):
                    chats.add('cloud:telegram:' + target.split('/telegram:', 1)[1])
        except (OSError, ValueError, RuntimeError):
            continue
    return sorted(chats)


def typing_forever(root, client, stop, log, lock):
    """Telegram's typing expires after five seconds; renew every four."""
    while not stop.is_set():
        try:
            chats = typing_targets(root) if lock.held else []
            if chats:
                client.typing(chats[:100])
        except Exception as exc:
            log(f'relay typing: {type(exc).__name__}: {exc}')
        stop.wait(4)
