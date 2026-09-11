"""``brnrd dominion consolidate`` — one resident, one dominion.

Move 1 of design-one-resident-per-machine.md. The resident's memory lives
per repo today (``<home>/repos/<label>/dominion/``) and the global kb under a
slug nobody reads as "mine" (``knowledge/_cross-repo/``). This command moves
both to where one resident keeps them:

- the primary repo's dominion (the account's default repo, unless named)
  → ``<home>/dominion/``;
- every other repo's dominion → ``<home>/dominion/places/<label>/``, as
  notes, with a ``places/README.md`` saying where each came from;
- ``knowledge/_cross-repo/*`` → ``knowledge/global/``.

Each emptied location keeps a one-line ``MOVED.md`` pointer. Moves are
``git mv`` inside whichever git repo owns the paths (the home and the
knowledge tree are separate repos), then one commit per repo. A dirty repo
refuses the whole run before anything moves. A second run finds nothing to
move and commits nothing.

The code that *reads* the new layout (``account.home_dominion_path``,
``account.account_knowledge_path``) resolves the old one until this has run,
so merging the code changes nothing; running this is the switch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as _dt
from pathlib import Path

from . import account, gitops
from . import schedule as schedule_mod


POINTER_FILE = "MOVED.md"
PLACES_README = "README.md"
# Left behind rather than moved: Finder litter, never memory.
_SKIP_NAMES = frozenset({POINTER_FILE, ".DS_Store"})


class ConsolidateError(RuntimeError):
    """The plan cannot be applied as it stands (a blocker, or a git failure)."""


@dataclass
class Move:
    src: Path
    dst: Path


@dataclass
class Step:
    title: str
    source: Path
    target: Path
    repo: Path | None
    moves: list[Move] = field(default_factory=list)
    place_label: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Plan:
    home: Path
    steps: list[Step]
    blockers: list[str]

    @property
    def empty(self) -> bool:
        return not any(step.moves for step in self.steps)

    def repos(self) -> list[Path]:
        seen: list[Path] = []
        for step in self.steps:
            if step.moves and step.repo is not None and step.repo not in seen:
                seen.append(step.repo)
        return seen


def _toplevel(path: Path) -> Path | None:
    probe = path if path.is_dir() else path.parent
    if not probe.is_dir():
        return None
    res = gitops._git(probe, "rev-parse", "--show-toplevel", check=False)
    out = res.stdout.strip() if res.returncode == 0 else ""
    return Path(out).resolve() if out else None


def _entries(source: Path) -> list[Path]:
    if not source.is_dir():
        return []
    return sorted(p for p in source.iterdir() if p.name not in _SKIP_NAMES)


def _step(
    title: str, source: Path, target: Path, blockers: list[str], home: Path,
    *, place_label: str | None = None,
) -> Step:
    step = Step(
        title=title, source=source, target=target, repo=_toplevel(source),
        place_label=place_label,
    )
    for entry in _entries(source):
        dst = target / entry.name
        if dst.exists() or dst.is_symlink():
            blockers.append(
                f"{_rel(dst, home)} already exists — {_rel(entry, home)} "
                "would overwrite it; reconcile by hand first"
            )
        step.moves.append(Move(src=entry, dst=dst))
    if step.moves and step.repo is None:
        blockers.append(f"{_rel(source, home)} is not inside a git repo")
    if step.moves and step.repo is not None:
        try:
            target.resolve().relative_to(step.repo)
        except ValueError:
            blockers.append(
                f"{_rel(target, home)} is outside {step.repo} — a git mv "
                "cannot cross repositories"
            )
    return step


def build_plan(ctx: account.HomeContext, *, primary_label: str | None = None) -> Plan:
    """Read the home and say what a consolidation would move. Moves nothing."""

    home = account.context_home_root(ctx)
    home_dominion = account.home_dominion_path(ctx)
    primary = primary_label or ctx.default_repo.label
    primary_slug = account.slug_repo_label(primary)
    blockers: list[str] = []
    steps: list[Step] = []

    primary_source = account.repo_dominion_path(ctx, primary)
    steps.append(_step(
        f"primary dominion ({primary})", primary_source, home_dominion,
        blockers, home,
    ))

    repos_dir = home / account.REPOS_PATH
    others = sorted(
        d for d in repos_dir.glob(f"*/{account.REPO_DOMINION_DIRNAME}")
        if d.is_dir() and d.parent.name != primary_slug
    ) if repos_dir.is_dir() else []
    for source in others:
        slug = source.parent.name
        step = _step(
            f"place notes ({slug})", source,
            home_dominion / account.PLACES_DIRNAME / slug, blockers, home,
            place_label=slug,
        )
        try:
            armed = schedule_mod.parse_schedule(source)
        except Exception:  # noqa: BLE001 - a warning, never a blocker
            armed = []
        if step.moves and armed:
            step.notes.append(
                f"{len(armed)} schedule entr{'y' if len(armed) == 1 else 'ies'} "
                f"in {slug}'s schedule.md will not fire from places/ — move "
                "them into dominion/schedule.md to keep them"
            )
        steps.append(step)

    knowledge_root = account.knowledge_path(ctx)
    steps.append(_step(
        "global kb", knowledge_root / account.CROSS_REPO_SLUG,
        knowledge_root / account.GLOBAL_KB_SLUG, blockers, home,
    ))

    plan = Plan(home=home, steps=steps, blockers=blockers)
    for repo in plan.repos():
        res = gitops._git(repo, "status", "--porcelain", check=False)
        dirty = [line for line in res.stdout.splitlines() if line.strip()]
        if res.returncode != 0:
            blockers.append(f"{_rel(repo, home)}: git status failed")
        elif dirty:
            blockers.append(
                f"{_rel(repo, home) or '.'} has {len(dirty)} uncommitted "
                "change(s) — commit them first (stop the daemon: its capture "
                "net writes here), so the move lands as its own commit"
            )
    return plan


def _rel(path: Path, home: Path) -> str:
    try:
        rel = path.resolve().relative_to(home.resolve()).as_posix()
        return "." if rel == "." else rel
    except (OSError, ValueError):
        return str(path)


def render_plan(plan: Plan, *, applying: bool = False) -> str:
    """The plan as the terminal shows it, dry run or not."""

    head = "applying" if applying else "dry run — nothing moved"
    lines = [f"brnrd dominion consolidate — {head}", f"home: {plan.home}", ""]
    if plan.empty:
        lines.append("nothing to move — already one dominion and one global kb.")
        return "\n".join(lines)
    n = 0
    for step in plan.steps:
        if not step.moves:
            continue
        n += 1
        repo = _rel(step.repo, plan.home) if step.repo else "(no git repo)"
        count = len(step.moves)
        lines.append(
            f"{n}. {step.title}: {_rel(step.source, plan.home)}/ → "
            f"{_rel(step.target, plan.home)}/  "
            f"({count} entr{'y' if count == 1 else 'ies'}, git: {repo})"
        )
        names = [m.src.name + ("/" if m.src.is_dir() else "") for m in step.moves]
        lines.append("   " + ", ".join(names))
        lines.append(f"   pointer left: {_rel(step.source / POINTER_FILE, plan.home)}")
        for note in step.notes:
            lines.append(f"   ⚠ {note}")
    lines.append("")
    if plan.blockers:
        lines.append("refused — fix these first:")
        lines.extend(f"  ✗ {b}" for b in plan.blockers)
    elif not applying:
        lines.append("re-run with --apply to git mv and commit.")
    return "\n".join(lines)


def _git(repo: Path, *args: str) -> None:
    res = gitops._git(repo, *args, check=False)
    if res.returncode != 0:
        raise ConsolidateError(
            f"git {' '.join(args[:2])} failed in {repo}: "
            f"{(res.stderr or res.stdout).strip()}"
        )


def _repo_rel(path: Path, repo: Path) -> str:
    return path.resolve().relative_to(repo).as_posix()


def apply_plan(plan: Plan, *, today: str | None = None) -> list[str]:
    """Move, leave pointers, commit once per repo. Returns one line per commit."""

    if plan.blockers:
        raise ConsolidateError("; ".join(plan.blockers))
    if plan.empty:
        return []
    date = today or _dt.date.today().isoformat()
    touched: dict[Path, list[str]] = {}
    places: list[Step] = []
    for step in plan.steps:
        if not step.moves or step.repo is None:
            continue
        repo = step.repo
        step.target.mkdir(parents=True, exist_ok=True)
        for move in step.moves:
            src_rel = _repo_rel(move.src, repo)
            dst_rel = _repo_rel(move.dst, repo)
            tracked = gitops._git(repo, "ls-files", "--", src_rel, check=False)
            if tracked.stdout.strip():
                _git(repo, "mv", "-k", src_rel, dst_rel)
            # Ignored or untracked leftovers (a dirty tree was refused, so
            # these can only be ignored files) move on disk, not in git.
            if move.src.exists() and not move.dst.exists():
                move.src.rename(move.dst)
        step.source.mkdir(parents=True, exist_ok=True)
        pointer = step.source / POINTER_FILE
        pointer.write_text(
            f"Moved to `{_repo_rel(step.target, repo)}/` by "
            f"`brnrd dominion consolidate`, {date}.\n",
            encoding="utf-8",
        )
        _git(repo, "add", "-f", "--", _repo_rel(pointer, repo))
        touched.setdefault(repo, []).append(
            f"- {_repo_rel(step.source, repo)}/ → {_repo_rel(step.target, repo)}/ "
            f"({len(step.moves)} entries)"
        )
        if step.place_label:
            places.append(step)

    if places:
        readme = places[0].target.parent / PLACES_README
        repo = places[0].repo
        assert repo is not None
        text = readme.read_text(encoding="utf-8") if readme.exists() else (
            "# Places — notes folded in from other repos' dominions\n\n"
            "Before one resident kept one dominion, each repo had its own. "
            "`brnrd dominion consolidate` folded the others in here as notes. "
            "Nothing here is read at wake, and a `schedule.md` here does not "
            "fire: an entry that should keep firing belongs in "
            "`dominion/schedule.md`.\n\n"
        )
        for step in places:
            row = (
                f"- `{step.place_label}/` ← `{_repo_rel(step.source, repo)}/` "
                f"({date})\n"
            )
            if f"`{step.place_label}/`" not in text:
                text += row
        readme.write_text(text, encoding="utf-8")
        _git(repo, "add", "--", _repo_rel(readme, repo))

    done: list[str] = []
    for repo, rows in touched.items():
        message = (
            "dominion: consolidate into one dominion and one global kb\n\n"
            + "\n".join(rows)
            + "\n\nbrnrd dominion consolidate (design-one-resident-per-machine, move 1)."
        )
        _git(repo, "commit", "-q", "-m", message)
        sha = gitops._git(repo, "rev-parse", "--short", "HEAD", check=False).stdout.strip()
        done.append(f"committed {sha} in {repo}")
    return done
