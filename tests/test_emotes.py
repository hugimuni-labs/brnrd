"""Tests for the emote library (#566, reworked #1925).

The mascot's honesty bar — "a tamagotchi that never lies" — is enforced
here structurally: every handle is unique and self-keyed, every face
animates without jitter (all frames equal width, base first and last),
every daemon state the body must speak resolves to a real face, and the
two lookup paths (resident via ``lookup``, daemon via ``for_telemetry``)
refuse to invent a mood for a name they don't know.

The 2026-09-11 rework collapsed the 113-handle, 33-family situational
palette into twelve plain words (``VOCABULARY``), each with its own still
frame distinct from the "no face" rest glyph. The old handles still
resolve — through ``LEGACY_ALIASES`` — but as frames of one of the twelve,
not as faces of their own; that half of the contract is pinned below
alongside the twelve-word structure itself.
"""

from __future__ import annotations

import unicodedata

import pytest

from brr import emotes
from brr.emotes import (
    EMOTES,
    LEGACY_ALIASES,
    REST_GLYPH,
    TELEMETRY_DEFAULTS,
    TELEMETRY_STATES,
    VOCABULARY,
    Emote,
)


def test_library_is_populated_in_range():
    """Telemetry keeps its floor; the situational half is now exactly the
    twelve-word vocabulary, not a wide range — that width reduction is the
    entire point of the rework."""
    kinds = {name: e.kind for name, e in EMOTES.items()}
    telemetry = [n for n, k in kinds.items() if k == "telemetry"]
    situational = [n for n, k in kinds.items() if k == "situational"]
    assert set(kinds.values()) == {"telemetry", "situational"}
    assert len(telemetry) >= 12
    assert situational == sorted(situational, key=lambda n: VOCABULARY.index(n)) \
        or set(situational) == set(VOCABULARY)
    assert set(situational) == set(VOCABULARY)
    assert len(VOCABULARY) == 12


def test_names_are_unique_and_equal_dict_keys():
    """The handle is the shared object between user and resident; an
    ambiguous or mis-keyed name breaks shared comprehension."""
    for key, e in EMOTES.items():
        assert isinstance(e, Emote)
        assert e.name == key
    assert len(EMOTES) == len({e.name for e in EMOTES.values()})


def test_every_emote_frames_are_equal_width():
    """Fixed-width mono is what keeps the mark from jittering. Width is
    codepoint count; a combining mark would smuggle in a zero-width glyph
    that len() can't see, so those are banned outright."""
    for name, e in EMOTES.items():
        for seq in (*e.sequences, (e.resting_frame,)):
            for f in seq:
                assert not any(unicodedata.combining(c) for c in f), (name, f)
        widths = {len(f) for seq in e.sequences for f in seq}
        widths.add(len(e.resting_frame))
        assert len(widths) == 1, (
            name,
            [(f, len(f)) for seq in e.sequences for f in seq],
            (e.resting_frame, len(e.resting_frame)),
        )


def test_every_emote_is_a_base_expression_base_animation():
    """2–5 frames, ≤ 12 wide, and the cycle returns to its base so the
    loop is seamless."""
    for name, e in EMOTES.items():
        for seq in e.sequences:
            assert 2 <= len(seq) <= 5, (name, seq)
            assert seq[0] == seq[-1], (name, seq)
            assert max(len(f) for f in seq) <= 12, (name, seq)
        # Alternates are alternates, not duplicates: a second cycle that
        # plays the same frames costs a wire field and buys no life.
        assert len({tuple(seq) for seq in e.sequences}) == len(e.sequences), name
        assert e.kind in {"telemetry", "situational"}
        assert e.trigger.strip(), name


def test_every_required_daemon_state_is_covered():
    """The maintainer's floor: idle, running, quota-starved, blocked-on-you,
    delivering, and the rest — each maps to a face the daemon can render."""
    required = {
        "idle", "running", "quota_starved", "blocked_on_user", "delivering",
        "spawning", "reviewing", "testing", "failing", "merging",
        "waiting_deploy", "stopped",
    }
    assert required <= set(TELEMETRY_STATES)
    assert required <= set(TELEMETRY_DEFAULTS)


def test_every_telemetry_state_maps_to_a_telemetry_face():
    """Every ``TELEMETRY_DEFAULTS`` value resolves, and it resolves to a
    daemon-derived face — a situational (resident-authored) face must never
    be rendered as if it were computed telemetry."""
    for state, name in TELEMETRY_DEFAULTS.items():
        e = EMOTES.get(name)
        assert e is not None, (state, name)
        assert e.kind == "telemetry", (state, name)


def test_telemetry_states_tuple_is_all_mapped():
    for state in TELEMETRY_STATES:
        assert state in TELEMETRY_DEFAULTS


def test_every_emote_has_an_in_range_pitch():
    """The body axis is a coordinate on [0, 1], gut to crown. Every face
    carries one; nothing may sit off the spectrum the dashboard maps to hue."""
    for name, e in EMOTES.items():
        assert isinstance(e.pitch, float), name
        assert 0.0 <= e.pitch <= 1.0, (name, e.pitch)


def test_pitch_tracks_the_body_axis():
    """Sanity that pitch is set with meaning, not left at a flat default:
    the heavy gut states sit low and the crown states sit high, on the
    right side of the midline."""
    assert EMOTES["worried"].pitch < 0.3     # dread/wary family, deep gut
    assert EMOTES["braced"].pitch < 0.3      # gut-warm, gritted
    assert EMOTES["tired"].pitch < 0.3       # weary, low
    assert EMOTES["x_x"].pitch < 0.3         # failing telemetry
    assert EMOTES["curious"].pitch > 0.6     # surprise/curiosity, crown
    assert EMOTES["proud"].pitch > 0.7       # triumph/delight
    # the working band sits near the middle
    assert 0.4 <= EMOTES["focused"].pitch <= 0.6
    # not every face shares one value — pitch is authored, not defaulted
    assert len({e.pitch for e in EMOTES.values()}) >= 8


def test_lookup_returns_emote_or_none():
    for name in EMOTES:
        assert emotes.lookup(name) is EMOTES[name]
    assert emotes.lookup("focused").kind == "situational"
    assert emotes.lookup("definitely-not-a-face") is None
    assert emotes.lookup("") is None


def test_for_telemetry_resolves_states_and_refuses_unknowns():
    for state in TELEMETRY_STATES:
        e = emotes.for_telemetry(state)
        assert e is not None and e.kind == "telemetry"
    assert emotes.for_telemetry("running").name == TELEMETRY_DEFAULTS["running"]
    # An unmapped state renders nothing rather than inventing a mood.
    assert emotes.for_telemetry("not_a_daemon_state") is None
    # A real *situational* word is not a telemetry state.
    assert emotes.for_telemetry("focused") is None


def test_emote_is_frozen():
    e = next(iter(EMOTES.values()))
    with pytest.raises(Exception):
        e.name = "mutated"  # type: ignore[misc]


def test_wordmark_faces_are_present():
    """#566 names the wordmark itself as a face space; at least the resting
    body and one mutation should live here."""
    marks = [e for e in EMOTES.values() if any("brnrd" in f or "Я" in f for f in e.frames)]
    assert marks, "expected at least one brnrd-wordmark face"


# ── The MIX layout + n-as-mouth rules (the maintainer's two decisions) ──
#
# Read a name-weave frame ``b r n r d`` as a face: b/d are the cheeks (the
# fixed frame), the two r's are the eyes, the n is the mouth. These tests
# pin the rework so a later "simplification" can't silently flatten the
# faces back to single-glyph moods or drop the wordmark from telemetry.

# Mouth glyphs that read as a forward/upward curl — the smug smirk shape.
_UP_MOUTHS = {"ᵕ", "‿", "^", "w"}


def _is_name_weave(frame: str) -> bool:
    """A name-weave face: the wordmark frame ``b<eye><mouth><eye>d`` — five
    cells, brand cheeks fixed at the ends."""
    return len(frame) == 5 and frame[0] == "b" and frame[-1] == "d"


def test_telemetry_faces_use_the_name_weave_wordmark_frame():
    """Decision 1, telemetry half: telemetry leans name-weave so the brand
    reads sharpest where the daemon speaks for the body. Every telemetry
    frame is the ``b…d`` wordmark frame, and each rests on the plain
    wordmark — the daemon's body is the mark itself, animated."""
    telemetry = [e for e in EMOTES.values() if e.kind == "telemetry"]
    for e in telemetry:
        for f in e.frames:
            assert _is_name_weave(f), (e.name, f)
        # base state first AND last is the neutral resting wordmark
        assert e.frames[0] == "brnrd", (e.name, e.frames[0])
        assert e.frames[-1] == "brnrd", (e.name, e.frames[-1])


def test_name_weave_neutral_resting_frame_is_exactly_brnrd():
    """Decision 2, anchor: the neutral resting face is the plain ``brnrd``
    (mouth ``n`` un-morphed). The idle body — awake, nothing queued — is
    the wordmark at rest, and it is byte-exact."""
    idle = EMOTES["id_l"]
    assert idle.frames[0] == "brnrd"
    assert idle.frames[-1] == "brnrd"
    # the mouth (n slot, index 2) is the un-morphed 'n' at rest
    assert idle.frames[0][2] == "n"


def test_amused_mutates_the_n_mouth_forward_and_upward():
    """Decision 2, the heart of it: the maintainer's named default — the
    n (mouth) extends forward and upward into an anime smirk, and the eyes
    (r's) shift with it. Neutral ``brnrd`` has a flat ``n`` mouth; the
    amused peak must curl that mouth up and move at least one eye. Post-
    rework this is ``EMOTES["amused"]`` — the smug/knew/told family folded
    into it — rather than a standalone ``smug_`` handle."""
    amused = EMOTES["amused"]
    assert _is_name_weave(amused.frames[0])
    assert amused.frames[0] == "brnrd"          # rests on neutral
    assert amused.frames[0][2] == "n"           # neutral mouth is flat 'n'
    # some frame curls the mouth (index 2) up-and-forward, away from 'n'
    curled = [f for f in amused.frames if f[2] in _UP_MOUTHS]
    assert curled, (amused.name, amused.frames)
    assert all(f[2] != "n" for f in curled)
    # and the eyes (index 1 / 3) shift with the mood — not left at 'r'
    assert any(f[1] != "r" or f[3] != "r" for f in amused.frames), amused.frames
    # the old handle still resolves onto the same face
    assert emotes.lookup("smug_") is amused


def test_situational_split_leans_cheek_form_with_a_name_weave_word():
    """Decision 1, situational half: situational faces lean cheek form
    (``b{eyes}d``) for two-eye nuance, while ``amused`` (the old smug/
    vindicated family) still reads best as name-weave. Both halves must be
    real — the mix is not all-one-thing, even at twelve faces."""
    situational = [e for e in EMOTES.values() if e.kind == "situational"]
    cheek = [e for e in situational if e.frames[0] != "brnrd"]
    name_weave = [e for e in situational if e.frames[0] == "brnrd"]
    assert len(cheek) >= 9, len(cheek)
    assert len(name_weave) >= 1
    assert EMOTES["amused"].frames[0] == "brnrd"


def test_cheek_form_carries_the_brand_cheeks():
    """A cheek-form face is a two-eye kaomoji wrapped in ``b…d`` — the
    example faces the maintainer named must be exactly that shape.
    ``stuck`` absorbed the old puzzled family (``hm_m`` → ``bo_·d``);
    ``braced`` absorbed the old annoyed family (``grr_`` → ``b>_<d``)."""
    stuck = EMOTES["stuck"]
    assert any(_is_name_weave(f) and f[0] == "b" and f[-1] == "d"
               for f in stuck.frames)
    assert "bo_·d" in stuck.alts[0]
    braced = EMOTES["braced"]
    assert "b>_<d" in braced.frames


def test_resting_frame_is_wearable_while_still():
    """`rest` is what a calm surface holds — the dashboard chip sits on it
    ~5s between flickers. Two things must hold: it is a real frame shape
    (checked for width above), and it belongs to *this* face rather than
    being a frame borrowed from the palette at large: an authored rest must
    at least appear in the face's own animation, so the still frame and the
    moving one are the same body.
    """
    for name, e in EMOTES.items():
        if e.rest is None:
            continue
        appears = any(e.rest in seq for seq in e.sequences)
        assert appears, (name, e.rest, e.sequences)


def test_every_vocabulary_word_has_a_unique_still_not_the_rest_glyph():
    """The whole defect the rework closes, pinned as a number: 61 of 113
    old stills equalled the "no face" glyph. Now every one of the twelve
    is distinct, and none is ``REST_GLYPH``."""
    situational = [e for e in EMOTES.values() if e.kind == "situational"]
    assert len(situational) == 12
    stills = [e.resting_frame for e in situational]
    assert REST_GLYPH not in stills
    assert len(set(stills)) == len(situational), "every word's still must be its own"


def test_sequences_of_is_the_publish_paths_only_reach_into_frames():
    """`cloud.py` published `frames[0]` directly and starved the dashboard
    for it. The library states the frame rules, so the library answers the
    questions about them: `glyph` (resting, legacy), `resting_frame`, and
    `sequences_of`. Unknown handles resolve to nothing, never a default.
    """
    from brr.emotes import sequences_of

    assert sequences_of("no-such-handle") is None
    focus = sequences_of("fo.cus")
    assert focus is not None and len(focus) >= 2, focus
    assert focus[0] != focus[1]
    # the legacy handle and the vocabulary word answer identically
    assert sequences_of("fo.cus") == sequences_of("focused")


def test_every_situational_face_declares_its_family():
    """A new face joining with no edit is the tell of an enumerated class.
    Post-rework a situational face's family equals its own name — the
    twelve words are their own family now — but the field still must not
    be empty, so a future addition can't slip in unclassified."""
    orphans = [
        e.name for e in EMOTES.values() if e.kind == "situational" and not e.family
    ]
    assert not orphans, orphans
    for e in EMOTES.values():
        if e.kind == "situational":
            assert e.family == e.name, e.name


def test_the_palette_the_docstring_advertises_is_actually_findable():
    """The claim and the check read the same source, so they cannot drift.

    The rework's docstring advertises the twelve words by name (in
    ``VOCABULARY``'s own definition line); every one of them must be
    findable through ``search``.
    """
    unfindable = [w for w in VOCABULARY if not emotes.search(w)]
    assert not unfindable, unfindable


def test_lookup_reads_the_word_for_the_feeling_not_only_the_legacy_mark():
    """``.mood`` is a machine-parsed channel; the old handles were coined
    marks (``fo.cus``). A run's older ``.mood`` file, and a resident
    writing the plain word, must land on the same face."""
    focus = EMOTES["focused"]
    assert emotes.lookup("fo.cus") is focus
    assert emotes.lookup("focus") is focus
    assert emotes.lookup("focused") is focus

    # Every current handle still resolves to itself — tolerance must not
    # shadow the exact spelling with a prefix neighbour.
    for name, emote in EMOTES.items():
        assert emotes.lookup(name) is emote, name

    # Every legacy handle resolves onto its assigned vocabulary word.
    for old, word in LEGACY_ALIASES.items():
        hit = emotes.lookup(old)
        assert hit is not None, old
        assert hit.name == word, (old, hit.name, word)


def test_lookup_resolves_synonym_and_legacy_family_word():
    """Every public vocabulary layer reaches a stable, existing face."""
    assert emotes.lookup("curious").family == "curious"
    # An old family word ("puzzled") now routes through to its new home.
    assert emotes.lookup("puzzled").name == "stuck"
    # An old synonym ("smirking", once under "smug") still resolves.
    assert emotes.lookup("smirking").name == "amused"
    assert emotes.lookup("attentive").name == "focused"


def test_one_handle_resolver_serves_every_public_reader():
    """Three functions asked "what face is this?" and answered separately —
    fixed once, upstream in ``lookup``, so every reader agrees.

    ``glyph`` is pinned to ``resting_frame``, not ``frames[0]`` — the old
    pin (``== emote.frames[0]``) codified the exact defect this rework
    fixes: six of twelve words share ``frames[0] == REST_GLYPH`` (the
    animation base), so pinning ``glyph`` to it made "no face" the
    *tested* behaviour for half the vocabulary. ``focused`` is one of the
    six (its ``frames[0]`` is ``REST_GLYPH``, its ``resting_frame`` is
    not) — kept in this parametrization on purpose so a regression back
    to ``frames[0]`` fails here, not just in the table-driven still test.
    """
    for spelling in ("fo.cus", "focus", "focused", "not-a-face-at-all", "puzzled"):
        emote = emotes.lookup(spelling)
        if emote is None:
            assert emotes.glyph(spelling) is None, spelling
            assert emotes.sequences_of(spelling) is None, spelling
        else:
            assert emotes.glyph(spelling) == emote.resting_frame, spelling
            assert emotes.sequences_of(spelling) == emote.sequences, spelling


def test_near_misses_is_empty_exactly_when_lookup_succeeds():
    """The two are one decision, so they may never disagree.

    If a handle resolves there is nothing to suggest; if it doesn't, staying
    quiet is the failure mode this whole slice is about.
    """
    for name in EMOTES:
        assert emotes.near_misses(name) == []
    for old in LEGACY_ALIASES:
        assert emotes.near_misses(old) == []
    # Nothing near ⇒ a bare miss, never three strangers ranked by luck.
    assert emotes.near_misses("xyzzy-not-a-feeling") == []
    # A slip past the typo tolerance still gets its shortlist.
    assert emotes.lookup("curiousity") is None
    assert emotes.near_misses("curiousity")


def test_an_unknown_word_names_the_vocabulary_never_guesses():
    """`brnrd do --mood` refuses an unknown word with the list — no silent
    nearest-face write. Two honest answers, kept distinct:

    - a **typo** gets the face it was reaching for (`resolve_nearest`)
    - a word that is simply **not ours** gets the vocabulary (`families`),
      because there is no thesaurus wide enough to bridge every feeling
    """
    assert [e.name for e in emotes.nearest("fokused")] == ["focused"]
    assert emotes.nearest("elated") == [], (
        "a word that is merely absent must not be guessed at"
    )
    assert set(emotes.families()) == set(VOCABULARY)
    assert len(emotes.families()) == 12


def test_lookup_accepts_a_typo_within_two_edits():
    """A small misspelling resolves before the ranked-shortlist path."""
    assert emotes.lookup("tird").name == "tired"
    assert emotes.lookup("focusd").name == "focused"
    assert emotes.search("focusd") == [], "search has no spell-check"
    assert emotes.near_misses("focusd") == []
    # A handle that *does* resolve still has no near misses to name.
    assert emotes.near_misses("fo.cus") == []


def test_legacy_aliases_cover_the_pre_rework_situational_palette():
    """98 old handles, one rework: every one of them still resolves, and
    none of them survives as a top-level ``EMOTES`` key — the whole point
    is that they are frames of a word now, not names of their own."""
    assert len(LEGACY_ALIASES) == 98
    for old, word in LEGACY_ALIASES.items():
        assert old not in EMOTES, old
        assert word in VOCABULARY, (old, word)
