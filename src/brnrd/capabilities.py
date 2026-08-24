"""The capability registry (design-capability-panel.md, build step 1).

Setup state used to be computed ad hoc in at least two places that
disagreed — `ColdStart.svelte` keyed "still onboarding" on *daemon ever
paired*, `/repos` keyed its own competing block on *installations.length ===
0*. Neither was wrong; nothing owned the question. This module is the one
server-evaluated answer: a flat list of :class:`Capability` rows, each a
lamp with a state, evidence for that state, and (for the ones GitHub or a
terminal step can actually perform) an act.

**No user-facing copy anywhere in this module.** The catch site here owns
classification only; the renderer (frontend, `brnrd status`, the wake) owns
every sentence — the house precedent is `MarkerNotice.svelte:3-4` (#969 /
#786). `evidence.source` is a slug, never prose.

**No `done` boolean.** Four states — `lit` / `dark` / `waiting` /
`unobservable` — because a boolean is what forced every current consumer
(`ColdStart.svelte`, `/repos`) to invent its own third state out of band.

See `evaluate_capabilities` for the entry point and the module docstring on
`CAPABILITY_CATALOG` for the requires-graph shape this evaluator assumes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import billing, terms
from .config import Settings
from .models import (
    Account,
    ChannelRoute,
    Daemon,
    GitHubInstallation,
    Repo,
    TermsAcceptance,
    Token,
)

# --------------------------------------------------------------------------
# Wire contract — the field vocabulary design-capability-panel.md §"The
# capability record" and this build's spec both name.
# --------------------------------------------------------------------------

SCOPE_ACCOUNT = "account"
SCOPE_MACHINE = "machine"
SCOPE_REPO = "repo"
SCOPES = (SCOPE_ACCOUNT, SCOPE_MACHINE, SCOPE_REPO)

STATE_LIT = "lit"
STATE_DARK = "dark"
STATE_WAITING = "waiting"
STATE_UNOBSERVABLE = "unobservable"
STATES = (STATE_LIT, STATE_DARK, STATE_WAITING, STATE_UNOBSERVABLE)

HEAT_REQUIRED = "required"
HEAT_RECOMMENDED = "recommended"
HEAT_OPTIONAL = "optional"
HEATS = (HEAT_REQUIRED, HEAT_RECOMMENDED, HEAT_OPTIONAL)

ACT_POST = "post"
ACT_DEEP_LINK = "deep-link"
ACT_COMMAND = "command"
ACT_NONE = "none"
ACT_KINDS = (ACT_POST, ACT_DEEP_LINK, ACT_COMMAND, ACT_NONE)


class CapabilityCatalogError(ValueError):
    """The catalog itself is malformed: an unknown id, a requires cycle, an
    optional id acting as a blocker, or a requires edge into an ambiguous
    multi-subject scope. Raised at import time for the real catalog, and on
    demand for `validate_catalog` given a test fixture."""


class CapabilityCycleError(CapabilityCatalogError):
    """A `requires` cycle — a bug in the catalog, per the spec's own words:
    "the evaluator should raise on it rather than loop."""


@dataclass(frozen=True)
class Evidence:
    """How we know a capability's state, never a sentence (spec §1)."""

    source: str
    as_of: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {"source": self.source, "as_of": self.as_of}


@dataclass(frozen=True)
class Act:
    """`{kind, target}` — the act ladder. `target` is `None` whenever the
    real destination is only mintable per-click (a telegram pairing code, a
    device-flow approval code) rather than a stable URL/path this GET can
    hand out; the renderer already owns the POST that mints it."""

    kind: str = ACT_NONE
    target: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {"kind": self.kind, "target": self.target}


@dataclass(frozen=True)
class CapabilityDef:
    """One catalog entry — declaration only, no runtime state."""

    id: str
    scope: str
    heat: str
    requires: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise CapabilityCatalogError(f"capability {self.id!r}: unknown scope {self.scope!r}")
        if self.heat not in HEATS:
            raise CapabilityCatalogError(f"capability {self.id!r}: unknown heat {self.heat!r}")


@dataclass(frozen=True)
class Capability:
    """One evaluated row — the wire contract this module ships."""

    id: str
    scope: str
    subject: str | None
    state: str
    evidence: Evidence
    requires: tuple[str, ...]
    heat: str
    act: Act
    frontier: bool

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "subject": self.subject,
            "state": self.state,
            "evidence": self.evidence.to_wire(),
            "requires": list(self.requires),
            "heat": self.heat,
            "act": self.act.to_wire(),
            "frontier": self.frontier,
        }


# --------------------------------------------------------------------------
# Catalog ids
# --------------------------------------------------------------------------

ID_SIGNED_IN = "signed-in"
ID_TERMS = "terms"
ID_GITHUB_APP = "github-app"
ID_SUBSCRIPTION = "subscription"

ID_CLI_INSTALLED = "cli-installed"
ID_MACHINE_PAIRED = "machine-paired"
ID_DAEMON_LIVE = "daemon-live"
ID_RUNNER_AVAILABLE = "runner-available"
ID_RUNNER_QUOTA = "runner-quota"

ID_REPO_ENABLED = "repo-enabled"
ID_PUBLISH_SCOPE = "publish-scope"
ID_REPO_INITIALISED = "repo-initialised"
ID_BOT_COLLABORATOR = "bot-collaborator"
ID_CHANNEL_BOUND = "channel-bound"
ID_GATE_HEALTH = "gate-health"


# --------------------------------------------------------------------------
# The v1 catalog (spec §3). `requires` edges only ever point at an
# account-scope id (singleton, subject=None regardless of the referrer's own
# subject) or at an id in the *same* scope (resolved against the referrer's
# own subject). That is a deliberate restriction, not an oversight: a
# repo-scope or account-scope capability requiring a *machine*-scope id would
# be ambiguous — which of an account's possibly-several daemons does it mean?
# `validate_catalog` enforces the restriction structurally rather than
# leaving it as a convention a future edit can silently break.
# --------------------------------------------------------------------------

CAPABILITY_CATALOG: tuple[CapabilityDef, ...] = (
    # account scope
    CapabilityDef(ID_SIGNED_IN, SCOPE_ACCOUNT, HEAT_REQUIRED),
    CapabilityDef(ID_TERMS, SCOPE_ACCOUNT, HEAT_REQUIRED, requires=(ID_SIGNED_IN,)),
    CapabilityDef(ID_GITHUB_APP, SCOPE_ACCOUNT, HEAT_REQUIRED, requires=(ID_SIGNED_IN,)),
    CapabilityDef(ID_SUBSCRIPTION, SCOPE_ACCOUNT, HEAT_REQUIRED, requires=(ID_SIGNED_IN,)),
    # machine scope (subject = daemon id)
    CapabilityDef(ID_CLI_INSTALLED, SCOPE_MACHINE, HEAT_REQUIRED),
    CapabilityDef(ID_MACHINE_PAIRED, SCOPE_MACHINE, HEAT_REQUIRED, requires=(ID_CLI_INSTALLED,)),
    CapabilityDef(ID_DAEMON_LIVE, SCOPE_MACHINE, HEAT_REQUIRED, requires=(ID_MACHINE_PAIRED,)),
    CapabilityDef(ID_RUNNER_AVAILABLE, SCOPE_MACHINE, HEAT_REQUIRED, requires=(ID_MACHINE_PAIRED,)),
    CapabilityDef(ID_RUNNER_QUOTA, SCOPE_MACHINE, HEAT_REQUIRED, requires=(ID_RUNNER_AVAILABLE,)),
    # repo scope (subject = repo id)
    CapabilityDef(ID_REPO_ENABLED, SCOPE_REPO, HEAT_REQUIRED, requires=(ID_GITHUB_APP,)),
    CapabilityDef(ID_PUBLISH_SCOPE, SCOPE_REPO, HEAT_REQUIRED, requires=(ID_REPO_ENABLED,)),
    CapabilityDef(ID_REPO_INITIALISED, SCOPE_REPO, HEAT_REQUIRED, requires=(ID_REPO_ENABLED,)),
    CapabilityDef(ID_BOT_COLLABORATOR, SCOPE_REPO, HEAT_OPTIONAL, requires=(ID_REPO_ENABLED,)),
    CapabilityDef(ID_CHANNEL_BOUND, SCOPE_REPO, HEAT_RECOMMENDED, requires=(ID_REPO_ENABLED,)),
    CapabilityDef(ID_GATE_HEALTH, SCOPE_REPO, HEAT_RECOMMENDED, requires=(ID_REPO_ENABLED,)),
)


# --------------------------------------------------------------------------
# Catalog validation: cycle detection + the two structural invariants the
# spec's test list asks for (optional never blocks; requires resolves
# unambiguously). Returns a topological id order — dependencies before
# dependents — which the evaluator's override pass relies on.
# --------------------------------------------------------------------------


def validate_catalog(catalog: Sequence[CapabilityDef]) -> tuple[str, ...]:
    by_id: dict[str, CapabilityDef] = {}
    for cdef in catalog:
        if cdef.id in by_id:
            raise CapabilityCatalogError(f"duplicate capability id {cdef.id!r}")
        by_id[cdef.id] = cdef

    for cdef in catalog:
        for dep in cdef.requires:
            dep_def = by_id.get(dep)
            if dep_def is None:
                raise CapabilityCatalogError(
                    f"capability {cdef.id!r} requires unknown id {dep!r}"
                )
            if dep_def.heat == HEAT_OPTIONAL:
                raise CapabilityCatalogError(
                    f"capability {cdef.id!r} requires {dep!r}, which is optional — "
                    "an optional capability may never block another "
                    "(the anti-nagging invariant, enforced structurally)"
                )
            if dep_def.scope not in (SCOPE_ACCOUNT, cdef.scope):
                raise CapabilityCatalogError(
                    f"capability {cdef.id!r} (scope={cdef.scope}) requires {dep!r} "
                    f"(scope={dep_def.scope}) — a requires edge may only reach "
                    "account scope or its own scope, never another multi-subject scope"
                )

    return _topological_order(catalog, by_id)


def _topological_order(
    catalog: Sequence[CapabilityDef], by_id: dict[str, CapabilityDef]
) -> tuple[str, ...]:
    order: list[str] = []
    visit_state: dict[str, int] = {}  # 0 unvisited (absent), 1 visiting, 2 done

    def visit(cid: str, path: list[str]) -> None:
        state = visit_state.get(cid, 0)
        if state == 2:
            return
        if state == 1:
            cycle = " -> ".join([*path[path.index(cid):], cid])
            raise CapabilityCycleError(f"capability catalog requires-cycle: {cycle}")
        visit_state[cid] = 1
        for dep in by_id[cid].requires:
            visit(dep, [*path, cid])
        visit_state[cid] = 2
        order.append(cid)

    for cdef in catalog:
        visit(cdef.id, [])
    return tuple(order)


_CATALOG_BY_ID: dict[str, CapabilityDef] = {c.id: c for c in CAPABILITY_CATALOG}
_CATALOG_ORDER: tuple[str, ...] = validate_catalog(CAPABILITY_CATALOG)


# --------------------------------------------------------------------------
# Evaluation context — one batch of queries per call, not per capability.
# --------------------------------------------------------------------------

_DAEMON_ONLINE_AFTER = timedelta(minutes=2)
# Matches `routers._session._DAEMON_ONLINE_AFTER`. Duplicated rather than
# imported: this module has no other dependency on the web routing layer,
# and `routers/dashboard.py` is the one importing *this* module, not the
# other way — importing back from `routers._session` here would invert that.
_QUOTA_EXHAUSTED_BELOW_PERCENT = 1.0  # matches src/brr/gates/cloud.py::_shell_level_label


def _dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = _dt(value)
    return value.isoformat() if value else None


def _json_list(raw: str | None) -> list[Any]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


@dataclass
class _Context:
    db: Session
    account: Account
    settings: Settings
    installations: list[GitHubInstallation]
    repos: list[Repo]
    daemons: list[Daemon]
    tokens_by_id: dict[str, Token]
    terms_rows: list[TermsAcceptance]
    channel_bound_repo_ids: dict[str, datetime | None]
    latest_daemon_by_repo: dict[str, Daemon]

    @classmethod
    def build(cls, db: Session, account: Account, settings: Settings) -> "_Context":
        installations = list(
            db.execute(
                select(GitHubInstallation).where(GitHubInstallation.account_id == account.id)
            ).scalars()
        )
        repos = list(db.execute(select(Repo).where(Repo.account_id == account.id)).scalars())
        daemons = list(db.execute(select(Daemon).where(Daemon.account_id == account.id)).scalars())
        terms_rows = list(
            db.execute(select(TermsAcceptance).where(TermsAcceptance.account_id == account.id)).scalars()
        )

        token_ids = {d.token_id for d in daemons if d.token_id}
        tokens_by_id: dict[str, Token] = {}
        if token_ids:
            tokens_by_id = {
                t.id: t
                for t in db.execute(
                    select(Token).where(
                        Token.id.in_(token_ids),
                        Token.account_id == account.id,
                    )
                ).scalars()
            }

        repo_ids = [r.id for r in repos]
        # Platform-general on purpose (brr/the-directory-reaches-the-wire):
        # `channel-bound`'s own detector (`_detect_channel_bound` below) asks
        # only "can this repo be reached by chat at all" — nothing in its
        # id, its evidence source, or its wire shape (`Capability.to_wire`
        # carries no platform) is Telegram-specific. The literal
        # `platform == "telegram"` filter that used to sit here was the same
        # narrowing bug as `_session._telegram_paired_repo_ids`: a paired
        # WhatsApp (or any future-platform) route left this dark forever.
        # No platform is named below — the directory this reads from already
        # carries whichever platforms have rows; enumerating them here would
        # reintroduce the bug in list form.
        channel_bound_repo_ids: dict[str, datetime | None] = {}
        if repo_ids:
            for route in db.execute(
                select(ChannelRoute).where(
                    ChannelRoute.repo_id.in_(repo_ids),
                    ChannelRoute.paired_principal_id.isnot(None),
                    ChannelRoute.account_id == account.id,
                )
            ).scalars():
                existing = channel_bound_repo_ids.get(route.repo_id)
                if existing is None or (route.created_at and route.created_at > existing):
                    channel_bound_repo_ids[route.repo_id] = route.created_at

        daemons_by_repo: dict[str, list[Daemon]] = {}
        for daemon in daemons:
            if daemon.repo_id:
                daemons_by_repo.setdefault(daemon.repo_id, []).append(daemon)
        # Same "most recently seen wins" pick as `_session._repo_views` —
        # gate-health below is this repo's own diagnostic, not the
        # account-wide machine-scope one, so it follows that legacy
        # `Daemon.repo_id` grouping rather than `ctx.daemons` at large.
        _epoch = datetime.min.replace(tzinfo=timezone.utc)
        latest_daemon_by_repo: dict[str, Daemon] = {
            repo_id: max(rows, key=lambda d: _dt(d.last_seen_at) or _epoch)
            for repo_id, rows in daemons_by_repo.items()
        }

        return cls(
            db=db,
            account=account,
            settings=settings,
            installations=installations,
            repos=repos,
            daemons=daemons,
            tokens_by_id=tokens_by_id,
            terms_rows=terms_rows,
            channel_bound_repo_ids=channel_bound_repo_ids,
            latest_daemon_by_repo=latest_daemon_by_repo,
        )


def _build(cdef: CapabilityDef, subject: str | None, state: str, evidence: Evidence, act: Act) -> Capability:
    return Capability(
        id=cdef.id,
        scope=cdef.scope,
        subject=subject,
        state=state,
        evidence=evidence,
        requires=cdef.requires,
        heat=cdef.heat,
        act=act,
        frontier=False,
    )


# --------------------------------------------------------------------------
# Detectors — one per catalog id. Each returns the *raw* rows for its
# capability: every subject this capability applies to, in `lit` / `dark` /
# `unobservable` state only. `waiting` and `frontier` are never a detector's
# job — the override pass below derives both from the requires graph.
# --------------------------------------------------------------------------


def _detect_signed_in(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    # True by construction: this evaluator only ever runs for a resolved
    # account (`dashboard_repos_api` 401s before calling in).
    return [_build(cdef, None, STATE_LIT, Evidence("session", None), Act(ACT_NONE))]


def _detect_terms(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    # Scoped to the current version on purpose (matches `_session._accepted_terms`):
    # a superseded acceptance is evidence of a contract once in force, not of
    # whether today's text is accepted.
    doc = terms.current(terms.DOC_TOS)
    accepted = [row for row in ctx.terms_rows if row.version == doc.version]
    if not accepted:
        return [_build(cdef, None, STATE_DARK, Evidence("db", None), Act(ACT_POST, doc.accept_path))]
    newest = max(accepted, key=lambda r: r.accepted_at or datetime.min)
    return [
        _build(cdef, None, STATE_LIT, Evidence("db", _iso(newest.accepted_at)), Act(ACT_POST, doc.accept_path))
    ]


def _detect_github_app(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    if not ctx.installations:
        return [
            _build(
                cdef, None, STATE_DARK, Evidence("github-app-sync", None),
                Act(ACT_DEEP_LINK, ctx.settings.github_install_url),
            )
        ]
    newest = max((_dt(i.last_synced_at) for i in ctx.installations if i.last_synced_at), default=None)
    return [
        _build(
            cdef, None, STATE_LIT, Evidence("github-app-sync", _iso(newest)),
            Act(ACT_DEEP_LINK, ctx.settings.github_install_url),
        )
    ]


def _detect_subscription(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    checkout_target = "/v1/accounts/subscription/checkout"
    ent = billing.entitlements(ctx.db, ctx.account)
    if ent.degraded:
        return [_build(cdef, None, STATE_UNOBSERVABLE, Evidence("none", None), Act(ACT_POST, checkout_target))]
    state = STATE_LIT if ent.headroom_lifted else STATE_DARK
    return [_build(cdef, None, state, Evidence("db", None), Act(ACT_POST, checkout_target))]


def _detect_cli_installed(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    if not ctx.daemons:
        return [_build(cdef, None, STATE_UNOBSERVABLE, Evidence("none", None), Act(ACT_COMMAND, None))]
    return [
        _build(
            cdef, daemon.id, STATE_LIT,
            Evidence("daemon-heartbeat", _iso(daemon.last_seen_at)), Act(ACT_COMMAND, None),
        )
        for daemon in ctx.daemons
    ]


def _detect_machine_paired(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    out: list[Capability] = []
    for daemon in ctx.daemons:
        token = ctx.tokens_by_id.get(daemon.token_id)
        if token is not None and token.revoked:
            out.append(
                _build(cdef, daemon.id, STATE_DARK, Evidence("db", _iso(daemon.last_seen_at)), Act(ACT_COMMAND, None))
            )
        else:
            out.append(
                _build(cdef, daemon.id, STATE_LIT, Evidence("db", _iso(daemon.last_seen_at)), Act(ACT_COMMAND, None))
            )
    return out


def _detect_daemon_live(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    now = datetime.now(timezone.utc)
    out: list[Capability] = []
    for daemon in ctx.daemons:
        last_seen = _dt(daemon.last_seen_at)
        online = bool(daemon.online) and last_seen is not None and now - last_seen <= _DAEMON_ONLINE_AFTER
        state = STATE_LIT if online else STATE_DARK
        out.append(_build(cdef, daemon.id, state, Evidence("daemon-heartbeat", _iso(last_seen)), Act(ACT_NONE)))
    return out


def _runner_unblocked(profile: Any) -> bool:
    return isinstance(profile, dict) and profile.get("available") is True


def _detect_runner_available(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    out: list[Capability] = []
    for daemon in ctx.daemons:
        if daemon.runners_updated_at is None:
            out.append(
                _build(cdef, daemon.id, STATE_UNOBSERVABLE, Evidence("none", None), Act(ACT_COMMAND, None))
            )
            continue
        profiles = _json_list(daemon.runners_json)
        state = STATE_LIT if any(_runner_unblocked(p) for p in profiles) else STATE_DARK
        out.append(
            _build(
                cdef, daemon.id, state,
                Evidence("daemon-heartbeat", _iso(daemon.runners_updated_at)), Act(ACT_COMMAND, None),
            )
        )
    return out


def _shell_exhausted(shell: Any) -> bool:
    if not isinstance(shell, dict):
        return False
    windows = shell.get("windows")
    if not isinstance(windows, list):
        return False
    known = [w.get("percent") for w in windows if isinstance(w, dict) and isinstance(w.get("percent"), (int, float))]
    if not known:
        return False
    return min(known) < _QUOTA_EXHAUSTED_BELOW_PERCENT


def _detect_runner_quota(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    out: list[Capability] = []
    for daemon in ctx.daemons:
        if daemon.quota_updated_at is None:
            out.append(_build(cdef, daemon.id, STATE_UNOBSERVABLE, Evidence("none", None), Act(ACT_NONE)))
            continue
        shells = _json_list(daemon.quota_json)
        if not shells:
            # Published, but `_quota_snapshot` omits a shell with no evidence
            # yet rather than reporting a fake zero — so an empty list here
            # is "no shell has reported in", not "every shell is exhausted".
            out.append(_build(cdef, daemon.id, STATE_UNOBSERVABLE, Evidence("none", None), Act(ACT_NONE)))
            continue
        all_exhausted = all(_shell_exhausted(s) for s in shells)
        state = STATE_DARK if all_exhausted else STATE_LIT
        out.append(
            _build(cdef, daemon.id, state, Evidence("daemon-heartbeat", _iso(daemon.quota_updated_at)), Act(ACT_NONE))
        )
    return out


def _detect_repo_enabled(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    return [
        _build(cdef, repo.id, STATE_LIT, Evidence("db", _iso(repo.created_at)), Act(ACT_POST, "/v1/repos/connect"))
        for repo in ctx.repos
    ]


def _detect_publish_scope(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    out: list[Capability] = []
    for repo in ctx.repos:
        target = f"/v1/repos/{repo.id}/publish-layers"
        if repo.publish_layers is None:
            out.append(_build(cdef, repo.id, STATE_DARK, Evidence("db", None), Act(ACT_POST, target)))
        else:
            out.append(
                _build(cdef, repo.id, STATE_LIT, Evidence("db", _iso(repo.updated_at)), Act(ACT_POST, target))
            )
    return out


def _detect_repo_initialised(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    # #1268: wired to the daemon's own boot-kernel measurement (#1261 added
    # `agents_md_missing`/`kb_missing` to `bootscore.BootHost`, computed
    # daemon-side, per-repo, fresh on every wake). It reaches here via
    # `Daemon.repo_agents_md_missing`/`.repo_kb_missing`, piggybacked on the
    # same `PUT /v1/daemons/quota` tick that already carries
    # `gate_health_json` — no new endpoint. Same "most recently seen daemon
    # for this repo" pick `_detect_gate_health` uses, since this is a
    # repo-scope fact only a paired daemon can report.
    #
    # `unobservable` survives as the honest reading for a repo with no
    # daemon at all, or one whose daemon hasn't published a boot reading
    # yet (older client, or first tick still pending) — distinct from the
    # #874-adjacent sensor gap this detector used to declare permanently
    # for *every* repo regardless of pairing.
    out: list[Capability] = []
    for repo in ctx.repos:
        daemon = ctx.latest_daemon_by_repo.get(repo.id)
        if daemon is None or daemon.repo_agents_md_missing is None or daemon.repo_kb_missing is None:
            out.append(
                _build(cdef, repo.id, STATE_UNOBSERVABLE, Evidence("none", None), Act(ACT_COMMAND, None))
            )
            continue
        initialised = not (daemon.repo_agents_md_missing or daemon.repo_kb_missing)
        state = STATE_LIT if initialised else STATE_DARK
        out.append(
            _build(
                cdef, repo.id, state,
                Evidence("daemon-heartbeat", _iso(daemon.quota_updated_at)), Act(ACT_COMMAND, None),
            )
        )
    return out


def _detect_bot_collaborator(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    out: list[Capability] = []
    for repo in ctx.repos:
        target = f"https://github.com/{repo.repo_full_name}/settings/access"
        evidence = Evidence("github-poll", _iso(repo.github_bot_checked_at))
        if repo.github_bot_collaborator is True:
            out.append(_build(cdef, repo.id, STATE_LIT, evidence, Act(ACT_DEEP_LINK, target)))
        elif repo.github_bot_collaborator is False:
            out.append(_build(cdef, repo.id, STATE_DARK, evidence, Act(ACT_DEEP_LINK, target)))
        else:
            out.append(_build(cdef, repo.id, STATE_UNOBSERVABLE, evidence, Act(ACT_DEEP_LINK, target)))
    return out


def _detect_channel_bound(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    out: list[Capability] = []
    for repo in ctx.repos:
        bound_at = ctx.channel_bound_repo_ids.get(repo.id)
        target = f"/v1/repos/{repo.id}/telegram-pair"
        if bound_at is not None:
            out.append(_build(cdef, repo.id, STATE_LIT, Evidence("db", _iso(bound_at)), Act(ACT_DEEP_LINK, target)))
        else:
            out.append(_build(cdef, repo.id, STATE_DARK, Evidence("db", None), Act(ACT_DEEP_LINK, target)))
    return out


def _detect_gate_health(ctx: _Context, cdef: CapabilityDef) -> list[Capability]:
    out: list[Capability] = []
    for repo in ctx.repos:
        daemon = ctx.latest_daemon_by_repo.get(repo.id)
        if daemon is None:
            out.append(_build(cdef, repo.id, STATE_UNOBSERVABLE, Evidence("none", None), Act(ACT_NONE)))
            continue
        gates = [row for row in _json_list(daemon.gate_health_json) if isinstance(row, dict)]
        if not gates:
            out.append(_build(cdef, repo.id, STATE_UNOBSERVABLE, Evidence("none", None), Act(ACT_NONE)))
            continue
        state = STATE_LIT if any(g.get("status") == "ok" for g in gates) else STATE_DARK
        out.append(
            _build(cdef, repo.id, state, Evidence("daemon-heartbeat", _iso(daemon.quota_updated_at)), Act(ACT_NONE))
        )
    return out


_DETECTORS: dict[str, Callable[[_Context, CapabilityDef], list[Capability]]] = {
    ID_SIGNED_IN: _detect_signed_in,
    ID_TERMS: _detect_terms,
    ID_GITHUB_APP: _detect_github_app,
    ID_SUBSCRIPTION: _detect_subscription,
    ID_CLI_INSTALLED: _detect_cli_installed,
    ID_MACHINE_PAIRED: _detect_machine_paired,
    ID_DAEMON_LIVE: _detect_daemon_live,
    ID_RUNNER_AVAILABLE: _detect_runner_available,
    ID_RUNNER_QUOTA: _detect_runner_quota,
    ID_REPO_ENABLED: _detect_repo_enabled,
    ID_PUBLISH_SCOPE: _detect_publish_scope,
    ID_REPO_INITIALISED: _detect_repo_initialised,
    ID_BOT_COLLABORATOR: _detect_bot_collaborator,
    ID_CHANNEL_BOUND: _detect_channel_bound,
    ID_GATE_HEALTH: _detect_gate_health,
}


def _requires_subject(dep_def: CapabilityDef, referrer_subject: str | None) -> str | None:
    return None if dep_def.scope == SCOPE_ACCOUNT else referrer_subject


def evaluate_capabilities(
    db: Session,
    account: Account,
    settings: Settings,
    *,
    catalog: Sequence[CapabilityDef] = CAPABILITY_CATALOG,
) -> list[Capability]:
    """Evaluate the whole registry for one account: every lit/dark/waiting/
    unobservable row, `waiting` computed transitively and `frontier` derived
    per spec §2 — the one entry point `routers/dashboard.py` calls.

    `catalog` is overridable for tests exercising `validate_catalog`'s
    invariants against a deliberately broken fixture; production callers
    always take the default, which reuses the validation already done once
    at import time rather than re-walking the requires graph on every
    dashboard poll.
    """
    if catalog is CAPABILITY_CATALOG:
        by_id, order = _CATALOG_BY_ID, _CATALOG_ORDER
    else:
        by_id = {c.id: c for c in catalog}
        order = validate_catalog(catalog)

    ctx = _Context.build(db, account, settings)

    instances: dict[tuple[str, str, str | None], Capability] = {}
    for cid in order:
        cdef = by_id[cid]
        detector = _DETECTORS.get(cid)
        if detector is None:
            raise CapabilityCatalogError(f"no detector registered for capability {cid!r}")
        for cap in detector(ctx, cdef):
            key = (cap.id, cap.scope, cap.subject)
            if key in instances:
                raise CapabilityCatalogError(f"duplicate capability instance {key!r}")
            instances[key] = cap

    # Override pass, in dependency order: dependencies are already finalized
    # by the time a dependent is visited, so `waiting` propagates
    # transitively for free — a prerequisite overridden to `waiting` reads
    # as not-lit to everything that requires it in turn.
    for cid in order:
        cdef = by_id[cid]
        if not cdef.requires:
            continue
        for key, cap in list(instances.items()):
            if key[0] != cid or cap.state not in (STATE_DARK,):
                continue
            reqs_lit = True
            for dep in cdef.requires:
                dep_def = by_id[dep]
                dep_key = (dep, dep_def.scope, _requires_subject(dep_def, key[2]))
                dep_cap = instances.get(dep_key)
                if dep_cap is None or dep_cap.state != STATE_LIT:
                    reqs_lit = False
                    break
            instances[key] = (
                replace(cap, frontier=True)
                if reqs_lit
                else replace(cap, state=STATE_WAITING, frontier=False)
            )

    return sorted(instances.values(), key=lambda c: (c.scope, c.id, c.subject or ""))
