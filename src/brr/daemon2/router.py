"""A letter chooses an ask/conversation before any project is placed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .. import conversations, protocol


class UnaddressedLetter(ValueError):
    pass


@dataclass(frozen=True)
class Address:
    conversation: str
    ask: str | None
    repo_hint: str | None
    parent: str | None = None
    edge: str | None = None


class Router:
    """No repo filter is allowed in seat selection.

    A person may write from any project and still reach their conversation.
    Internal child traffic needs its exact conversation plus parent/edge;
    it may never inherit whichever account seat happens to be current.
    """

    def __init__(self, event_lookup: Callable[[str], dict[str, Any] | None] | None = None):
        self.event_lookup = event_lookup

    def route(self, letter: dict[str, Any]) -> Address:
        source = str(letter.get("source") or "")
        conversation = str(letter.get("conversation_key")
                           or letter.get("thread")
                           or letter.get("thread_key")
                           or letter.get("spawn_parent_conversation_key") or "").strip()
        parent = str(letter.get("parent_run_id")
                     or letter.get("spawn_parent_run_id")
                     or letter.get("spawned_by_run")
                     or letter.get("parent") or "").strip()
        edge = str(letter.get("spawn_edge")
                   or letter.get("spawned_by_event")
                   or letter.get("edge") or "").strip()
        if source == "spawn" and not edge:
            edge = str(letter.get("id") or "").strip()
        if source == "dispatch_message" and not conversation and self.event_lookup:
            origin = self.event_lookup(str(letter.get("spawn_message_for_event") or ""))
            if origin is not None and origin.get("id") != letter.get("id"):
                conversation = self.route(origin).conversation
        if source in protocol.INTERNAL_SOURCES:
            child_source = source == "spawn" or source.startswith("spawn_")
            if not conversation or (child_source and not (parent and edge)):
                raise UnaddressedLetter("internal letter needs exact conversation and child edge")
        elif not conversation:
            # A new chat establishes its own address from transport identity.
            conversation = conversations.conversation_key_for_event(letter) or ""
        if source not in protocol.INTERNAL_SOURCES and not conversation:
            channel = str(letter.get("channel") or source).strip()
            correspondent = str(letter.get("correspondent")
                                or letter.get("sender_id") or "").strip()
            if not channel or not correspondent:
                raise UnaddressedLetter("person letter has no transport address")
            conversation = f"{channel}:{correspondent}"
        ask = letter.get("ask_id") or letter.get("item") or None
        return Address(conversation=conversation,
                       ask=str(ask) if ask else None,
                       repo_hint=str(letter.get("repo_label") or "") or None,
                       parent=parent or None, edge=edge or None)
