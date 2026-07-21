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
  file): issues touched, ad-hoc comments/messages, and an
  optional one-line summary. Nothing auto-tracks "which issue did this run
  comment on" today (#317 named this explicitly as the one genuinely new
  piece of bookkeeping) — a resident appends one JSON line per relic,
  same weight as writing ``.pr``.

Kb pages committed by the daemon's knowledge capture are auto-reported at
closeout alongside commits, branch, PR, and the archived terminal reply.
The full resident-facing grammar lives in ``brnrd docs portals``.

Append format — one JSON object per line, at least a ``"kind"`` key:

    {"kind": "summary", "text": "Closed #200 and #317 as one relics feature."}
    {"kind": "issue", "number": 317, "action": "closed", "url": "https://..."}
    {"kind": "kb", "path": "design-run-relics.md", "url": "https://..."}

Everything here is best-effort: a malformed line, a missing git repo, an
unparseable remote — all degrade to "fewer relics", never a closeout
failure. Same posture as the ``.pr`` reader this
module sits alongside.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from . import config as conf
from . import forges
from . import gitops
from . import knowledge

CONTROL_NAME = ".relics.jsonl"

_LIVE_KINDS = {
    "commit", "branch", "pr", "kb", "issue", "comment", "message", "file",
}

# A run that appends more than this is almost certainly looping, not
# reporting produce; cap rather than let one bad run blow up every reader
# downstream (ledger row, dashboard payload, chat card).
_MAX_RECORDS = 300
_MAX_LINE_BYTES = 4096

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


def label(record: dict[str, Any]) -> str:
    """One human line for a single relic. Mirrors ``runLedger.relicLabel``.

    Unknown kinds fall back through the common text-bearing fields and then
    to the kind name, so a relic vocabulary that grows on the backend still
    renders as *something* rather than a blank bullet.
    """
    kind = str(record.get("kind") or "")
    if kind == "commit":
        return f"{str(record.get('sha') or '')[:7]} {record.get('subject') or ''}".strip()
    if kind == "branch":
        return str(record.get("name") or "branch")
    if kind == "pr":
        return f"PR #{record.get('number') or '?'}"
    if kind == "issue":
        action = record.get("action")
        return f"issue #{record.get('number') or '?'}" + (f" ({action})" if action else "")
    if kind in {"kb", "file"}:
        return str(record.get("path") or kind)
    if kind == "comment":
        return str(record.get("on") or "comment")
    if kind == "message":
        return str(record.get("note") or record.get("channel") or "message")
    if kind == "reply":
        return str(record.get("excerpt") or "reply")
    if kind == "summary":
        return str(record.get("text") or "")
    for field in ("text", "path", "note", "name", "on"):
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return kind or "relic"


def render_markdown(records: list[dict[str, Any]]) -> list[str]:
    """Render a relic list as the run node's ``## Produce`` section.

    Markdown, not a schema: the run document is read by humans in a git diff
    and by the dashboard's ordinary Markdown renderer, so produce arrives on
    the node the same way every other section does — headings and links, no
    second parser to keep in sync. A ``summary`` relic is prose and leads as
    a paragraph; everything else is one linked bullet.
    """
    summaries = [r for r in records if r.get("kind") == "summary"]
    produce = [r for r in records if r.get("kind") != "summary"]
    body: list[str] = []
    summary_text = label(summaries[0]).strip() if summaries else ""
    if summary_text:
        body.extend([summary_text, ""])
    for record in produce:
        text = label(record).replace("[", "\\[").replace("]", "\\]").strip()
        if not text:
            continue
        url = str(record.get("url") or "").strip()
        body.append(f"- {icon(str(record.get('kind') or ''))} " + (f"[{text}]({url})" if url else text))
    if not body:
        return []
    return ["", "## Produce", "", *body]


def fingerprint(records: list[dict[str, Any]]) -> str:
    """A stable digest of a relic list, for change detection.

    The run node is rewritten when produce *changes*, never on a timer — a
    heartbeat-driven rewrite would churn the corpus fingerprint (and its
    full republish) every 30s for no new fact.
    """
    return json.dumps(records, sort_keys=True, default=str)


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
            if record["kind"] == "kb_page":
                record["kind"] = "kb"
            out.append(record)
        if len(out) >= _MAX_RECORDS:
            break
    return out


def _read_pr_control(outbox_dir: Path | None) -> str | None:
    """Read the shared explicit PR/MR control forms without importing the
    daemon (which would create a cycle). The parser lives in ``forges`` so
    ledger relics and the live ``remote_scm`` facet cannot disagree.
    """
    if outbox_dir is None:
        return None
    try:
        text = (outbox_dir / ".pr").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    return forges.parse_pull_request_number(text)


def collection_scope(
    meta: dict[str, Any], work_dir: Path | None,
) -> tuple[str | None, str | None]:
    """The ``(branch, seed)`` pair relic derivation should measure against.

    A worktree run pins both at prepare time (``branch_name`` / ``seed_ref``
    on the task manifest). A **host** run pins neither: ``HostEnv.prepare``
    assigns no branch, so every host run used to derive zero commit/branch
    relics — the run could close an issue and merge a PR and its node would
    still read "made nothing durable" (maintainer, 2026-07-19, on
    run-260719-1700-rcez). Worse, the usual host flow *merges to the seed
    branch*, so even naming the current branch wasn't enough: ``main..main``
    is empty by definition.

    So for a branchless task the scope falls back to the checkout's current
    branch, measured against the checkout's **HEAD OID captured at run
    start** (``host_start_oid``, stamped by the daemon at env prepare) — the
    commits that appeared during this run, regardless of what branch dance
    produced them. A detached HEAD yields no branch rather than the literal
    string ``HEAD``.
    """
    branch = str(meta.get("branch_name") or "") or None
    seed = str(meta.get("seed_ref") or "") or None
    if branch is None and work_dir is not None:
        try:
            current = gitops.current_branch(Path(work_dir))
        except Exception:
            current = None
        if current and current != "HEAD":
            branch = current
            seed = str(meta.get("host_start_oid") or "") or seed
    return branch, seed


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
            url = forges.pull_request_url(
                remote_url, f"{owner}/{repo}", pr_number,
                override_kind=override_kind, override_url_base=override_base,
            )
            out.append({"kind": "pr", "number": int(pr_number), "url": url})
    return out


def _identity(record: dict[str, Any]) -> tuple[str, str] | None:
    """The dedup key for a relic, or ``None`` when the kind has no stable
    identity (``summary``, ``comment``, ``message``, ``reply``, unknown kinds
    — those never merge; two comments are two comments).

    Commits key on the 7-char sha prefix so a reported full sha and an
    auto-derived ``git log --format=%h`` short sha still meet.
    """
    kind = str(record.get("kind") or "")
    if kind in {"pr", "issue"}:
        number = record.get("number")
        return (kind, str(number)) if number else None
    if kind == "commit":
        sha = str(record.get("sha") or "")
        return ("commit", sha[:7]) if sha else None
    if kind == "branch":
        name = str(record.get("name") or "")
        return ("branch", name) if name else None
    if kind in {"kb", "file"}:
        path = str(record.get("path") or "")
        return (kind, path) if path else None
    return None


def dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse records that name the same relic into one row.

    The observed failure (run-260721-0922-pfqd): the ``.pr`` control file
    auto-derived ``{"kind": "pr", "number": 532, "url": ...}`` while the
    resident also reported ``{"kind": "pr", "number": 532, "action":
    "opened"}`` — and the renderer showed two PR rows, one link-less.
    Same relic, two producers, zero dedup.

    Rows merge on :func:`_identity`; first occurrence keeps its position.
    The URL-bearing row wins field conflicts (a link beats its absence),
    and fields only one row carries (``action``, ``subject``) survive the
    merge, so preferring the auto row never erases resident annotations.
    """
    out: list[dict[str, Any]] = []
    index: dict[tuple[str, str], int] = {}
    for record in records:
        key = _identity(record)
        if key is None:
            out.append(record)
            continue
        slot = index.get(key)
        if slot is None:
            index[key] = len(out)
            out.append(record)
            continue
        kept = out[slot]
        if record.get("url") and not kept.get("url"):
            preferred, other = record, kept
        else:
            preferred, other = kept, record
        out[slot] = {**other, **preferred}
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
    reported = [
        record for record in read_reported(outbox_dir)
        if record.get("kind") != "pr" or record.get("number")
    ]
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
    return dedupe(summary + auto + rest_reported)


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


def live_summary(
    repo_root: Path,
    *,
    branch: str | None,
    seed_ref: str | None,
    outbox_dir: Path | None,
) -> dict[str, Any]:
    """Compile the run's attested produce for its live portal facet.

    This deliberately projects the same auto-derived and resident-reported
    records as closeout rather than creating a second accounting path.  It is
    read on the heartbeat, so every failure collapses to an explicit unknown
    facet instead of escaping into daemon liveness.
    """
    try:
        root = Path(repo_root)
        if not root.is_dir():
            return {"known": False}
        records = dedupe(
            derive_auto(
                root, branch=branch, seed_ref=seed_ref, outbox_dir=outbox_dir,
            )
            + read_reported(outbox_dir)
        )

        # A .pr number is useful live even when forge URL derivation cannot
        # inspect a remote.  derive_auto includes it in the normal case; add
        # the same attested control record only when that path degraded.
        if not any(record.get("kind") == "pr" for record in records):
            pr_control = _read_pr_control(outbox_dir)
            if pr_control:
                records.append({"kind": "pr", "number": int(pr_control)})

        latest_commit = next(
            (
                str(record["sha"])
                for record in records
                if record.get("kind") == "commit" and record.get("sha")
            ),
            None,
        )
        pr_number = None
        for record in records:
            if record.get("kind") != "pr" or not record.get("number"):
                continue
            try:
                pr_number = int(record["number"])
            except (TypeError, ValueError):
                continue
            break
        counts = {
            kind: count
            for kind, count in counts_by_kind(records).items()
            if kind in _LIVE_KINDS
        }
        return {
            "known": True,
            "counts": counts,
            "latest_commit": latest_commit,
            "branch": branch,
            "pr": pr_number,
            # The manifest itself, not only its shape. Counts answer "how
            # much"; a resident checking its own work mid-run is asking
            # "what" — and at closeout it is writing a receipt *from* this
            # list (maintainer, 2026-07-19: "make the live accrued relics
            # useful for you too... inspected as you go to maintain the
            # focus"). Same records the node's frame renders, so the two
            # faces of the run cannot drift.
            "records": records,
        }
    except Exception:
        return {"known": False}
