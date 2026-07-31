"""The gate-receipt writer — the mechanism `hooks.gate_command` never shipped.

``hooks.gate_command`` in ``.brr/config`` arms a Stop-hook obligation
(``hooks._gate_closeout_clause``): a run that changed the tree must leave a
fresh ``.gate-receipt.json`` behind, or the resident is told "the gate never
ran" and blocked. That obligation shipped; the writer did not. The only thing
on a brnrd checkout that ever produced a receipt was this repo's own
``scripts/gate.py`` — unshipped (``pyproject.toml``'s ``package-data`` carries
no ``scripts/`` entry) and specific to this repo (it parses
``.github/workflows/ci.yml`` for its own leg list). An adopter who follows the
init playbook, sets ``hooks.gate_command = make test``, and then just runs
``make test`` gets a permanent, unfixable "the gate never ran" — a config key
with no mechanism behind it (kb/design-io-layer-trim.md, THE OBLIGATION
NOTHING CAN SATISFY).

This module is that mechanism, generalized to *any* configured command
instead of one repo's CI workflow: ``brnrd gate-run`` runs
``hooks.gate_command`` (or an explicit override), writes the receipt, and
forwards the command's exit code. The referent shape — git's own output,
compared to git's own output, never a private fingerprint algorithm — mirrors
``scripts/gate.py``'s ``tree_referents``/``write_receipt`` so a receipt this
module writes reads identically to one ``scripts/gate.py`` writes, and
``hooks._gate_closeout_clause`` needs no changes to accept either.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

#: Written beside the run's other control dotfiles; same idiom as ``.card``,
#: matching ``hooks.GATE_RECEIPT_NAME`` and ``scripts/gate.py``'s own.
RECEIPT_NAME = ".gate-receipt.json"

#: The referents :func:`tree_referents` returns, in the order a reader should
#: hear about them: the coarse ones first. Named once so the writer, the
#: comparison and the reader cannot drift on the vocabulary.
REFERENT_KEYS = ("head", "status", "diff_digest", "untracked_digest")

_R = TypeVar("_R")


def git_out(repo_root: Path, args: list[str], timeout: int = 30) -> str | None:
    """Raw stdout of a read-only git command in *repo_root*, or ``None``.

    Best-effort like every other closeout reader in this product: a missing
    repo, an unknown ref, or a timeout degrades to "unassertable", never a
    crash.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def untracked_digest(repo_root: Path) -> str:
    """Content digest of every untracked, non-ignored file in *repo_root*.

    ``status --porcelain`` *names* untracked files but a receipt taken before
    editing a brand-new file would still read as fresh after — the most
    common shape of in-progress work there is. This closes it. Shared by the
    writer (here) and the reader (``hooks._gate_closeout_clause``, which
    calls this instead of keeping its own copy — two implementations of one
    digest is exactly the shape where copies agree with each other and are
    wrong together).
    """
    listing = git_out(repo_root, ["ls-files", "--others", "--exclude-standard"])
    if not listing:
        return ""
    paths = [line for line in listing.splitlines() if line]
    if not paths:
        return ""
    hashes = git_out(repo_root, ["hash-object", "--", *paths]) or ""
    return hashlib.sha256(
        "\n".join(
            f"{path}\t{blob}" for path, blob in zip(paths, hashes.splitlines())
        ).encode("utf-8", "replace")
    ).hexdigest()


def tree_referents(repo_root: Path) -> dict[str, str] | None:
    """The four raw git outputs that identify *which tree* was gated.

    ``None`` when *repo_root* is not a readable git checkout — the caller's
    signal that no receipt can be written, never a guess.
    """
    head = git_out(repo_root, ["rev-parse", "HEAD"])
    status = git_out(repo_root, ["status", "--porcelain"])
    diff = git_out(repo_root, ["diff", "HEAD"])
    if head is None or status is None or diff is None:
        return None
    return {
        "head": head.strip(),
        "status": status,
        "diff_digest": hashlib.sha256(diff.encode("utf-8", "replace")).hexdigest(),
        "untracked_digest": untracked_digest(repo_root),
    }


def tree_fields(repo_root: Path, before: dict[str, str] | None) -> dict[str, Any] | None:
    """The receipt's tree block: the end state, plus whether it held still.

    The top-level referents stay the *end* state, unchanged — that is what
    ``hooks._gate_closeout_clause`` compares its own fresh ``git`` output
    against, and moving them would break the one question the receipt already
    answers correctly (*is the tree you are ending on the tree in the
    receipt*). What is new sits beside them:

    - ``gated_from`` — the referents sampled **before** the first leg. The
      pair, not a boolean, because a reader holding both can diff the two
      ``status --porcelain`` blocks and name the *file* the gate never saw
      (:func:`moved_summary` does exactly that).
    - ``tree_moved_during_gate`` — the state itself, so *ran on a tree that
      then moved under it* is distinguishable from *never ran*. Different
      facts, different remedies, and a diagnostic aimed at the wrong cause
      is worse than none.
    - ``moved_referents`` — which of :data:`REFERENT_KEYS` disagree; present
      only when something did.

    Both new keys are **omitted** when the before-sample is unassertable (git
    unreadable at t0). Absent therefore means *this writer did not check* —
    which is also what every receipt written before this shape existed looks
    like, so the reader can treat absence as silence rather than firing on
    every legacy receipt.
    """
    after = tree_referents(repo_root)
    if after is None:
        return None
    fields: dict[str, Any] = dict(after)
    if before is None:
        return fields
    moved = [key for key in REFERENT_KEYS if before.get(key) != after.get(key)]
    fields["gated_from"] = dict(before)
    fields["tree_moved_during_gate"] = bool(moved)
    if moved:
        fields["moved_referents"] = moved
    return fields


def gated_run(repo_root: Path, run: Callable[[], _R]) -> tuple[_R, dict[str, Any] | None]:
    """Sample the tree, call *run*, sample again. The whole shape, once.

    *run* is the caller's own work — one shell command for ``brnrd
    gate-run``, a loop over CI's own legs for ``scripts/gate.py`` — and this
    function owns nothing but the two captures around it. That split is the
    point: the second writer gets the pre/post capture by *calling* this,
    never by growing a copy of it. Two implementations of one fingerprint
    agree with each other and are wrong together (#722), which is the exact
    bug class the receipt exists to foreclose.

    Returns *run*'s own return value untouched, plus the tree block for
    :func:`write_receipt` (``None`` when the end state is unassertable).
    """
    before = tree_referents(repo_root)
    result = run()
    return result, tree_fields(repo_root, before)


def moved_summary(receipt: dict[str, Any]) -> str:
    """Name what moved between a receipt's two captures — files first.

    Recording the pair buys this: most in-gate writes are a brand-new file or
    a newly-dirty one, so a line in ``status --porcelain`` that is in the
    after block and not the before block *is* the filename, no guessing. When
    only a digest moved — an edit to a file that was already dirty, where the
    porcelain line never changes — the referent name is the honest answer and
    the sentence says so rather than inventing a path.
    """
    # Hardened against a hand-edited or truncated receipt: every reader in
    # this neighbourhood degrades to "unassertable" rather than crashing, and
    # this one runs inside a Stop hook where a raised exception would turn a
    # green run red for the crime of having a malformed JSON file.
    before = receipt.get("gated_from")
    before = before if isinstance(before, dict) else {}
    before_lines = set(str(before.get("status") or "").splitlines())
    after_lines = set(str(receipt.get("status") or "").splitlines())
    paths = []
    for line in after_lines - before_lines:
        path = line[3:] if len(line) > 3 else line
        path = path.strip()
        if "->" in path:  # a rename: the destination is the interesting half
            path = path.split("->")[-1].strip()
        if path:
            paths.append(path)
    if paths:
        paths.sort()
        shown = ", ".join(paths[:3])
        if len(paths) > 3:
            shown += f" (+{len(paths) - 3} more)"
        return shown
    raw_moved = receipt.get("moved_referents")
    moved = [str(key) for key in raw_moved] if isinstance(raw_moved, list) else []
    if moved:
        return (
            f"no new path in `git status`, but {', '.join(moved)} moved — an "
            f"edit to a file that was already dirty when the gate started"
        )
    return "the tree referents moved"


def write_receipt(
    outbox_dir: Path,
    repo_root: Path,
    *,
    verdict: str,
    command: str,
    run_id: str = "",
    seconds: float | None = None,
    tree: dict[str, Any] | None = None,
) -> Path | None:
    """Record *verdict* and the tree it was reached on. Best-effort.

    Written for RED as well as GREEN: the obligation the Stop hook checks is
    *the gate ran on this tree*, never *the gate was green*. A run may end
    red and report it; a run that never looked is the failure.

    *tree* is :func:`gated_run`'s second return value — the end referents
    plus the stillness record. Omitted, this samples the end state alone, the
    way it always did: honest about *which* tree, silent about *when*, and
    that silence is what the reader keys off.
    """
    referents = tree if tree is not None else tree_referents(repo_root)
    if referents is None:
        return None
    payload: dict[str, object] = {
        **referents,
        "verdict": verdict,
        "gate_command": command,
        "run_id": run_id,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if seconds is not None:
        payload["seconds"] = round(seconds, 1)
    path = outbox_dir / RECEIPT_NAME
    tmp = path.with_name(path.name + ".tmp")
    try:
        outbox_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return None
    return path


def run_and_write_receipt(
    repo_root: Path,
    outbox_dir: Path,
    command: str,
    *,
    run_id: str = "",
) -> int:
    """Run *command* in *repo_root*, write its receipt, return its exit code.

    The whole point: any adopter's ``hooks.gate_command`` — ``make test``,
    ``npm test``, anything a shell can run — becomes checkable the same way
    this repo's own ``scripts/gate.py`` already is, without every adopter
    needing to write their own receipt writer.

    The referents are sampled *around* the command by :func:`gated_run`, not
    after it. A command that takes four minutes ran against the tree as it
    was when each of its legs started; sampling only at the end certifies
    every file the run wrote while it was working (#917).
    """
    def _run() -> tuple[int, float]:
        started = time.monotonic()
        completed = subprocess.run(command, shell=True, cwd=repo_root)
        return completed.returncode, time.monotonic() - started

    (returncode, elapsed), tree = gated_run(repo_root, _run)
    verdict = "GREEN" if returncode == 0 else "RED"
    receipt = write_receipt(
        outbox_dir, repo_root,
        verdict=verdict, command=command, run_id=run_id, seconds=elapsed,
        tree=tree,
    )
    if receipt is not None:
        print(f"[brnrd] gate-run: {verdict} — receipt {receipt}")
        # Said here as well as in the Stop hook: the person watching this
        # scroll by is the one who can still fix it cheaply, and a verdict
        # printed without this line reads as a clean gate.
        if (tree or {}).get("tree_moved_during_gate"):
            print(
                "[brnrd] gate-run: the tree moved while the gate ran — "
                f"{moved_summary(tree or {})}. {verdict} does not cover it; "
                "re-run on a still tree."
            )
    else:
        print(
            "[brnrd] gate-run: "
            f"{verdict} — no receipt written (not a git checkout, or the "
            "outbox directory could not be created)"
        )
    return returncode
