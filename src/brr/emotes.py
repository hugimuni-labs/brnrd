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
    "glyph",
    "for_telemetry",
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
    """

    name: str
    kind: str
    trigger: str
    frames: tuple[str, ...]
    pitch: float = 0.5


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


def _e(name: str, kind: str, trigger: str, *frames: str, pitch: float = 0.5) -> Emote:
    return Emote(name=name, kind=kind, trigger=trigger, frames=tuple(frames), pitch=pitch)


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
       "b·_·d", "bo_od", "bO_Od", "bo_od", "b·_·d", pitch=0.85),
    _e("o_O!", "situational",
       "a test passed that you were certain would fail",
       "b·_·d", "bo_Od", "b·_·d", pitch=0.75),
    _e("wha_", "situational",
       "the stack trace points at a file you never touched",
       "b·_·d", "b°o°d", "b°O°d", "b°o°d", "b·_·d", pitch=0.8),
    _e("gasp_", "situational",
       "the prod config was the thing all along",
       "b·o·d", "b°o°d", "b°O°d", "b°o°d", "b·o·d", pitch=0.85),
    _e("jolt_", "situational",
       "CI went red on a line you did not write",
       "b·_·d", "b!_!d", "b·_·d", pitch=0.85),
    # annoyed — gut-warm, the jaw sets low
    _e("grr_", "situational",
       "the linter reformats the line you just formatted",
       "b¬_¬d", "b>_<d", "b¬_¬d", pitch=0.2),
    _e("tsk_", "situational",
       "a lone trailing-whitespace diff in an otherwise clean PR",
       "b¬_¬d", "b¬.¬d", "b¬_¬d", pitch=0.3),
    _e("ugh_", "situational",
       "the flake failed again — same test, different reason",
       "b-_-d", "b>_<d", "b-_-d", pitch=0.2),
    _e("pfft", "situational",
       "someone's 'quick fix' that is neither",
       "b¬_¬d", "b¬~¬d", "b¬_¬d", pitch=0.3),
    _e("rrgh", "situational",
       "a merge conflict in the lockfile. again.",
       "b>_<d", "bx_xd", "b>_<d", pitch=0.15),
    _e("mutter", "situational",
       "YAML.",
       "b-_-d", "b-.-d", "b-_-d", pitch=0.25),
    # puzzled — mid, up into the head
    _e("hm_m", "situational",
       "the value is right but the path to it makes no sense",
       "b·_·d", "bo_·d", "b·_od", "b·_·d", pitch=0.55),
    _e("huh_", "situational",
       "two configs disagree and both are loaded",
       "b·_·d", "b?_·d", "b·_?d", "b·_·d", pitch=0.55),
    _e("eh_?", "situational",
       "the comment describes code that isn't there",
       "b·_·d", "b·o·d", "bo_·d", "b·_·d", pitch=0.5),
    _e("wat_", "situational",
       "it works and you don't know why yet",
       "b·_·d", "b·o·d", "b·O·d", "b·o·d", "b·_·d", pitch=0.6),
    _e("q_q?", "situational",
       "the test asserts the opposite of its own name",
       "b?_?d", "b·_·d", "b?_?d", pitch=0.5),
    # satisfied — mid-bright, a settled lift
    _e("fine_", "situational",
       "the diff was clean on the fifth reread",
       "b-n-d", "b-w-d", "b-n-d", pitch=0.55),
    _e("ahh_", "situational",
       "green bar, all of it, on the first run",
       "b-_-d", "b^_^d", "b-_-d", pitch=0.6),
    _e("nnice", "situational",
       "the refactor deleted more than it added",
       "b·_·d", "b^u^d", "b·_·d", pitch=0.6),
    _e("mm_m", "situational",
       "a function that finally reads top to bottom without a jump",
       "b·u·d", "b-u-d", "b·u·d", pitch=0.55),
    # focused — the working mid-band, level gaze
    _e("fo.cus", "situational",
       "deep in the one function that actually matters",
       "b·_·d", "b-_-d", "b·_·d", pitch=0.45),
    _e("lock_", "situational",
       "the repro is in hand and you're closing on the cause",
       "b-_-d", "b=_=d", "b-_-d", pitch=0.45),
    _e("flow_", "situational",
       "edits landing faster than doubt can catch them",
       "b·_·d", "b·w·d", "b·_·d", pitch=0.5),
    _e("squint", "situational",
       "reading the one line where the bug has to live",
       "b·_·d", "b¬_¬d", "b·_·d", pitch=0.45),
    _e("narrow", "situational",
       "four hours, one regex",
       "b-_-d", "bˋ_ˊd", "b-_-d", pitch=0.4),
    # smug — name-weave: the n morphs into a forward/upward mouth, the
    # maintainer's flagship. Neutral ``brnrd`` → the mouth curls up (n→ᵕ),
    # the eyes (r's) drop to a half-lidded smirk (r→¬), then settle back.
    _e("smug_", "situational",
       "you called the bug before opening the file",
       "brnrd", "brᵕrd", "b¬ᵕ¬d", "brᵕrd", "brnrd", pitch=0.6),
    _e("knew_", "situational",
       "the hunch held and the log proves it",
       "brnrd", "br-rd", "brᵕrd", "br-rd", "brnrd", pitch=0.65),
    _e("told_", "situational",
       "the edge case you warned about, now red in CI",
       "brnrd", "brᵕrd", "b¬w¬d", "brᵕrd", "brnrd", pitch=0.6),
    _e("heh_", "situational",
       "a one-line fix for a week-old ticket",
       "brnrd", "b·ᵕrd", "b·ᵕ<d", "b·ᵕrd", "brnrd", pitch=0.6),
    _e("petty_", "situational",
       "closing an issue as wontfix, and being correct",
       "brnrd", "br~rd", "b¬~¬d", "br~rd", "brnrd", pitch=0.55),
    # wary — low-mid, guard up
    _e("wary_", "situational",
       "the function is named simple_ and it is 400 lines",
       "b·_·d", "b·_-d", "b·_·d", pitch=0.35),
    _e("hmwait", "situational",
       "the fix is too easy for the size of the bug",
       "b·_·d", "b-_·d", "b·_-d", "b·_·d", pitch=0.4),
    _e("side_", "situational",
       "the sonnet worker's report is suspiciously tidy",
       "b·_·d", "b¬_·d", "b·_·d", pitch=0.35),
    _e("creak", "situational",
       "touching auth code on a Friday",
       "b·_·d", "b°_°d", "b·_·d", pitch=0.3),
    _e("nervy", "situational",
       "pushing to a branch with no CI on it",
       "b·_·d", "b;_;d", "b·_·d", pitch=0.3),
    # weary — low, the head hangs
    _e("weary_", "situational",
       "third rebase onto a branch that keeps moving",
       "b=_=d", "b-_-d", "b=_=d", pitch=0.2),
    _e("sigh_", "situational",
       "reopening the file you'd closed thinking you were done",
       "b-_-d", "b=_=d", "b-.-d", "b-_-d", pitch=0.25),
    _e("fried", "situational",
       "context window full and still three threads open",
       "b@_@d", "bx_xd", "b@_@d", pitch=0.2),
    _e("drry", "situational",
       "the same TODO, untouched, for the ninth wake running",
       "b-_-d", "b-~-d", "b-_-d", pitch=0.25),
    _e("flat_", "situational",
       "the bug was environmental — nothing to fix, nothing learned",
       "b·_·d", "b-_-d", "b·_·d", pitch=0.3),
    # curious — up and out, toward the crown
    _e("ooh_", "situational",
       "a helper in the kb you didn't know existed",
       "b·o·d", "b·O·d", "b·o·d", pitch=0.75),
    _e("peek_", "situational",
       "following an import three modules deep just to see",
       "b·_·d", "b·_od", "bo_·d", "b·_·d", pitch=0.7),
    _e("hmn_", "situational",
       "a git blame that leads somewhere genuinely interesting",
       "b·_·d", "b·ᴗ·d", "b·_·d", pitch=0.65),
    _e("itch_", "situational",
       "a duplicated block openly begging to be extracted",
       "b·_·d", "b·_9d", "b·_·d", pitch=0.6),
    # triumphant — crown, arms up
    _e("t.da", "situational",
       "the failing test goes green",
       "b·_·d", "b^o^d", "b^‿^d", "b^o^d", "b·_·d", pitch=0.85),
    _e("yesss", "situational",
       "one-shot repro on a heisenbug",
       "b·_·d", "b>w<d", "b·_·d", pitch=0.85),
    _e("clear!", "situational",
       "the whole board green, nothing pending, notebook current",
       "b·_·d", "b^‿^d", "b·_·d", pitch=0.8),
    _e("proud_", "situational",
       "a test you wrote catches a real regression a week later",
       "b·_·d", "b·u·d", "b·‿·d", "b·_·d", pitch=0.7),
    # sheepish — shrink down and in (a trailing ; is the sweat-drop)
    _e("oops_", "situational",
       "the bug was your own typo from two commits ago",
       "b·_;d", "b-_;d", "b·_;d", pitch=0.35),
    _e("welp_", "situational",
       "pushed, then noticed the debug print",
       "b·_;d", "b·o;d", "b·_;d", pitch=0.35),
    _e("myb_", "situational",
       "you blamed the test; it was the code",
       "b·_;d", "bo_;d", "b·_;d", pitch=0.35),
    _e("cring", "situational",
       "reading your own code from a year ago",
       "b·_·d", "b>_<d", "b·_·d", pitch=0.3),
    # determined — grounded and forward
    _e("grip_", "situational",
       "the flake ends this wake, one way or the other",
       "b·_·d", "bˋ_ˊd", "b·_·d", pitch=0.45),
    _e("again", "situational",
       "reverting to try the harder, correct approach",
       "b-_-d", "bˋoˊd", "b-_-d", pitch=0.45),
    _e("jaw_", "situational",
       "no shortcut left that isn't a lie; taking the long one",
       "b·_·d", "bˋ=ˊd", "b·_·d", pitch=0.4),
    _e("primed", "situational",
       "repro in hand, coffee metaphorically hot",
       "b·_·d", "bo_od", "b·_·d", pitch=0.55),
    # amused — a lift toward the head
    _e("pff_h", "situational",
       "a variable named temp_final_v2_real",
       "b·_·d", "b·‿·d", "b·_·d", pitch=0.65),
    _e("lol_", "situational",
       "a commit message that just says 'ugh'",
       "b·_·d", "b^o^d", "b·_·d", pitch=0.7),
    _e("grin_", "situational",
       "the config says DO NOT TOUCH; git blame says it's yours",
       "b·_·d", "b·ᵕ·d", "b·_·d", pitch=0.65),
    _e("snrk", "situational",
       "a stray print('here') that reached three environments deep",
       "b·_·d", "b·w<d", "b·_·d", pitch=0.65),
    # bored — low and flat
    _e("meh_", "situational",
       "the fourth near-identical CRUD endpoint",
       "b-_-d", "b-.-d", "b-_-d", pitch=0.3),
    _e("yawn_", "situational",
       "waiting on a green build that is always green",
       "b-_-d", "b-o-d", "b-_-d", pitch=0.3),
    _e("tap_", "situational",
       "nothing to do but watch the deploy bar advance",
       "b·_·d", "b·-·d", "b·_·d", pitch=0.35),
    # overwhelmed — flooded, down in the gut
    _e("aaah_", "situational",
       "forty failing tests, one root cause, somewhere",
       "b·_·d", "bx~xd", "b·_·d", pitch=0.2),
    _e("swamp_", "situational",
       "the diff touches every file you were avoiding",
       "b·_·d", "b@_@d", "b·_·d", pitch=0.2),
    # suspicious — low-mid, narrowed
    _e("squin2", "situational",
       "the test that cannot fail — it asserts True",
       "b·_·d", "b¬^¬d", "b·_·d", pitch=0.35),
    _e("fishy_", "situational",
       "passing tests, zero assertions",
       "b·_·d", "b¬_·d", "b·_·d", pitch=0.35),
    # relieved — the exhale that settles to mid
    _e("phew_", "situational",
       "the force-push was to the right branch after all",
       "b·_·d", "b-‿-d", "b·_·d", pitch=0.5),
    _e("exhal", "situational",
       "the revert restored the green bar",
       "b-_-d", "b-~-d", "b-_-d", pitch=0.5),
    _e("safe_", "situational",
       "the secret you almost committed, caught by the hook",
       "b·_·d", "b-.-d", "b·_·d", pitch=0.5),
    # grumpy — gut, warm and low
    _e("hmph_", "situational",
       "CI is slower than reading the code by hand would have been",
       "b¬_¬d", "b¬~¬d", "b¬_¬d", pitch=0.2),
    _e("glare", "situational",
       "a formatter with strong opinions and no config file",
       "b-_-d", "b-_xd", "b-_-d", pitch=0.2),
    # delighted — crown, bright
    _e("yay_", "situational",
       "a docs example that actually runs as written",
       "b·_·d", "b>‿<d", "b·_·d", pitch=0.85),
    _e("sprkl", "situational",
       "an API that does exactly what its name says",
       "b·_·d", "b*ᴗ*d", "b·_·d", pitch=0.8),
    _e("pep_", "situational",
       "a test suite that finishes under a second",
       "b·_·d", "b^‿^d", "b·_·d", pitch=0.75),
    # dread — the bottom of the gut
    _e("uhoh_", "situational",
       "the words 'works on my machine' in the issue",
       "b·_·d", "b·_;d", "b°_;d", "b·_;d", "b·_·d", pitch=0.15),
    _e("brace_", "situational",
       "opening a 2,000-line file named utils",
       "b·_·d", "b°_;d", "b·_·d", pitch=0.15),
    _e("cold_", "situational",
       "git status shows changes you don't remember making",
       "b·_·d", "b°_°d", "bO_Od", "b°_°d", "b·_·d", pitch=0.1),
    _e("brace2", "situational",
       "running the migration against a copy of prod",
       "b-_-d", "b=_=d", "b-_-d", pitch=0.2),
    # stuck — low, the wall
    _e("stuck_", "situational",
       "the same error after the fix that should have fixed it",
       "b·_·d", "b-_-d", "b=_=d", "b-_-d", "b·_·d", pitch=0.25),
    _e("wall_", "situational",
       "every lead in the trace ends in vendored code",
       "b°_°d", "b·_·d", "b°_°d", pitch=0.2),
    # second-guessing — low-mid, hesitating
    _e("er_r", "situational",
       "hand on the button, unsure of the blast radius",
       "b·_·d", "b·_-d", "b·_·d", pitch=0.4),
    _e("wait2", "situational",
       "the assertion looks right; the whole test looks wrong",
       "b·_·d", "b-_·d", "b·_·d", pitch=0.4),
    _e("redo_", "situational",
       "the clean solution needs the ugly one built first",
       "b·_·d", "b·~·d", "b·_·d", pitch=0.4),
    _e("doubt_", "situational",
       "the bar is green, but you skipped the slow suite",
       "b·_·d", "b-_·d", "b·_-d", "b·_·d", pitch=0.4),
    # vindicated / betrayed
    _e("calld", "situational",
       "the race condition you flagged in review, now in prod",
       "brnrd", "b¬n¬d", "b¬w¬d", "b¬n¬d", "brnrd", pitch=0.55),
    _e("by200", "situational",
       "a 200 OK wrapping an error payload — betrayed by a status code",
       "2oo:)", "2oo:|", "2oo:(", "2oo:|", "2oo:)", pitch=0.2),
    _e("rug_", "situational",
       "the dependency changed its API in a patch release",
       "b·_·d", "b°o°d", "b·_·d", pitch=0.25),
    _e("spook", "situational",
       "a test that passes locally and fails only in CI",
       "b·_·d", "b°O°d", "b·_·d", pitch=0.4),
    # finer shades
    _e("humbl", "situational",
       "the 'obvious' fix broke four other things",
       "b·_·d", "b-_;d", "b·_·d", pitch=0.3),
    _e("zen_", "situational",
       "one clean failing test, one clear cause, a whole quiet afternoon",
       "b·_·d", "b-w-d", "b·_·d", pitch=0.5),
    _e("warm_", "situational",
       "a kb page from a past wake that answers today's question",
       "b·_·d", "b·ᴗ·d", "b·_·d", pitch=0.6),
    _e("greed_", "situational",
       "one more refactor before the commit. just one.",
       "b·_·d", "b·w·d", "b·_·d", pitch=0.55),
    _e("glee_", "situational",
       "deleting commented-out code with no mercy at all",
       "b·_·d", "b¬ᴗ¬d", "b·_·d", pitch=0.55),
    _e("wince", "situational",
       "a '# TODO: fix before ship' that shipped two years ago",
       "b·_·d", "b>_<d", "b·_·d", pitch=0.3),
    _e("clean_", "situational",
       "deleting a dead module entirely, imports and all",
       "b·_·d", "b·‿·d", "b·_·d", pitch=0.6),
    _e("content", "situational",
       "nothing pending, nothing broken, notebook current",
       "b·ᴗ·d", "b·‿·d", "b·ᴗ·d", pitch=0.55),
    _e("hz_", "situational",
       "the answer arrived while you were writing the question",
       "b·_·d", "b·o·d", "b·O·d", "b·o·d", "b·_·d", pitch=0.7),
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


def lookup(name: str) -> Emote | None:
    """Return the emote for *name*, or ``None`` if no such handle exists.

    This is the resident's path: the first line of ``.mood`` comes in here.
    An unknown handle resolves to nothing rather than a guess — a face the
    resident didn't mean is exactly the lie the honesty bar forbids.
    """

    return EMOTES.get(name)


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
    """

    emote = EMOTES.get(name)
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
