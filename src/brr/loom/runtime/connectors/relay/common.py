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
                              params=dict(since=cursor, wait=cloud._POLL_WAIT_S))

    def send(self, payload):
        state = self._state()
        if payload.get('event_id'):
            payload = dict(payload, status='done')
            path = '/v1/daemons/responses'
        else:
            path = '/v1/daemons/messages'
        return cloud._request(state['brnrd_url'], 'POST', path, token=state['token'], json=payload, retry=False)

    def download_attachment(self, event_id, index, dest):
        state = self._state()
        return cloud._download_attachment(state['brnrd_url'], state['token'], event_id, index, dest)
