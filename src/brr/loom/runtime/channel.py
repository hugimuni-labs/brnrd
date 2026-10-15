"""Person identity and conversational routing, independent of transport."""

from brr.daemon2.facts import union
from .home import channel_parts
from .project import fold, sender_threads


def _sender(home, platform, user_id):
    # Only a connector's authenticated identity enters the person lookup.
    key = f"{platform}:{user_id}"
    matches = [p.parent.name for p in sorted((home.root / 'self/people').glob('*/channels.md'))
               if user_id not in (None, '') and key in set(map(str.strip, p.read_text().splitlines()))]
    return f'person:{matches[0]}' if len(matches) == 1 else f'stranger:{platform}:{"" if user_id is None else user_id}'


def person_dm(home, channel):
    """Only a unique enrolled person's direct chat may be spoken to."""
    try:
        platform, chat = channel_parts(channel)
    except ValueError:
        return None
    sender = _sender(home, platform, chat)
    return sender if chat is not None and sender.startswith('person:') else None


def _speech(facts, channel, kind):
    """Join trusted receipts to the strand which actually drafted the letter."""
    ordered = union(facts)
    letters = {str(f.data.get('id') or f.id): f for f in fold(ordered).accepted if f.kind == 'letter'}
    threads = sender_threads(ordered)
    for fact in ordered:
        if fact.kind != kind or not fact.by.startswith('loom:'):
            continue
        letter = letters.get(str(fact.data.get('key')))
        if letter is None or not letter.by.startswith('strand:') or letter.data.get('to') != channel:
            continue
        thread = threads.get(letter.by.removeprefix('strand:'))
        if thread:
            yield fact.data, thread


def thread_for_message(facts, channel, message_id):
    """A reply binds only to a loom receipt in the same chat."""
    if message_id not in (None, ''):
        for data, thread in _speech(facts, channel, 'speech.part'):
            if data.get('channel') == channel and str((data.get('receipt') or {}).get('message_id')) == str(message_id):
                return thread
    return None


def route_bare(facts, chat, reply_thread=None):
    """A reply's thread, else the last confirmed speaker, regardless of lease."""
    try:
        _platform, chat_id = channel_parts(f'channel:{chat}')
    except ValueError:
        return 'thread:inbox'
    if chat_id is None:
        return 'thread:inbox'
    if reply_thread:
        return f'thread:{reply_thread}'
    spoken = [thread for data, thread in _speech(facts, f'channel:{chat}', 'speech')
              if data.get('state') == 'sent']
    return f'thread:{spoken[-1]}' if spoken else 'thread:inbox'
