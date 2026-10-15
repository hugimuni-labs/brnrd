"""Bot API wire calls; no thread, person or loom policy lives here."""
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from brr.loom.runtime.config import resolve_secret, SECRET_SOURCES


class Refused(RuntimeError):
    pass


def state():
    return Path(os.environ['BRNRD_STATE'])


def token():
    value = (state() / 'token').read_text().strip()
    return resolve_secret(value) if value.partition(':')[0] in SECRET_SOURCES else value


def base():
    return os.environ.get('BRNRD_TELEGRAM_API', 'https://api.telegram.org').rstrip('/')


def call(method, payload, timeout=35):
    request = Request(f'{base()}/bot{token()}/{method}',
                      json.dumps(payload).encode(), {'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        # Never include the request URL (it contains the credential).
        if exc.code in (400, 401, 403, 404):
            raise Refused(f'Telegram refused {method} (HTTP {exc.code})') from None
        raise RuntimeError(f'Telegram {method}: HTTP {exc.code}') from None
    except OSError:
        raise RuntimeError(f'Telegram {method}: connection failed') from None
    if not result.get('ok'):
        code = result.get('error_code')
        error = Refused if code in (400, 401, 403, 404) else RuntimeError
        raise error(f'Telegram refused {method} ({code})')
    return result['result']


def download(file_id):
    path = call('getFile', dict(file_id=file_id))['file_path']
    try:
        with urlopen(f'{base()}/file/bot{token()}/{path}', timeout=30) as response:
            return response.read()
    except OSError:
        raise RuntimeError('Telegram attachment download failed') from None


def message_id(chat, ident):
    return f'{chat}:{ident}'


def reply_id(value, chat):
    prefix, colon, ident = value.rpartition(':')
    if not colon or prefix != str(chat) or not ident.isdecimal():
        raise Refused('Telegram message id belongs to another chat or is invalid')
    return int(ident)
