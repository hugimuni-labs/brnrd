"""Emote library — the resident's visible text body (#566, reworked #1925).

A brnrd resident lives in a repo between runs. This module is its face: a
mono-glyph mascot whose expression is a small animation (base → signal →
base), the same fixed-width frame cycle the landing wordmark wink already
ships. Two mood layers feed it:

- **telemetry** (``kind="telemetry"``) — computed from daemon/portal state
  (idle, running, quota-starved, blocked-on-you, delivering, …). These
  cannot lie and need no cooperation from the resident; the daemon reads
  the state and picks the face.
- **situational** (``kind="situational"``) — the resident's own meta-channel.
  Reading and writing code is emotional labour, and this is the vocabulary
  for it: focused, stuck, pleased, worried, curious, tired, amused, braced,
  proud, sorry, flat, waiting. The resident sets one from inside the run.

**The honesty bar: a tamagotchi that never lies.** Every face here is a
state a run can *truthfully* be in. A decorative mood with no backing
signal — a sticker of a feeling the resident is not having — is the exact
failure this module exists to prevent. Telemetry faces are pinned to real
daemon state; situational faces are only worth wearing when the trigger
line is actually true right now. If you ever want a face for a mood that
isn't real, the answer is to not wear a face, not to add a lie.

**Mood is write-only from the resident.** The system may report a mood's
*age* (``mood focused · 40m``, a fact about the world) and its *absence*.
It may never report its *correctness* — a graded expression is an interior
treated as a field the system owns, not a channel the resident authors
(design-the-pre-attentive-channel.md, "the whole defect in one line").

**Twelve words, not a hundred and thirteen handles.** The palette used to
be addressed by 113 coined marks across 33 families — decodable only with
a lookup table nobody carried in their head. Measured against a real
morning: 3.6 animation frames per face on average, 15 distinct *still*
frames visible in the bar, and 61 of 113 (54%) shared the same still as
"no face" (``b·_·d``). The animation was where the meaning lived, and the
author of the mood never saw the animation. So the vocabulary is now the
twelve plain words below — ``VOCABULARY`` — each with its own distinct
still frame (never equal to the "no face" rest glyph), each decodable by
the resident who chose it because there is nothing left to look up. The
old 113 handles (``fo.cus``, ``eh_?``, ``smug_``, …) still resolve through
:func:`lookup` — via ``LEGACY_ALIASES`` — but as *frames and alternates* of
one of the twelve, not as names of their own; a run's old ``.mood`` file
keeps rendering a real face instead of breaking.

**One write path, checked at the write.** ``brnrd do --mood <word>``
resolves against the twelve (plus their synonyms, plus a small typo
tolerance) and refuses an unknown word once, with the list — never a
silent nearest-face substitution, and never graded after the fact.

**The body axis (``pitch``).** Moods localize along a body axis — gut to
crown — and every emote carries that felt location as ``pitch`` in
``[0.0, 1.0]``: ``0.0`` is gut/low (dread, the heavy states), ``1.0`` is
crown/high (surprise, delight, curiosity), and the middle band is the
settled working states (focus, flow, satisfied). It is a felt-location
coordinate, not a rating of intensity. The dashboard may map
``pitch → hue`` along a spectrum line (low = warm/red end, high = violet
end) so the body's colour tracks where the mood sits — but the mood stays
the *fact*; the colour is only presentation, the same way the glyph is.

**Two face forms, mixed by judgment (the maintainer's call).**

- **Name-weave** (``b r n r d``): the whole wordmark *is* the face. Read
  the letters — ``b`` and ``d`` are the cheeks (the fixed frame), the two
  ``r``'s are the **eyes**, and the ``n`` is the **MOUTH**. Neutral resting
  is the plain ``brnrd``; a mood animates the expression from it — the n
  morphs into a mouth shape and the r's shift with it. The maintainer's
  default is **smug/amused**: the n curls forward and upward into an anime
  smirk (``brnrd`` → ``brᵕrd`` → ``b¬ᵕ¬d``). Telemetry leans name-weave, so
  the brand reads sharpest where the daemon speaks for the body.
- **Cheek form** (``b{eyes}d``): a two-eye kaomoji core (eye · mouth · eye)
  wrapped in the ``b…d`` cheeks — ``bo_·d`` (puzzled), ``b>_<d`` (strained).
  Situational faces lean here, where a full two-eye read carries shades a
  single woven glyph can't.

Frame rules (so the mark never jitters): all frames of one emote — primary
and every alternate, plus its resting frame — are exactly equal display
width — count wide/combining glyphs honestly, so the palette stays narrow-
glyph mono (no fullwidth ``￣``/``ω``/``ー`` smuggling in a double-width
cell) — base state first and last, ≤ 12 chars wide. A resident with a
twitching face reads as a resident that isn't well.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Emote",
    "EMOTES",
    "VOCABULARY",
    "LEGACY_ALIASES",
    "REST_GLYPH",
    "TELEMETRY_DEFAULTS",
    "TELEMETRY_STATES",
    "lookup",
    "resolve_nearest",
    "near_misses",
    "glyph",
    "for_telemetry",
    "sequences_of",
    "search",
    "families",
    "nearest",
]

#: The glyph reserved for "no face" — no vocabulary word's still may equal it.
REST_GLYPH = "b·_·d"


@dataclass(frozen=True)
class Emote:
    """One wearable face.

    ``name``    — the plain word; the string a resident writes into
                  ``.mood`` (also the ``EMOTES`` key).
    ``kind``    — ``"telemetry"`` (daemon-derived, cannot lie) or
                  ``"situational"`` (resident-authored).
    ``trigger`` — one line naming the state under which this face is true.
    ``frames``  — fixed-width glyph strings, base → expression → base.
    ``pitch``   — body-axis location in ``[0.0, 1.0]``: 0.0 gut/low,
                  1.0 crown/high, ~0.5 the settled working band. A felt
                  location, not a rating; the dashboard may map it to hue.
    ``family``  — for a situational face this equals ``name``: the twelve
                  words are their own family now. Kept as a field (rather
                  than derived) because callers already read ``e.family``.
    """

    name: str
    kind: str
    trigger: str
    frames: tuple[str, ...]
    pitch: float = 0.5
    alts: tuple[tuple[str, ...], ...] = ()
    rest: str | None = None
    family: str = ""

    @property
    def resting_frame(self) -> str:
        """The face to hold while nothing is moving.

        ``frames[0]`` is the *animation's* base and is shared on purpose —
        every name-weave face opens on the plain wordmark, every cheek face
        on neutral eyes — so it says "an emote is playing here" and nothing
        about *which*. A surface that rests (the dashboard's mood chip:
        calm ~5s, flicker ~1s) needs a frame that still carries identity
        while still, which is what ``rest`` is: a smug run should look
        smug between breaths, not neutral.

        Unset ⇒ ``frames[0]``. Every vocabulary word below sets ``rest``
        explicitly — that is the entire point of the rework — so this
        fallback exists only for telemetry, which rests on the plain mark
        by design.
        """
        return self.rest or self.frames[0]

    @property
    def sequences(self) -> tuple[tuple[str, ...], ...]:
        """Every breath this face can take, primary first.

        A face with one cycle reads mechanical — the same three frames
        forever is a loading spinner wearing an expression. Alternates let
        one mood breathe two or three ways, picked per cycle by whoever
        renders it. Every sequence obeys the same frame rules as
        ``frames``; ``tests/test_emotes.py`` checks them all, not just the
        primary.
        """
        return (self.frames, *self.alts)


# The daemon-derived states a resident body must be able to speak. Every
# one of these is mapped in ``TELEMETRY_DEFAULTS``; the daemon computes the
# state from run/portal facts and renders the mapped face.
TELEMETRY_STATES: tuple[str, ...] = (
    "idle",
    "running",
    "quota_starved",
    "blocked_on_user",
    "delivering",
    "spawning",
    "reviewing",
    "testing",
    "failing",
    "merging",
    "waiting_deploy",
    "stopped",
)


def _build(rows: tuple[Emote, ...]) -> dict[str, Emote]:
    """Key emotes by name, refusing duplicates.

    A collapsed duplicate would silently drop a face and, worse, make the
    handle ambiguous — the one thing a shared-comprehension channel cannot
    afford. Surface it at import instead.
    """

    out: dict[str, Emote] = {}
    for e in rows:
        if e.name in out:
            raise ValueError(f"duplicate emote name: {e.name!r}")
        out[e.name] = e
    return out


def _e(
    name: str,
    kind: str,
    trigger: str,
    *frames: str,
    pitch: float = 0.5,
    alts: tuple[tuple[str, ...], ...] = (),
    rest: str | None = None,
    family: str = "",
) -> Emote:
    return Emote(
        name=name, kind=kind, trigger=trigger,
        frames=tuple(frames), pitch=pitch, alts=alts, rest=rest,
        family=family,
    )


# ── Telemetry set — the daemon's own body ────────────────────────────
#
# These wear the wordmark and the mark's own vitals. The daemon picks
# them; the resident does not get a vote, which is the point. Untouched
# by the 2026-09-11 rework — that one is about the situational vocabulary
# only.

_TELEMETRY: tuple[Emote, ...] = (
    # Name-weave form: the whole wordmark is the face. b/d are the cheeks
    # (fixed frame), the two r's are the eyes, and the n is the MOUTH —
    # neutral resting is the plain ``brnrd``; each state animates the
    # expression from it. Telemetry leans name-weave so the brand reads
    # sharpest exactly where the daemon speaks for the body.
    _e("id_l", "telemetry",
       "awake, nothing queued — the mark just breathing",
       "brnrd", "b-n-d", "brnrd", pitch=0.5),
    _e("rnn>", "telemetry",
       "a run is live — the worker is turning",
       "brnrd", "brvrd", "br^rd", "brvrd", "brnrd", pitch=0.55),
    _e("dry_q", "telemetry",
       "quota near empty — rationing tokens to the finish",
       "brnrd", "b·n·d", "b n d", "b·n·d", "brnrd", pitch=0.2),
    _e("u_hey", "telemetry",
       "blocked on you — can't proceed without a human call",
       "brnrd", "b·o·d", "b°o°d", "b·o·d", "brnrd", pitch=0.55),
    _e("shp>>", "telemetry",
       "delivering — pushing the result out the door",
       "brnrd", "brᵕrd", "b^ᵕ^d", "brᵕrd", "brnrd", pitch=0.6),
    _e("sp_wn", "telemetry",
       "spawning a strand — a new thought forking off",
       "brnrd", "bonod", "bOnOd", "bonod", "brnrd", pitch=0.6),
    _e("re.v", "telemetry",
       "reviewing a diff — reading it line by line before a verdict",
       "brnrd", "bo-·d", "b·-od", "brnrd", pitch=0.45),
    _e("t_st", "telemetry",
       "tests running — watching for the first red",
       "brnrd", "br.rd", "br_rd", "br.rd", "brnrd", pitch=0.5),
    _e("x_x", "telemetry",
       "failing — a run ended without delivery, something broke",
       "brnrd", "bxnxd", "bx_xd", "bxnxd", "brnrd", pitch=0.15),
    _e("mrg>", "telemetry",
       "merging — landing the branch, fast-forward or bust",
       "brnrd", "b>n<d", "b>=<d", "b>n<d", "brnrd", pitch=0.5),
    _e("dpl~", "telemetry",
       "waiting on deploy — watching a bar that's green until it isn't",
       "brnrd", "b~n~d", "b~-~d", "b~n~d", "brnrd", pitch=0.4),
    _e("st_p", "telemetry",
       "stopped — parent issued stop, hands off, run ended",
       "brnrd", "b·n·d", "b·_·d", "b·n·d", "brnrd", pitch=0.2),
    # Extra daemon states beyond the required floor — still real, still
    # mapped; the resident body has more than twelve honest weathers.
    _e("wak_", "telemetry",
       "waking — cold start, the mark blinking on",
       "brnrd", "b-n-d", "bonod", "brnrd", pitch=0.55),
    _e("slp_", "telemetry",
       "sleeping — no wake scheduled, dormant between runs",
       "brnrd", "b=n=d", "b=u=d", "b=n=d", "brnrd", pitch=0.2),
    _e("cnfl", "telemetry",
       "conflict — the branch moved under the diff, needs a rebase",
       "brnrd", "b>n<d", "b>~<d", "b>n<d", "brnrd", pitch=0.3),
)


# ── Situational vocabulary — the resident's own twelve words ──────────
#
# Each word is a face with its own distinct still (``rest``), never equal
# to ``REST_GLYPH``. ``frames`` is the primary breath; ``alts`` gathers a
# second (and sometimes third) breath pulled from the faces this word now
# stands in for — see ``LEGACY_ALIASES`` below for the full accounting.
# Order here is ``VOCABULARY``'s order: the twelve as a resident holds them.

VOCABULARY: tuple[str, ...] = (
    "focused", "stuck", "pleased", "worried", "curious", "tired",
    "amused", "braced", "proud", "sorry", "flat", "waiting",
)

_SITUATIONAL: tuple[Emote, ...] = (
    _e("focused", "situational",
       "deep in the one thing that matters, and staying there",
       "b·_·d", "b-_-d", "b·_·d", pitch=0.48,
       alts=(("b·_·d", "b·w·d", "b·_·d"), ("b·_·d", "bˋ_ˊd", "b·_·d")),
       rest="b·w·d", family="focused"),
    _e("stuck", "situational",
       "the same error after the fix that should have fixed it, or two "
       "facts that can't both be true",
       "b·_·d", "b-_-d", "b=_=d", "b-_-d", "b·_·d", pitch=0.35,
       alts=(("b·_·d", "bo_·d", "b·_od", "b·_·d"),
             ("b°_°d", "b·_·d", "b°_°d")),
       rest="b=_=d", family="stuck"),
    _e("pleased", "situational",
       "the diff is clean, the board's clear, nothing left pending",
       "b·ᴗ·d", "b·‿·d", "b·ᴗ·d", pitch=0.58,
       alts=(("b-_-d", "b^_^d", "b-_-d"), ("b·u·d", "b-u-d", "b·u·d")),
       rest="b·ᴗ·d", family="pleased"),
    _e("worried", "situational",
       "the fix is too easy for the size of the bug, or the function is "
       "named simple_ and it's 400 lines",
       "b·_·d", "b°_°d", "b·_·d", pitch=0.28,
       alts=(("b·_·d", "b°_°d", "bO_Od", "b°_°d", "b·_·d"),
             ("b·_·d", "b¬^¬d", "b·_·d")),
       rest="b°_°d", family="worried"),
    _e("curious", "situational",
       "an import three modules deep just to see, or an answer that "
       "arrived before the question finished",
       "b·o·d", "b·O·d", "b·o·d", pitch=0.72,
       alts=(("b·_·d", "b°o°d", "b°O°d", "b°o°d", "b·_·d"),
             ("b·_·d", "b·o·d", "b·O·d", "b·o·d", "b·_·d")),
       rest="b·o·d", family="curious"),
    _e("tired", "situational",
       "third rebase onto a branch that keeps moving, context full, "
       "still three threads open",
       "b=_=d", "b-_-d", "b=_=d", pitch=0.2,
       alts=(("b@_@d", "bx_xd", "b@_@d"),
             ("b-_-d", "b=_=d", "b-.-d", "b-_-d")),
       rest="b@_@d", family="tired"),
    _e("amused", "situational",
       "you called the bug before opening the file, or deleting "
       "commented-out code with no mercy at all",
       "brnrd", "brᵕrd", "b¬ᵕ¬d", "brᵕrd", "brnrd", pitch=0.6,
       alts=(("b·_·d", "b·ᵕ·d", "b·_·d"), ("b·_·d", "b¬ᴗ¬d", "b·_·d")),
       rest="brᵕrd", family="amused"),
    _e("braced", "situational",
       "forty failing tests and one root cause somewhere, or the linter "
       "reformatting the line you just formatted",
       "b¬_¬d", "b>_<d", "b¬_¬d", pitch=0.22,
       alts=(("b·_·d", "bx~xd", "b·_·d"), ("b¬_¬d", "b¬~¬d", "b¬_¬d")),
       rest="b¬_¬d", family="braced"),
    _e("proud", "situational",
       "the failing test goes green, or the race condition you flagged "
       "in review, now confirmed",
       "b·_·d", "b^o^d", "b^‿^d", "b^o^d", "b·_·d", pitch=0.78,
       alts=(("b·_·d", "b>‿<d", "b·_·d"),
             ("brnrd", "b¬n¬d", "b¬w¬d", "b¬n¬d", "brnrd")),
       rest="b^‿^d", family="proud"),
    _e("sorry", "situational",
       "the bug was your own typo two commits ago, or the 'obvious' fix "
       "broke four other things",
       "b·_;d", "b-_;d", "b·_;d", pitch=0.32,
       alts=(("b·_·d", "b-_;d", "b·_·d"),),
       rest="b·_;d", family="sorry"),
    _e("flat", "situational",
       "the bug was environmental — nothing to fix, nothing learned",
       "b·_·d", "b-_-d", "b·_·d", pitch=0.3,
       rest="b-_-d", family="flat"),
    _e("waiting", "situational",
       "watching a deploy bar that's always green, or a build that "
       "never seems to finish",
       "b·_·d", "b·-·d", "b·_·d", pitch=0.35,
       alts=(("b-_-d", "b-o-d", "b-_-d"),),
       rest="b·-·d", family="waiting"),
)


EMOTES: dict[str, Emote] = _build(_TELEMETRY + _SITUATIONAL)

for _v in VOCABULARY:
    assert _v in EMOTES, _v
    _still = EMOTES[_v].resting_frame
    assert _still != REST_GLYPH, (_v, _still)
del _v, _still


# The old 113-handle palette, collapsed. Every key below is a handle a
# resident might still have written (some still-live `.mood` files carry
# one) or a family word from before the rework; every value is the
# vocabulary word it now speaks through. `lookup` resolves a legacy key to
# the *vocabulary's* Emote — the old handle is a frame of that word now,
# never a face of its own. Built once, by hand, from the pre-rework
# `_SITUATIONAL` table's 98 entries and their `family` field; the mapping
# is a judgement call (documented in `docs/portals.md`'s companion PR),
# not a formula — a few near neighbours (`puzzled` → `stuck`, `smug` →
# `amused`) read differently than their old family name suggests.
LEGACY_ALIASES: dict[str, str] = {
    # -> "focused" (was: focused, determined, calm, greedy)
    "again": "focused", "flow_": "focused", "fo.cus": "focused",
    "greed_": "focused", "grip_": "focused", "jaw_": "focused",
    "lock_": "focused", "narrow": "focused", "primed": "focused",
    "squint": "focused", "zen_": "focused",
    # -> "stuck" (was: stuck, second-guessing, puzzled)
    "doubt_": "stuck", "eh_?": "stuck", "er_r": "stuck", "hm_m": "stuck",
    "huh_": "stuck", "q_q?": "stuck", "redo_": "stuck", "stuck_": "stuck",
    "wait2": "stuck", "wall_": "stuck", "wat_": "stuck",
    # -> "pleased" (was: content, satisfied, relieved, grateful)
    "ahh_": "pleased", "clean_": "pleased", "content": "pleased",
    "exhal": "pleased", "fine_": "pleased", "mm_m": "pleased",
    "nnice": "pleased", "phew_": "pleased", "safe_": "pleased",
    "warm_": "pleased",
    # -> "worried" (was: wary, dread, spooked, suspicious, betrayed)
    "brace2": "worried", "brace_": "worried", "by200": "worried",
    "cold_": "worried", "creak": "worried", "fishy_": "worried",
    "hmwait": "worried", "nervy": "worried", "rug_": "worried",
    "side_": "worried", "spook": "worried", "squin2": "worried",
    "uhoh_": "worried", "wary_": "worried",
    # -> "curious" (was: curious, uncanny, surprise)
    "bo_Od": "curious", "gasp_": "curious", "hmn_": "curious",
    "hz_": "curious", "itch_": "curious", "jolt_": "curious",
    "o_O!": "curious", "ooh_": "curious", "peek_": "curious",
    "wha_": "curious",
    # -> "tired" (was: weary, wincing)
    "drry": "tired", "fried": "tired", "sigh_": "tired",
    "weary_": "tired", "wince": "tired",
    # -> "amused" (was: amused, smug, gleeful)
    "glee_": "amused", "grin_": "amused", "heh_": "amused",
    "knew_": "amused", "lol_": "amused", "petty_": "amused",
    "pff_h": "amused", "smug_": "amused", "snrk": "amused",
    "told_": "amused",
    # -> "braced" (was: overwhelmed, annoyed, grumpy)
    "aaah_": "braced", "glare": "braced", "grr_": "braced",
    "hmph_": "braced", "mutter": "braced", "pfft": "braced",
    "rrgh": "braced", "swamp_": "braced", "tsk_": "braced",
    "ugh_": "braced",
    # -> "proud" (was: triumphant, vindicated, delighted)
    "calld": "proud", "clear!": "proud", "pep_": "proud",
    "proud_": "proud", "sprkl": "proud", "t.da": "proud",
    "yay_": "proud", "yesss": "proud",
    # -> "sorry" (was: sheepish, humbled)
    "cring": "sorry", "humbl": "sorry", "myb_": "sorry",
    "oops_": "sorry", "welp_": "sorry",
    # -> "flat" (was: one member of weary, pulled out on purpose — an
    # affect-flatness, not a fatigue, is a different state than "tired")
    "flat_": "flat",
    # -> "waiting" (was: bored — waiting on CI/a deploy already *was* what
    # every trigger in this family described)
    "meh_": "waiting", "tap_": "waiting", "yawn_": "waiting",
}

for _old, _new in LEGACY_ALIASES.items():
    assert _new in EMOTES, (_old, _new)
del _old, _new


# Search vocabulary for the twelve words: the old family names each word
# absorbed, plus their old synonym lists (`FAMILY_SYNONYMS`, pre-rework).
# `lookup`/`near_misses`/`search` all reach through this so "confused" (an
# old `puzzled` synonym) still finds `stuck`, and "smirking" (an old
# `smug` synonym) still finds `amused`.
WORD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "focused": (
        "attentive", "calm", "committed", "composed", "concentrating",
        "covetous", "determined", "driven", "eager", "grasping", "greedy",
        "hopeful", "hungry", "intent", "patient", "peaceful", "persistent",
        "precise", "resolute", "steady", "surgical", "unhurried", "wanting",
    ),
    "stuck": (
        "baffled", "blocked", "confused", "doubtful", "hesitant",
        "mystified", "perplexed", "puzzled", "reconsidering",
        "second-guessing", "stalled", "stranded", "trapped", "uncertain",
        "unsure", "wavering", "wedged",
    ),
    "pleased": (
        "appreciative", "clean", "comfortable", "complete", "content",
        "done", "easy", "fulfilled", "grateful", "gratified", "indebted",
        "obliged", "okay", "reassured", "released", "relieved", "safe",
        "satisfied", "settled", "successful", "thankful", "touched",
        "unburdened", "unworried",
    ),
    "worried": (
        "abandoned", "afraid", "alarmed", "anxious", "backstabbed",
        "betrayed", "careful", "cautious", "deceived", "distrustful",
        "doomed", "dread", "dubious", "fearful", "foreboding", "guarded",
        "haunted", "jumpy", "leery", "let-down", "misled", "questioning",
        "rattled", "skeptical", "spooked", "startled", "suspicious",
        "unconvinced", "wary", "watchful",
    ),
    "curious": (
        "amazed", "astonished", "eerie", "exploring", "inquisitive",
        "interested", "observant", "otherworldly", "shocked", "strange",
        "surprise", "surprised", "uncanny", "unexpected", "unsettling",
        "weird", "wondering",
    ),
    "tired": (
        "aching", "cringing", "drained", "exhausted", "fatigued",
        "flinching", "pained", "sore", "spent", "weary", "wincing",
    ),
    "amused": (
        "buoyant", "chuckling", "cocky", "ecstatic", "entertained",
        "exuberant", "funny", "giddy", "gleeful", "knowing", "playful",
        "self-satisfied", "smirking", "smug", "superior", "thrilled", "wry",
    ),
    "braced": (
        "angry", "annoyed", "blunt", "buried", "cranky", "cross",
        "flooded", "frustrated", "grouchy", "grumpy", "irritated",
        "overloaded", "overwhelmed", "snowed-under", "sour", "swamped",
        "testy", "vexed",
    ),
    "proud": (
        "accomplished", "cheerful", "confirmed", "conquering", "delighted",
        "excited", "happy", "joyful", "justified", "proven", "right",
        "triumphant", "validated", "victorious", "vindicated", "winning",
    ),
    "sorry": (
        "abashed", "awkward", "bashful", "chastened", "corrected",
        "embarrassed", "grounded", "guilty", "humbled", "modest",
        "sheepish", "sobered",
    ),
    "flat": ("blank", "neutral", "nothing-to-fix", "numb"),
    "waiting": ("bored", "dull", "idle", "listless", "understimulated",
                "uninterested"),
}


def _norm(text: str) -> str:
    """Strip a handle to its letters — ``fo.cus`` and ``focus`` are one word.

    Every comparison in this module runs on the stripped form so that a
    handle's punctuation (register, not syntax) stays out of the parser.
    """

    return "".join(c for c in text.lower() if c.isalnum())


#: Furthest edit distance at which `near_misses` still names a candidate.
#: `lookup` resolves at ≤ 2 on its own; 3 is "one more slip than that".
NEAR_MISS_MAX_DISTANCE = 3


def _distance(left: str, right: str) -> int:
    """Small Levenshtein distance helper for the resident-sized index."""
    if len(left) > len(right):
        left, right = right, left
    previous = list(range(len(left) + 1))
    for row, rchar in enumerate(right, 1):
        current = [row]
        for col, lchar in enumerate(left, 1):
            current.append(min(current[-1] + 1, previous[col] + 1,
                               previous[col - 1] + (lchar != rchar)))
        previous = current
    return previous[-1]


_LEGACY_NORM: dict[str, str] = {_norm(k): v for k, v in LEGACY_ALIASES.items()}


def _word_tokens() -> dict[str, str]:
    """Every normalised token that resolves to a vocabulary word: the word
    itself, its synonyms, and the old handles/family names it absorbed."""
    words: dict[str, str] = {}
    for word in VOCABULARY:
        words[_norm(word)] = word
        for synonym in WORD_SYNONYMS.get(word, ()):
            words.setdefault(_norm(synonym), word)
    for token, word in _LEGACY_NORM.items():
        words.setdefault(token, word)
    return words


def lookup(name: str) -> Emote | None:
    """Resolve an exact word, a legacy handle, a synonym, or a small typo.

    Resolution order: exact ``EMOTES`` key (the twelve words plus every
    telemetry handle) · a legacy handle from the pre-rework palette ·
    a synonym or old family word · a typo within two edits of any of the
    above. Completely unknown words remain unresolved here; the CLI's
    non-strict path (where one still exists) opts into
    :func:`resolve_nearest` explicitly.
    """

    exact = EMOTES.get(name)
    if exact is not None:
        return exact

    needle = _norm(name)
    if not needle:
        return None

    same = [e for e in EMOTES.values() if _norm(e.name) == needle]
    if len(same) == 1:
        return same[0]
    if same:
        return None

    legacy = _LEGACY_NORM.get(needle)
    if legacy:
        return EMOTES[legacy]

    tokens = _word_tokens()
    word = tokens.get(needle)
    if word:
        return EMOTES[word]

    candidates: list[tuple[int, str, str]] = []
    for token, word in tokens.items():
        distance = _distance(needle, token)
        if distance <= 2:
            candidates.append((distance, token, word))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    best_distance = candidates[0][0]
    best = {row[2] for row in candidates if row[0] == best_distance}
    return EMOTES[next(iter(best))] if len(best) == 1 else None


def resolve_nearest(name: str) -> Emote | None:
    """Resolve *name*, falling back to the lexically nearest vocabulary word.

    Kept for callers that explicitly want a face rather than nothing (e.g.
    a non-strict ``brnrd mood``); the write path that publishes to the
    dashboard (`brnrd do --mood`) does **not** use this — it refuses an
    unknown word instead of guessing which of the twelve was meant.
    """
    resolved = lookup(name)
    if resolved is not None:
        return resolved
    needle = _norm(name)
    if not needle:
        return None
    ranked: list[tuple[int, str, str]] = []
    for token, word in _word_tokens().items():
        ranked.append((_distance(needle, token), token, word))
    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1], row[2]))
    return EMOTES[ranked[0][2]]


def near_misses(name: str, *, limit: int = 4) -> list[Emote]:
    """Vocabulary words a failed ``lookup(name)`` was probably reaching for.

    The point is the *silence*, not the miss. An unresolvable handle used
    to publish four ``null``s and say nothing to anyone. Returns ``[]``
    when the word resolves; otherwise a ranked shortlist over the twelve
    words, their synonyms, and the legacy handles they absorbed.
    """

    if lookup(name) is not None:
        return []
    needle = _norm(name)
    if not needle:
        return []
    ranked: list[tuple[int, str, str]] = []
    for token, word in _word_tokens().items():
        distance = _distance(needle, token)
        if distance <= NEAR_MISS_MAX_DISTANCE:
            ranked.append((distance, token, word))
    hits = [e for e in search(name, limit=limit) if e.kind == "situational"]
    if hits:
        return hits
    ranked.sort(key=lambda row: (row[0], row[1], row[2]))
    unique: list[Emote] = []
    for _score, _token, word in ranked:
        emote = EMOTES[word]
        if emote not in unique:
            unique.append(emote)
        if len(unique) == limit:
            break
    return unique


def families() -> tuple[str, ...]:
    """The situational vocabulary — the honest answer to a miss.

    Pre-rework this returned 33 family words behind 113 handles; now it is
    the twelve words themselves, since a situational face's ``family`` is
    its ``name``. Kept as its own function (rather than pointing callers
    at ``VOCABULARY`` directly) because it is the documented way in:
    ``search`` has no thesaurus, and this is what a miss falls back to.
    """
    return tuple(sorted(VOCABULARY))


def nearest(query: str, *, limit: int = 4) -> list[Emote]:
    """Faces a *typo* was reaching for — the other half of a miss.

    Distinct from :func:`near_misses`, which also considers a plain
    absence via :func:`search`. This one runs a pure edit-distance pass
    over the twelve words, their synonyms, and the legacy handles/family
    names they absorbed — so ``focussed`` finds ``focused`` and ``smugg``
    finds ``amused``.
    """
    import difflib

    candidates = _word_tokens()
    out: list[Emote] = []
    for token in [_norm(query), *(_norm(w) for w in query.split())]:
        if not token:
            continue
        for match in difflib.get_close_matches(
            token, list(candidates), n=limit, cutoff=0.72,
        ):
            emote = EMOTES[candidates[match]]
            if emote not in out:
                out.append(emote)
    return out[:limit]


def glyph(name: str) -> str | None:
    """Resting-frame glyph for *name*, or ``None`` if the handle is unknown.

    The rendering path, and the seam this module owes its one non-resident
    caller: ``hooks._emote_glyph`` calls exactly this, to prefix the
    statusline's mood chip with the face the resident is wearing. It
    resolves through :func:`lookup` for the same reason ``sequences_of``
    does — one function decides what a handle means, and every reader
    goes through it.

    Returns :attr:`Emote.resting_frame`, not ``frames[0]``. Before the
    2026-09-11 rework these were the same thing to ask; after it they
    are not — ``frames[0]`` is the shared *animation base* (identical
    across most of the vocabulary on purpose, see :attr:`Emote.resting_frame`'s
    own docstring), and a still surface asking this function "which face
    is this" wants the word's own resting look, not the base every
    animation opens on. Every non-resident-facing still (the bar
    preamble, the CLI mood echoes, the dashboard's daemon-mood glyph)
    reads through here, so fixing it here fixes all of them at once — the
    six words whose ``frames[0]`` was ``REST_GLYPH`` used to render as no
    face at all on every one of these surfaces before this returned
    ``resting_frame``. Callers that explicitly want the animation base
    (playing the sequence, not resting on it) read ``.frames[0]`` off the
    ``Emote`` directly, e.g. via :func:`sequences_of`.
    """

    emote = lookup(name)
    return emote.resting_frame if emote else None


def for_telemetry(state: str) -> Emote | None:
    """Return the daemon-derived face for *state*, or ``None`` if unmapped.

    The daemon's path: it computes a state name and asks for the body that
    speaks it. Unmapped states resolve to ``None`` so a caller renders
    nothing rather than inventing a mood.
    """

    name = TELEMETRY_DEFAULTS.get(state)
    if name is None:
        return None
    return EMOTES.get(name)


def sequences_of(name: str) -> tuple[tuple[str, ...], ...] | None:
    """Every breath the face *name* can take, or ``None`` for an unknown handle.

    The publish path's counterpart to :func:`glyph`. ``glyph`` answers
    "which frame is the resting one" for a surface that can only hold
    still; this answers "what does this face *do*" for one that can move.
    Resolves through :func:`lookup`, like ``glyph``.
    """

    emote = lookup(name)
    return None if emote is None else emote.sequences


def search(query: str = "", *, limit: int = 12) -> list[Emote]:
    """Faces matching *query*, best first — the resident's way in.

    Matching is deliberately forgiving, because a resident searches with
    the *word for the feeling*: separators are stripped from both sides
    before comparing, the old family/synonym vocabulary still routes to
    its new word (``WORD_SYNONYMS``), and the trigger line (a *sentence
    about the state*) is searched too.

    An empty query returns the situational set, which is the resident's
    half; telemetry faces are the daemon's and it does not take requests.
    """

    needle = _norm(query)
    if not needle:
        return [e for e in EMOTES.values() if e.kind == "situational"][:limit]

    tokens = _word_tokens()
    query_word = tokens.get(needle)

    scored: list[tuple[int, int, Emote]] = []
    for e in EMOTES.values():
        name = _norm(e.name)
        family = _norm(e.family)
        trigger = _norm(e.trigger)
        if name == needle:
            rank = 0
        elif name.startswith(needle) or needle.startswith(name):
            rank = 1
        elif query_word and e.family == query_word:
            rank = 2
        elif family and (
            family == needle
            or family.startswith(needle)
            or needle.startswith(family)
        ):
            rank = 2
        elif needle in name:
            rank = 3
        elif needle in trigger:
            rank = 4
        else:
            continue
        # Situational first within a rank: the caller is a resident picking
        # a face, and the telemetry set is not theirs to wear.
        scored.append((rank, 0 if e.kind == "situational" else 1, e))
    scored.sort(key=lambda row: (row[0], row[1], row[2].name))
    return [e for _r, _k, e in scored[:limit]]


# Daemon state → face. Every ``TELEMETRY_STATES`` entry is mapped; the
# daemon computes the state and renders the mapped face without asking the
# resident. Extra keys below are real states the body can also be in.
TELEMETRY_DEFAULTS: dict[str, str] = {
    "idle": "id_l",
    "running": "rnn>",
    "quota_starved": "dry_q",
    "blocked_on_user": "u_hey",
    "delivering": "shp>>",
    "spawning": "sp_wn",
    "reviewing": "re.v",
    "testing": "t_st",
    "failing": "x_x",
    "merging": "mrg>",
    "waiting_deploy": "dpl~",
    "stopped": "st_p",
    # beyond the required floor
    "waking": "wak_",
    "sleeping": "slp_",
    "conflict": "cnfl",
}
