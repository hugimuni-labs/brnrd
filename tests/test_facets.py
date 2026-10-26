"""The boundary facet schema — the single definition every renderer projects."""

from brr import facets


def test_schema_is_wall_derived_and_ordered():
    keys = [f.key for f in facets.FACETS]
    assert keys == [
        "quota", "spend", "context_window", "coexisting_runs", "remote_scm",
        "correspondent", "allowance",
    ]
    # Level facets are the walls; state facets are actionable posture.
    by_key = facets.FACETS_BY_KEY
    assert by_key["quota"].kind == facets.LEVEL
    assert by_key["spend"].kind == facets.LEVEL
    assert by_key["context_window"].kind == facets.LEVEL
    assert by_key["coexisting_runs"].kind == facets.STATE
    assert by_key["remote_scm"].kind == facets.STATE
    # The correspondent is posture, not a wall: a long quiet changes what a
    # run does next without being anything it can run out of.
    assert by_key["correspondent"].kind == facets.STATE
    assert by_key["allowance"].kind == facets.LEVEL
    # coexisting_runs (single-flight someday-nicety), allowance (a
    # strand's `spawn:` ceiling, or the resident seat's own standing
    # allowance — design-the-allowance.md slices 1-2) and correspondent
    # (a chat thread's own fact; a schedule-woken run has no correspondent
    # to read) are the optional facets — everything else is required of
    # every renderer.
    optional = {"coexisting_runs", "allowance", "correspondent"}
    assert {s.key for s in facets.FACETS if not s.required} == optional
    assert all(s.required for s in facets.FACETS if s.key not in optional)


# ── draws (brnrd#1810, design-the-seat-that-never-quits.md §"The
# measurement") — attribution of the quota facet, same attach pattern as
# ``pacing`` above ──────────────────────────────────────────────────────


# ── hold (design-the-seat-that-never-quits.md §"The machinery, in
# slices" #3) — same attach pattern as ``draws`` above ──────────────────


# ── coexisting_runs (live presence-registry read) ────────────────────────────


# ── #585: presence label must never carry another run's task prose ──────────
#
# The mechanism: a sibling's presence entry (name/label/stream/run_id) is
# read by `presence.list_active`, handed to `facets.build` as `coexisting`,
# and its rendered `summary` is injected into *this* run's own hook context
# at every tool-call boundary (`hooks.py` -> `facets.render_line`). Two
# guards: (1) daemon.py's presence `register()` call no longer falls back to
# `task.body` for `label` (see `daemon._presence_label_for_event`, tested in
# test_daemon.py); (2) this facet must independently refuse to emit long
# free text regardless of which field a sibling's entry carries it in — the
# tests below pin guard (2).
#
# N = 32 (`facets._SIBLING_HANDLE_MAX_CHARS`): long enough that a real
# handle (a run id like "run-260723-1241-xv6d", a short resident `.name`, a
# conversation key) renders in full; short enough that no truncated prefix
# of a task spec reads as a sentence-length directive a sibling could
# mistake for its own instruction.


# ── runner governance block (step 3, design-runner-cores.md) ─────────────────


