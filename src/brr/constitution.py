"""Constitution template machinery — versioned blocks + shell bridges.

Three jobs used to live in one file (`src/brr/AGENTS.md`): brr's own
repository contract, the adopter template, and the setup prompt's model.
Layer 0 of `design-init-as-a-wake.md` splits them; this module owns the two
mechanical halves of that split:

- **Versioned blocks (L1).** The adopter template
  (`templates/constitution.md`) wraps each universal section in a
  ``<!-- brnrd:block id=… v=… hash=… -->`` … ``<!-- /brnrd:block -->``
  marker pair carrying a schema version and a content hash. Upgrades then
  update *by block identity*, never by whole-file equality — which is what
  `probe_doc_drift` could not do before: it diffed the whole file, so
  per-repo tailoring read as drift and stale universals hid inside it. Per-
  repo material lives outside blocks and is never touched.

- **Shell bridges (L2).** Codex and Cursor read a root ``AGENTS.md``
  natively; Claude reads ``CLAUDE.md``. Init writes a small pointer stub
  (``@AGENTS.md``) for each detected shell that needs one —
  portable and auditable where a symlink is neither — and verification asks
  *reachability* (can this shell see the contract?), not mere existence.

This module is pure: no I/O beyond the explicit ``write_bridges`` /
``verify_reachability`` helpers that take a repo root. It has no daemon or
runner dependency, so init, the drift probe, and tests all share one
implementation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# ── Package paths ────────────────────────────────────────────────────

_PKG_ROOT = Path(__file__).resolve().parent
TEMPLATE_PATH = _PKG_ROOT / "templates" / "constitution.md"


# ── Versioned blocks (L1) ────────────────────────────────────────────

# Length of the content hash we stamp into markers. Full sha256 is
# overkill for a drift signal and makes the marker noisy; 12 hex chars
# (48 bits) is collision-safe for the handful of blocks in one file.
HASH_LEN = 12

_OPEN_RE = re.compile(
    r"<!--\s*brnrd:block\s+"
    r"id=(?P<id>[A-Za-z0-9._-]+)\s+"
    r"v=(?P<v>\d+)\s+"
    r"hash=(?P<hash>[0-9a-fA-F]+|PENDING)\s*-->"
)
_CLOSE_RE = re.compile(r"<!--\s*/brnrd:block\s*-->")


@dataclass(frozen=True)
class Block:
    """One universal block parsed out of a constitution document."""

    id: str
    version: int
    declared_hash: str
    body: str
    # Character offsets of the whole block (open marker … close marker)
    # in the source text, so a rewriter can splice without re-parsing.
    start: int
    end: int
    # Character offset of the hash= token's value, for cheap re-stamping.
    hash_start: int
    hash_end: int

    @property
    def computed_hash(self) -> str:
        return compute_hash(self.body)

    @property
    def hash_ok(self) -> bool:
        return self.declared_hash != "PENDING" and self.declared_hash == self.computed_hash


def compute_hash(body: str) -> str:
    """Content hash of a block body.

    Normalised by ``strip()`` so trailing-newline churn and editor
    whitespace at the block edges do not read as content drift. Interior
    text is hashed verbatim — that is the signal we want.
    """
    digest = hashlib.sha256(body.strip().encode("utf-8")).hexdigest()
    return digest[:HASH_LEN]


class ConstitutionError(ValueError):
    """A structural problem in a constitution document (bad markers)."""


def parse_blocks(text: str) -> list[Block]:
    """Parse every ``brnrd:block`` in *text*, in document order.

    Raises :class:`ConstitutionError` on an unterminated block, a stray
    close marker, or a nested/duplicate id — structural faults a caller
    cannot verify around.
    """
    blocks: list[Block] = []
    seen_ids: set[str] = set()
    pos = 0
    while True:
        om = _OPEN_RE.search(text, pos)
        if om is None:
            # No more opens — ensure no dangling close after here.
            if _CLOSE_RE.search(text, pos):
                raise ConstitutionError("close marker without a matching open")
            break
        # No open marker may appear between this open and its close.
        cm = _CLOSE_RE.search(text, om.end())
        if cm is None:
            raise ConstitutionError(f"block id={om.group('id')!r} is never closed")
        nested = _OPEN_RE.search(text, om.end())
        if nested is not None and nested.start() < cm.start():
            raise ConstitutionError(
                f"block id={om.group('id')!r} contains a nested open marker"
            )
        block_id = om.group("id")
        if block_id in seen_ids:
            raise ConstitutionError(f"duplicate block id={block_id!r}")
        seen_ids.add(block_id)
        body = text[om.end():cm.start()]
        blocks.append(
            Block(
                id=block_id,
                version=int(om.group("v")),
                declared_hash=om.group("hash"),
                body=body,
                start=om.start(),
                end=cm.end(),
                hash_start=om.start("hash"),
                hash_end=om.end("hash"),
            )
        )
        pos = cm.end()
    return blocks


def block_map(text: str) -> dict[str, Block]:
    """``{id: Block}`` for *text* (raises on structural faults)."""
    return {b.id: b for b in parse_blocks(text)}


@dataclass(frozen=True)
class HashMismatch:
    id: str
    declared: str
    computed: str


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of :func:`verify` over one document."""

    mismatches: list[HashMismatch]
    pending: list[str]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.pending and self.error is None


def verify(text: str) -> VerifyResult:
    """Check every block's declared hash against its content.

    A ``PENDING`` placeholder counts as unverified (an unstamped template
    that shipped by mistake), not as a match. Structural faults collapse to
    a single ``error`` — the document cannot be block-verified at all.
    """
    try:
        blocks = parse_blocks(text)
    except ConstitutionError as exc:
        return VerifyResult(mismatches=[], pending=[], error=str(exc))
    mismatches: list[HashMismatch] = []
    pending: list[str] = []
    for b in blocks:
        if b.declared_hash == "PENDING":
            pending.append(b.id)
        elif b.declared_hash != b.computed_hash:
            mismatches.append(
                HashMismatch(id=b.id, declared=b.declared_hash, computed=b.computed_hash)
            )
    return VerifyResult(mismatches=mismatches, pending=pending)


def stamp(text: str) -> str:
    """Return *text* with every block's ``hash=`` set to its computed hash.

    Authoring aid: edit a block's body, run ``stamp`` (see
    ``scripts``/tests), and the marker's hash is brought back into
    agreement. Rewrites right-to-left so earlier offsets stay valid.
    """
    blocks = parse_blocks(text)
    out = text
    for b in reversed(blocks):
        out = out[: b.hash_start] + b.computed_hash + out[b.hash_end :]
    return out


def verify_template() -> VerifyResult:
    """Verify the packaged adopter template (empty-ok if absent)."""
    if not TEMPLATE_PATH.exists():
        return VerifyResult(mismatches=[], pending=[], error="template not found")
    return verify(TEMPLATE_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class BlockDrift:
    """One universal block whose installed copy lags the current template."""

    id: str
    installed_version: int
    template_version: int
    installed_hash: str
    template_hash: str


def block_drift(installed_text: str, template_text: str) -> list[BlockDrift]:
    """Universal blocks that differ between an installed doc and the template.

    Compared by block **identity**, not whole-file equality — the whole
    point of L1. A block present in both whose version or content hash
    differs is drift; per-repo material (outside any block) and blocks the
    adopter dropped are ignored. Returns ``[]`` when either side carries no
    blocks (the pre-L1 shape), leaving the caller to fall back to a
    whole-file compare.
    """
    try:
        installed = block_map(installed_text)
        template = block_map(template_text)
    except ConstitutionError:
        return []
    drift: list[BlockDrift] = []
    for block_id, tpl in template.items():
        cur = installed.get(block_id)
        if cur is None:
            continue
        if cur.version != tpl.version or cur.computed_hash != tpl.computed_hash:
            drift.append(
                BlockDrift(
                    id=block_id,
                    installed_version=cur.version,
                    template_version=tpl.version,
                    installed_hash=cur.computed_hash,
                    template_hash=tpl.computed_hash,
                )
            )
    return drift


# ── Shell bridges (L2) ───────────────────────────────────────────────

# The contract every shell must reach. Codex and Cursor read it natively
# from the repo root; Claude needs a pointer stub.
CONTRACT_FILE = "AGENTS.md"

# Claude Code's import directive: ``@path`` pulls the target into its
# ``CLAUDE.md`` context file.
_IMPORT = f"@{CONTRACT_FILE}"

# shell -> bridge filename, or None when the shell reads AGENTS.md natively.
_BRIDGE_FILE: dict[str, str | None] = {
    "claude": "CLAUDE.md",
    "codex": None,
    "cursor": None,
}


def bridge_filename(shell: str) -> str | None:
    """Bridge file a *shell* needs, or ``None`` if it reads AGENTS.md natively."""
    return _BRIDGE_FILE.get(shell.lower())


def bridge_content(shell: str) -> str | None:
    """Stub contents for a *shell*'s bridge, or ``None`` when none is needed."""
    if bridge_filename(shell) is None:
        return None
    return (
        f"{_IMPORT}\n\n"
        f"<!-- brnrd bridge: {shell} reads this file; {CONTRACT_FILE} is the "
        f"single source of truth. Generated by `brnrd init`. -->\n"
    )


def write_bridges(repo_root: Path, shells: list[str]) -> list[str]:
    """Write a pointer stub for each *shell* that needs one.

    Idempotent and non-destructive: a bridge already pointing at the
    contract (stub *or* symlink) is left untouched; a missing file gets the
    stub; a **hand-authored** bridge file (exists, but no ``@AGENTS.md``
    import) is *repaired by prepending* the import — the user's own
    workflow prose is exactly what the bridge must never clobber. A symlink
    routed somewhere other than the contract is left alone entirely
    (rewriting through it would edit an unrelated file); reachability
    verification reports it instead. Returns the shells whose bridge this
    call created or repaired.
    """
    written: list[str] = []
    for shell in shells:
        fname = bridge_filename(shell)
        if fname is None:
            continue
        content = bridge_content(shell)
        assert content is not None  # guaranteed by fname is not None
        target = repo_root / fname
        if _points_at_contract(target):
            continue
        if target.is_symlink():
            # Routed elsewhere on purpose; not ours to rewrite through.
            continue
        if target.is_file():
            existing = target.read_text(encoding="utf-8")
            target.write_text(f"{content}\n{existing}", encoding="utf-8")
        else:
            target.write_text(content, encoding="utf-8")
        written.append(shell)
    return written


def _points_at_contract(path: Path) -> bool:
    """Whether *path* already routes a shell to the contract file."""
    if path.is_symlink():
        try:
            return path.resolve().name == CONTRACT_FILE
        except OSError:
            return False
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return _IMPORT in text


@dataclass(frozen=True)
class Reachability:
    """Whether one shell can see the contract from its native context path."""

    shell: str
    reachable: bool
    detail: str


def verify_reachability(repo_root: Path, shell: str) -> Reachability:
    """Can *shell* reach the contract from its native context file?

    Reachability, not existence (L2): the contract must both *exist* at the
    root and be *routed to* by whatever file the shell actually loads. Codex
    and Cursor read ``AGENTS.md`` directly, so the contract's presence is the
    whole check; Claude additionally needs its bridge to point at it.
    """
    shell = shell.lower()
    contract = repo_root / CONTRACT_FILE
    if not contract.exists():
        return Reachability(shell, False, f"{CONTRACT_FILE} missing at repo root")
    fname = bridge_filename(shell)
    if fname is None:
        return Reachability(shell, True, f"reads {CONTRACT_FILE} natively")
    bridge = repo_root / fname
    if not bridge.exists():
        return Reachability(shell, False, f"{fname} bridge missing")
    if not _points_at_contract(bridge):
        return Reachability(shell, False, f"{fname} does not point at {CONTRACT_FILE}")
    return Reachability(shell, True, f"{fname} → {CONTRACT_FILE}")
