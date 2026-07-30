"""GitHub events that summon the managed resident."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


def github_identity_candidates(settings) -> list[str]:
    """The resident's GitHub faces: configured login, App slug, legacy alias.

    Deduped case-insensitively, order preserved, ``@`` stripped. Shared by
    mention matching (``routers.webhooks._github_mention_candidates``) and
    assignee/reviewer summons matching (``resolve_github_summons`` below) —
    #874's unify ask: an assignment to the legacy ``brr-bot`` name should
    summon exactly like a mention of it does, not only a subset of the
    faces mentions already honor.
    """
    out: list[str] = []
    seen: set[str] = set()
    for handle in (
        getattr(settings, "github_bot_login", ""),
        getattr(settings, "github_app_slug", ""),
        "brr-bot",
    ):
        login = str(handle or "").strip().lstrip("@")
        if login and login.casefold() not in seen:
            out.append(login)
            seen.add(login.casefold())
    return out


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
    target_field: str
    target_source: str
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
        target_field="login",
        target_source="bot",
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
        target_field="login",
        target_source="bot",
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
        target_field="login",
        target_source="bot",
        item_field="pull_request",
        kind="pr-review-request",
        trigger="reviewer",
        is_pull_request=True,
        actor_reply="Review requested by",
        anonymous_reply="the review request",
    ),
    _SummonsSpec(
        event="issues",
        action="labeled",
        login_field="label",
        target_field="name",
        target_source="label",
        item_field="issue",
        kind="issue-label",
        trigger="label",
        is_pull_request=False,
        actor_reply="Work labeled by",
        anonymous_reply="the label",
    ),
    _SummonsSpec(
        event="pull_request",
        action="labeled",
        login_field="label",
        target_field="name",
        target_source="label",
        item_field="pull_request",
        kind="pr-label",
        trigger="label",
        is_pull_request=True,
        actor_reply="Work labeled by",
        anonymous_reply="the label",
    ),
)

_SUMMONS_BY_EVENT = {
    (spec.event, spec.action): spec for spec in _SUMMONS_SPECS
}
_SUMMONS_BY_KIND = {spec.kind: spec for spec in _SUMMONS_SPECS}


def resolve_github_summons(
    x_github_event: str | None,
    payload: object,
    bot_logins: str | Sequence[str],
    trigger_label: str = "brnrd",
) -> GitHubSummons | None:
    """Resolve a webhook payload addressed to one of ``bot_logins``, if any.

    ``bot_logins`` is normally ``github_identity_candidates(settings)`` — a
    single string still works (wrapped as a one-element list) for callers
    that only care about the configured login.
    """

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
    login = str(target.get(spec.target_field) or "").strip()
    if not login:
        return None

    if spec.target_source == "label":
        wanted = str(trigger_label or "").strip()
        matched = bool(wanted) and login.casefold() == wanted.casefold()
    else:
        candidates = [bot_logins] if isinstance(bot_logins, str) else list(bot_logins)
        wanted_logins = {
            str(c or "").strip().lstrip("@").casefold()
            for c in candidates
            if str(c or "").strip()
        }
        matched = login.casefold() in wanted_logins
    if not matched:
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
