"""GitHub events that summon the managed resident."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from brr.gates.github import parse as gh_parse


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
    reply_key: str
    trigger: str
    is_pull_request: bool
    # #879 members 3+4: a mention-sourced summons (review body / issue-or-PR
    # body) needs the authorization gate the assign/label/review-request
    # specs don't — those actions already require GitHub triage-or-higher,
    # a mention does not (anyone who can comment can mention). ``actor_*``
    # names who to check; unset (empty/False) for the non-mention specs,
    # which don't use them.
    requires_authz: bool = False
    actor_login: str = ""
    actor_association: str = ""
    # Set only when the event body content lives somewhere other than
    # ``item`` (a PR review's own text, not the PR's description) — see
    # the ``pr-review-summary`` spec below.
    content_override: str | None = None
    # Same idea for the reply's deep link: a review has its own
    # ``html_url`` (the review box), more useful than the PR's.
    html_url_override: str | None = None


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
    reply_key: str
    requires_authz: bool = False
    content_field: str | None = None
    # Match the *same* text the event body forwards. ``_format_event_body``
    # hands downstream ``title + body`` for an issue/PR, so matching ``body``
    # alone left a title-only mention unmatched — an issue titled
    # "@bot have a look" with an empty body is a completely normal way to
    # file something, and it reached nobody (#879 review of the first pass).
    # Unset for the review spec: a review has no title.
    match_title: bool = False


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
        reply_key="assignee",
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
        reply_key="assignee",
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
        reply_key="requested_reviewer",
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
        reply_key="label",
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
        reply_key="label",
    ),
    # #879 member 3 — a review *summary* (the review box itself, no inline
    # comment) mentioning the bot. ``review`` carries the text to match and
    # the author to authorize; ``pull_request`` is only the item (number,
    # html_url) — the event body must be the review's own text, not the
    # PR's description, hence ``content_field``.
    _SummonsSpec(
        event="pull_request_review",
        action="submitted",
        login_field="review",
        target_field="body",
        target_source="mention",
        item_field="pull_request",
        kind="pr-review-summary",
        trigger="mention",
        is_pull_request=True,
        actor_reply="Reviewed by",
        anonymous_reply="the review",
        reply_key="mention",
        requires_authz=True,
        content_field="body",
    ),
    # #879 member 4 — a newly opened issue/PR whose own body mentions the
    # bot. ``login_field == item_field`` here (the issue/PR is both the
    # match target and the item), so no ``content_field`` override is
    # needed — ``item``'s own title+body is already the right content.
    _SummonsSpec(
        event="issues",
        action="opened",
        login_field="issue",
        target_field="body",
        target_source="mention",
        item_field="issue",
        kind="issue-opened",
        trigger="mention",
        is_pull_request=False,
        actor_reply="Opened by",
        anonymous_reply="the new issue",
        reply_key="mention",
        requires_authz=True,
        match_title=True,
    ),
    _SummonsSpec(
        event="pull_request",
        action="opened",
        login_field="pull_request",
        target_field="body",
        target_source="mention",
        item_field="pull_request",
        kind="pr-opened",
        trigger="mention",
        is_pull_request=True,
        actor_reply="Opened by",
        anonymous_reply="the new PR",
        reply_key="mention",
        requires_authz=True,
        match_title=True,
    ),
)

_SUMMONS_BY_EVENT = {
    (spec.event, spec.action): spec for spec in _SUMMONS_SPECS
}
_SUMMONS_BY_KIND = {spec.kind: spec for spec in _SUMMONS_SPECS}


_OWN_BRANCH_PREFIX = "brr/"


def _is_own_machinery(payload: dict) -> bool:
    """Is this a pull request the resident's own worker fleet opened?

    Keyed on the head branch namespace (``brr/``), which brnrd owns and
    generates — never on the author login, which is the operator's for a
    worker push and therefore indistinguishable from a human's.
    """
    head = (payload.get("pull_request") or {}).get("head")
    ref = str((head or {}).get("ref") or "")
    return ref.startswith(_OWN_BRANCH_PREFIX)


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

    candidates = [bot_logins] if isinstance(bot_logins, str) else list(bot_logins)
    actor_login = ""
    actor_association = ""
    content_override: str | None = None
    html_url_override: str | None = None

    if spec.target_source == "mention":
        if spec.action == "opened" and _is_own_machinery(payload):
            # A PR the resident's own fleet just opened. Worker branches live
            # in brnrd's own ``brr/`` namespace and worker commits are pushed
            # under the operator's identity, so the author-skip below cannot
            # see them: a worker PR whose description @-mentions the bot
            # would summon a run *of its own PR body*, and that run may open
            # another PR. The loop is unlikely to be infinite and certain to
            # be confusing; the branch namespace is the structural tell, so
            # use it rather than trying to recognise a face.
            return None
        body_text = str(target.get(spec.target_field) or "")
        if spec.match_title:
            # Match what we forward: ``_format_event_body`` carries the
            # title too, so a title-only mention must summon.
            body_text = f"{str(target.get('title') or '')}\n{body_text}"
        mentions = [
            f"@{str(c or '').strip().lstrip('@')}"
            for c in candidates
            if str(c or "").strip()
        ]
        matched = gh_parse.find_mention(body_text, mentions)
        if matched is None:
            return None
        login = matched
        actor = target.get("user")
        if isinstance(actor, dict):
            actor_login = str(actor.get("login") or "").strip()
        actor_association = str(target.get("author_association") or "").strip().upper()
        if spec.content_field:
            content_override = str(target.get(spec.content_field) or "")
            # ``target`` differs from ``item`` exactly when a content
            # override exists (review vs. PR) — the deep link should
            # follow the same object the content came from.
            html_url_override = str(target.get("html_url") or "") or None
    else:
        login = str(target.get(spec.target_field) or "").strip()
        if not login:
            return None
        if spec.target_source == "label":
            wanted = str(trigger_label or "").strip()
            matched_ok = bool(wanted) and login.casefold() == wanted.casefold()
        else:
            wanted_logins = {
                str(c or "").strip().lstrip("@").casefold()
                for c in candidates
                if str(c or "").strip()
            }
            matched_ok = login.casefold() in wanted_logins
        if not matched_ok:
            return None

    item = payload.get(spec.item_field)
    if not isinstance(item, dict):
        return None
    return GitHubSummons(
        login=login,
        kind=spec.kind,
        item=item,
        reply_key=spec.reply_key,
        trigger=spec.trigger,
        is_pull_request=spec.is_pull_request,
        requires_authz=spec.requires_authz,
        actor_login=actor_login,
        actor_association=actor_association,
        html_url_override=html_url_override,
        content_override=content_override,
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
