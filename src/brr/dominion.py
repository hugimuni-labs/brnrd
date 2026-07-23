"""The agent's dominion — durable, owned working memory.

The current daemon shape stores resident memory inside the account dominion
repo, normally under ``repos/<repo-label>/dominion/``. That account repo is a
local-first git repo: it can stay purely local, or the user can opt into a
remote for off-machine durability. The older repo-local orphan branch
(``brr-home`` at ``.brr/dominion/``) remains supported as a legacy fallback
while installs migrate.

This module owns legacy bootstrap (:func:`ensure_dominion`), account-resident
seed files, and resolving the self-inject index into a wake-time digest
(:func:`resolve_self_inject`).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from . import gitops


DEFAULT_BRANCH = "brr-home"
WORKTREE_DIRNAME = "dominion"
SELF_INJECT_FILE = "self-inject"
PLAYBOOK_FILE = "playbook.md"
COMMIT_LOCK_FILE = "dominion.commit.lock"
SYNC_MARKER_FILE = "dominion.needs-sync"
# Total UTF-8 budget for the wake-time self-inject digest. Sized to fit
# the living playbook seed in full with headroom for the agent's own entries
# (recent pain, current focus); the agent can re-tune what it injects, and a
# repo can override via `dominion.inject_budget_bytes`. Bumped 8192 → 12288 →
# 20480 as the old fused seed grew; the identity-core split made the seed
# smaller again, but the guard test keeps the budget honest.
DEFAULT_INJECT_BUDGET_BYTES = 20480

# The seed playbook copied into a fresh dominion. It's the *starting*
# orientation — the agent owns and evolves its own copy thereafter.
_SEED_PLAYBOOK = Path(__file__).resolve().parent / "prompts" / "dominion-playbook.md"

_HEAD_RE = re.compile(r"^head:(\d+)$")
_TAIL_RE = re.compile(r"^tail:(\d+)$")


@dataclass(frozen=True)
class ResidentDominion:
    """One candidate resident-memory directory for a repo wake."""

    path: Path
    capture_root: Path
    label: str
    legacy: bool = False


def dominion_path(repo_root: Path) -> Path:
    """Return the legacy repo-local dominion worktree path."""
    return gitops.shared_brr_dir(repo_root) / WORKTREE_DIRNAME


def resident_dominion_candidates(
    repo_root: Path,
    cfg: dict | None = None,
    *,
    repo_label: str | None = None,
    include_legacy: bool = True,
) -> list[ResidentDominion]:
    """Return resident-memory locations, newest account path first.

    The account-scoped path is authoritative when present. The repo-local
    orphan-branch path remains a fallback so partially migrated installs can
    still wake with their old memory instead of going blank.
    """

    if cfg is None:
        from . import config as conf

        cfg = conf.load_config(repo_root)
    if not bool(cfg.get("dominion.enabled", cfg.get("dominion_enabled", True))):
        return []

    candidates: list[ResidentDominion] = []
    try:
        from . import account

        ctx = account.resolve_context(repo_root, cfg, create=False)
        if ctx.enabled:
            label = repo_label or account.repo_label(repo_root, cfg)
            candidates.append(
                ResidentDominion(
                    path=account.repo_dominion_path(ctx, label),
                    capture_root=ctx.dominion_repo,
                    label=f"account:{label}",
                )
            )
            candidates.append(
                ResidentDominion(
                    path=ctx.dominion_repo,
                    capture_root=ctx.dominion_repo,
                    label="account-root",
                )
            )
    except Exception:
        pass

    if include_legacy:
        legacy = dominion_path(repo_root)
        candidates.append(
            ResidentDominion(
                path=legacy,
                capture_root=legacy,
                label="legacy-repo-local",
                legacy=True,
            )
        )

    deduped: list[ResidentDominion] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            key = candidate.path.resolve()
        except OSError:
            key = candidate.path
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def seed_account_dominion(path: Path) -> None:
    """Seed missing starter files into an account-scoped resident dominion."""

    _seed(path, readme=_ACCOUNT_README, overwrite=False)


def ensure_dominion(
    repo_root: Path,
    *,
    branch: str = DEFAULT_BRANCH,
    remote: str | None = None,
    push: bool = True,
) -> Path:
    """Materialize the dominion worktree, creating the branch if needed.

    Idempotent. If *branch* is already checked out in a worktree, return
    that path untouched (a daemon restart re-attaches). Otherwise:

    - local branch exists → add the worktree on it (returning after the
      checkout was removed);
    - the remote has the branch → fetch and add a tracking worktree
      (second machine, reinstall, managed failover);
    - neither → create the orphan branch empty, add the worktree, seed a
      skeleton, and push it (best-effort) when a remote exists.

    Returns the worktree path. Raises ``RuntimeError`` only when the
    worktree genuinely cannot be created; boot-path callers treat that as
    a soft failure rather than crashing.
    """
    path = dominion_path(repo_root)

    existing = gitops.branch_checkout_path(repo_root, branch)
    if existing is not None:
        try:
            if existing.resolve() == path.resolve():
                return path
        except OSError:
            pass
        return existing

    if remote is None:
        remote = gitops.default_remote(repo_root)

    if gitops.branch_exists(repo_root, branch):
        gitops.add_worktree(repo_root, path, branch=branch)
        return path

    if remote and gitops.remote_branch_exists(repo_root, remote, branch):
        gitops.fetch_branch(repo_root, remote, branch)
        gitops.add_worktree(
            repo_root, path,
            branch=branch, create_branch=True,
            start_point=f"{remote}/{branch}", track=True,
        )
        return path

    commit = gitops.create_orphan_branch(
        repo_root, branch, message=f"{branch}: initialize dominion",
    )
    if commit is None:
        raise RuntimeError(
            f"could not create dominion branch {branch!r} "
            "(git plumbing failed — is a committer identity configured?)"
        )
    gitops.add_worktree(repo_root, path, branch=branch)
    _seed(path)
    gitops.commit_all(path, f"{branch}: seed dominion")
    if push and remote:
        gitops.push_branch(repo_root, remote, branch)
    return path


def _commit_lock(dominion_dir: Path, timeout: float):
    """Hold an exclusive cross-process lock for the dominion commit step.

    The lock file lives in the shared ``.brr/`` dir (the worktree's parent),
    not inside the worktree, so it never lands in the dominion's own
    history. The locking itself is :func:`brr.gitops.file_lock` — the same
    primitive the knowledge capture net uses, since both serialize the same
    hazard: two processes touching one shared git index.
    """
    return gitops.file_lock(dominion_dir.parent / COMMIT_LOCK_FILE, timeout)


def mark_needs_sync(brr_dir: Path, reason: str) -> None:
    """Record that the dominion's remote diverged (a push was rejected).

    A best-effort hint, written to the runtime dir (gitignored), surfaced
    in the next wake prompt so the resident reconciles the dominion remote
    itself — pull / merge / resolve / push is git-layer dissonance resolution,
    the agent's judgement, not the daemon's (``kb/design-self-scheduled-
    thoughts.md`` → sync companion). Cleared by the next successful push.

    The mechanism is shared with the knowledge chain's marker
    (:func:`brr.gitops.write_sync_marker`) — two memories, one divergence
    protocol.
    """
    gitops.write_sync_marker(brr_dir, SYNC_MARKER_FILE, reason)


def clear_needs_sync(brr_dir: Path) -> None:
    """Clear the dominion sync-needed marker (best-effort)."""
    gitops.clear_sync_marker(brr_dir, SYNC_MARKER_FILE)


def needs_sync(brr_dir: Path) -> str | None:
    """Return the dominion sync-needed reason, or ``None`` when in sync."""
    return gitops.read_sync_marker(brr_dir, SYNC_MARKER_FILE)


def commit(
    dominion_dir: Path,
    message: str,
    *,
    remote: str | None = None,
    branch: str | None = None,
    push: bool = False,
    lock_timeout: float = 30.0,
    conversation_id: str | None = None,
) -> bool:
    """Capture the dominion's working-tree changes as one commit, serialized.

    The persistence half of "the agent is its memory": whatever the
    resident wrote into ``.brr/dominion/`` during a thought is captured
    here, so it survives to the next wake without the agent having to
    remember a commit dance. Called by the daemon after each thought, on
    success *and* failure (a failed thought may still have recorded the
    pain that caused it).

    The commit step is serialized across processes by a file lock so an
    overlapping daemon thought and an ad-hoc session never race the shared
    worktree's git index — file *edits* stay free; only the index-touching
    commit serializes (the Society-of-Mind model,
    ``kb/design-agent-dominion.md`` §4). Returns True when a commit was
    made. A clean worktree is a no-op (False) — most thoughts don't touch
    the dominion. Every failure is swallowed to False: capturing memory
    must never break the thought that produced it.

    **Push is a durability floor, not a merge.** When *push* is on, brr
    best-effort pushes the dominion repo after committing. A *rejected* push
    (the remote diverged — a second machine / failover host wrote it) is
    **not** silently swallowed: it sets a ``needs_sync`` marker so the next
    thought reconciles by hand (fetch / merge / resolve / push is the
    agent's judgement). A successful push clears the marker — including a
    clean-tree no-op push, so a resident that reconciled out-of-band clears
    its own stale marker on the next capture.
    """
    if not dominion_dir.is_dir():
        return False
    brr_dir = dominion_dir.parent
    committed = False
    try:
        with _commit_lock(dominion_dir, lock_timeout) as held:
            if not held:
                return False
            if gitops.worktree_dirty(dominion_dir):
                committed = gitops.commit_all(
                    dominion_dir, message, conversation_id=conversation_id,
                )
        if push and remote:
            target = branch or gitops.current_branch(dominion_dir)
            # Push after a real commit, or to settle a standing divergence
            # (clean tree but a marker is set — the agent may have just
            # reconciled). Otherwise leave the network alone.
            if target and target != "HEAD" and (committed or needs_sync(brr_dir)):
                if gitops.push_branch(dominion_dir, remote, target):
                    clear_needs_sync(brr_dir)
                else:
                    mark_needs_sync(
                        brr_dir,
                        f"push of {target} to {remote} was rejected — the "
                        f"dominion's remote has diverged; reconcile by hand "
                        f"(fetch / merge / push) in {dominion_dir}",
                    )
        return committed
    except Exception:  # noqa: BLE001 - capture is best-effort, never fatal
        return False


@dataclass(frozen=True)
class InjectOverflow:
    """Structured accounting for a self-inject render that overran budget.

    Returned alongside the digest by :func:`resolve_self_inject_digest` so a
    caller (boot score, ``portal-state.json``, …) can surface the shortfall
    without re-parsing the banner prose. This change wires it no further
    than the dominion block itself — a later change can thread it further.
    """

    budget_bytes: int
    clipped_entry: str  # manifest line section-collapsed to fit budget; "" if none was
    clipped_dropped_bytes: int  # bytes cut from clipped_entry
    dropped_entries: tuple[tuple[str, int], ...]  # (manifest line, byte size) never rendered at all
    total_dropped_bytes: int  # clipped_dropped_bytes + every dropped_entries size
    total_source_bytes: int  # what the manifest would take in full, from this point on

    @property
    def dropped_entry_count(self) -> int:
        return len(self.dropped_entries)

    @property
    def percent_dropped(self) -> float:
        if not self.total_source_bytes:
            return 0.0
        return (self.total_dropped_bytes / self.total_source_bytes) * 100


_H2_RE = re.compile(r"(?m)^## .*$")


def _split_h2_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split *text* into a preamble and its H2 (``## ``) sections.

    Each section is ``(heading_line, body)``, where *body* is everything
    from just after the heading through (not including) the next H2 heading
    — so ``preamble + "".join(h + b for h, b in sections) == text`` exactly.
    Playbooks are ordered most-important-first (invariants at top): the
    preamble outranks every section, and earlier sections outrank later
    ones. An empty section list means *text* has no H2 headings at all —
    the caller's degenerate case, handled by :func:`_collapse_blocks_to_budget`.
    """
    matches = list(_H2_RE.finditer(text))
    if not matches:
        return text, []
    preamble = text[: matches[0].start()]
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(), text[body_start:body_end]))
    return preamble, sections


def _split_blocks(text: str) -> list[str]:
    """Split *text* into blank-line-separated top-level blocks.

    Each block keeps its own trailing blank-line separator (if any), so
    ``"".join(_split_blocks(text)) == text`` exactly — reconstructing the
    unclipped original never has to guess at whitespace.
    """
    pieces = re.split(r"(\n{2,})", text)
    blocks: list[str] = []
    for i in range(0, len(pieces), 2):
        block = pieces[i] + (pieces[i + 1] if i + 1 < len(pieces) else "")
        if block:
            blocks.append(block)
    return blocks


def _collapse_stub(dropped: str, *, source_label: str) -> str:
    """The one-line stand-in for a section/block collapsed to fit budget.

    Names its own size so the resident can judge, from the stub alone,
    whether the collapsed material is worth reading in full — and where.
    """
    lines = len(dropped.splitlines())
    n_bytes = len(dropped.encode("utf-8"))
    return (
        f"_(§ collapsed: {lines} lines, {n_bytes:,} bytes — "
        f"full text: {source_label})_"
    )


def _render_collapse_banner(
    *,
    source_label: str,
    source_bytes: int,
    budget_bytes: int,
    rendered_bytes: int,
    collapsed: tuple[str, ...],
) -> str:
    """Mandatory, resident-facing banner whenever section/block collapse
    fired — never a silent reshuffle. Exact byte math, not "roughly": a
    resident can act on "34% of playbook.md collapsed, here's which
    sections", it can't act on a vague "some content was cut"."""
    lines = [
        f"> **self-inject collapsed `{source_label}` to fit budget**",
        (
            f"> source {source_bytes:,} B, budget {budget_bytes:,} B, "
            f"rendered {rendered_bytes:,} B."
        ),
    ]
    if collapsed:
        shown = collapsed[:8]
        names = ", ".join(shown)
        if len(collapsed) > len(shown):
            names += f", and {len(collapsed) - len(shown)} more"
        lines.append(f"> collapsed bottom-up: {names}")
    return "\n".join(lines)


def _collapse_blocks_to_budget(
    text: str, max_bytes: int, *, source_bytes: int, source_label: str,
) -> tuple[str, int]:
    """Degenerate-input fallback for :func:`_collapse_markdown_to_budget`:
    *text* has no H2 headings at all, so there is no section to key
    priority off. Falls back to the same bottom-up treatment at
    blank-line-separated block granularity — still never a mid-line cut.
    The first block (the opening, highest-priority material) is kept whole
    whenever any other block can be collapsed instead.
    """
    blocks = _split_blocks(text)
    collapsed = [False] * len(blocks)
    labels: list[str] = []

    def render() -> str:
        parts = []
        for i, block in enumerate(blocks):
            parts.append(
                _collapse_stub(block, source_label=source_label) + "\n\n"
                if collapsed[i]
                else block
            )
        return "".join(parts)

    def with_banner() -> tuple[str, int]:
        # The banner names its own rendered size, which is a fixed point:
        # guess a size, render, and stop once the render's actual byte
        # length matches the guess (converges in 1-2 steps — the only thing
        # that can move is the digit/comma width of the number itself).
        body_text = render()
        size_guess = 0
        candidate = ""
        for _ in range(4):
            banner = _render_collapse_banner(
                source_label=source_label,
                source_bytes=source_bytes,
                budget_bytes=max_bytes,
                rendered_bytes=size_guess,
                collapsed=tuple(labels),
            )
            candidate = f"{banner}\n\n{body_text}" if body_text.strip() else banner
            actual = len(candidate.encode("utf-8"))
            if actual == size_guess:
                break
            size_guess = actual
        return candidate, len(candidate.encode("utf-8"))

    def dropped_bytes() -> int:
        return sum(
            len(blocks[i].encode("utf-8")) for i in range(len(blocks)) if collapsed[i]
        )

    for i in range(len(blocks) - 1, 0, -1):
        rendered, size = with_banner()
        if size <= max_bytes:
            return rendered, dropped_bytes()
        collapsed[i] = True
        labels.append(f"block {i + 1}")

    rendered, _size = with_banner()
    return rendered, dropped_bytes()


def _collapse_markdown_to_budget(
    text: str, max_bytes: int, *, source_label: str,
) -> tuple[str, int]:
    """Fit *text* into *max_bytes* UTF-8 bytes by collapsing whole sections
    bottom-up, never a mid-line byte cut.

    Playbooks are ordered most-important-first — invariants at top — so a
    budget shortfall must collapse the *least* important material first:
    H2 (``## ``) sections from the bottom of the document upward, each
    replaced by a one-line stub (see :func:`_collapse_stub`) under its own
    heading. The preamble (content before the first H2) is the
    highest-priority material and is never collapsed.

    If collapsing every section but the topmost still doesn't fit, the
    topmost section's own trailing blocks (paragraphs / list items) collapse
    next, bottom-up, within that section — same stub, never mid-line.
    Documents with no H2 headings at all fall back to the same bottom-up
    treatment at blank-line-separated block granularity (see
    :func:`_collapse_blocks_to_budget`).

    A banner naming the exact byte math and which sections collapsed rides
    at the top of the return value whenever *text* didn't already fit
    (never silent) and counts toward *max_bytes* itself.

    Returns ``(text, 0)`` unchanged — byte-identical — when *text* already
    fits *max_bytes*. Otherwise returns ``(rendered, bytes_dropped)``, where
    *bytes_dropped* is the source bytes of whatever got replaced by a stub
    (banner and stub overhead don't count as "dropped" — they're new,
    informational bytes, not content that vanished).
    """
    source_bytes = len(text.encode("utf-8"))
    if source_bytes <= max_bytes:
        return text, 0

    preamble, sections = _split_h2_sections(text)
    if not sections:
        return _collapse_blocks_to_budget(
            text, max_bytes, source_bytes=source_bytes, source_label=source_label,
        )

    headings = [heading for heading, _ in sections]
    bodies = [body for _, body in sections]
    n = len(sections)
    collapsed = [False] * n
    labels: list[str] = []
    top_trim: str | None = None  # replacement body for section 0, once trimmed

    def section_label(i: int) -> str:
        return headings[i].removeprefix("## ").strip()

    def render() -> str:
        parts = [preamble]
        for i in range(n):
            if collapsed[i]:
                stub = _collapse_stub(bodies[i], source_label=source_label)
                parts.append(f"{headings[i]}\n{stub}\n")
            elif i == 0 and top_trim is not None:
                parts.append(headings[0] + top_trim)
            else:
                parts.append(headings[i] + bodies[i])
        return "".join(parts)

    def with_banner() -> tuple[str, int]:
        # The banner names its own rendered size, which is a fixed point:
        # guess a size, render, and stop once the render's actual byte
        # length matches the guess (converges in 1-2 steps — the only thing
        # that can move is the digit/comma width of the number itself).
        body_text = render()
        size_guess = 0
        candidate = ""
        for _ in range(4):
            banner = _render_collapse_banner(
                source_label=source_label,
                source_bytes=source_bytes,
                budget_bytes=max_bytes,
                rendered_bytes=size_guess,
                collapsed=tuple(labels),
            )
            candidate = f"{banner}\n\n{body_text}" if body_text.strip() else banner
            actual = len(candidate.encode("utf-8"))
            if actual == size_guess:
                break
            size_guess = actual
        return candidate, len(candidate.encode("utf-8"))

    top_dropped_bytes = 0

    def dropped_bytes() -> int:
        total = sum(
            len(bodies[i].encode("utf-8")) for i in range(n) if collapsed[i]
        )
        return total + top_dropped_bytes

    # Pass 1: whole sections, bottom-up, topmost (index 0) exempt.
    for i in range(n - 1, 0, -1):
        rendered, size = with_banner()
        if size <= max_bytes:
            return rendered, dropped_bytes()
        collapsed[i] = True
        labels.append(section_label(i))

    rendered, size = with_banner()
    if size <= max_bytes:
        return rendered, dropped_bytes()

    # Pass 2: every section but the topmost is gone and it still doesn't
    # fit — trim the topmost section's own trailing blocks, bottom-up.
    blocks = _split_blocks(bodies[0])
    labels.append(f"{section_label(0)} (trailing items)")
    for cut in range(len(blocks) - 1, -1, -1):
        kept = "".join(blocks[:cut])
        dropped = "".join(blocks[cut:])
        # Keep the heading/body newline even when nothing of the section
        # survives (kept == "") — otherwise the heading and the stub run
        # together on one line.
        prefix = kept if kept.endswith("\n") else kept + "\n"
        top_trim = prefix + _collapse_stub(dropped, source_label=source_label) + "\n"
        top_dropped_bytes = len(dropped.encode("utf-8"))
        rendered, size = with_banner()
        if size <= max_bytes:
            return rendered, dropped_bytes()

    # Nothing left to trim without touching the preamble itself — return the
    # tightest achievable render (topmost section down to just its stub).
    return rendered, dropped_bytes()


def _dropped_entry_marker(label: str, frag_bytes: int) -> str:
    """A rendered stand-in for an entry the budget had no room for at all.

    Requirement: an entry that doesn't fit never drops to zero silently —
    it gets at least this, naming it and its size, so the resident knows a
    page exists that this wake cannot see.
    """
    return (
        f"<!-- self-inject: {label} -->\n"
        f"**[dropped — self-inject budget exhausted]** {frag_bytes:,} B not "
        "rendered this wake; the entry exists but is currently invisible."
    )


def _render_overflow_banner(overflow: InjectOverflow) -> str:
    """Loud, resident-facing summary that opens the digest on overflow.

    Never user-facing — this rides only inside the wake's own self-inject
    block. Actionable, not just factual: the resident can curate a file it
    is told is over budget; it can't act on a silent haircut.
    """
    lines = [
        "> **self-inject overflow — this wake is not seeing its full memory**",
        (
            f"> {overflow.total_dropped_bytes:,} B cut of "
            f"{overflow.total_source_bytes:,} B "
            f"({overflow.percent_dropped:.0f}%), budget "
            f"{overflow.budget_bytes:,} B."
        ),
    ]
    if overflow.clipped_entry:
        lines.append(
            f"> section-collapsed to fit: `{overflow.clipped_entry}` "
            f"— {overflow.clipped_dropped_bytes:,} B cut (see its own "
            "collapse banner below for which sections)."
        )
    if overflow.dropped_entries:
        names = ", ".join(
            f"`{label}` ({n:,} B)" for label, n in overflow.dropped_entries
        )
        lines.append(f"> dropped entirely — never rendered this wake: {names}")
    return "\n".join(lines)


def resolve_self_inject_digest(
    dominion_dir: Path,
    *,
    budget_bytes: int = DEFAULT_INJECT_BUDGET_BYTES,
) -> tuple[str, InjectOverflow | None]:
    """Resolve the self-inject manifest into a wake-time digest.

    Reads the ``self-inject`` manifest (one ``<mode> <path>`` entry per
    line; ``#`` comments and blank lines ignored), renders each entry
    against the dominion's own files, and concatenates the fragments in
    order within *budget_bytes* (UTF-8) — entries past the budget are
    accounted for, not just truncated, so order the manifest by importance.

    Modes: ``full`` | ``head:N`` | ``tail:N`` | ``grep:<pattern>``.
    ``exec`` is recognised but **not run** yet — it is the
    integrity-sensitive entry and lands with its guard in a later slice,
    so such entries are skipped.

    Returns ``(digest, overflow)``: *digest* is ``""`` when the manifest is
    missing, empty, or resolves to nothing. *overflow* is ``None`` on the
    happy path (everything fit — digest is byte-identical to a budget-less
    render) and an :class:`InjectOverflow` when something didn't fit: the
    first entry that overflows the budget is section-aware collapsed (see
    :func:`_collapse_markdown_to_budget`) rather than cut at a byte offset,
    and every entry after it still gets a rendered marker naming it and its
    size — nothing is ever dropped to zero silently.
    """
    manifest = dominion_dir / SELF_INJECT_FILE
    if not manifest.exists():
        return "", None

    fragments: list[str] = []
    used = 0
    overflowed = False
    clipped_label = ""
    clipped_dropped = 0
    clipped_kept_bytes = 0
    dropped_entries: list[tuple[str, int]] = []

    for raw in manifest.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        mode, _, rest = line.partition(" ")
        target = rest.strip()
        if not target:
            continue
        rendered = _render_entry(dominion_dir, mode, target)
        if not rendered:
            continue
        fragment = f"<!-- self-inject: {line} -->\n{rendered}".rstrip()
        sep = 2 if fragments else 0  # fragments are joined with "\n\n"
        frag_bytes = len(fragment.encode("utf-8"))

        if not overflowed and used + sep + frag_bytes <= budget_bytes:
            fragments.append(fragment)
            used += sep + frag_bytes
            continue

        if not overflowed:
            # First entry that doesn't fit: collapse it section-aware and
            # switch to accounting mode. Nothing past this point gets real
            # content (the budget is spent), but every entry — this one
            # included — still gets something rendered.
            overflowed = True
            remaining = budget_bytes - used - sep
            collapsed_text = ""
            collapse_drop = frag_bytes
            if remaining > 0:
                collapsed_text, collapse_drop = _collapse_markdown_to_budget(
                    fragment, remaining, source_label=Path(target).name,
                )
            if collapsed_text.strip():
                fragments.append(collapsed_text)
                clipped_label = line
                clipped_dropped = collapse_drop
                clipped_kept_bytes = len(collapsed_text.encode("utf-8"))
            else:
                # No room even for the collapse banner itself — this entry
                # is fully dropped, exactly like every one after it.
                dropped_entries.append((line, frag_bytes))
                fragments.append(_dropped_entry_marker(line, frag_bytes))
            continue

        # Budget already spent — every later entry is dropped entirely,
        # but never silently: each still gets a rendered marker.
        dropped_entries.append((line, frag_bytes))
        fragments.append(_dropped_entry_marker(line, frag_bytes))

    digest = "\n\n".join(fragments).strip()
    if not overflowed:
        return digest, None

    total_dropped = clipped_dropped + sum(n for _, n in dropped_entries)
    total_source = used + clipped_kept_bytes + total_dropped
    overflow = InjectOverflow(
        budget_bytes=budget_bytes,
        clipped_entry=clipped_label,
        clipped_dropped_bytes=clipped_dropped,
        dropped_entries=tuple(dropped_entries),
        total_dropped_bytes=total_dropped,
        total_source_bytes=total_source,
    )
    banner = _render_overflow_banner(overflow)
    return (f"{banner}\n\n{digest}" if digest else banner), overflow


def resolve_self_inject(
    dominion_dir: Path,
    *,
    budget_bytes: int = DEFAULT_INJECT_BUDGET_BYTES,
) -> str:
    """Resolve the self-inject manifest into a wake-time digest.

    Thin wrapper over :func:`resolve_self_inject_digest` for callers that
    only need the rendered text (the overflow banner, when present, rides
    inline at the top of the returned string). Use
    :func:`resolve_self_inject_digest` directly for the structured
    accounting.
    """
    digest, _overflow = resolve_self_inject_digest(
        dominion_dir, budget_bytes=budget_bytes,
    )
    return digest


def _render_entry(dominion_dir: Path, mode: str, target: str) -> str:
    """Render one self-inject entry to text, or ``""`` to skip it."""
    if mode == "exec":
        return ""  # deferred: persistent-execution surface needs its guard

    candidate = dominion_dir / target
    try:
        resolved = candidate.resolve()
        resolved.relative_to(dominion_dir.resolve())
    except (OSError, ValueError):
        return ""  # keep reads inside the dominion
    if not resolved.is_file():
        return ""
    text = resolved.read_text(encoding="utf-8", errors="replace")

    if mode == "full":
        return text.rstrip("\n")
    head = _HEAD_RE.match(mode)
    if head:
        return "\n".join(text.splitlines()[: int(head.group(1))])
    tail = _TAIL_RE.match(mode)
    if tail:
        n = int(tail.group(1))
        return "\n".join(text.splitlines()[-n:]) if n else ""
    if mode.startswith("grep:"):
        pattern = mode[len("grep:"):]
        if not pattern:
            return ""
        try:
            rx = re.compile(pattern)
            matched = [ln for ln in text.splitlines() if rx.search(ln)]
        except re.error:
            matched = [ln for ln in text.splitlines() if pattern in ln]
        return "\n".join(matched)
    return ""  # unknown mode


def _write_seed_file(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    path.write_text(text, encoding="utf-8")


def _seed(path: Path, *, readme: str = "", overwrite: bool = True) -> None:
    """Write starter files into a freshly created or newly scoped dominion."""
    path.mkdir(parents=True, exist_ok=True)
    _write_seed_file(path / "README.md", readme or _README, overwrite=overwrite)
    _write_seed_file(
        path / PLAYBOOK_FILE,
        _SEED_PLAYBOOK.read_text(encoding="utf-8"),
        overwrite=overwrite,
    )
    _write_seed_file(path / SELF_INJECT_FILE, _SELF_INJECT_SEED, overwrite=overwrite)
    _write_seed_file(path / "pitfalls.md", _PITFALLS_SEED, overwrite=overwrite)
    _write_seed_file(path / "schedule.md", _SCHEDULE_SEED, overwrite=overwrite)


_README = """\
# brr-home — the resident agent's working memory

This is an **orphan branch**: it shares no history with `main` and never merges
into it, so it won't appear in `main`'s diffs or pull requests. It's named
plainly so it reads as ordinary infrastructure to anyone browsing the repo —
nothing here needs your review.

It is brr's durable, owned working memory: the space the agent governs and
carries across runs — its notes, learned pitfalls, schedule, and playbook (the
design calls it the *dominion*; see `kb/design-agent-dominion.md` on `main`).
You're welcome to look — it's inspectable on purpose. You just don't have to.

## Please don't delete this branch

brr pushes `brr-home` as the **off-machine backup** of that memory. Deleting the
remote branch removes the only copy that outlives the machine running the agent,
so it can lose the context it has built up. (If the daemon is still running it
recreates the branch on its next push — but anything that lived only on the
remote is gone.) Leaving it alone costs you nothing: it never touches `main`.

Per-task branches named `brr/<run-id>` are a different thing — ordinary feature
branches that open PRs, safe to handle like any other.
"""

_ACCOUNT_README = """\
# Dominion — resident agent working memory

This directory is the resident-owned working memory for one registered repo
inside the account dominion repo. It is intentionally separate from the
project's source checkout and from the shared `kb/`: notes here are the
resident's workshop until a durable, user-shared fact is promoted outward.

The surrounding account home is local-first. It can remain only on this
machine, or you can opt into durability by pointing the account git repo at a
remote and letting brr push it. Default startup does not create a GitHub repo
or any other forge object on your behalf.
"""

_SELF_INJECT_SEED = """\
# self-inject — what rides into context on each wake.
#
# One entry per line: <mode> <path>
#   modes: full | head:N | tail:N | grep:<pattern> | exec
# Lines starting with '#' are comments. This file is yours: add, remove, and
# reorder freely. A budget cap bounds the total, and entries past it are
# truncated — so order by importance.
full playbook.md
"""

_PITFALLS_SEED = """\
# Pitfalls — trigger-indexed failure memory
#
# The *remember* step of the environment-shaping loop. When you hit
# friction worth recording but not yet worth a forcing function, write it
# here: brr surfaces a pitfall in your wake prompt when one of its
# triggers appears in the task at hand — the lesson placed in your path,
# not prose you must remember to re-read.
#
# Format: a `## ` heading (the lesson's name), a `trigger:` line
# (comma-separated keywords or loci that tend to appear when the failure
# is about to recur), then the lesson. Slash a pitfall once a lint, test,
# or baked tool guards the failure — the forcing function is the better
# memory, and a stale pitfall is just orientation tax.
#
# Example (delete once you have real ones):
#
# ## Blind 5xx retry masks caller bugs
# trigger: retry, 5xx, http client
# The HTTP client surfaces 5xx to the caller without retrying. If you add
# a retry, gate it behind idempotency — a blind retry hid a caller bug
# here before.
"""

_SCHEDULE_SEED = """\
# Schedule — thoughts you wake yourself for
#
# This is how you stop being purely reactive. Each entry here becomes an
# event in your inbox when it comes due — a fresh thought, woken on your
# own clock rather than by a user. Use it to defer work, run periodic
# upkeep, or chain a train of thought across wakes.
#
# Format: a `## ` heading (the entry's id), one trigger line, then the
# body — the prompt for the woken thought.
#   trigger:  at: <ISO-8601>   one-shot   e.g. at: 2026-06-10T09:00:00Z
#             every: <dur>     recurring  e.g. every: 24h  (s/m/h/d, summable: 1h30m)
# `every` is anchored when first seen (adding it doesn't fire instantly),
# then fires each interval. A self-scheduled thought's effect is the work
# it does (an edit, a commit, a reconcile) — it has no chat to reply to.
# This file is yours: add, edit, and remove entries freely.
#
# Example (delete once you have real ones):
#
# ## reconcile my dominion
# every: 24h
# Fetch and reconcile the account dominion remote if one is configured:
# pull, resolve any conflicts with the remote, and push. Then skim
# pitfalls.md and self-inject for anything stale.
"""
