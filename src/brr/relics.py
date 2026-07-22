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
    {"kind": "issue", "number": 317, "action": "closed"}
    {"kind": "kb", "path": "design-run-relics.md"}

``url`` is never the resident's job: the daemon knows the run's forge and
``owner/repo``, so issue/PR/commit/branch relics get their link derived at
collection time (:func:`link_reported`), and ``kb`` pages resolve against
the forge-tracking ref. Supply one explicitly only to point somewhere the
daemon *cannot* derive — a thread on another forge. A relic whose repo or
forge is unattested renders unlinked; nothing here fabricates a URL.

Everything here is best-effort: a malformed line, a missing git repo, an
unparseable remote — all degrade to "fewer relics", never a closeout
failure. Same posture as the ``.pr`` reader this
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

_LIVE_KINDS = {
    "commit", "branch", "pr", "merge", "kb", "issue", "comment", "message",
    "file",
}

# Subjects that attest a merge the run performed. GitHub's own generated
# forms: a true merge commit ("Merge pull request #N from owner/branch"),
# a local branch merge ("Merge branch 'name'"), and a squash-merge landing
# as a single-parent commit suffixed "(#N)".
_MERGE_PR_SUBJECT = re.compile(r"^Merge pull request #(\d+)\b")
_MERGE_BRANCH_SUBJECT = re.compile(r"^Merge branch '([^']+)'")
_SQUASH_PR_SUFFIX = re.compile(r"\(#(\d+)\)$")

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
    "merge": "⤵️",
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
    if kind == "merge":
        if record.get("pr"):
            return f"merged PR #{record.get('pr')}"
        if record.get("branch"):
            return f"merged {record.get('branch')}"
        subject = str(record.get("subject") or "").strip()
        return subject or f"merge {str(record.get('sha') or '')[:7]}".strip()
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
) -> list[tuple[str, str, int, str]]:
    """Return ``[(short_sha, subject, parent_count, committer_email), ...]``
    for commits on *branch* not on the seed ref, newest first (``git log``'s
    own default order — matches ``daemon.py``'s existing ``_commits_between``).
    Parent count and committer identify merges (see :func:`derive_auto`).
    Read-only ``git`` calls; any failure (no repo, unknown ref, timeout)
    degrades to ``[]``.
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
            ["git", "log", f"{base_ref}..{branch}", "--format=%h\x1f%P\x1f%ce\x1f%s", "--no-color"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    out: list[tuple[str, str, int, str]] = []
    for row in result.stdout.splitlines():
        if "\x1f" not in row:
            continue
        parts = row.split("\x1f", 3)
        if len(parts) != 4:
            continue
        sha, parents, committer, subject = parts
        if sha:
            out.append((sha, subject, len(parents.split()), committer.strip()))
    return out


class _ForgeLinks:
    """Forge URL derivation for one checkout's relics.

    A resident-authored relic line carries only *what* it produced —
    ``{"kind": "issue", "number": 566}``. The forge, the web host and the
    ``owner/repo`` are the daemon's knowledge, not the resident's, so link
    derivation belongs here (maintainer, 2026-07-22: issue relics rendered
    as bare "🎫 issue #566 (opened)" on the run node). One resolver serves
    both collection paths so auto-derived and self-reported produce cannot
    link differently.

    Every method returns ``None`` when the fact isn't attested — no remote,
    an unparseable remote, an unknown forge kind. A relic whose repo/forge
    is unknown renders unlinked rather than pointing at a fabricated URL.
    """

    def __init__(
        self,
        remote_url: str | None,
        *,
        override_kind: str | None = None,
        override_url_base: str | None = None,
    ) -> None:
        self.remote_url = remote_url or None
        self.override_kind = override_kind
        self.override_url_base = override_url_base
        self.repo_path: str | None = None
        if self.remote_url:
            parsed = forges.parse_remote(self.remote_url)
            if parsed is not None:
                _, owner, repo = parsed
                self.repo_path = f"{owner}/{repo}"

    def _thread_repo(self, repo: Any = None) -> str | None:
        """``owner/repo`` for a thread relic: the record's own ``repo`` field
        when it names another project, else this checkout's origin."""
        named = str(repo or "").strip().strip("/")
        return named if "/" in named else self.repo_path

    def pull_request(self, number: Any, repo: Any = None) -> str | None:
        path = self._thread_repo(repo)
        if not self.remote_url or not path:
            return None
        return forges.pull_request_url(
            self.remote_url, path, str(number),
            override_kind=self.override_kind,
            override_url_base=self.override_url_base,
        )

    def issue(self, number: Any, repo: Any = None) -> str | None:
        path = self._thread_repo(repo)
        if not self.remote_url or not path:
            return None
        return forges.thread_url(
            self.remote_url, path, str(number),
            override_kind=self.override_kind,
            override_url_base=self.override_url_base,
        )

    def commit(self, sha: Any) -> str | None:
        if not self.remote_url or not sha:
            return None
        return forges.commit_url(
            self.remote_url, str(sha),
            override_kind=self.override_kind,
            override_url_base=self.override_url_base,
        )

    def branch(self, name: Any) -> str | None:
        if not self.remote_url or not name:
            return None
        return forges.view_branch_url(
            self.remote_url, str(name),
            override_kind=self.override_kind,
            override_url_base=self.override_url_base,
        )


def forge_links(repo_root: Path | None) -> _ForgeLinks:
    """Build the URL resolver for *repo_root*'s origin remote.

    Best-effort like everything else here: a missing repo, an unreadable
    config, or a remote lookup failure yields a resolver that derives
    nothing rather than raising into closeout.
    """
    if repo_root is None:
        return _ForgeLinks(None)
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
    return _ForgeLinks(
        remote_url,
        override_kind=cfg.get("forge.kind") or None,
        override_url_base=cfg.get("forge.url_base") or None,
    )


def link_reported(
    records: list[dict[str, Any]], links: _ForgeLinks,
) -> list[dict[str, Any]]:
    """Fill in the forge ``url`` for self-reported issue/PR/commit/merge relics.

    The resident writes ``{"kind": "issue", "number": 566, "action":
    "opened"}`` and nothing more — the ``.relics.jsonl`` grammar explicitly
    does not ask it to know the forge's URL shape. Before this, only
    :func:`derive_auto` produced links, so every reported thread relic
    reached the run node's ``## Produce`` block as bare text.

    An explicit ``url`` on the record is honoured as-is (the resident may be
    pointing at a *different* forge than origin); records whose URL cannot
    be attested are returned untouched.
    """
    for record in records:
        if not isinstance(record, dict) or str(record.get("url") or "").strip():
            continue
        kind = str(record.get("kind") or "")
        url: str | None = None
        if kind == "issue" and record.get("number"):
            url = links.issue(record["number"], record.get("repo"))
        elif kind == "pr" and record.get("number"):
            url = links.pull_request(record["number"], record.get("repo"))
        elif kind == "commit" and record.get("sha"):
            url = links.commit(record["sha"])
        elif kind == "branch" and record.get("name"):
            url = links.branch(record["name"])
        elif kind == "merge":
            url = (
                links.pull_request(record["pr"]) if record.get("pr") else None
            ) or links.commit(record.get("sha"))
        if url:
            record["url"] = url
    return records


def derive_auto(
    repo_root: Path | None,
    *,
    branch: str | None,
    seed_ref: str | None,
    outbox_dir: Path | None,
    links: _ForgeLinks | None = None,
) -> list[dict[str, Any]]:
    """Zero-resident-effort relics: commits, merges, the pushed branch, and
    the PR.

    *links* lets a caller that already built the forge resolver (see
    :func:`forge_links`) reuse it instead of paying the ``git remote``
    lookups twice per collection.

    Commits and merges come from ``git log``; the PR from the existing
    ``.pr`` control file — nothing new is asked of the resident, matching
    #317's own recommended shape ("no new collection mechanism needed").

    **Merges are a separate block from PRs made** (maintainer, 2026-07-21,
    on run-260721-2122-x5ju's receipt): ``pr`` stays "a PR this run
    created"; a merge the run *performed* is promoted from its commit row
    to a ``merge`` relic instead of hiding among ordinary commits. Three
    attested forms:

    - a merge commit (≥2 parents) whose subject is GitHub's
      "Merge pull request #N …" → ``{"kind": "merge", "pr": N, ...}``
      linking the PR;
    - any other merge commit ("Merge branch 'x'", octopus, hand-written)
      → ``{"kind": "merge", "branch": x?, ...}`` linking the commit;
    - a squash-merge landing (single parent, subject suffixed "(#N)")
      **only when committed by GitHub itself** (``noreply@github.com``) —
      the committer check keeps a hand-written "fix retention race (#501)"
      issue reference from being misread as a merged PR.

    Merges the run performed purely on the remote (``gh pr merge`` without
    pulling the result into the local checkout) leave no local commit and
    stay self-reportable — the one case git archaeology cannot attest.
    """
    if repo_root is None:
        return []
    out: list[dict[str, Any]] = []
    if links is None:
        links = forge_links(repo_root)
    _pr_url = links.pull_request

    if branch:
        commits = _commits_since_seed(repo_root, branch, seed_ref)
        for sha, subject, parent_count, committer in commits[:_MAX_RECORDS]:
            commit_url = links.commit(sha)
            merge: dict[str, Any] | None = None
            if parent_count >= 2:
                merge = {"kind": "merge", "sha": sha, "subject": subject}
                pr_match = _MERGE_PR_SUBJECT.match(subject)
                branch_match = _MERGE_BRANCH_SUBJECT.match(subject)
                if pr_match:
                    merge["pr"] = int(pr_match.group(1))
                    merge["url"] = _pr_url(pr_match.group(1)) or commit_url
                else:
                    if branch_match:
                        merge["branch"] = branch_match.group(1)
                    merge["url"] = commit_url
            elif committer == "noreply@github.com":
                squash_match = _SQUASH_PR_SUFFIX.search(subject)
                if squash_match:
                    merge = {
                        "kind": "merge", "sha": sha, "subject": subject,
                        "pr": int(squash_match.group(1)),
                        "url": _pr_url(squash_match.group(1)) or commit_url,
                    }
            if merge is not None:
                out.append(merge)
            else:
                out.append({"kind": "commit", "sha": sha, "subject": subject, "url": commit_url})
        if commits:
            out.append(
                {"kind": "branch", "name": branch, "url": links.branch(branch)}
            )

    pr_number = _read_pr_control(outbox_dir)
    if pr_number:
        url = _pr_url(pr_number)
        if url:
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
    if kind == "merge":
        # A merge keys on its commit sha but in its own namespace: the
        # maintainer's explicit ask (2026-07-21) is that merges performed
        # are a separate block from PRs made, so a merge relic never
        # collapses into a ``pr`` relic for the same number.
        sha = str(record.get("sha") or "")
        return ("merge", sha[:7]) if sha else None
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
    links = forge_links(repo_root)
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
        link_reported(rest_reported, links)
    auto = derive_auto(
        repo_root, branch=branch, seed_ref=seed_ref, outbox_dir=outbox_dir,
        links=links,
    )
    return dedupe(summary + auto + rest_reported)


# A relic kind that may travel into markup and publish payloads unescaped.
_SAFE_KIND = re.compile(r"[a-z][a-z0-9_-]{0,31}")

# Mirrors ``daemon._LIVE_PORTAL_STATE_NAME``; defined locally because the
# daemon imports this module (importing back would be a cycle). The file
# layout (``.brr/outbox/<event>/portal-state.json``) is the daemon's live
# per-run capsule, refreshed on its heartbeat — see ``daemon.py::
# _write_live_portal_state``.
_PORTAL_STATE_NAME = "portal-state.json"


def live_portal_counts(brr_dir: Path, event_id: str | None) -> dict[str, int] | None:
    """Relics-so-far counts for a *live* run, read from its portal capsule.

    The daemon already runs :func:`live_summary` (git derivation +
    ``.relics.jsonl``) once per heartbeat and persists the result as the
    ``produce`` facet of ``outbox/<event>/portal-state.json`` — so a
    display surface that republishes every few seconds (the live-runs
    snapshot, a chat card re-render) reads that file instead of paying the
    git work again per tick (#342). Freshness is the heartbeat's (~30s),
    which is what the facet already promises its own reader.

    ``None`` when nothing attested is available: no event, no capsule on
    disk, or the facet itself reported ``known: false``. ``{}`` when the
    facet is known and the run has produced nothing yet. Never raises; a
    torn or half-written JSON read degrades to ``None``.
    """
    if not event_id:
        return None
    try:
        payload = json.loads(
            (brr_dir / "outbox" / event_id / _PORTAL_STATE_NAME)
            .read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    produce = payload.get("produce") if isinstance(payload, dict) else None
    if not isinstance(produce, dict) or not produce.get("known"):
        return None
    counts = produce.get("counts")
    if not isinstance(counts, dict):
        return None
    out: dict[str, int] = {}
    for kind, value in counts.items():
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        # Kind names flow into chat-gate markup (Telegram HTML) and the
        # publish payload verbatim; a reported record's ``kind`` is
        # resident-authored free text, so gate unknown vocabulary on a
        # conservative identifier shape rather than escaping downstream.
        if count > 0 and _SAFE_KIND.fullmatch(str(kind)):
            out[str(kind)] = count
    return out


# Compact-tail vocabulary: singular/plural noun per kind, in render order.
# ``branch`` is deliberately absent — mid-flight every commit-bearing run
# has exactly one branch (derive_auto appends it whenever commits exist),
# so a "1 branch" chip restates what the commits chip already implies
# (#329's family logic makes the same call on the receipt side; the
# issue's own example tail — "relics: 2 commits · 1 page" — omits it too).
# ``summary`` is prose, not produce (counts_by_kind already excludes it).
_TAIL_NOUNS: list[tuple[str, str, str]] = [
    ("commit", "commit", "commits"),
    ("merge", "merge", "merges"),
    ("pr", "PR", "PRs"),
    ("issue", "issue", "issues"),
    ("kb", "page", "pages"),
    ("file", "file", "files"),
    ("comment", "comment", "comments"),
    ("message", "message", "messages"),
    ("reply", "reply", "replies"),
]


def counts_phrase(counts: dict[str, int] | None) -> str:
    """``"2 commits · 1 page"`` — the compact relics tail for chat cards.

    Empty/None counts → empty string, so callers can drop the line
    entirely (zero relics must render as *no* line, not "relics: —").
    Unknown kinds render as ``N <kind>`` so a grown backend vocabulary
    still counts for something rather than silently vanishing.
    """
    if not counts:
        return ""
    parts: list[str] = []
    named = {kind for kind, _, _ in _TAIL_NOUNS}
    for kind, singular, plural in _TAIL_NOUNS:
        count = counts.get(kind, 0)
        if count > 0:
            parts.append(f"{count} {singular if count == 1 else plural}")
    for kind in sorted(counts):
        if kind in named or kind in {"branch", "summary"}:
            continue
        count = counts.get(kind, 0)
        if count > 0:
            parts.append(f"{count} {kind}")
    return " · ".join(parts)


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
        links = forge_links(root)
        records = dedupe(
            derive_auto(
                root, branch=branch, seed_ref=seed_ref, outbox_dir=outbox_dir,
                links=links,
            )
            + link_reported(read_reported(outbox_dir), links)
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
