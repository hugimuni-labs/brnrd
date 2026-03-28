"""Telegram connector utilities.

These functions handle authentication and connection to Telegram.  The
implementation here is a placeholder; it should be replaced with code
that calls the Telegram Bot API and stores tokens and chat IDs in
`.brr.local/telegram.json`.
"""

from __future__ import annotations

import json
from pathlib import Path


def auth() -> None:
    """Guide the user through Telegram bot authentication.

    This stub prints instructions to the console.  A real implementation
    would prompt for a token, validate it and save it to
    `.brr.local/telegram.json`.
    """
    print("[telegram] Telegram authentication is not implemented yet.  Please\n"
          "create a bot using BotFather and save its token in\n"
          "`.brr.local/telegram.json` with keys `token` and `chat_id` manually.")


def connect() -> None:
    """Bind the current repository to a Telegram chat/topic.

    This stub prints instructions.  A real implementation would list
    available supergroups, create a topic and store the topic ID in
    `.brr.local/telegram.json`.
    """
    print("[telegram] Chat connection is not implemented yet.  After you set up\n"
          "`.brr.local/telegram.json`, the brr daemon will route messages\n"
          "accordingly.")