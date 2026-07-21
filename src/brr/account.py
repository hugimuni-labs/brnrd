"""Home-scoped local state for the daemon.

The daemon stores durable resident/run/control state in a local-first brnrd
home. A home is selected by lane: an unconnected repo gets a project home
derived from its repo identity and absolute path; an explicitly connected
account lane gets an account home. There is no universal ``accounts/default``
fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import gitops

DEFAULT_REPO_LABEL = "local/default"
HOME_ROOT_LABEL = "home"
DEFAULT_STATE_NAMESPACE = "brnrd"
REGISTRY_PATH = "account/repos.json"
DISPATCH_INBOX_PATH = "dispatch/inbox"
RESPONSES_PATH = "dispatch/responses"
RUNS_PATH = "runs"
REPOS_PATH = "repos"
REPO_DOMINION_DIRNAME = "dominion"

# Shared user/resident-authored orientation.  Everything below this root is
# discovered by the wake and dashboard; adding a page does not require a code
# change.  The daemon-owned frame (runs/*/state.md, dispatch/, account/) stays
# outside this directory on purpose.
SURFACE_PATH = "surface"

# Names retained *inside* the discovered surface so the useful plan/ledger
# conventions survive without remaining separate orientation roots.
PLANS_PATH = "plans"
CROSS_REPO_SLUG = "_cross-repo"

# CS6 — stored runner policy
RUNNER_POLICY_PATH = "runner-policy"
ACCOUNT_RUNNER_POLICY_SLUG = "_account"
RUNNER_POLICY_PROPOSALS_SLUG = "_proposals"

# Loom envelope Phase 2 — pending config-change proposals (see
# ``config_change_proposals_path`` below)
CONFIG_CHANGE_PATH = "config-changes"
CONFIG_CHANGE_PROPOSALS_SLUG = "_proposals"

# CS7 — decision ledger
LEDGER_PATH = "ledger"

KNOWLEDGE_PATH = "knowledge"

# Legacy reply archive. New delivery records live in ``runs/``; this name is
# retained only so the idempotent migration can find pre-message-store homes.
REPLIES_PATH = "replies"

GITIGNORE = """\
/dispatch/inbox/
/dispatch/responses/
/knowledge/
/.brr/
*.tmp
"""


@dataclass(frozen=True)
class AccountRepo:
    """A repo registered under one local account daemon."""

    label: str
    root: Path


@dataclass(frozen=True)
class AccountRoot:
    """One selectable execution root in an account home."""

    label: str
    root: Path
    kind: str
    default: bool = False


@dataclass(frozen=True)
class HomeContext:
    """Resolved brnrd-home state for one daemon process."""

    account_id: str
    dominion_repo: Path
    dispatch_inbox: Path
    responses_dir: Path
    runs_dir: Path
    repos: dict[str, AccountRepo]
    default_repo: AccountRepo
    enabled: bool = True
    kind: str = "project"
    home_id: str = ""
    home_root: Path | None = None

    def repo_for_label(self, label: str | None) -> AccountRepo | None:
        if not label:
            return None
        return self.repos.get(label)

    def root_for_label(self, label: str | None) -> AccountRoot | None:
        """Resolve a selectable root without pretending home is a repo."""

        if is_home_label(label):
            return AccountRoot(
                label=HOME_ROOT_LABEL,
                root=context_home_root(self),
                kind="home",
            )
        repo = self.repo_for_label(label)
        if repo is None:
            return None
        return AccountRoot(
            label=repo.label,
            root=repo.root,
            kind="repo",
            default=repo.label == self.default_repo.label,
        )


AccountContext = HomeContext


def is_home_label(label: object) -> bool:
    """Return whether *label* names the reserved account-home root."""

    return str(label or "").strip().casefold() == HOME_ROOT_LABEL


def _truthy(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"", "1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._")
    return text or "home"


def _expand_path(raw: object, *, base: Path | None = None) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(os.path.expandvars(text)).expanduser()
    if not path.is_absolute() and base is not None:
        path = base / path
    return path


def _xdg_state_home() -> Path:
    raw = os.environ.get("XDG_STATE_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "state"


def _path_hash(path: Path) -> str:
    try:
        basis = str(path.resolve())
    except OSError:
        basis = str(path.absolute())
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


def _repo_slug(repo_root: Path, cfg: dict[str, Any] | None = None) -> str:
    return _slug(repo_label(repo_root, cfg).replace("/", "__"))


def _default_project_home(repo_root: Path, cfg: dict[str, Any] | None = None) -> Path:
    name = f"{_repo_slug(repo_root, cfg)}-{_path_hash(repo_root)}"
    return _xdg_state_home() / DEFAULT_STATE_NAMESPACE / "projects" / name / "home"


def _default_account_home(account_id: str) -> Path:
    return _xdg_state_home() / DEFAULT_STATE_NAMESPACE / "accounts" / _slug(account_id) / "home"


def _is_git_worktree(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _init_git_repo(path: Path) -> bool:
    """Init a git repo at *path* iff absent. Returns True on a fresh init.

    The return value is the birth signal ``resolve_context`` uses to seed
    the deed README exactly once — at true birth — so an owner who later
    deletes their deed stays deleted.
    """
    if (path / ".git").exists():
        return False
    result = subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _write_gitignore(path: Path) -> None:
    """Ensure every standing ignore rule is present, appending what's missing.

    Not a one-shot "write if absent": a home created before a rule existed
    (``/knowledge/``, added 2026-07-09 alongside the per-repo knowledge
    split — a nested git repo living untracked-but-ungitignored inside an
    already-git-tracked home is exactly the "embedded repository" confusion
    gitignoring exists to avoid) would otherwise carry a stale
    ``.gitignore`` forever, since ``resolve_context`` only calls this once
    at first creation.
    """
    ignore = path / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    existing_lines = set(existing.splitlines())
    wanted_lines = [line for line in GITIGNORE.splitlines() if line]
    missing = [line for line in wanted_lines if line not in existing_lines]
    if not missing:
        return
    new_text = existing
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    new_text += "\n".join(missing) + "\n"
    tmp = ignore.with_suffix(ignore.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(ignore)


def _load_registry(path: Path) -> tuple[dict[str, AccountRepo], str | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, None
    repos: dict[str, AccountRepo] = {}
    for item in raw.get("repos", []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        root = _expand_path(item.get("path"))
        if is_home_label(label) or str(item.get("kind") or "").casefold() == "home":
            if label and str(item.get("kind") or "").casefold() != "home":
                print(
                    f"[brnrd] warning: ignoring registered repo label {label!r}; "
                    f"{HOME_ROOT_LABEL!r} is reserved for the account home"
                )
            continue
        if label and root is not None:
            repos[label] = AccountRepo(label=label, root=root)
    default_repo = str(raw.get("default_repo") or "").strip() or None
    if is_home_label(default_repo):
        print(
            f"[brnrd] warning: ignoring default repo {default_repo!r}; "
            f"{HOME_ROOT_LABEL!r} is the home root, not a repository"
        )
        default_repo = None
    return repos, default_repo


def _write_registry(
    path: Path,
    repos: dict[str, AccountRepo],
    default_repo: str,
    *,
    account_id: str,
    home_kind: str = "account",
    home_id: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "account_id": account_id,
        "default_repo": default_repo,
        "home_id": home_id or account_id,
        "home_kind": home_kind,
        "repos": [
            {
                "label": HOME_ROOT_LABEL,
                "path": str(path.parent.parent),
                "kind": "home",
            },
            *[
                {"label": label, "path": str(repo.root), "kind": "repo"}
                for label, repo in sorted(repos.items())
                if not is_home_label(label)
            ],
        ],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def repo_label(repo_root: Path, cfg: dict[str, Any] | None = None) -> str:
    """Best-effort label for a repo registry entry."""

    cfg = cfg or {}
    for key in ("repo.label", "repo_label", "github_repo", "repo_full_name"):
        value = str(cfg.get(key) or "").strip()
        if value:
            return value
    try:
        remote = gitops.default_remote(repo_root)
        if remote:
            url = gitops.remote_url(repo_root, remote)
            if url:
                from .gates.github.parse import parse_origin_url

                parsed = parse_origin_url(url)
                if parsed:
                    return parsed
    except Exception:
        pass
    return repo_root.name or DEFAULT_REPO_LABEL


def event_repo_label(event: dict[str, Any]) -> str | None:
    """Return the repo label/address carried by an event, if any."""

    for key in ("github_repo", "repo_full_name", "repo", "repo_label", "repo_id"):
        value = str(event.get(key) or "").strip()
        if value:
            return value
    return None


def _configured_repos(
    cfg: dict[str, Any],
    *,
    base: Path,
) -> dict[str, AccountRepo]:
    repos: dict[str, AccountRepo] = {}
    prefix = "account.repo."
    for key, value in cfg.items():
        if not key.startswith(prefix):
            continue
        label = key[len(prefix):].strip()
        root = _expand_path(value, base=base)
        if is_home_label(label):
            print(
                f"[brnrd] warning: ignoring configured repo label {label!r}; "
                f"{HOME_ROOT_LABEL!r} is reserved for the account home"
            )
            continue
        if label and root is not None:
            repos[label] = AccountRepo(label=label, root=root)
    return repos


def _connected_account_id(repo_root: Path) -> str | None:
    """Return a connected brnrd account id from repo-local cloud state."""

    try:
        brr_dir = gitops.shared_brr_dir(repo_root)
    except Exception:
        return None
    state_path = brr_dir / "gates" / "cloud.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not (state.get("token") and state.get("brnrd_url") and state.get("repo_id")):
        return None
    value = str(state.get("account_id") or state.get("account") or "").strip()
    return value or "connected"


def _explicit_home(cfg: dict[str, Any]) -> Path | None:
    return (
        _expand_path(os.environ.get("BRNRD_HOME"))
        or _expand_path(cfg.get("home.path"))
        or _expand_path(cfg.get("home_root"))
    )


def resolve_context(
    repo_root: Path,
    cfg: dict[str, Any] | None = None,
    *,
    create: bool = True,
) -> HomeContext:
    """Resolve the brnrd home for a daemon started from *repo_root*."""

    cfg = cfg or {}
    explicit_account_id = str(
        cfg.get("account.id")
        or cfg.get("account_id")
        or cfg.get("forge.identity")
        or ""
    ).strip()
    connected_account_id = _connected_account_id(repo_root)
    explicit_home = _explicit_home(cfg)
    kind = str(cfg.get("home.kind") or "").strip().lower()
    if kind not in {"project", "account"}:
        kind = "account" if explicit_account_id or connected_account_id else "project"
    account_id = explicit_account_id or (connected_account_id if kind == "account" else "")
    home_id = account_id if kind == "account" else f"{_repo_slug(repo_root, cfg)}-{_path_hash(repo_root)}"
    home_root = explicit_home or (
        _default_account_home(account_id) if kind == "account"
        else _default_project_home(repo_root, cfg)
    )
    should_create = create and _truthy(cfg.get("home.autocreate", cfg.get("account.autocreate")), True) and (
        explicit_home is not None or _is_git_worktree(repo_root)
    )
    if should_create:
        home_root.mkdir(parents=True, exist_ok=True)
        if _init_git_repo(home_root):
            # Born just now, and silently (no terminal seam here) — the deed
            # README is the ceremony's artifact: it says what this repo is,
            # who writes it, and how to leave, and its commit founds the
            # repo by name instead of leaving an anonymous birth.
            from . import repo_deed

            repo_deed.ensure_deed(home_root, "dominion")
        _write_gitignore(home_root)

    registry_path = home_root / REGISTRY_PATH
    repos, registry_default = _load_registry(registry_path)
    repos.update(_configured_repos(cfg, base=repo_root))

    current_label = repo_label(repo_root, cfg)
    if is_home_label(current_label):
        raise ValueError(
            f"repo label {current_label!r} is reserved for the account home"
        )
    repos.setdefault(current_label, AccountRepo(label=current_label, root=repo_root))
    default_label = str(
        cfg.get("account.default_repo")
        or cfg.get("account_default_repo")
        or registry_default
        or current_label
    ).strip()
    if is_home_label(default_label):
        raise ValueError(
            f"default repo label {default_label!r} is reserved for the account home"
        )
    if default_label not in repos:
        repos[default_label] = AccountRepo(label=default_label, root=repo_root)
    default_repo = repos[default_label]

    if should_create:
        _write_registry(
            registry_path,
            repos,
            default_label,
            account_id=account_id,
            home_kind=kind,
            home_id=home_id,
        )
        for rel in (DISPATCH_INBOX_PATH, RESPONSES_PATH, RUNS_PATH, SURFACE_PATH):
            (home_root / rel).mkdir(parents=True, exist_ok=True)
        _migrate_legacy_run_state(home_root)
        _migrate_legacy_work_surface(home_root)
        _seed_work_surface(home_root, current_label)

    return HomeContext(
        account_id=account_id,
        dominion_repo=home_root,
        dispatch_inbox=home_root / DISPATCH_INBOX_PATH,
        responses_dir=home_root / RESPONSES_PATH,
        runs_dir=home_root / RUNS_PATH,
        repos=repos,
        default_repo=default_repo,
        enabled=_truthy(cfg.get("account.enabled"), True),
        kind=kind,
        home_id=home_id,
        home_root=home_root,
    )


def slug_repo_label(label: str) -> str:
    """Filesystem-safe repo label for account-store paths."""

    return _slug(label.replace("/", "__"))


def context_home_root(ctx: HomeContext) -> Path:
    """Return the brnrd home root for *ctx*."""

    return ctx.home_root or ctx.dominion_repo


def selectable_roots(ctx: HomeContext) -> list[AccountRoot]:
    """Return home plus registered repositories for project selectors."""

    roots = [
        AccountRoot(
            label=HOME_ROOT_LABEL,
            root=context_home_root(ctx),
            kind="home",
        )
    ]
    roots.extend(
        AccountRoot(
            label=repo.label,
            root=repo.root,
            kind="repo",
            default=repo.label == ctx.default_repo.label,
        )
        for repo in sorted(ctx.repos.values(), key=lambda item: item.label)
    )
    return roots


def run_dir(ctx: HomeContext, repo_label: str, run_id: str) -> Path:
    """Return the one durable directory representing a Wyrd run node."""

    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id.strip()).strip("-._")
    safe_run_id = safe_run_id or "unknown-run"
    return ctx.runs_dir / slug_repo_label(repo_label) / safe_run_id


def _migrate_legacy_run_state(home_root: Path) -> int:
    """Fold ``run-state/<repo>/<run>.md`` into each run's ``state.md``.

    State is a current snapshot rather than an append-only history. If an
    interrupted migration left both paths, the newer snapshot wins; git keeps
    the superseded version. The old tree is removed once empty so one run
    cannot retain two canonical representations.
    """

    legacy_root = home_root / "run-state"
    runs_root = home_root / RUNS_PATH
    if not legacy_root.is_dir():
        return 0
    moved = 0
    for source in sorted(legacy_root.glob("*/*.md")):
        destination = runs_root / source.parent.name / source.stem / "state.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not destination.exists() or source.stat().st_mtime_ns >= destination.stat().st_mtime_ns:
                source.replace(destination)
            else:
                source.unlink()
            moved += 1
        except OSError:
            continue
    for directory in sorted(legacy_root.rglob("*"), reverse=True):
        if directory.is_dir():
            try:
                directory.rmdir()
            except OSError:
                pass
    try:
        legacy_root.rmdir()
    except OSError:
        pass
    return moved


def knowledge_path(ctx: HomeContext) -> Path:
    """Return the home-level knowledge directory (single flat bucket).

    This is the physical git repo root regardless of split mode — see
    ``repo_knowledge_path`` / ``account_knowledge_path`` for the two
    sub-scopes an account home can present separately.
    """

    return context_home_root(ctx) / KNOWLEDGE_PATH


def repo_knowledge_path(ctx: HomeContext, repo_label_value: str) -> Path:
    """Return the repo-scoped knowledge directory inside home knowledge."""

    return knowledge_path(ctx) / REPOS_PATH / slug_repo_label(repo_label_value)


def account_knowledge_path(ctx: HomeContext) -> Path:
    """Return the cross-repo (account-wide) knowledge directory."""

    return knowledge_path(ctx) / CROSS_REPO_SLUG


def knowledge_split_mode(cfg: dict[str, Any] | None) -> str:
    """Return the configured knowledge split: ``per-repo`` or ``account-only``.

    Only meaningful for account-kind homes — a project home has exactly one
    repo, so there is nothing to split. ``knowledge.split=account-only`` in
    ``.brr/config`` opts an account home back into one flat bucket.
    """

    cfg = cfg or {}
    value = str(cfg.get("knowledge.split") or "per-repo").strip().lower()
    return "account-only" if value == "account-only" else "per-repo"


def register_repo(
    ctx: HomeContext,
    repo_root: Path,
    *,
    label: str | None = None,
    make_default: bool = True,
) -> AccountRepo:
    """Register *repo_root* in the resolved home registry."""

    repo_label_value = label or repo_label(repo_root)
    if is_home_label(repo_label_value):
        raise ValueError(
            f"repo label {repo_label_value!r} is reserved for the account home"
        )
    repos = dict(ctx.repos)
    repo = AccountRepo(label=repo_label_value, root=repo_root)
    repos[repo_label_value] = repo
    default_label = repo_label_value if make_default else ctx.default_repo.label
    _write_registry(
        context_home_root(ctx) / REGISTRY_PATH,
        repos,
        default_label,
        account_id=ctx.account_id,
        home_kind=ctx.kind,
        home_id=ctx.home_id,
    )
    return repo


def repo_dominion_path(ctx: AccountContext, repo_label: str) -> Path:
    """Return the resident-memory directory for one repo inside an account home."""

    return ctx.dominion_repo / REPOS_PATH / slug_repo_label(repo_label) / REPO_DOMINION_DIRNAME


def work_surface_path(ctx: AccountContext) -> Path:
    """Return the single discovered user/resident-authored work surface."""

    return context_home_root(ctx) / SURFACE_PATH


def _discover_markdown(root: Path) -> list[Path]:
    """Enumerate hardened Markdown files anywhere under *root* (unsorted).

    Shared by the authored surface and the knowledge/runs corpus layers so
    all three carry one hardening rule. ``os.walk`` runs with
    ``followlinks=False`` (the default) so a symlinked *directory* cannot
    smuggle an outside tree into the local-home mirror; file symlinks and
    hidden paths are excluded below for the same reason.
    """

    if not root.is_dir():
        return []
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or not name.endswith(".md"):
                continue
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                files.append(path)
    return files


def work_surface_files(ctx: AccountContext) -> list[Path]:
    """Return authored Markdown files in stable orientation order.

    ``index.md`` leads when present; every other path sorts by its
    home-relative name. Symlinks and hidden paths are excluded so a page cannot
    turn the local-home mirror into an arbitrary filesystem reader.
    """

    root = work_surface_path(ctx)
    files = _discover_markdown(root)
    return sorted(files, key=lambda path: (path.name != "index.md", path.relative_to(root).as_posix()))


# ── Unified corpus: the authored surface joined with home knowledge ──
#
# The dashboard renders one navigable corpus, not three disconnected trees:
# the authored *surface* links kb *knowledge* pages link per-run messages.
# The two git repos (the dominion and the nested brnrd-knowledge clone) stay
# separate — that split is the privacy/ACL boundary. The join happens here at
# discovery, and downstream at render. Every path is *home-relative*
# (``surface/index.md``, ``knowledge/repos/<slug>/foo.md``,
# ``runs/<slug>/<run>/messages/000001-terminal.md``) so a relative link authored in one
# layer resolves into another with no rewrite.

CORPUS_LAYERS = ("authored", "knowledge", "runs")


@dataclass(frozen=True)
class CorpusFile:
    """One discovered Markdown page in the unified corpus."""

    layer: str  # one of CORPUS_LAYERS
    path: str  # home-relative posix path
    abspath: Path


def corpus_files(ctx: AccountContext) -> list[CorpusFile]:
    """Enumerate the navigable corpus across the authored + knowledge layers.

    Layers appear in reading order — authored, then knowledge, then runs —
    and within a layer ``index.md`` leads, then home-relative name order
    (matching :func:`work_surface_files`). Same hardening as the authored
    surface: no symlinked dirs/files, no hidden paths, ``.md`` only.
    """

    home = context_home_root(ctx)
    knowledge = knowledge_path(ctx)
    roots = (
        ("authored", work_surface_path(ctx)),
        ("knowledge", knowledge / REPOS_PATH),
        ("runs", home / RUNS_PATH),
    )
    result: list[CorpusFile] = []
    for layer, root in roots:
        entries: list[CorpusFile] = []
        for abspath in _discover_markdown(root):
            try:
                rel = abspath.relative_to(home).as_posix()
            except ValueError:
                continue  # a root outside home (never expected) is not corpus
            entries.append(CorpusFile(layer=layer, path=rel, abspath=abspath))
        entries.sort(key=lambda f: (Path(f.path).name != "index.md", f.path))
        result.extend(entries)
    return result


def _contains_no_files(root: Path) -> bool:
    """True when *root* is a directory skeleton — no regular files anywhere."""

    return not any(p.is_file() for p in root.rglob("*"))


def _move_surface_entry(src: Path, dst: Path) -> None:
    """Move one legacy authored root without ever merging two histories.

    Tolerant by design: a pre-surface daemon that is still running (or ran
    until the restart that triggers this migration) re-creates the legacy
    roots as empty directory skeletons — ``resolve_context`` used to mkdir
    ``plans/`` on every boot. A skeleton is not authored history; delete it.
    A genuine collision (real content on both sides) must never brick
    ``brnrd up``: warn, leave both in place, and let the boot continue —
    the surface side wins for discovery, the legacy side stays for the
    operator to reconcile by hand.
    """

    if not src.exists():
        return
    if src.is_dir() and _contains_no_files(src):
        shutil.rmtree(src)
        return
    if dst.exists():
        if src.is_file() and dst.is_file() and src.read_bytes() == dst.read_bytes():
            src.unlink()
            return
        print(
            f"[brnrd] work-surface migration: both {src} and {dst} exist with "
            "content; leaving the legacy copy in place (surface wins for "
            "discovery) — reconcile and delete the legacy path by hand"
        )
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def _migrate_legacy_work_surface(home_root: Path) -> None:
    """Fold the three pre-surface authored roots into ``surface/`` once."""

    surface = home_root / SURFACE_PATH
    surface.mkdir(parents=True, exist_ok=True)
    _move_surface_entry(home_root / "workflow.md", surface / "workflow.md")
    _move_surface_entry(home_root / PLANS_PATH, surface / PLANS_PATH)
    _move_surface_entry(home_root / LEDGER_PATH, surface / LEDGER_PATH)


def _seed_work_surface(home_root: Path, repo_label_value: str) -> None:
    """Create the light orientation seed once; subsequent authors own it."""

    surface = home_root / SURFACE_PATH
    index = surface / "index.md"
    if index.exists():
        return
    slug = slug_repo_label(repo_label_value)
    index.write_text(
        "# Work surface\n\n"
        "The shared orientation for user and resident. The wake and dashboard "
        "discover Markdown here because it exists; code chooses typography, "
        "not which authored pages matter.\n\n"
        "## Standing pages\n\n"
        "- [Current plan](plans/" + slug + "/active.md) — ranked moves and "
        "cross-run position for this repo.\n"
        "- [Workflow](workflow.md) — the agreement about autonomy, delivery, "
        "gating, and cadence.\n"
        "- [Decision ledger](ledger/decisions.md) — the shared decision "
        "through-line.\n\n"
        "## Shape\n\n"
        "Keep this layer free-form and link pages that belong together: those "
        "links become the later loom graph's edges. Daemon-attested files such "
        "as `runs/*/state.md` frame each run body, not pages to "
        "move in here. Add a page when it earns a shared purpose; do not turn "
        "the surface into a form.\n",
        encoding="utf-8",
    )


# ── Inter-run plan helpers inside the discovered surface ─────────────


def repo_plans_path(ctx: AccountContext, repo_label: str) -> Path:
    """Return the plans directory for one repo inside an account home.

    The active inter-run plan lives at ``repo_plans_path(...) / "active.md"``.
    Past plans can be archived under ``repo_plans_path(...) / "archive/"``.
    """
    return work_surface_path(ctx) / PLANS_PATH / slug_repo_label(repo_label)


def active_plan_path(ctx: AccountContext, repo_label: str) -> Path:
    """Return the active plan file path for one repo.

    Write or update this file to leave a plan that survives across wakes.
    The daemon injects it at the top of the next wake (perception=injection).
    Retire by deleting or emptying the file.
    """
    return repo_plans_path(ctx, repo_label) / "active.md"


def cross_repo_plans_path(ctx: AccountContext) -> Path:
    """Return the plans directory for cross-repo plans.

    Cross-repo plans (spanning two or more managed repos) live here;
    they cannot belong to any one repo's namespace.
    """
    return work_surface_path(ctx) / PLANS_PATH / CROSS_REPO_SLUG


# ── CS6 — runner policy helpers ───────────────────────────────────────


def runner_policy_path(ctx: AccountContext, repo_label: str) -> Path:
    """Return the stored runner policy file for one repo.

    Standing runner preferences live here (e.g. "prefer haiku for quick
    tasks, escalate to opus for design reviews"). Operators can edit it
    directly; resident-originated changes flow through the daemon-owned
    proposal/approval path. The daemon injects the policy into each wake
    so the resident can reference it when selecting a runner or proposing
    a respawn.
    """
    return (
        ctx.dominion_repo
        / RUNNER_POLICY_PATH
        / slug_repo_label(repo_label)
        / "policy.md"
    )


def account_runner_policy_path(ctx: AccountContext) -> Path:
    """Return the account-wide stored runner policy file.

    Applies across all repos registered under this account. Repo-level
    policy (see :func:`runner_policy_path`) takes precedence.
    """
    return ctx.dominion_repo / RUNNER_POLICY_PATH / ACCOUNT_RUNNER_POLICY_SLUG / "policy.md"


def runner_policy_proposals_path(ctx: AccountContext) -> Path:
    """Return the daemon-owned pending runner-policy proposal directory.

    Residents can propose runner-policy edits, but the daemon applies them
    only after an operator approval event. Pending proposals live here until
    approved or rejected; the policy files themselves stay under the repo or
    account runner-policy paths above.
    """
    return ctx.dominion_repo / RUNNER_POLICY_PATH / RUNNER_POLICY_PROPOSALS_SLUG


# ── Loom envelope Phase 2 — config-change proposals ───────────────────


def config_change_proposals_path(ctx: AccountContext) -> Path:
    """Return the daemon-owned pending config-change proposal directory.

    A resident can propose a change to an allowlisted ``.brr/config`` key
    (today: ``spawn.max_concurrent``) that it wants moved past the ceiling
    the operator set. Unlike runner-policy proposals above, this one is
    never applied on a chat-typed reply — the daemon mints a brnrd.dev
    approve/confirm URL (``gates/cloud.propose_config_change``) and only
    applies the change once that request comes back approved over the
    account's existing ``/v1/daemons/inbox`` long-poll. Pending proposals
    live here until approved, rejected, or superseded.
    """
    return ctx.dominion_repo / CONFIG_CHANGE_PATH / CONFIG_CHANGE_PROPOSALS_SLUG


# ── Decision/workflow conventions inside the discovered surface ──────


def decisions_ledger_path(ctx: AccountContext) -> Path:
    """Return the resident-maintained decision ledger file.

    The resident creates and updates this file with key decisions and
    current plan-position in plain language — the user-facing through-line
    that complements ``kb/log.md`` (which is more technical). When the
    account dominion has a remote, this file is web-visible there.
    """
    return work_surface_path(ctx) / LEDGER_PATH / "decisions.md"


def workflow_doc_path(ctx: AccountContext) -> Path:
    """Return the account-wide workflow preferences doc.

    Co-owned by the user and the resident — the declared pace and flow of
    the collaboration: delivery ceremony level, autonomy scope (whose
    agenda a wake follows), merge/gating policy, progress-visibility
    cadence. Either side edits the file directly; the daemon injects it
    every wake (perception=injection) and mirrors it to the dashboard
    beside the plans, so the preferences are always visible and editable
    rather than folklore. Absent file = the defaults described in the
    injected orientation.
    """
    return work_surface_path(ctx) / "workflow.md"


def run_state_blob_url(
    ctx: AccountContext,
    run_state_path: Path,
    *,
    cfg: dict[str, Any] | None = None,
) -> str | None:
    """Project a persisted run state document to a web-visible URL, or ``None``.

    The account dominion repo is local-first; once it tracks a forge-hosted
    remote (the additive brnrd-projection step), a run state document committed
    under ``runs/<label>/<run>/state.md`` has a stable blob URL. This derives it
    from the dominion repo's remote so the live card and run surfaces can link
    the durable run state object instead of leaking a host-local path that a
    remote chat reader cannot open. Returns ``None`` for a purely-local
    dominion (no remote), an unparseable remote, or a path outside the store —
    callers then fall back to a non-path label rather than an absolute path.
    """
    from . import forges

    try:
        rel = run_state_path.resolve().relative_to(ctx.dominion_repo.resolve())
    except (OSError, ValueError):
        return None
    try:
        remote = gitops.default_remote(ctx.dominion_repo)
        if not remote:
            return None
        url = gitops.remote_url(ctx.dominion_repo, remote)
        if not url:
            return None
        branch = gitops.current_branch(ctx.dominion_repo)
        if branch in ("", "HEAD"):
            # An account dominion can sit on an unborn branch (git init, no
            # commit yet); ``symbolic-ref`` still names it ("main").
            res = subprocess.run(
                ["git", "symbolic-ref", "--short", "HEAD"],
                cwd=ctx.dominion_repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            branch = res.stdout.strip() if res.returncode == 0 else ""
        branch = branch or "main"
        cfg = cfg or {}
        return forges.view_blob_url(
            url,
            branch,
            rel.as_posix(),
            override_kind=cfg.get("account.forge.kind") or cfg.get("forge.kind") or None,
            override_url_base=(
                cfg.get("account.forge.url_base") or cfg.get("forge.url_base") or None
            ),
        )
    except Exception:
        return None


# ── Repo relabel — following a repo that changed address ──────────────
#
# Every resident-memory scope is keyed by ``slug_repo_label(repo_label)``, and
# ``repo_label`` derives from the origin remote. So the day a repo moves
# (``Gurio/brr`` → ``hugimuni-labs/brnrd``), every scope below silently
# re-keys and the resident wakes into an empty home on a mature project. The
# knowledge, the dominion, the plans, the policy and the run history are all
# still on disk — under a slug nothing looks up any more.
#
# ``relabel_repo`` is the migration: move each scope, rewrite the registry
# entry, and leave the history where the next wake reads it.


@dataclass(frozen=True)
class RelabelMove:
    """One scope directory to be carried from the old slug to the new one."""

    scope: str
    src: Path
    dst: Path
    home: str  # "dominion" | "knowledge" — which git repo owns this path


class RelabelError(RuntimeError):
    """A relabel that must not proceed (collision, bad label, wrong home)."""


def relabel_scopes(ctx: HomeContext, label: str) -> list[tuple[str, Path, str]]:
    """Every slug-keyed scope directory for *label*, as (scope, path, home).

    The single source of truth for "what is keyed by the repo slug". A new
    slug-keyed scope added elsewhere in the codebase and not added here is a
    scope that silently fails to migrate — keep this list honest.
    """

    slug = slug_repo_label(label)
    home_root = context_home_root(ctx)
    knowledge_root = knowledge_path(ctx)
    return [
        ("dominion", ctx.dominion_repo / REPOS_PATH / slug, "dominion"),
        ("surface-plans", work_surface_path(ctx) / PLANS_PATH / slug, "dominion"),
        ("runner-policy", ctx.dominion_repo / RUNNER_POLICY_PATH / slug, "dominion"),
        ("runs", home_root / RUNS_PATH / slug, "dominion"),
        ("knowledge", knowledge_root / REPOS_PATH / slug, "knowledge"),
        ("replies", knowledge_root / REPLIES_PATH / slug, "knowledge"),
    ]


def plan_relabel(ctx: HomeContext, old_label: str, new_label: str) -> list[RelabelMove]:
    """Return the moves a relabel would perform. Raises on refusal.

    Pure: touches nothing. ``brnrd account relabel --dry-run`` prints exactly
    this, so an operator can see the blast radius before consenting to it.
    """

    old_label = (old_label or "").strip()
    new_label = (new_label or "").strip()
    if not old_label or not new_label:
        raise RelabelError("both the old and the new repo label are required")
    if is_home_label(old_label) or is_home_label(new_label):
        raise RelabelError(
            f"{HOME_ROOT_LABEL!r} is reserved for the account home and cannot "
            "be used as a repo label"
        )
    if old_label == new_label:
        raise RelabelError(f"{old_label!r} is already the label; nothing to do")

    old_slug = slug_repo_label(old_label)
    new_slug = slug_repo_label(new_label)
    if old_slug == new_slug:
        raise RelabelError(
            f"{old_label!r} and {new_label!r} both slug to {old_slug!r} — the "
            "on-disk layout is identical, so only the registry entry needs "
            "rewriting (rerun without --move, or edit account/repos.json)"
        )

    old_scopes = relabel_scopes(ctx, old_label)
    new_scopes = {scope: path for scope, path, _ in relabel_scopes(ctx, new_label)}

    moves: list[RelabelMove] = []
    for scope, src, home in old_scopes:
        if not src.exists():
            continue
        dst = new_scopes[scope]
        if dst.exists() and any(dst.iterdir()):
            raise RelabelError(
                f"{scope}: {dst} already exists and is not empty — refusing to "
                "merge two histories. Move or remove it first."
            )
        moves.append(RelabelMove(scope=scope, src=src, dst=dst, home=home))
    return moves


def relabel_repo(
    ctx: HomeContext,
    old_label: str,
    new_label: str,
    *,
    dry_run: bool = False,
) -> list[RelabelMove]:
    """Carry every memory scope for *old_label* over to *new_label*.

    Moves the scope directories, rewrites the registry entry (including
    ``default_repo`` when it pointed at the old label), and returns the moves
    performed. Committing the two homes is the caller's job — see
    ``cli.cmd_account_relabel``.
    """

    moves = plan_relabel(ctx, old_label, new_label)
    if dry_run:
        return moves

    for move in moves:
        move.dst.parent.mkdir(parents=True, exist_ok=True)
        if move.dst.exists():  # exists but empty, per plan_relabel's guard
            move.dst.rmdir()
        os.replace(move.src, move.dst)
        # A now-childless parent (repos/, surface/plans/, …) is noise, not history.
        try:
            move.src.parent.rmdir()
        except OSError:
            pass

    _rewrite_surface_index_repo_slug(ctx, old_label, new_label)
    _relabel_registry(ctx, old_label, new_label)
    return moves


def _rewrite_surface_index_repo_slug(
    ctx: HomeContext, old_label: str, new_label: str
) -> None:
    """Keep the seed's current-plan edge live across a repo relabel."""

    index = work_surface_path(ctx) / "index.md"
    if not index.is_file():
        return
    old = f"plans/{slug_repo_label(old_label)}/"
    new = f"plans/{slug_repo_label(new_label)}/"
    content = index.read_text(encoding="utf-8")
    rewritten = content.replace(old, new)
    if rewritten == content:
        return
    tmp = index.with_suffix(".md.tmp")
    tmp.write_text(rewritten, encoding="utf-8")
    tmp.replace(index)


def _relabel_registry(ctx: HomeContext, old_label: str, new_label: str) -> None:
    """Rekey the registry entry for *old_label* to *new_label*, in place."""

    registry = context_home_root(ctx) / REGISTRY_PATH
    repos, default_repo = _load_registry(registry)
    entry = repos.pop(old_label, None)
    if entry is None and new_label not in repos:
        # Nothing registered under either label: the caller relabelled a repo
        # the registry never knew. The scope moves above still stand.
        return
    if entry is not None:
        repos[new_label] = AccountRepo(label=new_label, root=entry.root)
    default = new_label if default_repo == old_label else (default_repo or new_label)
    _write_registry(
        registry,
        repos,
        default,
        account_id=ctx.account_id,
        home_kind=ctx.kind,
        home_id=ctx.home_id,
    )


def detect_relabelled_repo(
    ctx: HomeContext, repo_root: Path, current_label: str
) -> str | None:
    """Return the stale label this repo is still registered under, if it moved.

    The failure this exists for is invisible from the inside: after a repo
    changes address, every memory scope re-keys, the wake's dominion/knowledge
    blocks silently render empty, and a blank home is indistinguishable from a
    new project. The resident cannot tell it has *lost* anything — so the
    detection has to come from outside its context.

    The registry is the tell. It maps label → root, and a move leaves this
    repo's root registered under the old label while ``repo_label`` derives the
    new one. Same root, different label, and the old label still has memory on
    disk ⇒ ``brnrd account relabel`` has not been run yet.

    Returns ``None`` for the ordinary cases: registered under the current
    label, unregistered, or a stale entry with no memory behind it (nothing to
    lose, nothing to say).
    """

    if not current_label:
        return None
    # NB: ``resolve_context`` auto-registers the current repo under its derived
    # label, so after a move the registry holds *both* labels pointing at this
    # same root. "Is the current label registered?" is therefore always yes and
    # tells us nothing — the question that discriminates is which label the
    # memory is actually sitting under.
    if _label_has_memory(ctx, current_label):
        return None
    try:
        here = repo_root.resolve()
    except OSError:
        here = repo_root.absolute()

    for label, repo in ctx.repos.items():
        if label == current_label:
            continue
        try:
            there = repo.root.resolve()
        except OSError:
            there = repo.root.absolute()
        if there != here:
            continue
        # Same tree, another label, and that label is where the memory lives:
        # it moved and the migration hasn't run. A stale entry over an empty
        # home is bookkeeping, not a loss — stay quiet for it.
        if _label_has_memory(ctx, label):
            return label
    return None


def _label_has_memory(ctx: HomeContext, label: str) -> bool:
    """True when any slug-keyed scope for *label* holds something."""

    for _scope, path, _home in relabel_scopes(ctx, label):
        try:
            if path.is_dir() and any(path.iterdir()):
                return True
        except OSError:
            continue
    return False
