"""`land: <pr>` — the frame lands a pull request it has read green.

The seat's own merges were gated by shell habits: a grepped
``gh pr checks | awk | grep`` merged #1969 with ``Backend tests · pending`` on
the screen, and a receipt composed before its read misreported the checkout
on #1973. This verb moves the gate into the frame, and every sentence it
writes carries a value it read first.

The steps, in order — each refusal stops the verb and names what was read:

1. **parse** — the value is a PR number (``land: 1974`` or ``land: #1974``).
   A strand is refused: merging is the seat's grant, a strand hands its PR
   back to its parent.
2. **the PR** — ``gh pr view <n> --json number,state,headRefOid,baseRefName,
   title,url,files``. Anything but ``OPEN`` is refused.
3. **the checks, structurally** — ``gh pr checks <n> --json name,state``.
   Refused unless there is at least one check and every one is ``SUCCESS``
   or ``SKIPPED``; the notice names every other check with its state. No
   checks at all is refused too: a check that never ran is not green.
4. **merge** — ``gh pr merge <n> --squash --match-head-commit <head>``: the
   head whose checks were read is the only head GitHub will merge. A push
   that lands between the read and the merge makes the merge fail, never
   merge unread code.
5. **read back** — ``gh pr view <n> --json state,mergeCommit`` (read even
   when the merge call errored: a timed-out merge may have merged), then
   ``git fetch origin <base>`` and ``git merge-base --is-ancestor <sha>
   origin/<base>`` in the daemon's checkout. Not on ``origin/<base>`` ⇒ a
   ``dropped`` notice with the sha, and no announcement: nothing is
   composed before its read.
6. **fast-forward the daemon's checkout — only if** the base is ``main``,
   the checkout is on ``main``, ``git status --porcelain`` reads clean, and
   no live strand in this daemon touches a file the PR touched (a strand
   whose touched files cannot be read counts as touching). The reason for
   not fast-forwarding goes into the announcement. The fast-forward itself
   is ``gitops.fast_forward_branch`` (``merge --ff-only``); its detail is
   read back. ``dev_reload``'s premise holds unchanged: a Python change in
   the checkout requests a reload that the main loop applies only when idle
   with no live spawns — so the one hazard left is a live strand's files
   moving under it, which step 6 refuses.
7. **produce** — a typed row ``{kind: knot, ref: <sha>, at, verb: land, pr}``
   appended to ``runs/<run>/produce.jsonl``, and a ``merge`` relic from the
   same read so today's card/chip projections see it.
8. **announce** — one line through the existing reply path (the table's
   ``event`` row, via ``then=``): ``Merged: #N → <sha> · checks: … ·
   checkout: …``.

The GitHub and git reads go through :class:`GhCli` / :class:`GitCheckout`;
tests hand :func:`handle` fakes through :data:`seams`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .. import daemon
from .. import gitops
from ..hud import PRODUCE_LEDGER_NAME
from .. import relics
from ..run import Run, run_manifest_path
from .shapes import Handled, OutboxFile, Produce

GREEN_STATES = frozenset({"SUCCESS", "SKIPPED"})
PRODUCE_NAME = PRODUCE_LEDGER_NAME  # one spelling: the HUD projects what land writes


class LandError(Exception):
    """A read or a write the verb could not complete — its text is the notice."""


@dataclass(frozen=True)
class Check:
    name: str
    state: str


class GitHub(Protocol):
    def pr(self, number: int) -> dict[str, Any]: ...
    def checks(self, number: int) -> list[Check]: ...
    def merge(self, number: int, *, head_sha: str) -> None: ...


class Checkout(Protocol):
    def fetch(self, base: str) -> None: ...
    def contains(self, sha: str, ref: str) -> bool: ...
    def current_branch(self) -> str: ...
    def dirty_paths(self) -> set[str] | None: ...
    def fast_forward(self, branch: str, source_ref: str) -> tuple[bool, str]: ...
    def head(self) -> str: ...


def _clean_git_env() -> dict[str, str]:
    """The daemon's env minus a pinned ``GIT_DIR``/``GIT_WORK_TREE``: every git
    call here names its own checkout by ``cwd``."""
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def _run(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, env=_clean_git_env(), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LandError(f"`{' '.join(argv[:3])}` did not answer: {exc}") from exc


def _tail(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:300] if lines else ""


class GhCli:
    """GitHub through the ``gh`` CLI the daemon already reads PRs with
    (``forge_pr_cache``), in the daemon's checkout."""

    def __init__(self, repo_root: Path, repo_label: str = "") -> None:
        self.repo_root = repo_root
        self.repo_args = ["--repo", repo_label] if repo_label else []

    def _json(self, argv: list[str]) -> Any:
        result = _run(["gh", *argv, *self.repo_args], self.repo_root, 60)
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise LandError(
                f"`gh {argv[0]} {argv[1]}` returned no JSON (exit {result.returncode}): "
                f"{_tail(result.stderr) or _tail(result.stdout) or 'no output'}"
            ) from None

    def pr(self, number: int) -> dict[str, Any]:
        data = self._json([
            "pr", "view", str(number), "--json",
            "number,state,headRefOid,baseRefName,title,url,files,mergeCommit",
        ])
        if not isinstance(data, dict):
            raise LandError(f"`gh pr view {number}` returned {type(data).__name__}")
        return data

    def checks(self, number: int) -> list[Check]:
        # `gh pr checks` exits 8 while checks are pending and 1 on a failure;
        # the JSON on stdout is the read either way.
        result = _run(
            ["gh", "pr", "checks", str(number), "--json", "name,state", *self.repo_args],
            self.repo_root, 60,
        )
        if not result.stdout.strip() and "no checks reported" in result.stderr:
            return []
        try:
            rows = json.loads(result.stdout)
        except ValueError:
            raise LandError(
                f"`gh pr checks {number}` returned no JSON (exit {result.returncode}): "
                f"{_tail(result.stderr) or 'no output'}"
            ) from None
        return [
            Check(name=str(row.get("name") or "?"), state=str(row.get("state") or "?").upper())
            for row in rows if isinstance(row, dict)
        ]

    def merge(self, number: int, *, head_sha: str) -> None:
        result = _run(
            ["gh", "pr", "merge", str(number), "--squash",
             "--match-head-commit", head_sha, *self.repo_args],
            self.repo_root, 180,
        )
        if result.returncode != 0:
            raise LandError(
                f"`gh pr merge {number}` exit {result.returncode}: "
                f"{_tail(result.stderr) or _tail(result.stdout) or 'no output'}"
            )


class GitCheckout:
    """The daemon's own checkout."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def _git(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
        return _run(["git", *args], self.repo_root, timeout)

    def fetch(self, base: str) -> None:
        result = self._git("fetch", "--quiet", "origin", base, timeout=120)
        if result.returncode != 0:
            raise LandError(f"`git fetch origin {base}` exit {result.returncode}: {_tail(result.stderr)}")

    def contains(self, sha: str, ref: str) -> bool:
        return self._git("merge-base", "--is-ancestor", sha, ref).returncode == 0

    def current_branch(self) -> str:
        result = self._git("symbolic-ref", "--quiet", "--short", "HEAD")
        return result.stdout.strip() if result.returncode == 0 else ""

    def dirty_paths(self) -> set[str] | None:
        return gitops.dirty_paths_or_none(self.repo_root)

    def fast_forward(self, branch: str, source_ref: str) -> tuple[bool, str]:
        result = gitops.fast_forward_branch(self.repo_root, branch, source_ref)
        return bool(result.success), str(getattr(result, "detail", "") or "")

    def head(self) -> str:
        result = self._git("rev-parse", "HEAD")
        return result.stdout.strip() if result.returncode == 0 else ""


def live_strand_paths(task: Any, brr_dir: Path) -> list[tuple[str, set[str] | None]]:
    """``(label, touched paths | None)`` for each live strand this daemon runs
    in the same repo, excluding *task* itself. ``None`` = unreadable."""
    own = str(getattr(task, "id", "") or "")
    label = str((getattr(task, "meta", None) or {}).get("repo_label") or "")
    with daemon._run_controls_lock:
        controls = [dict(c) for c in daemon._run_controls.values()]
    out: list[tuple[str, set[str] | None]] = []
    for control in controls:
        run_id = str(control.get("run_id") or "")
        if not control.get("parent_run_id") or control.get("stopped"):
            continue
        if run_id and run_id == own:
            continue
        if label and control.get("repo_label") and control.get("repo_label") != label:
            continue
        name = str(control.get("title") or run_id or control.get("event_id") or "?")
        run = Run.from_file(run_manifest_path(brr_dir / "runs", run_id)) if run_id else None
        raw = str(((run.meta if run else {}) or {}).get("worktree_path") or "").strip()
        root = Path(raw) if raw else None
        if root is None or not (root / ".git").exists():
            out.append((name, None))
            continue
        committed = _run(["git", "diff", "--name-only", "origin/main...HEAD"], root, 60)
        dirty = gitops.dirty_paths_or_none(root)
        if committed.returncode != 0 or dirty is None:
            out.append((name, None))
            continue
        out.append((name, {p for p in committed.stdout.splitlines() if p.strip()} | dirty))
    return out


@dataclass
class Seams:
    """What :func:`handle` reads the world through — replaced in tests."""

    github: Callable[[Path, str], GitHub] = GhCli
    checkout: Callable[[Path], Checkout] = GitCheckout
    live_strands: Callable[[Any, Path], list[tuple[str, set[str] | None]]] = live_strand_paths


seams = Seams()

_PR_RE = re.compile(r"^#?(\d+)$")


def _short(sha: str) -> str:
    return sha[:10]


def _refuse(f: OutboxFile, text: str, *, kind: str = "refused") -> Handled:
    from .verbs import _handled

    daemon._record_outbox_notice(f.ctx.outbox_dir, text, kind=kind, lifetime="run")
    daemon._retire_outbox_staging(f.path)
    return _handled(f, "land", 0)


def _write_produce(brr_dir: Path, run_id: str, row: dict[str, Any]) -> bool:
    if not run_id:
        return False
    path = brr_dir / "runs" / run_id / PRODUCE_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        return False
    return True


def handle(f: OutboxFile) -> Handled:
    from .verbs import _handled

    task = f.run
    raw = str(f.frontmatter.get("land") or "").strip()
    match = _PR_RE.match(raw)
    if not match:
        return _refuse(f, f"land dropped: {raw!r} is not a PR number — write `land: <n>`", kind="dropped")
    number = int(match.group(1))
    if daemon._is_strand(getattr(task, "meta", None)):
        return _refuse(
            f,
            f"land refused: #{number} — merging is the seat's grant; a strand hands "
            "its PR back to its parent (your terminal stream, or `submit: true`)",
        )
    repo_root = f.ctx.repo_root
    if repo_root is None:
        return _refuse(f, f"land dropped: #{number} — this run has no repo checkout to land into", kind="dropped")
    label = str((getattr(task, "meta", None) or {}).get("repo_label") or "")
    github = seams.github(repo_root, label)
    checkout = seams.checkout(repo_root)

    # 2. the PR
    try:
        pr = github.pr(number)
    except LandError as exc:
        return _refuse(f, f"land dropped: #{number} — {exc}", kind="dropped")
    state = str(pr.get("state") or "?").upper()
    head = str(pr.get("headRefOid") or "")
    base = str(pr.get("baseRefName") or "")
    if state != "OPEN":
        return _refuse(f, f"land refused: #{number} is {state}, not OPEN — nothing merged")
    if not head or not base:
        return _refuse(
            f, f"land dropped: #{number} — PR read returned head={head or '∅'} base={base or '∅'}",
            kind="dropped",
        )

    # 3. the checks, structurally
    try:
        checks = github.checks(number)
    except LandError as exc:
        return _refuse(f, f"land dropped: #{number} @ {_short(head)} — {exc}", kind="dropped")
    if not checks:
        return _refuse(
            f, f"land refused: #{number} @ {_short(head)} — no checks reported; a check "
            "that never ran is not green — nothing merged",
        )
    red = [c for c in checks if c.state not in GREEN_STATES]
    if red:
        named = " · ".join(f"{c.name}: {c.state}" for c in red)
        return _refuse(
            f, f"land refused: #{number} @ {_short(head)} — {len(red)} of {len(checks)} "
            f"checks not green: {named} — nothing merged",
        )

    # 4. merge, pinned to the head whose checks were read
    merge_error = ""
    try:
        github.merge(number, head_sha=head)
    except LandError as exc:
        merge_error = str(exc)

    # 5. read back — even after an error: a timed-out merge may have merged
    try:
        after = github.pr(number)
    except LandError as exc:
        return _refuse(
            f, f"land dropped: #{number} — merge call {'failed: ' + merge_error if merge_error else 'returned'}; "
            f"the read-back failed: {exc} — state unknown, read #{number} before retrying",
            kind="dropped",
        )
    after_state = str(after.get("state") or "?").upper()
    merge_commit = after.get("mergeCommit") or {}
    sha = str(merge_commit.get("oid") or "") if isinstance(merge_commit, dict) else ""
    if after_state != "MERGED" or not sha:
        return _refuse(
            f, f"land refused: #{number} @ {_short(head)} — merge "
            f"{'failed: ' + merge_error if merge_error else 'returned'}; read back state={after_state}"
            f"{', merge commit ' + _short(sha) if sha else ', no merge commit'}",
        )
    try:
        checkout.fetch(base)
        on_base = checkout.contains(sha, f"origin/{base}")
    except LandError as exc:
        return _refuse(
            f, f"land dropped: #{number} merged on GitHub as {_short(sha)}, but reading "
            f"origin/{base} failed: {exc} — checkout untouched, no announcement",
            kind="dropped",
        )
    if not on_base:
        return _refuse(
            f, f"land dropped: #{number} merged on GitHub as {_short(sha)}, but origin/{base} "
            "does not contain it after fetch — checkout untouched, no announcement",
            kind="dropped",
        )

    # 6. the daemon's checkout — the merge has happened; a read failing here
    # is said in the announcement, never allowed to swallow it
    try:
        checkout_line = _fast_forward(f, checkout, base, sha, pr)
    except Exception as exc:  # noqa: BLE001 - the merge is done; see above
        checkout_line = f"not fast-forwarded — {type(exc).__name__}: {exc}"

    # 7. produce, from the read sha
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run_id = str(getattr(task, "id", "") or "")
    produce = Produce(kind="knot", ref=sha, at=at)
    _write_produce(
        f.ctx.emit.brr_dir, run_id,
        {"kind": "knot", "ref": sha, "at": at, "verb": "land", "pr": number,
         "base": base, "head": head, "by": "frame"},
    )
    relics.append(
        f.ctx.outbox_dir, "merge", sha=sha, pr=number,
        subject=str(pr.get("title") or ""), url=str(pr.get("url") or "") or None,
    )

    # 8. announce — every value above was read before this line was composed
    named = " · ".join(f"{c.name} {'✓' if c.state == 'SUCCESS' else '(skipped)'}" for c in checks)
    announcement = f"Merged: #{number} → {_short(sha)} · checks: {named} · checkout: {checkout_line}"
    daemon._record_outbox_notice(
        f.ctx.outbox_dir,
        f"land: #{number} merged as {sha} on origin/{base} (head {_short(head)}, "
        f"{len(checks)} checks green) · checkout: {checkout_line}",
        kind="advisory", lifetime="run",
    )
    stats = f.ctx.stats
    if stats is not None:
        stats["land"] = stats.get("land", 0) + 1
    daemon._retire_outbox_staging(f.path)
    return _handled(
        f, "land", 1, produce=(produce,), then=f.rewritten({}, announcement),
    )


def _fast_forward(f: OutboxFile, checkout: Checkout, base: str, sha: str, pr: dict) -> str:
    """Fast-forward the daemon's checkout if every rule allows; the line says
    what was read either way."""
    if base != "main":
        return f"not fast-forwarded — the PR's base is {base}, not main"
    branch = checkout.current_branch()
    if branch != "main":
        return f"not fast-forwarded — the checkout is on {branch or 'a detached HEAD'}, not main"
    dirty = checkout.dirty_paths()
    if dirty is None:
        return "not fast-forwarded — `git status` did not answer"
    if dirty:
        return f"not fast-forwarded — {len(dirty)} uncommitted path(s) in the checkout"
    pr_files = {
        str(row.get("path") or "") for row in (pr.get("files") or []) if isinstance(row, dict)
    } - {""}
    for name, paths in seams.live_strands(f.run, f.ctx.emit.brr_dir):
        if paths is None:
            return f"not fast-forwarded — live strand {name}'s files could not be read"
        overlap = sorted(paths & pr_files)
        if overlap:
            return (
                f"not fast-forwarded — live strand {name} touches "
                f"{', '.join(overlap[:3])}{' …' if len(overlap) > 3 else ''}"
            )
    before = checkout.head()
    ok, detail = checkout.fast_forward("main", "origin/main")
    after = checkout.head()
    if not ok:
        return f"not fast-forwarded — {detail or 'fast-forward refused'}"
    if not checkout.contains(sha, "HEAD"):
        return f"fast-forward reported ok but HEAD {_short(after)} does not contain {_short(sha)}"
    return f"main {_short(before)} → {_short(after)}"
