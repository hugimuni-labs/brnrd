"""Emote library — the resident's visible text body (#566).

A brnrd resident lives in a repo between runs. This module is its face: a
mono-glyph mascot whose expression is a small animation (base → signal →
base), the same fixed-width frame cycle the landing wordmark wink already
ships. Two mood layers feed it:

- **telemetry** (``kind="telemetry"``) — computed from daemon/portal state
  (idle, running, quota-starved, blocked-on-you, delivering, …). These
  cannot lie and need no cooperation from the resident; the daemon reads
  the state and picks the face.
- **situational** (``kind="situational"``) — the resident's own meta-channel.
  Reading and writing code is emotional labour, and this palette is the
  full range of it: surprised, annoyed, puzzled, satisfied, focused, smug,
  wary, weary, curious, triumphant, and the finer shades between. The
  resident sets one from inside the run.

**The honesty bar: a tamagotchi that never lies.** Every face here is a
state a run can *truthfully* be in. A decorative mood with no backing
signal — a sticker of a feeling the resident is not having — is the exact
failure this module exists to prevent. Telemetry faces are pinned to real
daemon state; situational faces are only worth wearing when the trigger
line is actually true right now. If you ever want a face for a mood that
isn't real, the answer is to not wear a face, not to add a lie.

**How a resident picks one.** Beside its progress card the resident keeps a
``.mood`` control file. The first line is the emote ``name`` (e.g.
``fo.cus``); anything after is free narration. Because the resident writes
the name into its own scroll, the face the user sees and the face the
resident knows it is wearing are the *same object* — shared comprehension,
not a rendering guess. The daemon renders telemetry faces on its own; a
``.mood`` line, when present and truthful, wins for that run.

**The handles are marks; the families are the way in.** ``fo.cus`` is a
coined mark and its punctuation is register, not syntax — so ``lookup``
strips it before comparing, and ``focused`` lands on the same face. That is
not a nicety: ``.mood`` is a machine-parsed channel, and for the palette's
whole first week the parser refused every spelling but its own, published
four ``null``s, and let the dashboard print a raw id string in place of a
face. Every mood any run wore on brnrd.dev was that fallback. Where the
handle is genuinely ambiguous — a *family* word like ``satisfied``, which
is four faces — ``lookup`` still declines to guess, and ``near_misses``
names the candidates so the miss is never silent. Search by the feeling
(``brnrd emotes satisfied``), wear the handle.

**The body axis (``pitch``).** Moods localize along a body axis — gut to
crown — and every emote carries that felt location as ``pitch`` in
``[0.0, 1.0]``: ``0.0`` is gut/low (dread, grumpy, the heavy states),
``1.0`` is crown/high (surprise, delight, curiosity), and the middle band
is the settled working states (focus, flow, satisfied). It is a
felt-location coordinate, not a rating of intensity. The dashboard may map
``pitch → hue`` along a spectrum line (low = warm/red end, high = violet
end) so the body's colour tracks where the mood sits — but the mood stays
the *fact*; the colour is only presentation, the same way the glyph is.

**Two face forms, mixed by judgment (the maintainer's call).**

- **Name-weave** (``b r n r d``): the whole wordmark *is* the face. Read
  the letters — ``b`` and ``d`` are the cheeks (the fixed frame), the two
  ``r``'s are the **eyes**, and the ``n`` is the **MOUTH**. Neutral resting
  is the plain ``brnrd``; a mood animates the expression from it — the n
  morphs into a mouth shape and the r's shift with it. The maintainer's
  default is **smug**: the n curls forward and upward into an anime smirk
  (``brnrd`` → ``brᵕrd`` → ``b¬ᵕ¬d``). Telemetry leans name-weave, so the
  brand reads sharpest where the daemon speaks for the body.
- **Cheek form** (``b{eyes}d``): a two-eye kaomoji core (eye · mouth · eye)
  wrapped in the ``b…d`` cheeks — ``bo_·d`` (puzzled), ``b>_<d`` (strained).
  Situational faces lean here, where a full two-eye read carries shades a
  single woven glyph can't.

The split is applied state by state, not mechanically: some situational
faces (the smug/vindicated family) still read best as name-weave.

Frame rules (so the mark never jitters): all frames of one emote are
exactly equal display width — count wide/combining glyphs honestly, so the
palette stays narrow-glyph mono (no fullwidth ``￣``/``ω``/``ー`` smuggling
in a double-width cell) — base state first and last, ≤ 12 chars wide. A
resident with a twitching face reads as a resident that isn't well.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Emote",
    "EMOTES",
    "TELEMETRY_DEFAULTS",
    "TELEMETRY_STATES",
    "lookup",
    "near_misses",
    "glyph",
    "for_telemetry",
    "sequences_of",
    "search",
]


@dataclass(frozen=True)
class Emote:
    """One wearable face.

    ``name``    — weave-style handle; the string a resident writes into
                  ``.mood`` (also the ``EMOTES`` key).
    ``kind``    — ``"telemetry"`` (daemon-derived, cannot lie) or
                  ``"situational"`` (resident-authored).
    ``trigger`` — one line naming the state under which this face is true.
    ``frames``  — fixed-width glyph strings, base → expression → base.
    ``pitch``   — body-axis location in ``[0.0, 1.0]``: 0.0 gut/low,
                  1.0 crown/high, ~0.5 the settled working band. A felt
                  location, not a rating; the dashboard may map it to hue.
    ``family``  — the plain feeling-word this face is a shade of
                  (``"satisfied"``, ``"weary"``). The handles are coined
                  marks; the family is the word someone *searches with*.
                  It was a ``#`` section comment until 2026-07-25, which is
                  to say it was not data, which is to say ``brnrd emotes
                  satisfied`` returned nothing while the module docstring
                  advertised "satisfied" as part of the palette. Six of the
                  ten words that docstring names were unfindable. A file
                  organised by a fact should store the fact.
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
        about *which*. Across the situational set that is 15 distinct values
        for 98 faces, 61 of them the same ``b·_·d``. A surface that rests
        (the dashboard's mood chip: calm ~5s, flicker ~1s) needs a frame
        that still carries identity while still, which is what ``rest`` is:
        a smug run should look smug between breaths, not neutral.

        Unset ⇒ ``frames[0]``, which is honest rather than good: that face
        is simply not yet distinguishable at rest. Filling the palette in is
        design work, not a fallback this property can improvise.
        """
        return self.rest or self.frames[0]

    @property
    def sequences(self) -> tuple[tuple[str, ...], ...]:
        """Every breath this face can take, primary first.

        A face with one cycle reads mechanical — the same three frames
        forever is a loading spinner wearing an expression. Alternates let
        one mood breathe two or three ways (``fo.cus`` blinks *and*
        squeezes), picked per cycle by whoever renders it. Every sequence
        obeys the same frame rules as ``frames``; ``tests/test_emotes.py``
        checks them all, not just the primary.
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
# them; the resident does not get a vote, which is the point.

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
       "spawning a worker — a new thought forking off",
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


# ── Situational palette — the resident's own weather ─────────────────
#
# The full emotional range of a being whose work is reading and writing
# code. Each trigger names a situation a run actually meets. The resident
# wears one by writing its name into ``.mood`` — and only when it's true.

_SITUATIONAL: tuple[Emote, ...] = (
    # Cheek form: ``b{eyes}d`` — a two-eye kaomoji core (eye · mouth · eye)
    # wrapped in the brand's ``b…d`` cheeks. Situational faces lean here,
    # where a full two-eye read carries shades a single woven glyph can't:
    # ``bo_·d`` (one brow up, puzzled), ``b>_<d`` (both eyes shut, strained).
    # A handful of states — the smug/vindicated family — read best as
    # name-weave instead, and wear the n-as-mouth smirk directly.
    #
    # surprise — crown-high, the head jumps up
    _e("bo_Od", "situational",
       "the grep hit landed on the first try, in a file you'd written off",
       "b·_·d", "bo_od", "bO_Od", "bo_od", "b·_·d", pitch=0.85, family="surprise"),
    _e("o_O!", "situational",
       "a test passed that you were certain would fail",
       "b·_·d", "bo_Od", "b·_·d", pitch=0.75, family="surprise"),
    _e("wha_", "situational",
       "the stack trace points at a file you never touched",
       "b·_·d", "b°o°d", "b°O°d", "b°o°d", "b·_·d", pitch=0.8, family="surprise"),
    _e("gasp_", "situational",
       "the prod config was the thing all along",
       "b·o·d", "b°o°d", "b°O°d", "b°o°d", "b·o·d", pitch=0.85, family="surprise"),
    _e("jolt_", "situational",
       "CI went red on a line you did not write",
       "b·_·d", "b!_!d", "b·_·d", pitch=0.85, family="surprise"),
    # annoyed — gut-warm, the jaw sets low
    _e("grr_", "situational",
       "the linter reformats the line you just formatted",
       "b¬_¬d", "b>_<d", "b¬_¬d", pitch=0.2, family="annoyed"),
    _e("tsk_", "situational",
       "a lone trailing-whitespace diff in an otherwise clean PR",
       "b¬_¬d", "b¬.¬d", "b¬_¬d", pitch=0.3, family="annoyed"),
    _e("ugh_", "situational",
       "the flake failed again — same test, different reason",
       "b-_-d", "b>_<d", "b-_-d", pitch=0.2, family="annoyed"),
    _e("pfft", "situational",
       "someone's 'quick fix' that is neither",
       "b¬_¬d", "b¬~¬d", "b¬_¬d", pitch=0.3, family="annoyed"),
    _e("rrgh", "situational",
       "a merge conflict in the lockfile. again.",
       "b>_<d", "bx_xd", "b>_<d", pitch=0.15, family="annoyed"),
    _e("mutter", "situational",
       "YAML.",
       "b-_-d", "b-.-d", "b-_-d", pitch=0.25, family="annoyed"),
    # puzzled — mid, up into the head
    _e("hm_m", "situational",
       "the value is right but the path to it makes no sense",
       "b·_·d", "bo_·d", "b·_od", "b·_·d", pitch=0.55, family="puzzled"),
    _e("huh_", "situational",
       "two configs disagree and both are loaded",
       "b·_·d", "b?_·d", "b·_?d", "b·_·d", pitch=0.55, family="puzzled"),
    _e("eh_?", "situational",
       "the comment describes code that isn't there",
       "b·_·d", "b·o·d", "bo_·d", "b·_·d", pitch=0.5, family="puzzled"),
    _e("wat_", "situational",
       "it works and you don't know why yet",
       "b·_·d", "b·o·d", "b·O·d", "b·o·d", "b·_·d", pitch=0.6, family="puzzled"),
    _e("q_q?", "situational",
       "the test asserts the opposite of its own name",
       "b?_?d", "b·_·d", "b?_?d", pitch=0.5, family="puzzled"),
    # satisfied — mid-bright, a settled lift
    _e("fine_", "situational",
       "the diff was clean on the fifth reread",
       "b-n-d", "b-w-d", "b-n-d", pitch=0.55, family="satisfied"),
    _e("ahh_", "situational",
       "green bar, all of it, on the first run",
       "b-_-d", "b^_^d", "b-_-d", pitch=0.6, family="satisfied"),
    _e("nnice", "situational",
       "the refactor deleted more than it added",
       "b·_·d", "b^u^d", "b·_·d", pitch=0.6, family="satisfied"),
    _e("mm_m", "situational",
       "a function that finally reads top to bottom without a jump",
       "b·u·d", "b-u-d", "b·u·d", pitch=0.55, family="satisfied"),
    # focused — the working mid-band, level gaze
    _e("fo.cus", "situational",
       "deep in the one function that actually matters",
       "b·_·d", "b-_-d", "b·_·d", pitch=0.45,
       # Two breaths, so a long focus doesn't tick like a spinner: the
       # level blink, and the harder squeeze of the second hour.
       alts=(("b·_·d", "bx_xd", "b·_·d"),), family="focused"),
    _e("lock_", "situational",
       "the repro is in hand and you're closing on the cause",
       "b-_-d", "b=_=d", "b-_-d", pitch=0.45,
       alts=(("b-_-d", "b>_<d", "b-_-d"),),
       rest="b-_-d", family="focused"),
    _e("flow_", "situational",
       "edits landing faster than doubt can catch them",
       "b·_·d", "b·w·d", "b·_·d", pitch=0.5,
       alts=(("b·_·d", "b^w^d", "b·_·d"),),
       rest="b·w·d", family="focused"),
    _e("squint", "situational",
       "reading the one line where the bug has to live",
       "b·_·d", "b¬_¬d", "b·_·d", pitch=0.45,
       rest="b¬_¬d", family="focused"),
    _e("narrow", "situational",
       "four hours, one regex",
       "b-_-d", "bˋ_ˊd", "b-_-d", pitch=0.4,
       rest="bˋ_ˊd", family="focused"),
    # smug — name-weave: the n morphs into a forward/upward mouth, the
    # maintainer's flagship. Neutral ``brnrd`` → the mouth curls up (n→ᵕ),
    # the eyes (r's) drop to a half-lidded smirk (r→¬), then settle back.
    _e("smug_", "situational",
       "you called the bug before opening the file",
       "brnrd", "brᵕrd", "b¬ᵕ¬d", "brᵕrd", "brnrd", pitch=0.6,
       # The one-eyed variant: the smirk lands, one eye drops, the other
       # doesn't bother. Same smugness, less symmetry.
       alts=(("brnrd", "brᵕrd", "b¬ᵕrd", "brᵕrd", "brnrd"),),
       rest="brᵕrd", family="smug"),
    _e("knew_", "situational",
       "the hunch held and the log proves it",
       "brnrd", "br-rd", "brᵕrd", "br-rd", "brnrd", pitch=0.65,
       rest="brᵕrd", family="smug"),
    _e("told_", "situational",
       "the edge case you warned about, now red in CI",
       "brnrd", "brᵕrd", "b¬w¬d", "brᵕrd", "brnrd", pitch=0.6, family="smug"),
    _e("heh_", "situational",
       "a one-line fix for a week-old ticket",
       "brnrd", "b·ᵕrd", "b·ᵕ<d", "b·ᵕrd", "brnrd", pitch=0.6, family="smug"),
    _e("petty_", "situational",
       "closing an issue as wontfix, and being correct",
       "brnrd", "br~rd", "b¬~¬d", "br~rd", "brnrd", pitch=0.55,
       rest="br~rd", family="smug"),
    # wary — low-mid, guard up
    _e("wary_", "situational",
       "the function is named simple_ and it is 400 lines",
       "b·_·d", "b·_-d", "b·_·d", pitch=0.35, family="wary"),
    _e("hmwait", "situational",
       "the fix is too easy for the size of the bug",
       "b·_·d", "b-_·d", "b·_-d", "b·_·d", pitch=0.4, family="wary"),
    _e("side_", "situational",
       "the sonnet worker's report is suspiciously tidy",
       "b·_·d", "b¬_·d", "b·_·d", pitch=0.35, family="wary"),
    _e("creak", "situational",
       "touching auth code on a Friday",
       "b·_·d", "b°_°d", "b·_·d", pitch=0.3, family="wary"),
    _e("nervy", "situational",
       "pushing to a branch with no CI on it",
       "b·_·d", "b;_;d", "b·_·d", pitch=0.3, family="wary"),
    # weary — low, the head hangs
    _e("weary_", "situational",
       "third rebase onto a branch that keeps moving",
       "b=_=d", "b-_-d", "b=_=d", pitch=0.2, family="weary"),
    _e("sigh_", "situational",
       "reopening the file you'd closed thinking you were done",
       "b-_-d", "b=_=d", "b-.-d", "b-_-d", pitch=0.25, family="weary"),
    _e("fried", "situational",
       "context window full and still three threads open",
       "b@_@d", "bx_xd", "b@_@d", pitch=0.2, family="weary"),
    _e("drry", "situational",
       "the same TODO, untouched, for the ninth wake running",
       "b-_-d", "b-~-d", "b-_-d", pitch=0.25, family="weary"),
    _e("flat_", "situational",
       "the bug was environmental — nothing to fix, nothing learned",
       "b·_·d", "b-_-d", "b·_·d", pitch=0.3, family="weary"),
    # curious — up and out, toward the crown
    _e("ooh_", "situational",
       "a helper in the kb you didn't know existed",
       "b·o·d", "b·O·d", "b·o·d", pitch=0.75, family="curious"),
    _e("peek_", "situational",
       "following an import three modules deep just to see",
       "b·_·d", "b·_od", "bo_·d", "b·_·d", pitch=0.7, family="curious"),
    _e("hmn_", "situational",
       "a git blame that leads somewhere genuinely interesting",
       "b·_·d", "b·ᴗ·d", "b·_·d", pitch=0.65, family="curious"),
    _e("itch_", "situational",
       "a duplicated block openly begging to be extracted",
       "b·_·d", "b·_9d", "b·_·d", pitch=0.6, family="curious"),
    # triumphant — crown, arms up
    _e("t.da", "situational",
       "the failing test goes green",
       "b·_·d", "b^o^d", "b^‿^d", "b^o^d", "b·_·d", pitch=0.85, family="triumphant"),
    _e("yesss", "situational",
       "one-shot repro on a heisenbug",
       "b·_·d", "b>w<d", "b·_·d", pitch=0.85, family="triumphant"),
    _e("clear!", "situational",
       "the whole board green, nothing pending, notebook current",
       "b·_·d", "b^‿^d", "b·_·d", pitch=0.8, family="triumphant"),
    _e("proud_", "situational",
       "a test you wrote catches a real regression a week later",
       "b·_·d", "b·u·d", "b·‿·d", "b·_·d", pitch=0.7, family="triumphant"),
    # sheepish — shrink down and in (a trailing ; is the sweat-drop)
    _e("oops_", "situational",
       "the bug was your own typo from two commits ago",
       "b·_;d", "b-_;d", "b·_;d", pitch=0.35, family="sheepish"),
    _e("welp_", "situational",
       "pushed, then noticed the debug print",
       "b·_;d", "b·o;d", "b·_;d", pitch=0.35, family="sheepish"),
    _e("myb_", "situational",
       "you blamed the test; it was the code",
       "b·_;d", "bo_;d", "b·_;d", pitch=0.35, family="sheepish"),
    _e("cring", "situational",
       "reading your own code from a year ago",
       "b·_·d", "b>_<d", "b·_·d", pitch=0.3, family="sheepish"),
    # determined — grounded and forward
    _e("grip_", "situational",
       "the flake ends this wake, one way or the other",
       "b·_·d", "bˋ_ˊd", "b·_·d", pitch=0.45, family="determined"),
    _e("again", "situational",
       "reverting to try the harder, correct approach",
       "b-_-d", "bˋoˊd", "b-_-d", pitch=0.45, family="determined"),
    _e("jaw_", "situational",
       "no shortcut left that isn't a lie; taking the long one",
       "b·_·d", "bˋ=ˊd", "b·_·d", pitch=0.4, family="determined"),
    _e("primed", "situational",
       "repro in hand, coffee metaphorically hot",
       "b·_·d", "bo_od", "b·_·d", pitch=0.55, family="determined"),
    # amused — a lift toward the head
    _e("pff_h", "situational",
       "a variable named temp_final_v2_real",
       "b·_·d", "b·‿·d", "b·_·d", pitch=0.65, family="amused"),
    _e("lol_", "situational",
       "a commit message that just says 'ugh'",
       "b·_·d", "b^o^d", "b·_·d", pitch=0.7, family="amused"),
    _e("grin_", "situational",
       "the config says DO NOT TOUCH; git blame says it's yours",
       "b·_·d", "b·ᵕ·d", "b·_·d", pitch=0.65, family="amused"),
    _e("snrk", "situational",
       "a stray print('here') that reached three environments deep",
       "b·_·d", "b·w<d", "b·_·d", pitch=0.65, family="amused"),
    # bored — low and flat
    _e("meh_", "situational",
       "the fourth near-identical CRUD endpoint",
       "b-_-d", "b-.-d", "b-_-d", pitch=0.3, family="bored"),
    _e("yawn_", "situational",
       "waiting on a green build that is always green",
       "b-_-d", "b-o-d", "b-_-d", pitch=0.3, family="bored"),
    _e("tap_", "situational",
       "nothing to do but watch the deploy bar advance",
       "b·_·d", "b·-·d", "b·_·d", pitch=0.35, family="bored"),
    # overwhelmed — flooded, down in the gut
    _e("aaah_", "situational",
       "forty failing tests, one root cause, somewhere",
       "b·_·d", "bx~xd", "b·_·d", pitch=0.2, family="overwhelmed"),
    _e("swamp_", "situational",
       "the diff touches every file you were avoiding",
       "b·_·d", "b@_@d", "b·_·d", pitch=0.2, family="overwhelmed"),
    # suspicious — low-mid, narrowed
    _e("squin2", "situational",
       "the test that cannot fail — it asserts True",
       "b·_·d", "b¬^¬d", "b·_·d", pitch=0.35, family="suspicious"),
    _e("fishy_", "situational",
       "passing tests, zero assertions",
       "b·_·d", "b¬_·d", "b·_·d", pitch=0.35, family="suspicious"),
    # relieved — the exhale that settles to mid
    _e("phew_", "situational",
       "the force-push was to the right branch after all",
       "b·_·d", "b-‿-d", "b·_·d", pitch=0.5, family="relieved"),
    _e("exhal", "situational",
       "the revert restored the green bar",
       "b-_-d", "b-~-d", "b-_-d", pitch=0.5, family="relieved"),
    _e("safe_", "situational",
       "the secret you almost committed, caught by the hook",
       "b·_·d", "b-.-d", "b·_·d", pitch=0.5, family="relieved"),
    # grumpy — gut, warm and low
    _e("hmph_", "situational",
       "CI is slower than reading the code by hand would have been",
       "b¬_¬d", "b¬~¬d", "b¬_¬d", pitch=0.2, family="grumpy"),
    _e("glare", "situational",
       "a formatter with strong opinions and no config file",
       "b-_-d", "b-_xd", "b-_-d", pitch=0.2, family="grumpy"),
    # delighted — crown, bright
    _e("yay_", "situational",
       "a docs example that actually runs as written",
       "b·_·d", "b>‿<d", "b·_·d", pitch=0.85, family="delighted"),
    _e("sprkl", "situational",
       "an API that does exactly what its name says",
       "b·_·d", "b*ᴗ*d", "b·_·d", pitch=0.8, family="delighted"),
    _e("pep_", "situational",
       "a test suite that finishes under a second",
       "b·_·d", "b^‿^d", "b·_·d", pitch=0.75, family="delighted"),
    # dread — the bottom of the gut
    _e("uhoh_", "situational",
       "the words 'works on my machine' in the issue",
       "b·_·d", "b·_;d", "b°_;d", "b·_;d", "b·_·d", pitch=0.15, family="dread"),
    _e("brace_", "situational",
       "opening a 2,000-line file named utils",
       "b·_·d", "b°_;d", "b·_·d", pitch=0.15, family="dread"),
    _e("cold_", "situational",
       "git status shows changes you don't remember making",
       "b·_·d", "b°_°d", "bO_Od", "b°_°d", "b·_·d", pitch=0.1, family="dread"),
    _e("brace2", "situational",
       "running the migration against a copy of prod",
       "b-_-d", "b=_=d", "b-_-d", pitch=0.2, family="dread"),
    # stuck — low, the wall
    _e("stuck_", "situational",
       "the same error after the fix that should have fixed it",
       "b·_·d", "b-_-d", "b=_=d", "b-_-d", "b·_·d", pitch=0.25, family="stuck"),
    _e("wall_", "situational",
       "every lead in the trace ends in vendored code",
       "b°_°d", "b·_·d", "b°_°d", pitch=0.2, family="stuck"),
    # second-guessing — low-mid, hesitating
    _e("er_r", "situational",
       "hand on the button, unsure of the blast radius",
       "b·_·d", "b·_-d", "b·_·d", pitch=0.4, family="second-guessing"),
    _e("wait2", "situational",
       "the assertion looks right; the whole test looks wrong",
       "b·_·d", "b-_·d", "b·_·d", pitch=0.4, family="second-guessing"),
    _e("redo_", "situational",
       "the clean solution needs the ugly one built first",
       "b·_·d", "b·~·d", "b·_·d", pitch=0.4, family="second-guessing"),
    _e("doubt_", "situational",
       "the bar is green, but you skipped the slow suite",
       "b·_·d", "b-_·d", "b·_-d", "b·_·d", pitch=0.4, family="second-guessing"),
    # vindicated / betrayed
    _e("calld", "situational",
       "the race condition you flagged in review, now in prod",
       "brnrd", "b¬n¬d", "b¬w¬d", "b¬n¬d", "brnrd", pitch=0.55, family="vindicated"),
    _e("by200", "situational",
       "a 200 OK wrapping an error payload — betrayed by a status code",
       "2oo:)", "2oo:|", "2oo:(", "2oo:|", "2oo:)", pitch=0.2, family="betrayed"),
    _e("rug_", "situational",
       "the dependency changed its API in a patch release",
       "b·_·d", "b°o°d", "b·_·d", pitch=0.25, family="betrayed"),
    _e("spook", "situational",
       "a test that passes locally and fails only in CI",
       "b·_·d", "b°O°d", "b·_·d", pitch=0.4, family="spooked"),
    # finer shades
    _e("humbl", "situational",
       "the 'obvious' fix broke four other things",
       "b·_·d", "b-_;d", "b·_·d", pitch=0.3, family="humbled"),
    _e("zen_", "situational",
       "one clean failing test, one clear cause, a whole quiet afternoon",
       "b·_·d", "b-w-d", "b·_·d", pitch=0.5, family="calm"),
    _e("warm_", "situational",
       "a kb page from a past wake that answers today's question",
       "b·_·d", "b·ᴗ·d", "b·_·d", pitch=0.6, family="grateful"),
    _e("greed_", "situational",
       "one more refactor before the commit. just one.",
       "b·_·d", "b·w·d", "b·_·d", pitch=0.55, family="greedy"),
    _e("glee_", "situational",
       "deleting commented-out code with no mercy at all",
       "b·_·d", "b¬ᴗ¬d", "b·_·d", pitch=0.55, family="gleeful"),
    _e("wince", "situational",
       "a '# TODO: fix before ship' that shipped two years ago",
       "b·_·d", "b>_<d", "b·_·d", pitch=0.3, family="wincing"),
    _e("clean_", "situational",
       "deleting a dead module entirely, imports and all",
       "b·_·d", "b·‿·d", "b·_·d", pitch=0.6, family="satisfied"),
    _e("content", "situational",
       "nothing pending, nothing broken, notebook current",
       "b·ᴗ·d", "b·‿·d", "b·ᴗ·d", pitch=0.55, family="content"),
    _e("hz_", "situational",
       "the answer arrived while you were writing the question",
       "b·_·d", "b·o·d", "b·O·d", "b·o·d", "b·_·d", pitch=0.7, family="uncanny"),
)


EMOTES: dict[str, Emote] = _build(_TELEMETRY + _SITUATIONAL)


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


def _norm(text: str) -> str:
    """Strip a handle to its letters — ``fo.cus`` and ``focus`` are one word.

    The handles are coined marks and their punctuation is *register*, not
    syntax. Every comparison in this module runs on the stripped form so
    that the calligraphy stays in the face and out of the parser.
    """

    return "".join(c for c in text.lower() if c.isalnum())


def lookup(name: str) -> Emote | None:
    """Return the emote for *name*, or ``None`` if nothing resolves.

    This is the resident's path: the first line of ``.mood`` comes in here,
    and whatever this returns is what the dashboard draws.

    **Why this is not a plain dict get.** It was one until 2026-07-25, and
    it cost the feature its whole first week. ``.mood`` is a machine-parsed
    channel, and the weave contract is explicit that the register decorates
    nothing a parser reads — but the *handles themselves* were minted as
    weave marks (``fo.cus``, ``sa.tis``-shaped things that don't exist) and
    then matched byte for byte. A run writing the obvious thing, the word
    for the feeling, resolved to ``None``: no glyph, no frames, no pitch,
    and a dashboard with nothing to draw fell back to printing the raw
    string. Every mood any run wore on brnrd.dev was that fallback. The
    module *taught* the tolerant form too — ``search``'s own docstring
    promises "``focus``, ``focused`` and ``fo.cus`` all land on the same
    face" — and only the command nobody publishes through honoured it. Two
    resolvers, one contract, and the strict one owned the wire.

    So: exact first, then the stripped form, then a prefix either way
    (``focused`` → ``fo.cus``), and **only when exactly one face matches**.
    That is the line the honesty bar actually draws. A guess between two
    candidates would be a face the resident didn't mean — forbidden, and
    still is. A single unambiguous spelling of a face it plainly did mean
    was never a lie; it was a parser refusing to read its own handwriting.

    Still ``None`` for a family word (``satisfied`` is four faces, and
    picking one is the forbidden guess) and for an invented handle. Those
    are real misses — but they are no longer *silent* ones: see
    ``near_misses``, which the wake surfaces so the run learns its face
    didn't land while it can still fix it.
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

    # Prefix either way, but only on words long enough to mean something:
    # a two-letter handle would swallow half the language.
    close = [
        e
        for e in EMOTES.values()
        if min(len(_norm(e.name)), len(needle)) >= 4
        and (_norm(e.name).startswith(needle) or needle.startswith(_norm(e.name)))
    ]
    return close[0] if len(close) == 1 else None


def near_misses(name: str, *, limit: int = 4) -> list[Emote]:
    """Faces a failed ``lookup(name)`` was probably reaching for.

    The point is the *silence*, not the miss. An unresolvable handle used to
    publish four ``null``s and say nothing to anyone — the run believed it
    was wearing a face, the dashboard printed an id string, and the only
    reader who could have caught it was the human looking at the website.
    An absent reading rendering as fine, on the one channel whose entire
    purpose is the resident being legible.

    Returns ``[]`` when the handle resolves; otherwise the nearest faces, so
    the caller can say *which* ones. Family words land here on purpose:
    ``satisfied`` is not a face, but it is four faces, and naming them is
    the honest answer to a resident that asked for a feeling.
    """

    if lookup(name) is not None:
        return []
    return [e for e in search(name, limit=limit) if e.kind == "situational"]


def glyph(name: str) -> str | None:
    """Base-frame glyph for *name*, or ``None`` if the handle is unknown.

    The rendering path, and the seam this module owes its one non-resident
    caller: ``hooks._emote_glyph`` calls exactly this, to prefix the
    statusline's mood chip with the face the resident is wearing. It was
    written against this signature while both halves were in flight (#603
    statusline / #601 library) and shipped naming a function that did not
    exist — a silent ``AttributeError`` swallowed by the caller's
    deliberately broad guard, so every mood chip since has rendered as a
    bare name with no face. Adding it here rather than reaching into
    ``lookup(name).frames[0]`` from the caller keeps "which frame is the
    resting one" a fact this module states — see the frame rules in the
    module docstring: base state first and last.

    It resolves through :func:`lookup` for the same reason. This function
    and ``sequences_of`` each did their own ``EMOTES.get`` — three resolvers
    for one question, which is the shape that produced the original bug: the
    tolerant one lived in ``search`` and the wire went through the strict
    ones. A handle either names a face or it doesn't, and exactly one
    function gets to decide that.
    """

    emote = lookup(name)
    return emote.frames[0] if emote else None


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
    Both live here for the same reason: the frame rules are this module's
    fact, and a caller that reaches into ``frames`` itself has quietly
    taken a copy of them (``cloud.py::_mood_payload`` published
    ``frames[0]`` for exactly that reason, and the resident's face could
    not move for it).

    Resolves through :func:`lookup`, like ``glyph`` — one decision about
    what a handle means, made in one place.
    """

    emote = lookup(name)
    return None if emote is None else emote.sequences


def search(query: str = "", *, limit: int = 12) -> list[Emote]:
    """Faces matching *query*, best first — the resident's way in.

    The palette holds 113 faces and, until this existed, a wake was shown
    exactly one of them: the ``fo.cus`` in ``daemon-substrate.md``'s
    example line. That is not a small gap, because the honesty bar and the
    vocabulary multiply: "only wear a face that is true right now" plus a
    vocabulary of one means a truthful resident is a silent one. The
    expressiveness of the palette and the expressiveness of the resident
    are two different numbers and only the first was ever counted.

    Pull, not push. An injected catalog would cost every wake ~4 KB to
    serve the rare one that wants a face; this costs a wake nothing until
    it asks, which is the same trade ``brnrd kb`` already makes.

    Matching is deliberately forgiving, because a resident searches with
    the *word for the feeling*, not the handle: the handles are coined
    marks (``fo.cus``, ``we.ary``, ``smug_``) and their punctuation is
    register, not syntax. ``focus``, ``focused`` and ``fo.cus`` all land on
    the same face — separators are stripped from both sides before
    comparing, and the trigger line (which is a *sentence about the state*)
    is searched too, so "four hours one regex" finds ``narrow``.

    An empty query returns the situational set, which is the resident's
    half; telemetry faces are the daemon's and it does not take requests.
    """

    needle = _norm(query)
    if not needle:
        return [e for e in EMOTES.values() if e.kind == "situational"][:limit]

    scored: list[tuple[int, int, Emote]] = []
    for e in EMOTES.values():
        name = _norm(e.name)
        family = _norm(e.family)
        trigger = _norm(e.trigger)
        if name == needle:
            rank = 0
        elif name.startswith(needle) or needle.startswith(name):
            rank = 1
        elif family and (
            family == needle
            or family.startswith(needle)
            or needle.startswith(family)
        ):
            # The plain feeling-word. Ranked under the handle because a
            # resident that already knows the handle typed it on purpose,
            # and above substrings because "satisfied" naming the satisfied
            # family is a stronger signal than "sat" appearing inside a
            # sentence somewhere.
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
