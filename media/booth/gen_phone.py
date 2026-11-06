"""Props for the phone shots (booth-gen's contract): cut.py calls props(key) per slot.

The shots draw from src/gen/chat/script.json, which holds every on-screen line with its letter id and time
from the ledger (receipts.md, section chat), so a slot needs nothing here beyond its key and seconds."""


def props(key: str) -> dict:
    return {}
