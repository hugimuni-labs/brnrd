"""The portal change_token must not move because the resident's last reply cost tokens."""
from brr import daemon


def test_change_token_ignores_resources():
    base = {"inbound": {"pending": 0}, "resources": {"context_window": {"tokens": 1}},
            "generated_at": "t1"}
    moved = {**base, "resources": {"context_window": {"tokens": 999}, "allowance": {"spent": 5}},
             "generated_at": "t2"}
    assert daemon._change_token(base) == daemon._change_token(moved)
    assert daemon._change_token(base) != daemon._change_token({**base, "inbound": {"pending": 1}})


def test_change_token_ignores_the_heddle_glow_but_not_the_heddles():
    """run-260919-1802-6zeq, 2026-09-19 — the third face of #282.

    `heddles[].brightness` is a decay curve: it moves every heartbeat for as
    long as a heddle has ever been lit, whether or not anything happened.
    Four heartbeats with the resident idle produced four different tokens,
    so the Stop hook's "unchanged token ⇒ bare {} ⇒ stop cleanly" gate could
    never latch — and a seat whose `halt:` had been *accepted* re-fired Stop
    a dozen times without reaching the worker seam that finalizes it.
    """
    base = {
        "inbound": {"pending": 0},
        "heddles": [
            {"slug": "the-clockwork", "rune": "ᛃ", "brightness": 0.9859,
             "last_match_at": "2026-09-19T21:08:24Z", "matched_by": ["place"]},
        ],
    }
    # The clock in disguise: only the glow and its stamp moved.
    glowed = {
        **base,
        "heddles": [
            {**base["heddles"][0], "brightness": 0.9981,
             "last_match_at": "2026-09-19T21:09:49Z"},
        ],
    }
    assert daemon._change_token(base) == daemon._change_token(glowed)

    # Real change still moves it: a heddle lighting up for the first time...
    lit = {**base, "heddles": base["heddles"] + [
        {"slug": "the-loom", "rune": "ᚱ", "brightness": 0.5,
         "last_match_at": "2026-09-19T21:09:49Z", "matched_by": ["place"]},
    ]}
    assert daemon._change_token(base) != daemon._change_token(lit)

    # ...and what matched it, which is why this drops fields rather than
    # excluding `heddles` whole.
    rematched = {**base, "heddles": [
        {**base["heddles"][0], "matched_by": ["place", "topic"]},
    ]}
    assert daemon._change_token(base) != daemon._change_token(rematched)


def test_change_token_holds_still_across_an_idle_heartbeat():
    """The property the Stop gate actually needs: with nothing but the
    daemon's own clock moving, two consecutive snapshots must agree."""
    snapshot = {
        "generated_at": "2026-09-19T21:08:31Z",
        "tick": {"n": 4011},
        "resources": {"context_window": {"tokens_used": 369_300}},
        "budget": {"elapsed_seconds": 11_160},
        "card": {"age_seconds": 240, "state_moved_seconds": 240, "stale": False},
        "heddles": [{"slug": "the-post", "brightness": 0.86,
                     "last_match_at": "2026-09-19T20:56:31Z"}],
        "inbound": {"pending": 0},
    }
    later = {
        **snapshot,
        "generated_at": "2026-09-19T21:08:42Z",
        "tick": {"n": 4012},
        "resources": {"context_window": {"tokens_used": 371_800}},
        "budget": {"elapsed_seconds": 11_171},
        "card": {"age_seconds": 251, "state_moved_seconds": 251, "stale": False},
        "heddles": [{"slug": "the-post", "brightness": 0.8559,
                     "last_match_at": "2026-09-19T20:56:31Z"}],
    }
    assert daemon._change_token(snapshot) == daemon._change_token(later)
