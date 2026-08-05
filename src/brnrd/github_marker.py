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

Two different credentials, on purpose (#1141 — "the lamp that blamed the
app"). Accepting an invitation can only ever be done as the invited user, so
that stays on ``settings.github_bot_token`` (brnrd-bot's own PAT). But the
*collaborator check* (``GET /repos/{owner}/{repo}/collaborators/{username}``)
only needs ``Metadata: read``, which the GitHub App already holds, and an
App-token answer is definitive (204/404) — a 403 from the App genuinely means
a grant gap. The same call on the bot's *user* token is not definitive: GitHub
documents that a user-authenticated caller needs push access just to *use*
this endpoint, so a bot with no push access 403s regardless of whether it is
itself a collaborator — the "not a collaborator" answer wearing a permission
fault's clothes. So the check runs on the App installation token covering the
repo whenever one exists, and only falls back to the user token (with the
403 misread corrected — see ``MarkerCheckPrincipal.BOT_USER_COLLABORATOR_CHECK``)
for a manually-connected repo with no App installation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

import httpx

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GitHubInstallation, GitHubInstalledRepo, Repo
from .platforms import github as gh
from .platforms import github_app as gh_app

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


class MarkerCheckPrincipal(str, Enum):
    """Which credential (and therefore which documented 401/403 contract)
    produced the exception ``classify_marker_check_failure`` is classifying.

    Not a guess from the exception, and never a string match on its prose
    (see that function's own docstring) — the caller always knows which of
    these three calls it just made, so it says so explicitly.
    """

    # GitHub App installation token, collaborators endpoint. Only needs
    # ``Metadata: read``, which the App already holds — a 401/403 here is a
    # genuine grant gap.
    APP_INSTALLATION = "app_installation"
    # brnrd-bot's own user token, ``PATCH /user/repository_invitations/{id}``.
    # A 401/403 here is a genuine token/scope problem — this endpoint has no
    # "caller needs push access" quirk, that's specific to the collaborators
    # endpoint below.
    BOT_USER_INVITATION = "bot_user_invitation"
    # brnrd-bot's own user token, collaborators endpoint — only reached when
    # no App installation covers the repo (see ``_resolve_installation``).
    # GitHub's documented contract for a user-authenticated caller requires
    # push access just to *use* this endpoint, so a bot with no push access
    # 403s regardless of whether it is itself a collaborator. A 403 here is
    # the "not a collaborator" answer, not a permission fault.
    BOT_USER_COLLABORATOR_CHECK = "bot_user_collaborator_check"


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

    Re-registered 2026-08-04 (`docs/concepts/gates.md`): this used to read as
    a remediation notice for a broken summons path ("comment-tags... won't
    reach the resident"), which overstated it — the App-native ``brnrd``
    label already summons the resident unconditionally. The invite is an
    optional upgrade for the affordances only a real GitHub account can hold
    (assignee slot, reviewer slot, ``@`` autocomplete), not a defect to fix.
    Kept in step with ``MarkerNotice.svelte``'s primary rendering of the same
    fact — this copy only reaches clients on the legacy
    ``github_bot_marker_notice`` compatibility field.
    """
    login = str(bot_login or "").strip().lstrip("@") or "the configured GitHub bot"
    return (
        f"{login} isn't a collaborator — optional, not required: the brnrd "
        "label already summons it. Invite it in Settings → Collaborators to "
        "add assignment, review requests, and @ autocomplete."
    )


def classify_marker_check_failure(
    exc: Exception, *, principal: MarkerCheckPrincipal
) -> MarkerCheckState:
    """Collapse transport failures at the catch site, while details are typed.

    Authentication/authorization failures have a permission remedy — except a
    403 from ``BOT_USER_COLLABORATOR_CHECK``, whose documented contract makes
    that status ambiguous with "not a collaborator" rather than a grant fault
    (see ``MarkerCheckPrincipal``). Retryable HTTP and network failures mean
    the check is unavailable. Everything else is explicitly unknown; no
    classifier matches exception prose.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 403 and principal is MarkerCheckPrincipal.BOT_USER_COLLABORATOR_CHECK:
            return MarkerCheckState.NOT_A_COLLABORATOR
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
        # Rewritten 2026-08-05 (#1141): operator-scope fact, not a user task —
        # whichever credential produced this, no end user can act on it. See
        # `MarkerNotice.svelte`'s matching copy and the module docstring.
        return (
            "collaborator status unavailable — brnrd's own check against "
            "GitHub failed; this is on the brnrd operator to fix, not "
            "something to change here."
        )
    if state is MarkerCheckState.CHECK_UNAVAILABLE:
        return "collaborator status unavailable — GitHub could not be reached; try again later."
    if state is MarkerCheckState.NOT_CONFIGURED:
        return (
            "collaborator check not run — github_bot_login is not configured; "
            "set it in the server settings."
        )
    return "collaborator status unknown — the check failed for an unclassified reason."


def _resolve_installation(db: Session, repo: Repo) -> GitHubInstallation | None:
    """The App installation covering ``repo``, or ``None`` when none does.

    ``None`` is the normal shape for a manually-connected repo (the
    "connect this repository" manual form, no App install at all) — not an
    error. Same match-by-forge-id-then-name shape as
    ``routers.daemons.publishing_credential`` (forge id survives transfers/
    renames, name does not); unlike that endpoint this never self-heals the
    ``Repo`` row or raises — it is a best-effort lookup for a background
    sync, not a request that owes the caller a definitive answer.
    """
    base_query = (
        select(GitHubInstalledRepo, GitHubInstallation)
        .join(
            GitHubInstallation,
            GitHubInstallation.id == GitHubInstalledRepo.github_installation_id,
        )
        .where(GitHubInstallation.account_id == repo.account_id)
        .order_by(GitHubInstalledRepo.last_seen_at.desc())
    )
    installed = None
    if repo.forge_repo_id:
        installed = db.execute(
            base_query.where(GitHubInstalledRepo.forge_repo_id == repo.forge_repo_id)
        ).first()
    if installed is None:
        installed = db.execute(
            base_query.where(GitHubInstalledRepo.repo_full_name == repo.repo_full_name)
        ).first()
    if installed is None:
        return None
    _installed_repo, installation = installed
    return installation


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
                repo.github_bot_notice = classify_marker_check_failure(
                    exc, principal=MarkerCheckPrincipal.BOT_USER_INVITATION
                ).value
                repo.github_bot_collaborator = None
                failed += 1
            repo.github_bot_checked_at = now

    bot_login = str(settings.github_bot_login or "").strip().lstrip("@")
    checked = 0
    # Minted at most once per installation per call, not once per repo — a
    # batch of repos sharing one installation reuses the same short-lived
    # credential instead of re-minting it per row.
    installation_tokens: dict[str, str] = {}
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
        # #1141 — the check runs on the App installation token when one
        # covers this repo: it needs only `Metadata: read` (already granted)
        # and answers definitively (204/404), so a 403 from it is an honest
        # permission fault. Only a manually-connected repo with no App
        # installation falls back to the bot's own user token — whose 403 on
        # this endpoint is reclassified below rather than silently
        # recreating the original bug (see `MarkerCheckPrincipal`).
        installation = _resolve_installation(db, repo)
        principal = (
            MarkerCheckPrincipal.APP_INSTALLATION
            if installation is not None
            else MarkerCheckPrincipal.BOT_USER_COLLABORATOR_CHECK
        )
        try:
            if installation is not None:
                check_token = installation_tokens.get(installation.id)
                if check_token is None:
                    check_token = gh_app.installation_access_token(
                        settings, installation.installation_id
                    )
                    installation_tokens[installation.id] = check_token
            else:
                check_token = token
            repo.github_bot_collaborator = gh.check_repository_collaborator(
                check_token,
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
            repo.github_bot_notice = classify_marker_check_failure(
                exc, principal=principal
            ).value
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
