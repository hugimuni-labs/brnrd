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
        for f in e.frames:
            assert not any(unicodedata.combining(c) for c in f), name
        widths = {len(f) for f in e.frames}
        assert len(widths) == 1, (name, [(f, len(f)) for f in e.frames])


def test_every_emote_is_a_base_expression_base_animation():
    """2–5 frames, ≤ 12 wide, and the cycle returns to its base so the
    loop is seamless."""
    for name, e in EMOTES.items():
        assert 2 <= len(e.frames) <= 5, name
        assert e.frames[0] == e.frames[-1], name
        assert max(len(f) for f in e.frames) <= 12, name
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
