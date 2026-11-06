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


def call(method, payload, timeout=35, content_type='application/json'):
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    request = Request(f'{base()}/bot{token()}/{method}', data, {'Content-Type': content_type})
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        # Never include the request URL (it contains the credential); the
        # body's description is Telegram's own reason and carries no token.
        why = _description(exc)
        if exc.code in (400, 401, 403, 404):
            raise Refused(f'Telegram refused {method} (HTTP {exc.code}{why})') from None
        if exc.code == 409:
            raise RuntimeError(f'Telegram {method}: HTTP 409{why}: another program is reading '
                               'this bot (an older brnrd or brr on some computer?); stop it') from None
        raise RuntimeError(f'Telegram {method}: HTTP {exc.code}{why}') from None
    except OSError:
        raise RuntimeError(f'Telegram {method}: connection failed') from None
    if not result.get('ok'):
        code = result.get('error_code')
        error = Refused if code in (400, 401, 403, 404) else RuntimeError
        why = f": {result['description']}" if isinstance(result.get('description'), str) else ''
        raise error(f'Telegram refused {method} ({code}{why})')
    return result['result']


def send_document(chat, path, name, reply_to=None, timeout=120):
    """sendDocument as multipart/form-data: the file under its own name."""
    import secrets
    boundary = 'brnrd-' + secrets.token_hex(12)
    fields = dict(chat_id=str(chat))
    if reply_to is not None:
        fields['reply_parameters'] = json.dumps(dict(message_id=reply_to))
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    safe = name.replace('"', "'").replace('\r', ' ').replace('\n', ' ')
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{safe}"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n'.encode())
    body = b''.join(parts) + Path(path).read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    return call('sendDocument', body, timeout, content_type=f'multipart/form-data; boundary={boundary}')


def _description(exc):
    """``": <Telegram's description>"`` from an error body, or ""."""
    try:
        body = json.loads(exc.read() or b'{}')
    except (OSError, ValueError):
        return ''
    text = body.get('description') if isinstance(body, dict) else None
    return f': {text[:200]}' if isinstance(text, str) and text else ''


def download(file_id):
    path = call('getFile', dict(file_id=file_id))['file_path']
    try:
        with urlopen(f'{base()}/file/bot{token()}/{path}', timeout=30) as response:
            return response.read()
    except OSError:
        raise RuntimeError('Telegram attachment download failed') from None


def message_id(chat, ident):
    return f'{chat}:{ident}'


def reply_id(value, chat, quotable=False):
    """A message id in this chat; with quotable, one Telegram never sent is None.

    A letter dropped into the chat's folder by hand (the board's `board-…`)
    carries an id of this chat that no Telegram message has: its reply goes
    out unquoted rather than refused."""
    prefix, colon, ident = value.rpartition(':')
    if not colon or prefix != str(chat) or not (ident.isdecimal() or quotable and ident):
        raise Refused('Telegram message id belongs to another chat or is invalid')
    return int(ident) if ident.isdecimal() else None
