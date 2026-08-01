"""brnrd-bot's own GitHub credential: auto-accept + collaborator status.

#874, rescoped 2026-07-29 — the invite itself stays the user's hand (add
`brnrd-bot` as a collaborator through normal repo settings); this module is
the auto half kept from the original ask: brnrd-bot's own token
(``settings.github_bot_token``) accepts its own pending invitations, scoped
to repos already bound to a brnrd account — it never joins a repo it wasn't
invited to and never widens to "every invitation this token can see" — and
refreshes the marker's collaborator state onto the owning ``Repo`` row so
the dashboard can render it honestly (see ``routers.dashboard._repo_view_out``).

Token unset ⇒ the whole feature is a no-op; callers still surface that
plainly (the repo view renders "unknown" rather than a checked-and-false
state — see ``marker_absence_text``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

import httpx

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Repo
from .platforms import github as gh

logger = logging.getLogger(__name__)


class MarkerCheckState(str, Enum):
    """Machine-readable outcomes that may need user action.

    These values, rather than exception sentences, cross the repo-view API
    boundary.  ``None`` remains the successful-collaborator / never-checked
    shape; the latter is distinguishable by ``github_bot_checked_at``.
    """

    PERMISSION_MISSING = "permission-missing"
    NOT_A_COLLABORATOR = "not-a-collaborator"
    CHECK_UNAVAILABLE = "check-unavailable"
    NOT_CONFIGURED = "not-configured"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MarkerSyncResult:
    accepted: int = 0
    checked: int = 0
    failed: int = 0


def marker_absence_text(bot_login: str) -> str:
    """The plain, one-sentence repo-view line when the marker isn't a
    collaborator — names the *effective* configured login (#874 ask 3), so a
    misconfigured call sign (the 2026-07-29 outage class) is visible in the
    UI rather than only discoverable by forensics.
    """
    login = str(bot_login or "").strip().lstrip("@") or "the configured GitHub bot"
    return (
        f"{login} not a collaborator — assigns / review-requests / "
        "comment-tags addressed to it won't reach the resident; invite it "
        "in Settings → Collaborators."
    )


def classify_marker_check_failure(exc: Exception) -> MarkerCheckState:
    """Collapse transport failures at the catch site, while details are typed.

    Authentication/authorization failures have a permission remedy.  Retryable
    HTTP and network failures mean the check is unavailable.  Everything else
    is explicitly unknown; no classifier matches exception prose.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return MarkerCheckState.PERMISSION_MISSING
        if status in (408, 429) or status >= 500:
            return MarkerCheckState.CHECK_UNAVAILABLE
    elif isinstance(exc, httpx.RequestError):
        return MarkerCheckState.CHECK_UNAVAILABLE
    return MarkerCheckState.UNKNOWN


def marker_check_state(repo: Repo) -> MarkerCheckState | None:
    """Read the persisted class, including safe handling of legacy notices."""
    if repo.github_bot_collaborator is False:
        return MarkerCheckState.NOT_A_COLLABORATOR
    if repo.github_bot_collaborator is True or not repo.github_bot_notice:
        return None
    try:
        return MarkerCheckState(repo.github_bot_notice)
    except ValueError:
        # Rows written before #969 contain raw exception sentences.  They are
        # never copied outward; their only honest class is unknown.
        return MarkerCheckState.UNKNOWN


def marker_state_text(state: MarkerCheckState, bot_login: str) -> str:
    """Compatibility copy for API consumers that have not moved to the enum."""
    if state is MarkerCheckState.NOT_A_COLLABORATOR:
        return marker_absence_text(bot_login)
    if state is MarkerCheckState.PERMISSION_MISSING:
        return (
            "collaborator status unavailable — the GitHub App lacks the grant "
            "for the collaborators endpoint; grant Administration: read in the "
            "App's repository permissions."
        )
    if state is MarkerCheckState.CHECK_UNAVAILABLE:
        return "collaborator status unavailable — GitHub could not be reached; try again later."
    if state is MarkerCheckState.NOT_CONFIGURED:
        return (
            "collaborator check not run — github_bot_login is not configured; "
            "set it in the server settings."
        )
    return "collaborator status unknown — the check failed for an unclassified reason."


def sync_marker_for_repos(db: Session, settings, repos: list[Repo]) -> MarkerSyncResult:
    """Accept pending brnrd-bot invitations for ``repos`` and refresh their
    collaborator state.

    ``repos`` must already be account-bound rows the caller resolved (repo
    bind, installation sync, or the dashboard's coarse recheck) — this
    function only ever acts on what it's handed, never on a wider listing.
    Never raises: a GitHub API error is caught per-repo and recorded as a
    notice on that row; the row's bind stays untouched either way (#868).
    """
    token = settings.github_bot_token
    if not token or not repos:
        return MarkerSyncResult()

    by_name = {r.repo_full_name.casefold(): r for r in repos}
    accepted = 0
    failed = 0
    now = datetime.now(timezone.utc)
    # Repos an invitation-accept attempt already gave a fresh, specific
    # answer for this pass (success *or* failure) — the collaborator-check
    # loop below skips them so a subsequent ambiguous check can't clobber a
    # precise "invitation accept failed: ..." notice with a vaguer one.
    processed: set[str] = set()

    invitations: list[dict] | None
    try:
        invitations = gh.list_repository_invitations(
            token, settings.github_api_base_url, settings.github_api_version
        )
    except Exception as exc:
        logger.warning("brnrd-bot invitation list failed: %s", exc)
        invitations = None

    if invitations is not None:
        for invite in invitations:
            repo_name = str((invite.get("repository") or {}).get("full_name") or "")
            repo = by_name.get(repo_name.casefold())
            invite_id = invite.get("id")
            if repo is None or invite_id is None:
                continue  # not one of ours — never accept a stray invite
            processed.add(repo.id)
            try:
                gh.accept_repository_invitation(
                    token, settings.github_api_base_url, settings.github_api_version, invite_id
                )
                repo.github_bot_collaborator = True
                repo.github_bot_notice = None
                accepted += 1
            except Exception as exc:
                logger.warning(
                    "brnrd-bot invitation accept failed for %s: %s", repo.repo_full_name, exc
                )
                repo.github_bot_notice = classify_marker_check_failure(exc).value
                repo.github_bot_collaborator = None
                failed += 1
            repo.github_bot_checked_at = now

    bot_login = str(settings.github_bot_login or "").strip().lstrip("@")
    checked = 0
    for repo in repos:
        if repo.id in processed:
            continue  # already got a definitive, specific answer above
        if not bot_login:
            # Nothing valid to check against — an empty login would query
            # `/collaborators/` and could read GitHub's 404 as a false "not a
            # collaborator" for what is actually a config gap, not an absent
            # marker. Say the real reason instead of guessing — the gap is a
            # classifiable state with a named remedy, never "unknown".
            repo.github_bot_collaborator = None
            repo.github_bot_notice = MarkerCheckState.NOT_CONFIGURED.value
            repo.github_bot_checked_at = now
            continue
        try:
            repo.github_bot_collaborator = gh.check_repository_collaborator(
                token,
                settings.github_api_base_url,
                settings.github_api_version,
                repo.repo_full_name,
                bot_login,
            )
            repo.github_bot_notice = None
        except Exception as exc:
            logger.warning(
                "brnrd-bot collaborator check failed for %s: %s", repo.repo_full_name, exc
            )
            repo.github_bot_notice = classify_marker_check_failure(exc).value
            repo.github_bot_collaborator = None
            failed += 1
        repo.github_bot_checked_at = now
        checked += 1

    db.commit()
    return MarkerSyncResult(accepted=accepted, checked=checked, failed=failed)


def account_repos(db: Session, account_id: str) -> list[Repo]:
    """The account's bound repos — the scope every marker-sync call site
    passes in, so the bot never widens its reach past what's actually bound.
    """
    return list(db.execute(select(Repo).where(Repo.account_id == account_id)).scalars())
