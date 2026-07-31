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
from datetime import datetime, timezone
from pathlib import Path

#: Written beside the run's other control dotfiles; same idiom as ``.card``,
#: matching ``hooks.GATE_RECEIPT_NAME`` and ``scripts/gate.py``'s own.
RECEIPT_NAME = ".gate-receipt.json"


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


def write_receipt(
    outbox_dir: Path,
    repo_root: Path,
    *,
    verdict: str,
    command: str,
    run_id: str = "",
    seconds: float | None = None,
) -> Path | None:
    """Record *verdict* and the tree it was reached on. Best-effort.

    Written for RED as well as GREEN: the obligation the Stop hook checks is
    *the gate ran on this tree*, never *the gate was green*. A run may end
    red and report it; a run that never looked is the failure.
    """
    referents = tree_referents(repo_root)
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
    """
    started = time.monotonic()
    completed = subprocess.run(command, shell=True, cwd=repo_root)
    elapsed = time.monotonic() - started
    verdict = "GREEN" if completed.returncode == 0 else "RED"
    receipt = write_receipt(
        outbox_dir, repo_root,
        verdict=verdict, command=command, run_id=run_id, seconds=elapsed,
    )
    if receipt is not None:
        print(f"[brnrd] gate-run: {verdict} — receipt {receipt}")
    else:
        print(
            "[brnrd] gate-run: "
            f"{verdict} — no receipt written (not a git checkout, or the "
            "outbox directory could not be created)"
        )
    return completed.returncode
