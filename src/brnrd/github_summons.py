"""GitHub events that summon the managed resident."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GitHubSummons:
    """A webhook payload resolved to one resident summons."""

    login: str
    kind: str
    item: dict[str, Any]
    login_field: str
    trigger: str
    is_pull_request: bool


@dataclass(frozen=True)
class _SummonsSpec:
    event: str
    action: str
    login_field: str
    item_field: str
    kind: str
    trigger: str
    is_pull_request: bool
    actor_reply: str
    anonymous_reply: str


_SUMMONS_SPECS = (
    _SummonsSpec(
        event="issues",
        action="assigned",
        login_field="assignee",
        item_field="issue",
        kind="issue-assignment",
        trigger="assignee",
        is_pull_request=False,
        actor_reply="Work assigned by",
        anonymous_reply="the assignment",
    ),
    _SummonsSpec(
        event="pull_request",
        action="assigned",
        login_field="assignee",
        item_field="pull_request",
        kind="pr-assignment",
        trigger="assignee",
        is_pull_request=True,
        actor_reply="Work assigned by",
        anonymous_reply="the assignment",
    ),
    _SummonsSpec(
        event="pull_request",
        action="review_requested",
        login_field="requested_reviewer",
        item_field="pull_request",
        kind="pr-review-request",
        trigger="reviewer",
        is_pull_request=True,
        actor_reply="Review requested by",
        anonymous_reply="the review request",
    ),
)

_SUMMONS_BY_EVENT = {
    (spec.event, spec.action): spec for spec in _SUMMONS_SPECS
}
_SUMMONS_BY_KIND = {spec.kind: spec for spec in _SUMMONS_SPECS}


def resolve_github_summons(
    x_github_event: str | None,
    payload: object,
    bot_login: str,
) -> GitHubSummons | None:
    """Resolve a webhook payload addressed to ``bot_login``, if any."""

    if not isinstance(payload, dict):
        return None
    spec = _SUMMONS_BY_EVENT.get(
        (str(x_github_event or ""), str(payload.get("action") or ""))
    )
    if spec is None:
        return None

    target = payload.get(spec.login_field)
    if not isinstance(target, dict):
        return None
    login = str(target.get("login") or "").strip()
    wanted = str(bot_login or "").strip()
    if not login or not wanted or login.casefold() != wanted.casefold():
        return None

    item = payload.get(spec.item_field)
    if not isinstance(item, dict):
        return None
    return GitHubSummons(
        login=login,
        kind=spec.kind,
        item=item,
        login_field=spec.login_field,
        trigger=spec.trigger,
        is_pull_request=spec.is_pull_request,
    )


def github_summons_reply_prefix(
    kind: str,
    author: str,
    url: str,
) -> str | None:
    """Return the quoted reply lead for a summons kind."""

    spec = _SUMMONS_BY_KIND.get(kind)
    if spec is None or not url:
        return None
    if author:
        return f"> {spec.actor_reply} [@{author}]({url})\n\n"
    return f"> Replying to [{spec.anonymous_reply}]({url})\n\n"
