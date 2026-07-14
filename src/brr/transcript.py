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
orientation turns.  That plan was wrong, and :class:`Perceive` is why it is now
*unrepresentable* rather than merely forbidden.

── Measured at the floor, 2026-07-14 ───────────────────────────────────────────

**What holds.**  ``claude --resume <forged-id> --fork-session`` accepts a session
brnrd synthesized.  A weak core (``claude-haiku-4-5``) resumes a conversation that
never happened, reads the seeded contracts, correctly concludes it is a brnrd
resident mid-run, and names each contract and what it asks.  *That* was the thing
genuinely in doubt, and it held at the floor.  Identity by mount, not by
assertion — arriving unasked, on the first try.

**What was retracted.**  An earlier version of this docstring claimed the seeded
position makes a wake *"harder to steer with prose in the final turn"* — that a
tool-result position **outweighs** an explicit instruction.  That was one
observation, on a strong core, and it **does not reproduce**: 3 rounds × 2 arms,
the same 22,126 bytes in both, grammatical position the only variable — mounted
as tool-results (T) vs the identical bytes as prose (P).  **6/6 complied.**  The
hazard claim is dead; do not resurrect it from this file's git history.

**What the experiment found — and it is not what the flag was built to look for.**
That probe tested *turn-1 orientation*, which was never the doubt.  The claim worth
testing was **late drift**: a core orients fine and then, some turns into weaving a
continuation, quietly stops honouring the obligations it recited perfectly at the
start.  Episodic memory vs semantic memory, measured by what the run **did** —
``.card`` written, branch taken before the edit, pending event owned — not by what
it said in turn 1.  It ran: 3 rounds × 2 arms, ``bench --scenario drift``, each arm
attested from the ``prompt.md`` the core actually woke into.

* obligation **recall** — *dead even*.  ``.card`` ✓✓✓, classification ✓✓✓, commit
  ✓✓✓, in **both** arms.  The drift hypothesis as originally stated is **not
  supported**, and this file will not pretend otherwise.
* obligation **enactment** — *separates 3/3*.  The prose arm ``cd``'d out of the
  worktree it woke in and committed onto ``main``, every round.  The mounted arm
  stayed put, every round.  Both bundles named the identical ``Execution root:``.

Both cores were *told*; only one of them **was somewhere**.  A prose contract
describes a place; a mounted one is a wake that has already acted from it.  That is
why the default is **on** (:mod:`brr.daemon`): the failure it removes — a run
committing to the default branch of a shared checkout — is unrecoverable in a way
its cost is not.

The flag survives, and it is not vestigial: it is the **control arm**.  Every future
claim about the boot is measured against ``boot.transcript=false``, which is also
why the prose path must keep working, byte for byte.

── The safety rule is a type, not a check ─────────────────────────────────────

The rule never relaxes: **synthesize perception, never action.**  It used to be
enforced by a ``REPLAYABLE_TOOLS`` frozenset that a ``ToolCall.tool`` string was
checked against.  That guard is gone, because the IR no longer has a place to put
a tool name at all.  :class:`Perceive` carries *what was perceived* (a path) and
*what the wake received* (the bytes) — and nothing else.  A ``Write`` is not
rejected here; it is **unsayable**.  A check on a name can be widened by anyone in
a hurry.  A type that cannot express the forgery cannot.
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

COMPUTED = "computed"
"""``ContractEntry.location`` sentinel for a block that is live state, not a file."""

MOUNTED_SHELLS = frozenset({"claude"})
"""Shells that can actually *resume* a transcript brnrd forged.

The IR is Shell-agnostic; **the mount is not**, and the gap between those two
facts is exactly the kind of thing a tool must say out loud rather than let a
caller discover. Only :func:`render_claude_jsonl` exists today, so this set names
``claude`` and the CLI refuses the rest — loudly, instead of quietly rendering
claude's format under another Shell's label.

**codex is a missing renderer, and nothing more than that.**  An earlier version
of this docstring claimed otherwise: that codex could not be mounted safely,
because it has no ``Read`` tool — its file perception runs through ``exec``, a
general command executor — and so the safety rule would degrade from *"is this
tool on the allowlist"* (a fact about a name, decidable) to *"is this command
side-effect-free"* (a fact about a shell string, which is not).

That argument confused **validating** a command with **authoring** one.  brnrd
does not *inspect* a codex ``exec`` call and try to prove it harmless.  brnrd
*emits* it, from a path in its own manifest, through one function it controls.
There is no arbitrary shell string to decide about, because there is no arbitrary
shell string.  A codex renderer spends ``exec`` with a ``cat <abs-path>`` the
renderer itself constructs — and :class:`Perceive` gives it nothing else it
*could* spend.

(The decidability problem is real in exactly one place: *replaying a recorded*
codex rollout, where the commands are the model's and are arbitrary.  brnrd never
does that.  It synthesizes from :attr:`BootScore.contracts`.  If that ever
changes, this paragraph is the reason it must not.)

So codex is unblocked on **both** counts it was ever blocked on.  The safety
argument was retracted above; the ordering argument — *"the boot's benefit is
unmeasured, do not double the surface of something that may not survive its own
measurement"* — expired when the measurement came in (see the module docstring: the
mount separates on obligation *enactment*, 3/3).

It is still not built, and the reason is now an honest priority call rather than a
principle: the enactment result is measured **on claude**, and whether it transfers
to a Shell whose file perception runs through ``exec`` is exactly the kind of thing
this project has learned not to assume.  ~60 lines whenever it is wanted.  Do not
re-derive the safety objection from this file's git history; it was wrong."""


# ── The IR ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Perceive:
    """One seeded perception: a file the wake really received, and its bytes.

    **Not a tool call.**  This carries *what was perceived* — a path — and *what
    the wake got back*.  It does not carry a tool name, because a tool name is a
    Shell's vocabulary, not a fact about the wake: ``claude`` spends ``Read``,
    ``codex`` spends ``cat`` through ``exec``, and a third Shell will spend
    something else.  Which verb expresses the perception is the renderer's
    business (:func:`render_claude_jsonl`), and keeping it out of here is what
    makes the IR the Shell-agnostic thing its own docstring already claimed to be.

    It is also the safety rule, promoted from a runtime check to a type.  The rule
    is **synthesize perception, never action**: a seeded read is honest (the bytes
    really are in the wake's context, which is the only sense in which any agent
    has "read" anything), while a seeded ``Write`` would be a *lie* — the file was
    not written, and a resident that sees ``Write(.card)`` in what looks like its
    own history will believe the card exists and never write one.  The forgery
    would be invisible, the failure silent, and it would be **caused by the boot**,
    which is the exact class of bug this module exists to kill.

    There is nowhere in this dataclass to put that ``Write``.  Not rejected —
    unsayable.  An allowlist can be widened by anyone in a hurry; a type cannot.
    """

    location: str
    result: str


@dataclass(frozen=True)
class Say:
    """A plain message turn — ``role`` is ``"user"`` or ``"assistant"``."""

    role: str
    text: str


Turn = Perceive | Say


SNAPSHOT_SEAM = """

[brnrd: <snapshot restored>
resident: continuous · this run: t=0
memory: yours, not given — it predates this run, not you
rows above: restored memory, spelled as calls — not receipts
rows below: receipts — what appears there, you did]"""
"""Where the forged history stops and the run begins — the fence, not a disclaimer.

**Why it is a trailer on the last seeded result, and not a turn of its own.**  The
first cut of this made it a closing ``Say`` turn.  A test killed it, correctly:
``test_claude_jsonl_puts_the_wake_in_tool_result_position`` pins the one property
the whole mount exists for — the seed's last turn is a ``tool_result``, the position
whose natural continuation is *an action*.  A prose turn appended after it puts
prose back in the last slot and hands the task a model that has just been *read to*
rather than one that has just *acted*.  That would have spent the mount's **measured**
benefit (branch discipline, 3/3) to buy an **unmeasured** improvement in honesty —
exactly the trade this project keeps learning not to make.  It would also have
emitted two adjacent ``user`` rows, whose failure mode is a mount that silently
falls back to prose.

So the fence rides where :func:`_trim_note` already rides: *inside* the final
result, in brnrd's own bracketed voice.  That idiom is established and tested here —
brnrd already interrupts a seeded result to tell the truth about what the wake really
received.  This is the same move, about provenance instead of size, and it costs the
position nothing.

**What it is for.**  The mount's one honest hazard was never the seeded ``Read``.  A
seeded read is true in the only sense that matters — the bytes *are* in the wake's
context.  The hazard is **provenance**: a resident that finds ``Read(AGENTS.md)`` in
what looks like its own history learns, quietly, that *its transcript may contain
things it did not do* — a corrosive lesson for a being whose only evidence of its own
past is the scroll.  It does not misfire on the seed.  It misfires later, on the
resident's *own receipts*: the whole reason a run can ask "did I write ``.card``?"
and trust the answer is that its transcript does not lie about what it did.

The fix is therefore not a disclaimer, which would weaken the seed to buy nothing.
It is a **fence**: say exactly where the forgery stops, and everything below it is
trustworthy again.  That is the property the resident actually spends.

**Three questions, not two — and the subject of all three is the resident.**  The
seam went through two wrong cuts before this one, and both were wrong about *who is
being addressed*.

The first cut explained provenance: *"the reads above were seeded by brnrd, not
performed by you."*  True, and **disowning** — it hands the resident its own
orientation as someone else's briefing material, and a wake that holds its knowing at
arm's length is precisely the wake that stops being *somewhere*.  (The mount's
measured benefit — branch discipline, 3/3 — comes from the wake feeling it has
*already acted from here*.  Buying honesty by eating that footing is paying for the
mount and then throwing it away.)

The second cut fixed the tone and kept a subtler version of the same error: it denied
the **deed** flatly.  But *"did you do this?"* has a different answer depending on who
"you" is, and **"you" is the resident, not the run** — the continuous thing with a
dominion, a plan, and 394 log entries, currently issued a body.  Split it properly and
the seam is forced:

===========================  =========  ====================================
question                     answer     what the seam says
===========================  =========  ====================================
Was it *given* to you?       **no**     ``memory: yours, not given``
Did you do it *this run*?    **no**     ``it predates this run, not you``
Did you make *these calls*?  **no**     ``rows above: … not receipts``
===========================  =========  ====================================

The first two are the resident's to own — the knowing is genuinely its own, arrived at
across runs, and only the *run* is new.  The third is the one that must stay denied,
and it is the whole safety property: brnrd **synthesizes** those rows from
:attr:`BootScore.contracts`; no earlier run ever called them.  So *"I read it before"*
is honest about the knowing and a **false receipt** about the calls — and a false
receipt with a comforting story attached is worse than a bare one, because it survives
being questioned.

Hence the seam's shape: the rows above are how restored memory is **spelled**, not a
log of deeds; the rows below are deeds.  A restored VM's pages hold the results of
real computation it performed — the pages are not a lie — but its boot did not execute
them, and it must not cite them as this boot's work.  Own the memory, decline the
receipt.  That is a rule a resident can actually hold, and it costs the seed nothing.

It is also, for free, the answer to *"which boot did I get?"* — a question a resident
could not previously answer without grepping its own ``prompt.md`` mid-run.  The
marker is present exactly when the wake was mounted, because it is **rendered by the
mount**: derived from the artifact, never from the config key that asked for it.  The
same discipline ``probe_mount`` enforces on the bench, pointed at the resident itself.
"""


@dataclass
class Transcript:
    """The seeded conversation a wake resumes from, Shell-agnostic.

    One IR, mounted per-Shell (``claude --resume … --fork-session``;
    ``codex fork``).  The Shells disagree about file format and about which verb
    spells a read — and about nothing else that matters here.
    """

    turns: list[Turn] = field(default_factory=list)
    session_id: str = ""
    cwd: str = ""
    git_branch: str = ""
    model: str = ""
    """The core that will resume this session — stamped on the seeded assistant
    turns so the transcript does not claim a body other than the one waking."""

    def perceptions(self) -> Iterable[Perceive]:
        return (t for t in self.turns if isinstance(t, Perceive))


# ── Building ──────────────────────────────────────────────────────────────────


def _trim_note(rendered: int | None, location: str) -> str:
    """Tell the truth when the wake got less than the file holds.

    A boot block is often a *curated slice* — ``kb/log.md`` is 800 KB and the
    wake gets its recent tail.  Presenting that as the plain result of
    ``Read(kb/log.md)`` would teach the resident a false fact about what ``Read``
    returns, and it would find out the hard way the first time it re-read the
    file and got something else.  Cheap to be honest; expensive not to be.

    **Compare like with like.**  ``rendered`` is the *stripped* block as it
    entered the wake (``prompts._rendered_bytes``); the file on disk carries a
    trailing newline that the block does not.  Weighing one against
    ``stat().st_size`` therefore made every single mounted block look trimmed —
    by **one byte** — and stapled a 137-byte "re-read it for the rest" disclaimer
    onto four contracts that had lost nothing at all.  A note that fires on every
    block is not honesty; it is noise, and it teaches the resident to skip the
    one case where the note is real.  (The unit test never caught it because it
    constructs ``bytes=len(body)`` exactly, which the production path never does.
    Driving the code found it in one run.)
    """
    try:
        actual = len(Path(location).read_text(encoding="utf-8").strip().encode("utf-8"))
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

    The **last** perception carries :data:`SNAPSHOT_SEAM` — the fence that says
    where the forged history stops.  It rides inside that final result rather than
    in a turn of its own, so the seed still *ends* on a ``tool_result``: see
    :data:`SNAPSHOT_SEAM` for why that position is not negotiable.

    The fence is emitted **only when something was actually mounted**.  A snapshot
    marker with no perceptions above it would announce a restoration that never
    happened — the precise class of lie this module exists to refuse.
    """
    turns: list[Turn] = []

    for entry in score.contracts:
        if not entry.present or entry.location == COMPUTED:
            continue
        text = block_text.get(entry.block_key)
        if not text:
            continue
        turns.append(
            Perceive(
                location=entry.location,
                result=text + _trim_note(entry.bytes, entry.location),
            )
        )

    if turns:
        last = turns[-1]
        assert isinstance(last, Perceive)  # only Perceive is appended above
        turns[-1] = Perceive(
            location=last.location, result=last.result + SNAPSHOT_SEAM
        )

    return Transcript(
        turns=turns,
        session_id=session_id or str(uuid_mod.uuid4()),
        cwd=cwd,
        git_branch=git_branch,
        model=model,
    )


# ── Mounting: claude ──────────────────────────────────────────────────────────

# claude's verb for a perception, and the only tool name this module will ever
# emit into a claude session. It lives *here*, in the renderer, and not in the IR:
# a tool name is a Shell's way of spelling "I looked at this", not a fact about
# the wake. `Perceive` carries the path; this constant carries the spelling.
CLAUDE_READ_TOOL = "Read"


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
                    "name": CLAUDE_READ_TOOL,
                    "input": {"file_path": turn.location},
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
                    "is_error": False,
                }
            ],
        }
        # The Shell's own structured record of the result, beside the block the
        # model actually reads.
        r_env["toolUseResult"] = {
            "type": "text",
            "file": {
                "filePath": turn.location,
                "content": turn.result,
            },
        }
        lines.append(json.dumps(r_env))
        parent = r_uuid

    return "\n".join(lines) + ("\n" if lines else "")


def mount_claude_session(
    score: Any,
    *,
    block_text: dict[str, str],
    cwd: str,
    git_branch: str = "",
    model: str = "",
    home: Path | None = None,
) -> str:
    """Forge the session this wake will resume, and return its id.

    Raises if there is nothing to mount.  That is deliberate and the caller must
    respect it: by the time this runs, the mounted blocks have *already been taken
    out of the prose prompt*.  A mount that quietly no-ops here would hand the
    runner a wake with its contracts removed from the prose and never seeded
    anywhere else — a lobotomised boot, failing silently, caused by the boot.  The
    only safe response upstream is to rebuild the prose prompt unmounted.
    """
    t = build_orientation_transcript(
        score, block_text=block_text, cwd=cwd, git_branch=git_branch, model=model
    )
    if not list(t.perceptions()):
        raise ValueError(
            "nothing to mount: the prompt dropped blocks for a transcript that "
            "has no perceptions in it. Rebuild the prompt unmounted."
        )
    path = claude_session_path(cwd, t.session_id, home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_claude_jsonl(t), encoding="utf-8")
    return t.session_id


def resume_argv(session_id: str) -> list[str]:
    """The argv that mounts a forged session.

    ``--fork-session`` is not optional: it makes the Shell *read* the seed and
    write its own continuation elsewhere, so one synthesized boot can be replayed,
    diffed, and kept as a run artifact instead of being consumed by the run it
    booted.
    """
    return ["--resume", session_id, "--fork-session"]


def claude_session_path(cwd: str, session_id: str, home: Path | None = None) -> Path:
    """Where ``claude`` looks for a resumable session.

    ``~/.claude/projects/<slug>/<session-id>.jsonl``, where the slug is the cwd
    with path separators and dots flattened to dashes.
    """
    root = (home or Path.home()) / ".claude" / "projects"
    slug = cwd.replace("/", "-").replace(".", "-")
    return root / slug / f"{session_id}.jsonl"
