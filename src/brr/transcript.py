"""Boot as a transcript — the wake as *evidence of having oriented*.

Slice 4, and it is a move about **grammatical position**, not about content.

The whole 73 KB wake is delivered as a *single user message* (``runner.py``
passes the prompt as one argv token).  It therefore contains no ``tool_use`` and
no ``tool_result``: it is structurally incapable of being an example of *action*.
It holds only **descriptions** of actions, written as prose, sitting in
prose-position.  And a language model completes the grammar it is handed — prose
in prose-position completes as more prose.

That is why the ~400-byte post-tool portal capsule moves a resident when 5 KB of
identity prose does not.  Earlier work filed that as "differential and short."
Wrong.  It lands as a **tool result** — the one position in a conversation whose
natural continuation is *an action*.  Position → grammar → continuation.

So: stop *telling* the wake to orient, and hand it a conversation in which it
**already has**.  Read(AGENTS.md) and its result.  Read(kb/index.md) and its
result.  Hooks interwoven where hooks really fire.  Then the task — arriving to a
model whose own recent turns are tool calls, in tool-result position, mid-work.

The IR for this already existed and nobody noticed: :attr:`BootScore.contracts`
carries a ``location`` for every block — an absolute path, or the sentinel
``"computed"``.  That *is* the mounting table.  It gives the split a principle
instead of a hand-kept list, so a block added next year classifies itself:

- **backed by a file** → mount as a ``Read`` tool call and its result.
- **``"computed"``** (the kernel, the run bundle, live portal state) → stays
  prose.  There is no honest ``Read`` that returns it; it is not on disk.

── The one safety rule ────────────────────────────────────────────────────────

**Synthesize perception. Never synthesize action.**

A ``Read`` in the seeded transcript is *honest*: the bytes really are in the
wake's context, which is the only sense in which any agent has "read" anything.
A ``Write`` would be a **lie** — the file was not written, and a resident that
sees ``Write(.card)`` in what appears to be its own history will believe the card
exists and never write one.  The forgery would be invisible and the failure
silent, and it would be *caused by the boot*, which is the exact class of bug
this work exists to kill.

An earlier plan for this slice proposed seeding ``.card written`` among the
orientation turns.  That plan was wrong.  :data:`REPLAYABLE_TOOLS` is the guard,
and :func:`build_orientation_transcript` refuses anything outside it.

── Verified, 2026-07-14 ────────────────────────────────────────────────────────

``claude --resume <forged-id> --fork-session`` **accepts a session brnrd
synthesized.**  Measured: a 4-row seed (two ``Read`` calls and their results) was
resumed; both forged ``toolu_`` ids were replayed verbatim into the fork, and the
model continued for 319 rows.

The continuation is the finding.  The final user message said *"Without using any
tools: name the two files you just read."*  It **immediately used tools** — Read,
Bash, ``git status`` — and never answered the question.  Having woken in
tool-result position, it completed *the grammar it was in* rather than the
sentence it was handed.

Read that twice before extending this module.  It is the thesis working, and it
is a live hazard: a transcript-seeded wake is **harder to steer with prose in the
final turn**, because the seeded position outweighs it.  That is precisely the
power being bought here — the boot stops asking and starts demonstrating — and
precisely the reason :data:`REPLAYABLE_TOOLS` may never grow a mutating tool.  A
forged action would not be a suggestion to the wake.  It would be a fact it acts
on.

(It also read the four boot contracts in the seed, concluded it was a brnrd
resident mid-run, and went looking for the conversation directory.  Identity by
mount, not by assertion — arriving unasked, on the first try.)
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Perception only. Every tool here must be free of side effects — a seeded call
# to it changes nothing in the world, so replaying it as though it happened is a
# true statement about the wake's context rather than a forgery about the disk.
#
# Adding a mutating tool to this set would let the boot lie to the resident about
# work it never did. There is no use case that justifies it; if one ever seems to
# appear, the thing to build is a real pre-run action by the daemon, executed for
# real, not a fabricated turn describing one.
REPLAYABLE_TOOLS = frozenset({"Read"})

COMPUTED = "computed"
"""``ContractEntry.location`` sentinel for a block that is live state, not a file."""


class ForgedActionError(RuntimeError):
    """Raised when a transcript would claim an action the daemon did not take."""


# ── The IR ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolCall:
    """One seeded perception: an assistant tool_use and the result it received."""

    tool: str
    input: dict[str, Any]
    result: str
    is_error: bool = False

    def __post_init__(self) -> None:
        if self.tool not in REPLAYABLE_TOOLS:
            raise ForgedActionError(
                f"{self.tool!r} is not replayable. The boot transcript may "
                f"synthesize perception ({', '.join(sorted(REPLAYABLE_TOOLS))}) "
                f"and never action — a seeded {self.tool!r} would tell the wake "
                f"it did something it did not do."
            )


@dataclass(frozen=True)
class Say:
    """A plain message turn — ``role`` is ``"user"`` or ``"assistant"``."""

    role: str
    text: str


Turn = ToolCall | Say


@dataclass
class Transcript:
    """The seeded conversation a wake resumes from, Shell-agnostic.

    One IR, mounted per-Shell (``claude --resume … --fork-session``;
    ``codex fork``).  The Shells disagree about file format and about nothing
    else that matters here.
    """

    turns: list[Turn] = field(default_factory=list)
    session_id: str = ""
    cwd: str = ""
    git_branch: str = ""
    model: str = ""
    """The core that will resume this session — stamped on the seeded assistant
    turns so the transcript does not claim a body other than the one waking."""

    def tool_calls(self) -> Iterable[ToolCall]:
        return (t for t in self.turns if isinstance(t, ToolCall))


# ── Building ──────────────────────────────────────────────────────────────────


def _trim_note(rendered: int | None, location: str) -> str:
    """Tell the truth when the wake got less than the file holds.

    A boot block is often a *curated slice* — ``kb/log.md`` is 800 KB and the
    wake gets its recent tail.  Presenting that as the plain result of
    ``Read(kb/log.md)`` would teach the resident a false fact about what ``Read``
    returns, and it would find out the hard way the first time it re-read the
    file and got something else.  Cheap to be honest; expensive not to be.
    """
    try:
        actual = Path(location).stat().st_size
    except OSError:
        return ""
    if rendered is None or rendered >= actual:
        return ""
    return (
        f"\n\n[brnrd: this block was rendered to {rendered:,} bytes for the wake "
        f"budget; the file on disk is {actual:,} bytes. Re-read it directly for "
        f"the rest.]"
    )


def build_orientation_transcript(
    score: Any,
    *,
    block_text: dict[str, str],
    session_id: str | None = None,
    cwd: str = "",
    git_branch: str = "",
    model: str = "",
) -> Transcript:
    """Turn a :class:`BootScore` into the conversation the wake wakes up inside.

    *block_text* maps ``block_key`` → the text that block actually contributed to
    the wake.  The rendered text, not the file's — a trimmed block is mounted as
    what the wake really received, with :func:`_trim_note` saying so.

    Blocks whose ``location`` is :data:`COMPUTED` are skipped: they are live
    state (the kernel, the run bundle, portal posture), they exist nowhere on
    disk, and a ``Read`` returning them would be fiction.  Callers keep rendering
    those as prose — which is correct, because *that is what they are*.
    """
    turns: list[Turn] = []

    for entry in score.contracts:
        if not entry.present or entry.location == COMPUTED:
            continue
        text = block_text.get(entry.block_key)
        if not text:
            continue
        turns.append(
            ToolCall(
                tool="Read",
                input={"file_path": entry.location},
                result=text + _trim_note(entry.bytes, entry.location),
            )
        )

    return Transcript(
        turns=turns,
        session_id=session_id or str(uuid_mod.uuid4()),
        cwd=cwd,
        git_branch=git_branch,
        model=model,
    )


# ── Mounting: claude ──────────────────────────────────────────────────────────


def _envelope(
    kind: str,
    t: Transcript,
    uuid: str,
    parent: str | None,
    stamp: str,
) -> dict[str, Any]:
    return {
        "parentUuid": parent,
        "isSidechain": False,
        "type": kind,
        "uuid": uuid,
        "timestamp": stamp,
        "userType": "external",
        "entrypoint": "sdk-cli",
        "cwd": t.cwd,
        "sessionId": t.session_id,
        "version": "2.1.207",
        "gitBranch": t.git_branch,
    }


def render_claude_jsonl(t: Transcript, *, now: datetime | None = None) -> str:
    """Render the transcript as a resumable ``claude`` session file.

    Mounted with ``claude --resume <session_id> --fork-session``: the fork means
    the seeded file is read and never written back, so one synthesized boot can
    be replayed, diffed, and kept as a run artifact.
    """
    stamp = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    lines: list[str] = []
    parent: str | None = None

    for turn in t.turns:
        if isinstance(turn, Say):
            u = str(uuid_mod.uuid4())
            env = _envelope(turn.role, t, u, parent, stamp)
            env["message"] = {"role": turn.role, "content": turn.text}
            lines.append(json.dumps(env))
            parent = u
            continue

        call_id = f"toolu_{uuid_mod.uuid4().hex[:24]}"

        # Provider response metadata a real turn carries. None of it is read by
        # the model — but the Shell parses these files, and a row that is merely
        # *plausible* is a row that may be rejected. Populate the shape fully; a
        # boot that fails to mount is worse than one that mounts imperfectly.
        a_uuid = str(uuid_mod.uuid4())
        a_env = _envelope("assistant", t, a_uuid, parent, stamp)
        a_env["requestId"] = f"req_{uuid_mod.uuid4().hex[:24]}"
        a_env["message"] = {
            "id": f"msg_{uuid_mod.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "model": t.model or "claude-sonnet-4-6",
            "content": [
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": turn.tool,
                    "input": turn.input,
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
        lines.append(json.dumps(a_env))

        r_uuid = str(uuid_mod.uuid4())
        r_env = _envelope("user", t, r_uuid, a_uuid, stamp)
        r_env["sourceToolAssistantUUID"] = a_uuid
        r_env["promptId"] = str(uuid_mod.uuid4())
        r_env["message"] = {
            "role": "user",
            "content": [
                {
                    "tool_use_id": call_id,
                    "type": "tool_result",
                    "content": turn.result,
                    "is_error": turn.is_error,
                }
            ],
        }
        # The Shell's own structured record of the result, beside the block the
        # model actually reads.
        r_env["toolUseResult"] = {
            "type": "text",
            "file": {
                "filePath": turn.input.get("file_path", ""),
                "content": turn.result,
            },
        }
        lines.append(json.dumps(r_env))
        parent = r_uuid

    return "\n".join(lines) + ("\n" if lines else "")


def claude_session_path(cwd: str, session_id: str, home: Path | None = None) -> Path:
    """Where ``claude`` looks for a resumable session.

    ``~/.claude/projects/<slug>/<session-id>.jsonl``, where the slug is the cwd
    with path separators and dots flattened to dashes.
    """
    root = (home or Path.home()) / ".claude" / "projects"
    slug = cwd.replace("/", "-").replace(".", "-")
    return root / slug / f"{session_id}.jsonl"
