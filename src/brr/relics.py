"""Run relics: the durable, linkable produce of one run.

A "relic" is one thing a run produced that a human would want a link back
to: a commit, a pushed branch, a PR, an issue it touched, a kb page it
edited, a free-form summary line. The maintainer's own framing (#200/#317,
2026-07-09): a task receipt should "list and link all the stuff the run
produced... give a user a place to see the run's produce, and move from
there." This module is the notation and the collection logic; the ledger
row (`run_ledger.py`) and the dashboard/chat card are the renderers.

Two collection paths, deliberately different in cost to the resident:

- **Auto-derived** (:func:`derive_auto`): commits and the pushed branch come
  straight from ``git log``; the PR comes from the ``.pr`` control file
  daemon.py already reads for ``remote_scm``. Zero new bookkeeping — a
  resident does nothing and still gets a real commit/PR manifest.
- **Self-reported** (:func:`read_reported`, the ``.relics.jsonl`` control
  file): issues touched, kb pages edited, ad-hoc comments/messages, and an
  optional one-line summary. Nothing auto-tracks "which issue did this run
  comment on" today (#317 named this explicitly as the one genuinely new
  piece of bookkeeping) — a resident appends one JSON line per relic,
  same weight as writing ``.task-classification`` or ``.pr``.

Append format — one JSON object per line, at least a ``"kind"`` key:

    {"kind": "summary", "text": "Closed #200 and #317 as one relics feature."}
    {"kind": "issue", "number": 317, "action": "closed", "url": "https://..."}
    {"kind": "kb", "path": "design-run-relics.md", "url": "https://..."}

Everything here is best-effort: a malformed line, a missing git repo, an
unparseable remote — all degrade to "fewer relics", never a closeout
failure. Same posture as the ``.pr``/``.task-classification`` readers this
module sits alongside.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from . import config as conf
from . import forges
from . import gitops
from . import knowledge

CONTROL_NAME = ".relics.jsonl"

# A run that appends more than this is almost certainly looping, not
# reporting produce; cap rather than let one bad run blow up every reader
# downstream (ledger row, dashboard payload, chat card).
_MAX_RECORDS = 300
_MAX_LINE_BYTES = 4096

_PR_NUMBER_RE = re.compile(r"(\d+)\s*$")

# Rendering icon per kind — mirrored in ``runLedger.ts``'s ``RELIC_ICONS``
# on the frontend. Keep the two in sync; nothing enforces it mechanically
# today (noted in ``kb/design-run-relics.md`` as a follow-up: emit this map
# once, e.g. as generated JSON, instead of hand-mirroring in two languages).
_ICONS: dict[str, str] = {
    "summary": "📝",
    "commit": "🔨",
    "branch": "🌿",
    "pr": "🔀",
    "issue": "🎫",
    "comment": "💬",
    "kb": "📚",
    "file": "📄",
    "message": "✉️",
    "reply": "🗣️",
}


def icon(kind: str) -> str:
    return _ICONS.get(kind, "•")


def append(outbox_dir: Path | None, kind: str, **fields: Any) -> None:
    """Append one relic record to the control file. Best-effort, never raises.

    Silently drops the record if it can't be serialized or is implausibly
    large (a bug producing a huge payload shouldn't corrupt the file for
    every subsequent reader).
    """
    if outbox_dir is None or not kind:
        return
    record: dict[str, Any] = {"kind": kind}
    record.update({k: v for k, v in fields.items() if v is not None})
    try:
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return
    if len(line.encode("utf-8")) > _MAX_LINE_BYTES:
        return
    path = outbox_dir / CONTROL_NAME
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def read_reported(outbox_dir: Path | None) -> list[dict[str, Any]]:
    """Parse the self-reported ``.relics.jsonl`` control file.

    Tolerant of blank or malformed lines (skipped, not fatal) and capped at
    :data:`_MAX_RECORDS`. Missing file → ``[]``, same as no relics reported.
    """
    if outbox_dir is None:
        return []
    try:
        text = (outbox_dir / CONTROL_NAME).read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("kind"):
            out.append(record)
        if len(out) >= _MAX_RECORDS:
            break
    return out


def _read_pr_control(outbox_dir: Path | None) -> str | None:
    """Same tolerant parse as ``daemon.py::_read_pr_control`` — a bare
    number, ``#``-prefixed, or a full PR URL. Re-implemented locally
    rather than imported to keep this module import-cycle-free of
    ``daemon.py``, matching how the ``.task-classification`` reader
    already lives directly in ``run_ledger.py`` rather than shared.
    """
    if outbox_dir is None:
        return None
    try:
        text = (outbox_dir / ".pr").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    match = _PR_NUMBER_RE.search(text)
    return match.group(1) if match else None


def _commits_since_seed(
    repo_root: Path, branch: str, seed_ref: str | None,
) -> list[tuple[str, str]]:
    """Return ``[(short_sha, subject), ...]`` for commits on *branch* not on
    the seed ref, newest first (``git log``'s own default order — matches
    ``daemon.py``'s existing ``_commits_between``). Read-only ``git`` calls;
    any failure (no repo, unknown ref, timeout) degrades to ``[]``.
    """
    if not branch:
        return []
    seed = seed_ref or gitops.default_branch(repo_root) or "HEAD"
    try:
        merge_base = subprocess.run(
            ["git", "merge-base", seed, branch],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        base_ref = merge_base.stdout.strip() if merge_base.returncode == 0 else seed
        result = subprocess.run(
            ["git", "log", f"{base_ref}..{branch}", "--format=%h\x1f%s", "--no-color"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    out: list[tuple[str, str]] = []
    for row in result.stdout.splitlines():
        if "\x1f" not in row:
            continue
        sha, _, subject = row.partition("\x1f")
        if sha:
            out.append((sha, subject))
    return out


def derive_auto(
    repo_root: Path | None,
    *,
    branch: str | None,
    seed_ref: str | None,
    outbox_dir: Path | None,
) -> list[dict[str, Any]]:
    """Zero-resident-effort relics: commits, the pushed branch, and the PR.

    All three are already knowable — ``git log`` for commits, the existing
    ``.pr`` control file for the PR — so this asks nothing new of the
    resident, matching #317's own recommended shape ("no new collection
    mechanism needed").
    """
    if repo_root is None:
        return []
    out: list[dict[str, Any]] = []
    remote_url: str | None = None
    try:
        remote_name = gitops.default_remote(repo_root)
        remote_url = gitops.remote_url(repo_root, remote_name) if remote_name else None
    except Exception:
        remote_url = None

    try:
        cfg = conf.load_config(repo_root)
    except Exception:
        cfg = {}
    override_kind = cfg.get("forge.kind") or None
    override_base = cfg.get("forge.url_base") or None

    if branch:
        commits = _commits_since_seed(repo_root, branch, seed_ref)
        for sha, subject in commits[:_MAX_RECORDS]:
            url = (
                forges.commit_url(
                    remote_url, sha,
                    override_kind=override_kind, override_url_base=override_base,
                )
                if remote_url else None
            )
            out.append({"kind": "commit", "sha": sha, "subject": subject, "url": url})
        if commits:
            branch_url = (
                forges.view_branch_url(
                    remote_url, branch,
                    override_kind=override_kind, override_url_base=override_base,
                )
                if remote_url else None
            )
            out.append({"kind": "branch", "name": branch, "url": branch_url})

    pr_number = _read_pr_control(outbox_dir)
    if pr_number and remote_url:
        parsed = forges.parse_remote(remote_url)
        if parsed is not None:
            _, owner, repo = parsed
            url = forges.thread_url(
                remote_url, f"{owner}/{repo}", pr_number,
                override_kind=override_kind, override_url_base=override_base,
            )
            out.append({"kind": "pr", "number": int(pr_number), "url": url})
    return out


def collect(
    repo_root: Path | None,
    *,
    branch: str | None,
    seed_ref: str | None,
    outbox_dir: Path | None,
) -> list[dict[str, Any]]:
    """The full relic list for one run: summary first, then produce.

    Ordering is deliberate for the renderer: a lone ``summary`` relic (if
    the resident wrote one) leads the list so a collapsed receipt's
    expansion reads top-down like a note, not an unordered bag of links.
    """
    reported = read_reported(outbox_dir)
    summary = [r for r in reported if r.get("kind") == "summary"][:1]
    rest_reported = [r for r in reported if r.get("kind") != "summary"]
    if repo_root is not None:
        for record in rest_reported:
            if record.get("kind") != "kb":
                continue
            url = knowledge.kb_page_url(repo_root, str(record.get("path") or ""))
            # A reported URL is only trustworthy if the page's current blob
            # is present at the forge-tracking ref.  Replace it from the
            # resolver or remove it rather than preserving a plausible 404
            # (or a link to stale pre-edit content).
            record.pop("url", None)
            if url:
                record["url"] = url
    auto = derive_auto(repo_root, branch=branch, seed_ref=seed_ref, outbox_dir=outbox_dir)
    return summary + auto + rest_reported


def counts_by_kind(relics: list[dict[str, Any]]) -> dict[str, int]:
    """Collapsed-receipt counts, e.g. ``{"commit": 3, "pr": 1}`` — the
    "3 commits, 1 pr, 1 issue modified" summary the maintainer asked for.
    The ``summary`` kind is prose, not produce, so it's excluded from counts.
    """
    out: dict[str, int] = {}
    for record in relics:
        kind = record.get("kind")
        if not kind or kind == "summary":
            continue
        out[kind] = out.get(kind, 0) + 1
    return out
