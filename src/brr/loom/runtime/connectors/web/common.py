"""The board page on this computer as a door: its port, its link, its one chat."""
import os
from pathlib import Path

from brr.loom.runtime.config import load_config

#: The chat the board's composer writes as (``Parts.send``); one page, one person, so one id.
USER = 'local'
#: The port tried first, so the page keeps one address across setups; a taken one gets a free port.
PORT = 4777


def home():
    return Path(os.environ['BRNRD_CONFIG']).parent.parent


def link(port):
    return f'http://127.0.0.1:{port}'


def port():
    """The ``board_port`` the loom serves the page on, or None while it is off."""
    value = load_config(home()).board_port
    return int(value) if value and str(value).isdigit() else None
