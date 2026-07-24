"""Tests for the emote library (#566).

The mascot's honesty bar — "a tamagotchi that never lies" — is enforced
here structurally: every handle is unique and self-keyed, every face
animates without jitter (all frames equal width, base first and last),
every daemon state the body must speak resolves to a real face, and the
two lookup paths (resident via ``lookup``, daemon via ``for_telemetry``)
refuse to invent a mood for a name they don't know.
"""

from __future__ import annotations

import unicodedata

import pytest

from brr import emotes
from brr.emotes import EMOTES, TELEMETRY_DEFAULTS, TELEMETRY_STATES, Emote


def test_library_is_populated_in_range():
    """Extensive-by-mandate: the palette is large and the split is real —
    a floor of daemon-derived faces and a wide resident-authored range."""
    assert 80 <= len(EMOTES) <= 130
    kinds = {name: e.kind for name, e in EMOTES.items()}
    telemetry = [n for n, k in kinds.items() if k == "telemetry"]
    situational = [n for n, k in kinds.items() if k == "situational"]
    assert set(kinds.values()) == {"telemetry", "situational"}
    assert len(telemetry) >= 12
    assert 70 <= len(situational) <= 100


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
        # Every sequence, not just the primary: an alternate that jitters
        # jitters exactly as visibly, and pinning only `frames` is how a
        # second cycle would arrive unchecked. `rest` is held between
        # flickers, so it has to match the width too or the chip twitches
        # on the way in and out of the animation.
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
    assert EMOTES["cold_"].pitch < 0.3       # dread, deep gut
    assert EMOTES["uhoh_"].pitch < 0.3
    assert EMOTES["rrgh"].pitch < 0.3        # gut-warm annoyance
    assert EMOTES["x_x"].pitch < 0.3         # failing telemetry
    assert EMOTES["bo_Od"].pitch > 0.7       # surprise, crown
    assert EMOTES["t.da"].pitch > 0.7        # triumph
    assert EMOTES["yay_"].pitch > 0.7        # delight
    assert EMOTES["ooh_"].pitch > 0.6        # curiosity
    # the working band sits near the middle
    assert 0.4 <= EMOTES["fo.cus"].pitch <= 0.6
    assert 0.4 <= EMOTES["flow_"].pitch <= 0.6
    # not every face shares one value — pitch is authored, not defaulted
    assert len({e.pitch for e in EMOTES.values()}) >= 8


def test_lookup_returns_emote_or_none():
    for name in EMOTES:
        assert emotes.lookup(name) is EMOTES[name]
    assert emotes.lookup("fo.cus").kind == "situational"
    assert emotes.lookup("definitely-not-a-face") is None
    assert emotes.lookup("") is None


def test_for_telemetry_resolves_states_and_refuses_unknowns():
    for state in TELEMETRY_STATES:
        e = emotes.for_telemetry(state)
        assert e is not None and e.kind == "telemetry"
    assert emotes.for_telemetry("running").name == TELEMETRY_DEFAULTS["running"]
    # An unmapped state renders nothing rather than inventing a mood.
    assert emotes.for_telemetry("not_a_daemon_state") is None
    # A real *situational* handle is not a telemetry state.
    assert emotes.for_telemetry("fo.cus") is None


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


def test_smug_mutates_the_n_mouth_forward_and_upward():
    """Decision 2, the heart of it: 'smug' is the maintainer's named default
    — the n (mouth) extends forward and upward into an anime smirk, and the
    eyes (r's) shift with it. Neutral ``brnrd`` has a flat ``n`` mouth; the
    smug peak must curl that mouth up and move at least one eye."""
    smug = EMOTES["smug_"]
    assert _is_name_weave(smug.frames[0])
    assert smug.frames[0] == "brnrd"          # rests on neutral
    assert smug.frames[0][2] == "n"           # neutral mouth is flat 'n'
    # some frame curls the mouth (index 2) up-and-forward, away from 'n'
    curled = [f for f in smug.frames if f[2] in _UP_MOUTHS]
    assert curled, (smug.name, smug.frames)
    assert all(f[2] != "n" for f in curled)
    # and the eyes (index 1 / 3) shift with the mood — not left at 'r'
    assert any(f[1] != "r" or f[3] != "r" for f in smug.frames), smug.frames


def test_situational_split_leans_cheek_form_with_a_name_weave_family():
    """Decision 1, situational half: situational faces lean cheek form
    (``b{eyes}d``) for two-eye nuance, while a named family (smug /
    vindicated) still reads best as name-weave. Both halves must be real —
    the mix is not all-one-thing."""
    situational = [e for e in EMOTES.values() if e.kind == "situational"]
    cheek = [e for e in situational if all(_is_name_weave(f) for f in e.frames)
             and any(f == "brnrd" for f in e.frames) is False
             and e.frames[0] != "brnrd"]
    name_weave = [e for e in situational if e.frames[0] == "brnrd"]
    # the bulk wear the brand cheeks
    assert len(cheek) >= 50, len(cheek)
    # the smug/vindicated family carries the wordmark smirk
    assert len(name_weave) >= 3
    for handle in ("smug_", "knew_", "told_"):
        assert EMOTES[handle].frames[0] == "brnrd", handle


def test_cheek_form_carries_the_brand_cheeks():
    """A cheek-form face is a two-eye kaomoji wrapped in ``b…d`` — the
    example faces the maintainer named must be exactly that shape."""
    puzzled = EMOTES["hm_m"]        # bo_·d — one brow up
    assert any(_is_name_weave(f) and f[0] == "b" and f[-1] == "d"
               for f in puzzled.frames)
    assert "bo_·d" in puzzled.frames
    strained = EMOTES["grr_"]       # b>_<d — both eyes shut
    assert "b>_<d" in strained.frames


def test_resting_frame_is_wearable_while_still():
    """`rest` is what a calm surface holds — the dashboard chip sits on it
    ~5s between flickers. Two things must hold: it is a real frame shape
    (checked for width above), and it belongs to *this* face rather than
    being a frame borrowed from the palette at large. The weaker property
    is the one asserted, because the strong one ("visually distinct from
    every sibling") is a design judgement no test can make: an authored
    rest must at least appear in the face's own animation, so the still
    frame and the moving one are the same body.
    """
    for name, e in EMOTES.items():
        if e.rest is None:
            continue
        appears = any(e.rest in seq for seq in e.sequences)
        assert appears, (name, e.rest, e.sequences)


def test_the_resting_palette_gap_is_measured_rather_than_assumed():
    """The bug this whole slice came from, kept as a number.

    `frames[0]` is shared by design, so resting on it renders most of the
    situational palette identically. This pins the *direction of travel*:
    distinct resting frames must never drop below what is authored today.
    It is deliberately a floor and not an equality — the palette pass that
    authors the rest of them should make this test pass harder, never
    edit it.
    """
    situational = [e for e in EMOTES.values() if e.kind == "situational"]
    by_base = {e.frames[0] for e in situational}
    by_rest = {e.resting_frame for e in situational}
    assert len(by_rest) >= len(by_base), (len(by_rest), len(by_base))
    assert len(by_rest) >= 17, (
        f"{len(by_rest)} distinct resting frames for {len(situational)} faces"
    )


def test_sequences_of_is_the_publish_paths_only_reach_into_frames():
    """`cloud.py` published `frames[0]` directly and starved the dashboard
    for it. The library states the frame rules, so the library answers the
    questions about them: `glyph` (resting, legacy), `resting_frame`, and
    `sequences_of`. Unknown handles resolve to nothing, never a default.
    """
    from brr.emotes import sequences_of

    assert sequences_of("no-such-handle") is None
    focus = sequences_of("fo.cus")
    assert focus is not None and len(focus) == 2, focus
    assert focus[0] != focus[1]


def test_every_situational_face_declares_its_family():
    """A new face joining with no edit is the tell of an enumerated class.

    The families were ``#`` section comments until 2026-07-25 — a fact the
    file was *organised by* and did not *store*. So ``search("satisfied")``
    returned nothing while the module docstring advertised satisfied as part
    of the palette, and a resident that searched the obvious word, found
    nothing, and invented a plausible-looking handle got a raw id string
    published to the dashboard. That is the observed sequence, not a
    hypothetical one.

    This is the guard that makes the class closed rather than merely known:
    add a face outside a family and the suite goes red at the moment of the
    edit, instead of at the moment someone searches for it.
    """
    orphans = [
        e.name for e in EMOTES.values() if e.kind == "situational" and not e.family
    ]
    assert not orphans, orphans


def test_the_palette_the_docstring_advertises_is_actually_findable():
    """The claim and the check read the same source, so they cannot drift.

    Six of the ten feeling-words this module's own docstring names as the
    situational palette — surprised, annoyed, puzzled, satisfied, curious,
    triumphant — returned *nothing* from ``search``. A document describing a
    capability the code does not have is worse than silence: it is the
    reason a resident stops searching and starts guessing.

    The word list is parsed out of the docstring rather than copied here. A
    word added to the prose is a word this test then demands the palette can
    answer for.
    """
    import re

    doc = emotes.__doc__ or ""
    m = re.search(r"full range of it:(.+?)\.", doc, re.S)
    assert m, "the docstring no longer advertises a palette — update this test"
    advertised = [
        w.strip()
        for w in m.group(1).replace("\n", " ").split(",")
        if w.strip() and "finer shades" not in w and not w.strip().startswith("and ")
    ]
    assert len(advertised) >= 8, advertised

    unfindable = [w for w in advertised if not emotes.search(w)]
    assert not unfindable, unfindable


def test_lookup_reads_the_word_for_the_feeling_not_only_the_mark():
    """``.mood`` is a machine-parsed channel; the handles are coined marks.

    ``weave.md`` is explicit that the register decorates nothing a parser
    reads — and then the palette minted every handle *as* a mark (``fo.cus``)
    and matched it byte for byte. A run writing ``focused`` resolved to
    ``None``, published four ``null``s, and the dashboard fell back to
    printing the raw string; that fallback was every mood brnrd.dev ever
    showed. Meanwhile ``search``'s own docstring promised these three
    spellings were one face, and they were — in the command nobody publishes
    through. Two resolvers, one contract, and the strict one owned the wire.
    """
    focus = EMOTES["fo.cus"]
    assert emotes.lookup("fo.cus") is focus
    assert emotes.lookup("focus") is focus
    assert emotes.lookup("focused") is focus

    # Every handle still resolves to itself — tolerance must not shadow the
    # exact spelling with a prefix neighbour.
    for name, emote in EMOTES.items():
        assert emotes.lookup(name) is emote, name


def test_lookup_resolves_names_and_never_families():
    """Tolerance is not a licence to invent, and the line is *the name*.

    ``lookup`` reads handles, forgivingly. It does not read families, ever —
    ``satisfied`` names four faces and choosing one would be the lie the
    honesty bar exists to prevent. The rule is stated as a property rather
    than a list of words: whatever comes back must be a face whose own name
    is the query, once the register's punctuation is stripped off both. So
    ``focused`` → ``fo.cus`` is legal (it *is* that handle, spelled by a
    human) and ``satisfied`` → ``fine_`` never can be.

    Written after the first draft of this test asserted the sloppier thing —
    "a crowded family resolves to nothing" — and went red on ``focused``,
    which is both a five-face family *and* the plain spelling of one handle.
    The looser claim would have banned the fix's whole point.
    """
    norm = emotes._norm

    for e in EMOTES.values():
        if not e.family:
            continue
        hit = emotes.lookup(e.family)
        if hit is None:
            assert emotes.near_misses(e.family), e.family
            continue
        a, b = norm(hit.name), norm(e.family)
        assert a.startswith(b) or b.startswith(a), (e.family, hit.name)

    # The families with no handle-shaped spelling stay unresolvable, named
    # here because they are the ones a resident actually types.
    for word in ("satisfied", "curious", "triumphant", "annoyed", "puzzled"):
        assert emotes.lookup(word) is None, word
        assert emotes.near_misses(word), word


def test_one_handle_resolver_serves_every_public_reader():
    """Three functions asked "what face is this?" and answered separately.

    ``glyph`` and ``sequences_of`` each ran their own ``EMOTES.get``, while
    ``search`` normalized and ``lookup`` did not — so "is ``focused`` a
    face?" had two answers depending on who asked, and the wire happened to
    ask the strict one. A fact stored four times is repaired once and stays
    broken three times.
    """
    for spelling in ("fo.cus", "focus", "focused", "not-a-face-at-all", "satisfied"):
        emote = emotes.lookup(spelling)
        if emote is None:
            assert emotes.glyph(spelling) is None, spelling
            assert emotes.sequences_of(spelling) is None, spelling
        else:
            assert emotes.glyph(spelling) == emote.frames[0], spelling
            assert emotes.sequences_of(spelling) == emote.sequences, spelling


def test_near_misses_is_empty_exactly_when_lookup_succeeds():
    """The two are one decision, so they may never disagree.

    If a handle resolves there is nothing to suggest; if it doesn't, staying
    quiet is the failure mode this whole slice is about.
    """
    for name in EMOTES:
        assert emotes.near_misses(name) == []
    assert emotes.near_misses("sa.tis"), "the maintainer's invented handle must guide"
    assert emotes.near_misses("xyzzy-not-a-feeling") == []
