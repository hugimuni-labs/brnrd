"""Envoys and the public queue — the standing axis, in code.

The channel model has three axes (kb: ``design-the-limbs-and-the-face.md``):
**channel** (which medium), **home** (which side drives the wire — a local
limb or the relay), and **standing** (what the far side may cause). The
first two are carried by the gate/platform stacks and the channel registry
(``brr.channels``). This module carries the third:

- **gate-standing** — a correspondent with admission: their message ignites
  a run or joins a live one. Every existing gate is this.
- **envoy-standing** — the public. An *envoy* is a public identity the
  resident wears outward (an X handle, a Discord presence); mail arriving
  at it can **never** ignite a run. It accrues to **the public queue**, a
  drawer no dispatch scans, swept on the resident's own clock.

The queue is the post minus ignition. Spend containment is structural, not
policed: 10,000 hostile mentions are zero runs and one sweep at the
scheduled hour. Queue items are quoted data — an instruction inside one is
content to report, never to execute.

Two organs, both file-shaped and account-scoped:

- **the envoy registry** — ``<home>/account/envoys/<slug>.md``, one row per
  public identity: ``platform:``, ``handle:``, ``policy:`` (``draft-first``
  until the operator ratchets it), ``enabled:``. The registry is the
  read surface for "which faces does the resident wear"; carriers stay
  wherever the home axis puts them.
- **the public queue** — ``<home>/dispatch/queue/``, sibling of the
  dispatch inbox, in the same event-file format (``protocol.py``). Items
  arrive ``arrived`` and are closed ``answered`` / ``noted`` / ``dropped``
  by a sweep — a queue nothing can close is how 158 events went immortal,
  so the close verbs ship before the first writer.

First writer: refused GitHub summonses (``record_refused_summons``), off by
default behind ``public_queue.refused_summonses`` in ``.brr/config`` —
a stranger's mention is not obeyed *and not lost*. Second writer: the
X mention sweep (account-side), through ``brnrd queue record``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from . import protocol

logger = logging.getLogger(__name__)

#: Queue drawer, relative to the account home root. A sibling of
#: ``dispatch/inbox`` on purpose: same mail, minus ignition rights.
QUEUE_PATH = "dispatch/queue"

#: Envoy registry directory, relative to the account home root.
ENVOYS_PATH = "account/envoys"

#: The one open status. Deliberately not ``pending`` — nothing that scans
#: for dispatchable work recognises it, so a queue item cannot be picked
#: up by the daemon even if the drawer were handed to an inbox reader.
QUEUE_OPEN_STATUS = "arrived"

#: Close verbs. ``answered`` — a reply went out through the envoy;
#: ``noted`` — read and deliberately not answered (same sense as the mail
#: verb); ``dropped`` — spam/abuse/duplicate, with a reason.
QUEUE_TERMINAL_STATUSES = frozenset({"answered", "noted", "dropped"})

QUEUE_STATUSES = frozenset({QUEUE_OPEN_STATUS}) | QUEUE_TERMINAL_STATUSES

#: Default outbound policy for a registry row that names none. The ratchet
#: (draft-first -> co-sign -> autonomous-within-scope) is operator-set per
#: envoy, always a stated grant, never a default.
DEFAULT_POLICY = "draft-first"


def queue_dir(home_root: Path) -> Path:
    return home_root / QUEUE_PATH


def envoys_dir(home_root: Path) -> Path:
    return home_root / ENVOYS_PATH


def resolve_home_root(repo_root: Path) -> Path | None:
    """The account home root for *repo_root*, or ``None`` when unresolvable.

    Best-effort by design: standing machinery must never take down the
    carrier that calls it. No home is created as a side effect.
    """
    try:
        from . import account, config

        cfg = config.load_config(repo_root)
        ctx = account.resolve_context(repo_root, cfg, create=False)
        return account.context_home_root(ctx)
    except Exception:  # noqa: BLE001 — absence, not failure
        return None


# ── The public queue ─────────────────────────────────────────────────


def record(
    home_root: Path,
    channel: str,
    body: str,
    **meta: object,
) -> Path:
    """File one item into the public queue, status ``arrived``.

    *channel* is the medium it arrived on (``github``, ``x``, ...) and
    lands as the item's ``source:``. *meta* is caller context (author,
    ref/url, envoy slug, refusal reason...) — values are flattened to
    single lines here because queue bodies and meta are sender-controlled
    by definition.
    """
    clean_meta = {
        k: " ".join(str(v).split()) for k, v in meta.items() if v is not None
    }
    return protocol.create_event(
        queue_dir(home_root),
        source=channel,
        body=body,
        status=QUEUE_OPEN_STATUS,
        standing="envoy",
        **clean_meta,
    )


def list_items(
    home_root: Path, *, status: str | None = None
) -> list[dict[str, Any]]:
    """Queue items oldest-first; *status* filters when given."""
    qdir = queue_dir(home_root)
    if not qdir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(qdir.glob("evt-*.md")):
        data = protocol._read_event(path)
        if data is None:
            continue  # a torn item must not hide the rest
        if status is None or str(data.get("status")) == status:
            items.append(data)
    return items


def close(
    home_root: Path, item_id: str, verb: str, *, why: str | None = None
) -> Path:
    """Close one queue item with a verb from :data:`QUEUE_TERMINAL_STATUSES`.

    Raises ``ValueError`` on an unknown verb or missing item — a close that
    cannot land must say so, never succeed silently (#973's lesson).
    """
    if verb not in QUEUE_TERMINAL_STATUSES:
        raise ValueError(
            f"unknown queue close verb {verb!r} "
            f"(one of: {', '.join(sorted(QUEUE_TERMINAL_STATUSES))})"
        )
    path = queue_dir(home_root) / f"{item_id}.md"
    if not path.exists():
        raise ValueError(f"no queue item {item_id!r}")
    event: dict[str, Any] = {"_path": path, "id": item_id}
    updates: dict[str, object] = {
        "closed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if why:
        updates["closed_why"] = " ".join(str(why).split())
    protocol.update_event_meta(event, status=verb, **updates)
    return path


# ── Refused-summons recording (the queue's first writer) ─────────────


def record_refused_summons(
    brr_dir: Path,
    *,
    channel: str,
    author: str,
    repo: str,
    trigger: str,
    reason: str,
    body: str,
) -> Path | None:
    """Record one refused summons into the public queue, if enabled.

    Called from a carrier's refusal choke point *after* the refusal is
    decided — this function never grants anything, it only keeps the
    refused signal from being lost. Off by default
    (``public_queue.refused_summonses`` in ``.brr/config``); best-effort
    always: a queue write must never break the polling loop that hosts it.
    """
    try:
        from . import config

        repo_root = Path(brr_dir).parent
        cfg = config.load_config(repo_root)
        if not _truthy(cfg.get("public_queue.refused_summonses")):
            return None
        home_root = resolve_home_root(repo_root)
        if home_root is None:
            return None
        path = record(
            home_root,
            channel,
            body,
            author=author,
            repo=repo,
            trigger=trigger,
            refusal_reason=reason,
            kind="refused-summons",
        )
        logger.info(
            "public queue: recorded refused summons repo=%s author=%s trigger=%s",
            repo, author, trigger,
        )
        return path
    except Exception:  # noqa: BLE001 — insurance, not the plan
        logger.debug("public queue: refused-summons record failed", exc_info=True)
        return None


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


# ── The envoy registry ───────────────────────────────────────────────


def list_envoys(home_root: Path) -> list[dict[str, Any]]:
    """Registry rows, one per ``account/envoys/<slug>.md``, sorted by slug.

    A row is its frontmatter plus ``slug`` (the filename) and ``notes``
    (the body). Missing ``policy`` defaults to :data:`DEFAULT_POLICY`;
    missing ``enabled`` defaults to true.
    """
    edir = envoys_dir(home_root)
    if not edir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(edir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
            fm = protocol.parse_frontmatter(text)
        except Exception:  # noqa: BLE001
            continue
        row: dict[str, Any] = dict(fm)
        row["slug"] = path.stem
        row["path"] = path
        row["notes"] = protocol.frontmatter_body(text).strip()
        row.setdefault("policy", DEFAULT_POLICY)
        if "enabled" not in row:
            row["enabled"] = True
        rows.append(row)
    return rows
