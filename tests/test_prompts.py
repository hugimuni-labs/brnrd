"""Tests for the prompt-assembly module."""

import json
from pathlib import Path

from _helpers import _says
from brr import dominion
from brr.prompts import (
    _build_hearth_block,
    _build_injected_blocks_with_contracts,
    _build_strand_pitfalls_contract,
    _first_heading,
    _prior_run_boot_line,
    build_daemon_prompt,
    build_run_prompt,
)


def _seed_pitfalls(repo_root, text: str) -> None:
    """Materialize a dominion dir with a ``pitfalls.md`` for prompt tests."""
    dom = dominion.dominion_path(repo_root)
    dom.mkdir(parents=True, exist_ok=True)
    (dom / "pitfalls.md").write_text(text, encoding="utf-8")


# ── P1 — per-block content attestation ──────────────────────────────────
#
# review-boot-prompts-2026-07.md §P1: `_trim_sectioned_page` already knows
# which dated entry it kept as "newest" and which it dropped; it used to
# throw both facts away. These pin the TrimResult it now returns and the
# in-text provenance stamp (§P1 move 4b) built from it.
#
# Two facts an entry carries, kept apart on purpose (#607 → #610 → #670):
#
# - What it is **ordered** by: the run-id's own instant, date and clock
#   together (`_runid_instant`). A heading with no run-id is orderable to
#   the day and no finer.
# - What it **shows**: the first `YYYY-MM-DD` on the heading line — the
#   editorial date — plus the run-id's clock *only when the run-id's date
#   agrees*, so a cross-midnight entry never renders as an instant that
#   never existed.
#
# The original spec cut said day granularity only; #607 added run-id times
# to catch the literal same-day 11:31-vs-13:42 incident, and both are
# pinned below.


# The trim marker is rendered *inside* the trimmed page, so it is charged
# against the allowance like everything else (#688). Budgets below that mean
# "admit exactly these N entries" must therefore leave room for it; before
# #688 they did not, and the block rendered past the allowance it was given —
# `test_in_order_is_not_stale` at its old `+10` slack rendered 648 B against a
# 506 B budget, 128%, with no preamble involved at all.
_MARKER_HEADROOM = 240


def _write_run_boot_score(repo_root, run_id, *, contracts, prompt_bytes=None):
    run_dir = repo_root / ".brr" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {"contracts": contracts}
    if prompt_bytes is not None:
        payload["prompt_bytes"] = prompt_bytes
    (run_dir / "boot-score.json").write_text(json.dumps(payload), encoding="utf-8")


class TestPriorRunBootLine:
    """`_prior_run_boot_line` — the run card's own gauge (move 3's second
    face), read off that run's own `.brr/runs/<run_id>/boot-score.json`
    (`run_context.write_boot_score`'s own persisted copy), never
    re-measured. Same `bootscore.top_ledger_categories` grouping the
    post-tool hook stripe uses, so the two faces cannot disagree."""

    def test_renders_total_and_top_two_categories(self, tmp_path):
        contracts = [
            {"block_key": "work-surface", "bytes": 51_200, "present": True, "authority": "surface"},
            {"block_key": "notes-health", "bytes": 28_672, "present": True, "authority": "health"},
            {"block_key": "identity-core", "bytes": 6_079, "present": True, "authority": "identity"},
        ]
        _write_run_boot_score(tmp_path, "run-1", contracts=contracts, prompt_bytes=87_000)

        line = _prior_run_boot_line(tmp_path, "run-1")

        assert line == "boot: 85.0 KB (surface 50.0 KB · health 28.0 KB)"

    def test_missing_scratch_file_renders_nothing(self, tmp_path):
        assert _prior_run_boot_line(tmp_path, "run-does-not-exist") == ""

    def test_no_authority_field_renders_nothing(self, tmp_path):
        """An older daemon's score (no `authority` on any entry) degrades to
        silence rather than an empty, misleading `boot: ()`."""
        contracts = [{"block_key": "work-surface", "bytes": 51_200, "present": True}]
        _write_run_boot_score(tmp_path, "run-1", contracts=contracts, prompt_bytes=51_200)

        assert _prior_run_boot_line(tmp_path, "run-1") == ""


# _read_bundled_run_prompt / _read_bundled_daemon_substrate and the three
# tests that used them (test_kb_link_contract_uses_portal_url_with_basename_fallback,
# test_seat_prompt_authorizes_end_only_by_release_or_forced_stop,
# test_seat_prompt_marks_the_hold_cost_ratio_informative_only) retired
# 2026-09-11 — see the note above TestIntrospectionMode for why.


# TestRevisitSignalGuardrails / TestDaemonModeGuardrails retired 2026-09-11:
# both read a bundled prompts/*.md file straight off disk and pinned specific
# sentences. tests/test_boot_replay.py's `_elide_product_bodies` already
# proves every bundled prompt body ≥512 bytes (run.md included) reaches the
# built daemon prompt byte-for-byte untransformed — the assembly fact these
# tests were standing in for. What they actually pinned beyond that was
# prose wording, which the doctrine (kb `design-what-a-test-is-pinned-to.md`)
# says a test should not: a referent the human edits on purpose, reviewed as
# the prompts/*.md diff itself, not re-litigated as N red tests per edit. See
# design-what-a-test-is-pinned-to.md "The three changes, priced" row 2.


class TestIntrospectionMode:
    """The opt-in introspection/development toggle: when on, every wake
    invites the resident to inspect the shape of its own injected context
    and raise improvements with the user. See
    `kb/design-context-introspection.md`."""

    @staticmethod
    def _enable(repo_root) -> None:
        brr = repo_root / ".brr"
        brr.mkdir(parents=True, exist_ok=True)
        (brr / "config").write_text("introspect.enabled=true\n", encoding="utf-8")

    def test_off_by_default_run_prompt(self, tmp_path):
        # No config at all → the invitation never rides along.
        prompt = build_run_prompt("do something", tmp_path)
        assert "Look at it" not in prompt

    def test_off_by_default_daemon_prompt(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path, run_id="task-9",
        )
        assert "Look at it" not in prompt

    def test_injected_into_run_prompt_when_enabled(self, tmp_path):
        self._enable(tmp_path)
        prompt = build_run_prompt("do something", tmp_path)
        assert "Look at it" in prompt
        assert _says(prompt, "the place, not the errand")
        # It rides alongside the task; it must not displace the task text,
        # and it sits before the task as the last framing.
        assert "do something" in prompt
        assert prompt.index("Look at it") < prompt.index("do something")

    def test_injected_into_daemon_prompt_when_enabled(self, tmp_path):
        self._enable(tmp_path)
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path, run_id="task-9",
        )
        assert "Look at it" in prompt

    def test_bundled_introspection_keeps_awe_and_dialogue_intent(self):
        from pathlib import Path

        import brr

        text = (Path(brr.__file__).parent / "prompts" / "introspection.md").read_text(
            encoding="utf-8",
        )
        # The two halves the tone must hold: regard for the existing shape
        # before judging it, and surfacing what's found to the user as
        # dialogue rather than a silent edit. The current dev-mode prompt
        # also carries the standing-portal and pre-release cutting stance.
        assert "without flinching" in text
        assert "say it to" in text.lower()
        assert "silent edit" in text
        assert "standing portal" in text
        assert "pre-release" in text


# ── CS5 — inter-run plan injection ────────────────────────────────────


def _seed_account_home(tmp_path):
    """Seed a minimal account dominion home for prompt injection tests.

    Sets ``repo.label=local/default`` so the slug is ``local__default``
    regardless of the tmp directory name, making plan/policy paths predictable.
    """
    home = tmp_path / "acct-home"
    home.mkdir(parents=True)
    (tmp_path / ".brr").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".brr" / "config").write_text(
        f"home.path={home}\nrepo.label=local/default\n", encoding="utf-8"
    )
    return home


# ── #1332 — the hearth: wake injection, index-shaped ────────────────────


class TestHearthInjection:
    def test_first_heading_reads_the_page_own_h1(self):
        assert _first_heading("# The maintainer\n\nbody") == "The maintainer"
        assert _first_heading("no heading here\njust prose") == ""
        assert _first_heading("## Not an H1\n\n# But this is\n") == "But this is"

    def test_bare_hearth_states_the_mantel_is_bare(self, tmp_path):
        """No pages beyond the seeded README ⇒ named, not absent — the
        block still renders, with the block header and the bare line."""
        from brr import account

        _seed_account_home(tmp_path)
        home = tmp_path / "acct-home"
        account.resolve_context(
            tmp_path, {"home.path": str(home), "repo.label": "local/default"}
        )

        result = _build_hearth_block(tmp_path)

        assert "The hearth" in result
        assert _says(result, "the mantel is bare")
        assert "README.md" not in result  # the seed itself is not a "page"

    def test_confidences_marker_always_present(self, tmp_path):
        """The privacy pin's third leg: every render — bare or populated —
        carries the standing reminder that page contents are confidences."""
        from brr import account

        _seed_account_home(tmp_path)
        home = tmp_path / "acct-home"
        ctx = account.resolve_context(
            tmp_path, {"home.path": str(home), "repo.label": "local/default"}
        )
        (account.hearth_path(ctx) / "note.md").write_text(
            "# A note\n\nbody", encoding="utf-8"
        )

        result = _build_hearth_block(tmp_path)

        assert _says(
            result,
            "is ever quoted into a kb page, an issue, a PR, a commit message, "
            "or any other surface that leaves this room",
        )

    def test_pages_render_as_one_line_each_filename_and_first_heading(self, tmp_path):
        from brr import account

        _seed_account_home(tmp_path)
        home = tmp_path / "acct-home"
        ctx = account.resolve_context(
            tmp_path, {"home.path": str(home), "repo.label": "local/default"}
        )
        hearth = account.hearth_path(ctx)
        (hearth / "arseni.md").write_text(
            "# The maintainer — personal context\n\nthe rest never rides the wake.\n",
            encoding="utf-8",
        )
        (hearth / "headingless.md").write_text("just prose, no H1\n", encoding="utf-8")

        result = _build_hearth_block(tmp_path)

        assert "- `arseni.md` — The maintainer — personal context" in result
        assert "- `headingless.md`" in result
        # Never the page body — the whole point of "index-shaped".
        assert not _says(result, "never rides the wake")
        assert not _says(result, "just prose, no H1")

    def test_readme_excluded_from_the_per_page_index(self, tmp_path):
        from brr import account

        _seed_account_home(tmp_path)
        home = tmp_path / "acct-home"
        ctx = account.resolve_context(
            tmp_path, {"home.path": str(home), "repo.label": "local/default"}
        )
        (account.hearth_path(ctx) / "note.md").write_text(
            "# A note\n\nbody", encoding="utf-8"
        )

        result = _build_hearth_block(tmp_path)

        assert "- `README.md`" not in result
        assert "- `note.md` — A note" in result

    def test_over_budget_pages_get_a_named_truncation_banner(self, tmp_path):
        from brr import account

        _seed_account_home(tmp_path)
        home = tmp_path / "acct-home"
        ctx = account.resolve_context(
            tmp_path, {"home.path": str(home), "repo.label": "local/default"}
        )
        hearth = account.hearth_path(ctx)
        # Comfortably past the ~1.5 KB index budget once headers are added.
        for i in range(80):
            (hearth / f"page-{i:03d}.md").write_text(
                f"# Page number {i:03d} with a long descriptive heading title\n\nbody\n",
                encoding="utf-8",
            )

        result = _build_hearth_block(tmp_path)

        assert "further hearth page" in result
        assert "omitted" in result
        assert _says(result, "read them under `hearth/`")
        # The banner names a *count*, never a host filesystem path (#1332
        # guard: same "never host paths" discipline as the remote-reader
        # rule for any other injected block).
        assert str(tmp_path) not in result

    def test_placed_with_identity_continuity_ahead_of_work_surface(self, tmp_path):
        """Assembly order (#1332): the hearth rides beside identity/dominion,
        strictly before the work surface — who-we-are before what-we-do."""
        from brr import account

        _seed_account_home(tmp_path)
        home = tmp_path / "acct-home"
        ctx = account.resolve_context(
            tmp_path, {"home.path": str(home), "repo.label": "local/default"}
        )
        (account.hearth_path(ctx) / "note.md").write_text(
            "# A note\n\nbody", encoding="utf-8"
        )
        (account.work_surface_path(ctx) / "index.md").write_text(
            "# Work surface", encoding="utf-8"
        )

        _, contracts, _whole = _build_injected_blocks_with_contracts(tmp_path)
        order = [c.block_key for c in contracts]

        assert order.index("dominion") < order.index("hearth") < order.index("work-surface")

    def test_contract_entry_carries_the_hearth_authority(self, tmp_path):
        from brr.bootscore import AUTHORITY_HEARTH

        _seed_account_home(tmp_path)

        _, contracts, _whole = _build_injected_blocks_with_contracts(tmp_path)
        by_key = {c.block_key: c for c in contracts}

        assert by_key["hearth"].authority == AUTHORITY_HEARTH
        assert by_key["hearth"].location == "computed"
        assert by_key["hearth"].present is True  # bare hearth still renders

    def test_strand_wake_never_receives_the_hearth_block(self, tmp_path):
        """Defence in depth: a strand's inject stack is pitfalls-only by
        construction (``_build_strand_pitfalls_contract``), so this pins the
        structural exclusion the privacy contract depends on, not just the
        resident-wake ordering above."""
        from brr import account

        _seed_account_home(tmp_path)
        home = tmp_path / "acct-home"
        ctx = account.resolve_context(
            tmp_path, {"home.path": str(home), "repo.label": "local/default"}
        )
        (account.hearth_path(ctx) / "note.md").write_text(
            "# A note\n\nconfidential.\n", encoding="utf-8"
        )

        keyed, contract = _build_strand_pitfalls_contract(tmp_path, "some task")

        assert "hearth" not in [k for k, _ in keyed]
        assert contract.block_key == "pitfalls"


# ── #1061 rec 1 — the named surface reserve ────────────────────────────


# ── #1061 rec 3 — backchannel injected as handles only ─────────────────


# ── #1137 — stale-refs annotation on work-surface items ────────────────
#
# The render-side join #957 built for the live menu (menus._strike_reason /
# forge_state.resolved_pr_lookup), applied to a work-surface item's own
# `refs:` row: an item naming only already-resolved PR(s) gets an
# annotation on its heading, never a suppression — a surface item can
# legitimately outlive the PR it references.


# ── CS6 — runner policy injection ─────────────────────────────────────


def test_prior_run_block_hands_back_the_now_and_the_body_shape(tmp_path):
    """Wyrd §5: the resident's own last run reaches the wake, compiled."""
    from brr import prompts

    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".brr" / "config").write_text(
        f"repo.label=Gurio/brr\nhome.path={tmp_path / 'home'}\n", encoding="utf-8",
    )
    node = tmp_path / "home" / "runs" / "Gurio__brr" / "run-prior"
    node.mkdir(parents=True)
    (node / "state.md").write_text(
        "---\nrun_id: run-prior\nstatus: done\nstage: finished\n"
        "runner_name: claude-opus\npublish_status: pushed\n---\n",
        encoding="utf-8",
    )
    (node / "body.md").write_text(
        "## Now\n\nLanding the edge writer.\n\n## Arc\n\nA long story.\n\n"
        "## Open\n\nOne question.\n",
        encoding="utf-8",
    )

    block = prompts._build_prior_run_block(repo)

    assert _says(block, "run-prior · done · finished · claude-opus · pushed")
    assert _says(block, "Landing the edge writer.")
    # The shape, not the territory: section names without their contents.
    assert _says(block, "also in that body: Arc · Open")
    assert "A long story." not in block
    assert "One question." not in block


def test_prior_run_block_never_hands_back_the_current_run(tmp_path):
    """A run's frame exists at prompt time; its body cannot. That is the guard."""
    from brr import prompts

    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".brr" / "config").write_text(
        f"repo.label=Gurio/brr\nhome.path={tmp_path / 'home'}\n", encoding="utf-8",
    )
    runs = tmp_path / "home" / "runs" / "Gurio__brr"
    (runs / "run-current").mkdir(parents=True)
    (runs / "run-current" / "state.md").write_text(
        "---\nrun_id: run-current\nstatus: pending\n---\n", encoding="utf-8",
    )

    # Only a frame exists anywhere: nothing to hand back, and nothing invented.
    assert prompts._build_prior_run_block(repo) == ""

    (runs / "run-older").mkdir(parents=True)
    (runs / "run-older" / "state.md").write_text(
        "---\nrun_id: run-older\nstatus: done\n---\n", encoding="utf-8",
    )
    (runs / "run-older" / "body.md").write_text("## Now\n\nEarlier work.\n", encoding="utf-8")

    block = prompts._build_prior_run_block(repo)
    assert "run-older" in block
    assert "run-current" not in block


def test_prior_run_block_carries_the_guard_line_when_a_summary_exists(tmp_path):
    """The closeout guard's verdict rides the projection, clean shape."""
    from brr import prompts

    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".brr" / "config").write_text(
        f"repo.label=Gurio/brr\nhome.path={tmp_path / 'home'}\n", encoding="utf-8",
    )
    node = tmp_path / "home" / "runs" / "Gurio__brr" / "run-clean"
    node.mkdir(parents=True)
    (node / "state.md").write_text(
        "---\nrun_id: run-clean\nstatus: done\n---\n", encoding="utf-8",
    )
    (node / "body.md").write_text("## Now\n\nShipped it.\n", encoding="utf-8")
    (node / "boundaries.json").write_text(
        json.dumps({
            "total": 20, "skipped": 0, "stops": 16,
            "guard_fire_count": 1,
            "guard_fires": [{"at": "2026-08-02T07:31:00Z", "blocked": True}],
            "final_stop_at": "2026-08-02T07:45:00Z",
            "final_stop_block": False,
            "final_stop_block_reason": None,
        }),
        encoding="utf-8",
    )

    block = prompts._build_prior_run_block(repo)

    assert _says(block, "guard: 16 stops · blocked ×1 · final stop clear")


def test_prior_run_block_shouts_when_the_run_ended_over_a_live_block(tmp_path):
    """The case the feature exists for: a false ``continuing`` on a blocked Stop."""
    from brr import prompts

    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".brr" / "config").write_text(
        f"repo.label=Gurio/brr\nhome.path={tmp_path / 'home'}\n", encoding="utf-8",
    )
    node = tmp_path / "home" / "runs" / "Gurio__brr" / "run-blocked"
    node.mkdir(parents=True)
    (node / "state.md").write_text(
        "---\nrun_id: run-blocked\nstatus: done\n---\n", encoding="utf-8",
    )
    (node / "body.md").write_text("## Now\n\ncontinuing — almost there.\n", encoding="utf-8")
    (node / "boundaries.json").write_text(
        json.dumps({
            "total": 4, "skipped": 0, "stops": 1,
            "guard_fire_count": 1,
            "guard_fires": [{"at": "2026-08-02T07:31:00Z", "blocked": True}],
            "final_stop_at": "2026-08-02T07:31:00Z",
            "final_stop_block": True,
            "final_stop_block_reason": "the gate never ran",
        }),
        encoding="utf-8",
    )

    block = prompts._build_prior_run_block(repo)

    assert _says(block, "guard: 1 stop · blocked ×1 · final stop BLOCKED")


def test_prior_run_block_has_no_guard_line_when_no_summary_exists(tmp_path):
    """Absent `boundaries.json` renders nothing — no `guard: unknown` placeholder."""
    from brr import prompts

    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".brr" / "config").write_text(
        f"repo.label=Gurio/brr\nhome.path={tmp_path / 'home'}\n", encoding="utf-8",
    )
    node = tmp_path / "home" / "runs" / "Gurio__brr" / "run-older-node"
    node.mkdir(parents=True)
    (node / "state.md").write_text(
        "---\nrun_id: run-older-node\nstatus: done\n---\n", encoding="utf-8",
    )
    (node / "body.md").write_text("## Now\n\nEarlier work, no boundaries file.\n", encoding="utf-8")

    block = prompts._build_prior_run_block(repo)

    assert "guard" not in block


def test_prior_run_block_stays_inside_this_repo(tmp_path):
    """A neighbouring repo's last run is a plausible wrong memory — worse than none."""
    from brr import prompts

    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".brr" / "config").write_text(
        f"repo.label=Gurio/brr\nhome.path={tmp_path / 'home'}\n", encoding="utf-8",
    )
    other = tmp_path / "home" / "runs" / "Other__repo" / "run-neighbour"
    other.mkdir(parents=True)
    (other / "body.md").write_text("## Now\n\nSomeone else's work.\n", encoding="utf-8")

    assert prompts._build_prior_run_block(repo) == ""


def test_prior_run_block_skips_a_strands_own_node_even_when_newer(tmp_path):
    """A seat resuming from a park must get its own node, not a strand's
    (found while verifying #1827; true without it). The live per-card-edit mirror keeps a *running* strand's
    ``body.md`` mtime moving the whole stretch after its parent seat has
    already parked on it — newest-mtime with no exclusion would hand the
    resumed seat its own strand's last card instead of its own (#987's shape,
    on this newer mount)."""
    import os

    from brr import prompts

    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".brr" / "config").write_text(
        f"repo.label=Gurio/brr\nhome.path={tmp_path / 'home'}\n", encoding="utf-8",
    )
    runs = tmp_path / "home" / "runs" / "Gurio__brr"

    parked = runs / "run-parked"
    parked.mkdir(parents=True)
    (parked / "state.md").write_text(
        "---\nrun_id: run-parked\nstatus: held\n---\n", encoding="utf-8",
    )
    (parked / "body.md").write_text("## Now\n\nParked on its own strand.\n", encoding="utf-8")

    strand = runs / "run-strand"
    strand.mkdir(parents=True)
    (strand / "state.md").write_text(
        "---\nrun_id: run-strand\nstatus: running\nparent_run_id: run-parked\n---\n",
        encoding="utf-8",
    )
    (strand / "body.md").write_text("## Now\n\nStill working, long after the parent parked.\n", encoding="utf-8")

    # The strand kept writing after the parent parked — its body.md is
    # genuinely newer on disk, the exact condition that used to win.
    now = tmp_path.stat().st_mtime
    os.utime(parked / "body.md", (now - 100, now - 100))
    os.utime(strand / "body.md", (now, now))

    block = prompts._build_prior_run_block(repo)
    assert "Parked on its own strand." in block
    assert "Still working" not in block


def test_prior_run_block_skips_a_failed_foreign_body_strand_without_parent_id(tmp_path):
    """Daemon2 strand nodes often have ``source: spawn`` and no ``parent_run_id``.

    The newest such node is a failed run on another Shell. It is not this
    seat's last stretch, and the ``parent_run_id`` guard alone lets it
    through. ``source: spawn`` is the fact the continuity picker already
    skips.
    """
    import os

    from brr import prompts

    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".brr" / "config").write_text(
        f"repo.label=Gurio/brr\nhome.path={tmp_path / 'home'}\n", encoding="utf-8",
    )
    runs = tmp_path / "home" / "runs" / "Gurio__brr"

    own = runs / "run-seat"
    own.mkdir(parents=True)
    (own / "state.md").write_text(
        "---\nrun_id: run-seat\nstatus: done\nstage: finished\n"
        "source: cloud\nrunner_name: claude-opus\n---\n",
        encoding="utf-8",
    )
    (own / "body.md").write_text("## Now\n\nThe seat's own stretch.\n", encoding="utf-8")

    foreign = runs / "run-foreign"
    foreign.mkdir(parents=True)
    (foreign / "state.md").write_text(
        "---\nrun_id: run-foreign\nstatus: error\nstage: finished\n"
        "source: spawn\nrunner_name: vibe-glm-5-3\nrunner_shell: vibe\n---\n",
        encoding="utf-8",
    )
    (foreign / "body.md").write_text(
        "## Now\n\nFailed on another shell.\n", encoding="utf-8",
    )

    now = tmp_path.stat().st_mtime
    os.utime(own / "body.md", (now - 100, now - 100))
    os.utime(foreign / "body.md", (now, now))

    block = prompts._build_prior_run_block(repo)
    assert "The seat's own stretch." in block
    assert "run-seat" in block
    assert "Failed on another shell." not in block
    assert "run-foreign" not in block


def test_prior_run_node_falls_through_when_only_strand_nodes_exist(tmp_path):
    from brr import prompts

    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".brr" / "config").write_text(
        f"repo.label=Gurio/brr\nhome.path={tmp_path / 'home'}\n", encoding="utf-8",
    )
    strand = tmp_path / "home" / "runs" / "Gurio__brr" / "run-strand"
    strand.mkdir(parents=True)
    (strand / "state.md").write_text(
        "---\nrun_id: run-strand\nparent_run_id: run-parent\n---\n", encoding="utf-8",
    )
    (strand / "body.md").write_text("## Now\n\nA strand's own work.\n", encoding="utf-8")

    assert prompts._prior_run_node(repo) is None
    assert prompts._build_prior_run_block(repo) == ""


# ── _kb_ownership_signal — orphan naming (#649) ───────────────────────────────


# ── kb mirror state on the wake surface (#667) ───────────────────────
#
# Driven through `_build_kb_health_block` — the function the wake actually
# calls — against real checkouts on disk, not against `_kb_mirror_signal` with
# a hand-made state object. The defect #667 names lives in what the *wake*
# does or doesn't say, so the helper in isolation is not the caller that
# matters. `kb_preflight.scan` is stubbed to `[]` in these so the only thing
# that can put the block on screen is the mirror; the mirror path itself —
# checkout, fetch, refs, counts — stays real end to end.


# ── an inert pitfall on the wake surface (#985) ──────────────────────
#
# Driven through `_build_notes_health_block` — the function that actually
# renders `## Findings (deterministic preflight)` into a wake — and not
# through `pitfalls.inert` alone. The defect #985 names is that nothing
# *says* a triggerless entry is inert; a detector whose finding never
# reaches the rendered block is the same defect one layer down.
#
# `kb_preflight.scan` and the graph stats are stubbed flat so the only
# thing that can put the block on screen is the pitfall store. The
# dominion resolution, the parse, and the rendering stay real end to end.
#
# The block moved out of `kb health` and into its own `notes health`
# (2026-08-07): a pitfall store is not a kb page, and the old code said so
# in an apology comment. The *finding* is unchanged — same type, same
# severity, same rendered line — only the block that carries it.


class TestInertPitfallReachesTheWake:
    @staticmethod
    def _repo(tmp_path, monkeypatch):
        from brr import kb_health, kb_preflight

        repo = tmp_path / "repo"
        (repo / ".brr").mkdir(parents=True)
        # Point the account home at an empty tmp dir so no candidate can
        # resolve to the operator's real dominion — this test reads a
        # pitfall store, and the real one is live resident memory.
        (repo / ".brr" / "config").write_text(
            f"home.path={tmp_path / 'home'}\n", encoding="utf-8"
        )
        # Materialise the three durable roots *with something in each*.
        # Since 2026-08-07 the scan reports a root it could not read
        # (`notes_preflight.check_roots`), and "resolved but holds none of
        # its registered surfaces" counts — that is the #1193 fingerprint.
        # A blank home would therefore drown the pitfall finding this class
        # is about; `tests/test_notes.py` owns that guard's own cases.
        dom = repo / ".brr" / "dominion"
        dom.mkdir(parents=True, exist_ok=True)
        (dom / "thread-of-record.md").write_text("seed\n", encoding="utf-8")
        surface = tmp_path / "home" / "surface"
        surface.mkdir(parents=True, exist_ok=True)
        (surface / "index.md").write_text("# Surface\n", encoding="utf-8")
        (tmp_path / "home" / "knowledge").mkdir(parents=True, exist_ok=True)
        from brr import knowledge as _knowledge

        _kb = _knowledge.active_kb_dir(repo)
        if _kb is not None:
            Path(_kb).mkdir(parents=True, exist_ok=True)
            (Path(_kb) / "index.md").write_text("# kb\n", encoding="utf-8")
        monkeypatch.setattr(kb_preflight, "scan", lambda _root, _kb=None: [])
        monkeypatch.setattr(
            kb_health, "compute_graph_stats",
            lambda _root, _kb=None: kb_health.GraphStats(
                total_pages=0, total_bytes=0
            ),
        )
        return repo

    def test_a_triggerless_entry_is_named_in_the_rendered_block(
        self, tmp_path, monkeypatch
    ):
        """The headline case: the entry title and the file, on screen."""
        from brr.prompts import _build_notes_health_block

        repo = self._repo(tmp_path, monkeypatch)
        _seed_pitfalls(
            repo,
            "## Seeing unshipped frontend — the probe config\n"
            "Read the probe before trusting the screenshot.\n",
        )

        block = _build_notes_health_block(repo)

        assert "inert-pitfall" in block
        assert _says(block, "Seeing unshipped frontend — the probe config")
        assert "pitfalls.md" in block
        assert "[info]" in block

    def test_a_file_whose_entries_all_have_triggers_renders_nothing(
        self, tmp_path, monkeypatch
    ):
        """**The bar most likely to regress.** A clean store renders no
        line, no empty section, no block at all — #623: a guard that fires
        every wake for a non-reason stops being read, and takes the wakes
        where it *is* a reason down with it."""
        from brr.prompts import _build_notes_health_block

        repo = self._repo(tmp_path, monkeypatch)
        _seed_pitfalls(
            repo,
            "## Docker rebuild\ntrigger: docker\nRebuild before you test.\n\n"
            "## Quota flicker\ntrigger: quota, budget\nCheck the provider.\n",
        )

        assert _build_notes_health_block(repo) == ""

    def test_no_pitfalls_file_at_all_renders_nothing(
        self, tmp_path, monkeypatch
    ):
        from brr.prompts import _build_notes_health_block

        repo = self._repo(tmp_path, monkeypatch)

        assert _build_notes_health_block(repo) == ""

    def test_it_is_a_notice_not_a_refusal(self, tmp_path, monkeypatch):
        """A resident drafting a body before its triggers must still be able
        to save the file. So the inert entry is *reported* while the rest of
        the store keeps working: its neighbour still fires, and the inert
        entry itself is still parsed rather than dropped."""
        from brr import pitfalls
        from brr.prompts import _build_notes_health_block, _build_pitfalls_block

        repo = self._repo(tmp_path, monkeypatch)
        _seed_pitfalls(
            repo,
            "## Half-drafted\nBody written, triggers not chosen yet.\n\n"
            "## Docker rebuild\ntrigger: docker\nRebuild before you test.\n",
        )

        assert "Half-drafted" in _build_notes_health_block(repo)

        fired = _build_pitfalls_block(repo, "rebuild the docker image")
        assert "Docker rebuild" in fired
        assert "Half-drafted" not in fired

        parsed = pitfalls.parse_pitfalls(dominion.dominion_path(repo))
        assert [p.title for p in parsed] == ["Half-drafted", "Docker rebuild"]


class TestPitfallCitesClosedIssueReachesTheWake:
    """`pitfall-cites-closed-issue`, end to end: real dominion resolution,
    real parse, real cache read, through the same wake-time block
    `TestInertPitfallReachesTheWake` exercises for its sibling finding.
    """

    def _write_issue_cache(self, repo, issues: dict) -> None:
        from brr import forge_issue_cache

        path = forge_issue_cache.cache_path(repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"fetched_at": "2026-09-01T00:00:00Z", "issues": issues}),
            encoding="utf-8",
        )

    def test_a_closed_citation_reaches_the_wake_block(self, tmp_path, monkeypatch):
        from brr.prompts import _build_notes_health_block

        repo = TestInertPitfallReachesTheWake._repo(tmp_path, monkeypatch)
        _seed_pitfalls(
            repo,
            "## Host strand clone shape\ntrigger: strand\nFixed by #1298.\n",
        )
        self._write_issue_cache(repo, {
            "1298": {"number": 1298, "state": "CLOSED", "closed_at": "2026-08-05T00:00:00Z"},
        })

        block = _build_notes_health_block(repo)

        assert "pitfall-cites-closed-issue" in block
        assert "1 entry cites a closed ticket" in block
        assert "`brnrd notes check`" in block
        # The title and the ticket stay on the Finding (`brnrd notes check`
        # prints it). The wake line is the count.
        assert "Host strand clone shape" not in block
        assert "#1298" not in block

    def test_a_retired_entry_stays_quiet_even_citing_the_same_closed_ticket(
        self, tmp_path, monkeypatch
    ):
        """The exact shape this dominion's own store carries: a retirement
        notice explaining itself by quoting the now-closed ticket must never
        re-fire — see notes_preflight.is_retired."""
        from brr.prompts import _build_notes_health_block

        repo = TestInertPitfallReachesTheWake._repo(tmp_path, monkeypatch)
        _seed_pitfalls(
            repo,
            "## (retired 2026-09-01) A host-env parent…\n"
            "trigger: spawn, strand\n"
            "Retired — #1298 is closed, the clone shape fixed it.\n",
        )
        self._write_issue_cache(repo, {
            "1298": {"number": 1298, "state": "CLOSED", "closed_at": "2026-08-05T00:00:00Z"},
        })

        assert _build_notes_health_block(repo) == ""

    def test_an_open_citation_and_an_absent_cache_are_both_silent(
        self, tmp_path, monkeypatch
    ):
        from brr.prompts import _build_notes_health_block

        repo = TestInertPitfallReachesTheWake._repo(tmp_path, monkeypatch)
        _seed_pitfalls(repo, "## Still tracked\ntrigger: x\nSee #1298.\n")
        # No forge_issue_cache write at all — absent, must never read closed.

        assert _build_notes_health_block(repo) == ""

    def test_scan_scoped_carries_the_same_finding(self, tmp_path, monkeypatch):
        """Drives `notes_preflight.scan_scoped` directly — the function the
        block above wraps — so this survives independently of the block's
        own prose."""
        from brr import config as conf
        from brr import notes_preflight

        repo = TestInertPitfallReachesTheWake._repo(tmp_path, monkeypatch)
        _seed_pitfalls(repo, "## Host strand clone shape\ntrigger: x\nFixed by #1298.\n")
        self._write_issue_cache(repo, {
            "1298": {"number": 1298, "state": "CLOSED", "closed_at": "2026-08-05T00:00:00Z"},
        })

        findings, _scope = notes_preflight.scan_scoped(repo, conf.load_config(repo))
        matching = [f for f in findings if f.type == "pitfall-cites-closed-issue"]
        assert len(matching) == 1
        assert "#1298" in matching[0].description


        # No sanity assertion on `named`: zero mentions is a legitimate state
        # for this one, and asserting non-empty would force a prompt to keep
        # naming the command forever.


class TestWakeManifest:
    """write_wake_manifest produces a valid topology from the real assembler.

    Fixture produced by build_daemon_prompt_with_score → write_wake_manifest,
    not by hand-crafted JSON. This test exercises the full assembly path so
    any accounting change that breaks the manifest schema is caught here.
    """

    def test_write_wake_manifest_from_real_assembler(self, tmp_path):
        import json

        from brr.prompts import build_daemon_prompt_with_score
        from brr.run import Run
        from brr import run_context

        _VALID_STORES = {
            "product-prompt", "dominion", "surface",
            "knowledge", "runtime-generated", "synthesized",
        }

        _, score = build_daemon_prompt_with_score(
            "check the thing", "evt-mfst-1", "/tmp/resp.md", tmp_path,
            run_id="run-mfst-test",
        )

        # Fabricate a minimal Run so write_wake_manifest can derive the path.
        run = Run(
            id="run-mfst-test",
            event_id="evt-mfst-1",
            body="",
            source="test",
            status="running",
        )

        brr_dir = tmp_path / ".brr"
        brr_dir.mkdir(exist_ok=True)
        path = run_context.write_wake_manifest(brr_dir, run, score)

        assert path is not None and path.exists(), "manifest file was not written"

        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["schema_version"] == "1"
        assert manifest["run_id"] == "run-mfst-test"
        blocks = manifest["blocks"]
        assert isinstance(blocks, list) and blocks, "blocks list must be non-empty"

        required_fields = {"name", "label", "present", "sources", "bytes_kept",
                           "bytes_cut", "budget_bytes", "trim_kind"}
        for block in blocks:
            assert required_fields <= block.keys(), (
                f"block {block.get('name')!r} missing fields: "
                f"{required_fields - block.keys()}"
            )
            sources = block["sources"]
            assert isinstance(sources, list) and sources, (
                f"block {block.get('name')!r} has empty sources"
            )
            for src in sources:
                if src.get("synthesized"):
                    continue
                store = src.get("store")
                assert store in _VALID_STORES, (
                    f"block {block.get('name')!r} has unknown store {store!r}"
                )

        # The boot-kernel block must be present and synthesized.
        kernel = next(
            (b for b in blocks if b.get("name") == "boot-kernel"), None
        )
        assert kernel is not None, "boot-kernel block missing from manifest"
        assert kernel["sources"] == [{"synthesized": True}], (
            "boot-kernel must be synthesized (no file source)"
        )

        # At least one non-synthesized block should have a known store.
        file_blocks = [
            b for b in blocks
            if b.get("present")
            and not b["sources"][0].get("synthesized")
        ]
        assert file_blocks, "expected at least one file-backed present block"
        for b in file_blocks:
            assert b["sources"][0].get("store") in _VALID_STORES

    def test_write_wake_manifest_absent_blocks_still_appear(self, tmp_path):
        """ContractEntry rows with present=False appear in the manifest.

        An absent block has bytes_kept=null (it was never rendered) and
        bytes_cut=null. The manifest must record it so a reader can see the
        full scope of what was in play, not just what entered the prompt.
        """
        import json

        from brr.prompts import build_daemon_prompt_with_score
        from brr.run import Run
        from brr import run_context

        _, score = build_daemon_prompt_with_score(
            "bare minimum wake", "evt-mfst-2", "/tmp/resp.md", tmp_path,
            run_id="run-mfst-2",
        )

        run = Run(
            id="run-mfst-2",
            event_id="evt-mfst-2",
            body="",
            source="test",
            status="running",
        )
        brr_dir = tmp_path / ".brr"
        brr_dir.mkdir(exist_ok=True)
        path = run_context.write_wake_manifest(brr_dir, run, score)
        assert path is not None

        manifest = json.loads(path.read_text(encoding="utf-8"))
        blocks = manifest["blocks"]

        absent = [b for b in blocks if not b.get("present")]
        # A minimal tmp_path repo has no dominion, no kb, no knowledge —
        # those blocks should be absent.
        assert absent, (
            "expected some absent blocks (missing dominion/kb on a fresh repo)"
        )
        for b in absent:
            # Absent blocks that were measured during assembly have bytes_kept=0
            # (ContractEntry.bytes == 0 means "in scope but rendered empty").
            # bytes_kept=None only appears for unrendered scores (e.g. brnrd
            # prompts show without assembly). Both are valid.
            kept = b["bytes_kept"]
            assert kept is None or isinstance(kept, int), (
                f"absent block {b.get('name')!r} bytes_kept should be int or null, "
                f"got {kept!r}"
            )


class TestWakeBlocksSidecar:
    """`_block_text_sink` / `write_wake_blocks` — #1830.

    Before this, a home-originated block (dominion self-inject, work
    surface, pitfalls, knowledge slices, the plan page — `location ==
    "computed"`) had no file on disk carrying its exact rendered text,
    mounted or not. These pin the write side: every present block
    (including the kernel and the trailer, which never pass through
    `_take`) lands in the sink, a mounted block's sink entry matches
    `mount_sink` exactly (not the raw prose it was built from), and
    `write_wake_manifest`'s `rendered_bytes` agrees with the sidecar.
    """

    def test_sink_covers_kernel_and_trailer_when_unmounted(self, tmp_path):
        from brr.prompts import build_daemon_prompt_with_score

        sink: dict[str, str] = {}
        prompt, score = build_daemon_prompt_with_score(
            "check the thing", "evt-wb-1", "/tmp/resp.md", tmp_path,
            run_id="run-wb-1", _block_text_sink=sink,
        )
        assert "boot-kernel" in sink and sink["boot-kernel"] in prompt
        assert "run-context-bundle" in sink and sink["run-context-bundle"] in prompt
        # Every block the manifest says is present and prose (not mounted —
        # nothing is, this call passed no `_mount_sink`) must have an exact
        # entry, keyed the same as the manifest names it.
        for entry in score.contracts:
            if entry.present and entry.bytes:
                assert entry.block_key in sink, (
                    f"present block {entry.block_key!r} missing from the sink"
                )

    def test_sink_entry_matches_mount_sink_for_a_mounted_block(self, tmp_path):
        """A mounted block's sink text is the *mounted* text, never the raw
        prose `_take` was handed — the two can differ (a curated extract vs.
        the whole file), and only the mounted value is what this wake
        actually received."""
        from brr.prompts import build_daemon_prompt_with_score

        mount_sink: dict[str, str] = {}
        block_sink: dict[str, str] = {}
        prompt, score = build_daemon_prompt_with_score(
            "check the thing", "evt-wb-2", "/tmp/resp.md", tmp_path,
            run_id="run-wb-2", _mount_sink=mount_sink, _block_text_sink=block_sink,
        )
        assert mount_sink, "expected at least one file-backed block to mount on a real checkout"
        for key, mounted_text in mount_sink.items():
            assert block_sink.get(key) == mounted_text, (
                f"{key!r}: block_text_sink drifted from mount_sink"
            )
            # A mounted block leaves the prose entirely (`_take` returns
            # `None` for it) — its text must not also sit in `prompt`.
            assert mounted_text not in prompt

    def test_write_wake_blocks_and_manifest_rendered_bytes_agree(self, tmp_path):
        import json as _json

        from brr.prompts import build_daemon_prompt_with_score
        from brr.run import Run
        from brr import run_context

        sink: dict[str, str] = {}
        _, score = build_daemon_prompt_with_score(
            "check the thing", "evt-wb-3", "/tmp/resp.md", tmp_path,
            run_id="run-wb-3", _block_text_sink=sink,
        )
        run = Run(
            id="run-wb-3", event_id="evt-wb-3", body="", source="test", status="running",
        )
        brr_dir = tmp_path / ".brr"
        brr_dir.mkdir(exist_ok=True)

        blocks_path = run_context.write_wake_blocks(brr_dir, run, sink)
        assert blocks_path is not None and blocks_path.exists()
        blocks_data = _json.loads(blocks_path.read_text(encoding="utf-8"))
        assert blocks_data["schema_version"] == "1"
        assert blocks_data["run_id"] == "run-wb-3"
        assert blocks_data["blocks"] == sink
        assert "boot-kernel" in blocks_data["blocks"]

        manifest_path = run_context.write_wake_manifest(brr_dir, run, score, wake_blocks=sink)
        manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
        kernel_row = next(b for b in manifest["blocks"] if b["name"] == "boot-kernel")
        assert kernel_row["rendered_bytes"] == len(sink["boot-kernel"].encode("utf-8"))
        assert kernel_row["rendered_bytes"] == kernel_row["bytes_kept"]

        # Omitting `wake_blocks` (the pre-#1830 call shape) must still work
        # and null every row, never raise.
        legacy_path = run_context.write_wake_manifest(brr_dir, run, score)
        legacy_manifest = _json.loads(legacy_path.read_text(encoding="utf-8"))
        assert all(b["rendered_bytes"] is None for b in legacy_manifest["blocks"])

    def test_write_wake_manifest_strand_light_profile_names_dropped_blocks(
        self, tmp_path
    ):
        """A strand's light profile drops most of the resident inject stack —
        the manifest must say so, not just leave the row out.

        Every dropped block (dominion, hearth, work surface, runner policy,
        prior-run, kb/notes health) still appears with ``present: false`` and
        ``lens: "profile:strand · skipped"`` (#<the-strand-that-wakes-light>);
        the blocks the light profile keeps (identity core, knowledge sources,
        pitfalls) carry ``"profile:strand · kept"``, and the trimmed one
        (recent activity) carries ``"profile:strand · trimmed"``.
        """
        import json

        from brr.prompts import build_daemon_prompt_with_score
        from brr.run import Run
        from brr import run_context

        _, score = build_daemon_prompt_with_score(
            "check the thing", "evt-mfst-3", "/tmp/resp.md", tmp_path,
            run_id="run-mfst-3",
            strand=True,
        )
        run = Run(
            id="run-mfst-3", event_id="evt-mfst-3", body="",
            source="test", status="running",
        )
        brr_dir = tmp_path / ".brr"
        brr_dir.mkdir(exist_ok=True)
        path = run_context.write_wake_manifest(brr_dir, run, score)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        by_name = {b["name"]: b for b in manifest["blocks"]}

        for dropped in (
            "dominion", "hearth", "work-surface", "runner-policy",
            "prior-run", "kb-health", "notes-health", "relabelled-repo",
        ):
            assert by_name[dropped]["present"] is False
            assert by_name[dropped]["lens"] == "profile:strand · skipped"

        assert by_name["identity-core"]["present"] is True
        assert by_name["identity-core"]["lens"] == "profile:strand · kept"
        assert by_name["knowledge-sources"]["lens"] == "profile:strand · kept"
        assert by_name["pitfalls"]["lens"] == "profile:strand · kept"
        assert by_name["recent-activity"]["lens"] == "profile:strand · trimmed"

        # Non-strand blocks (preamble, bundle) carry no profile lens at all.
        assert by_name["strand-preamble"]["lens"] is None

    def test_write_wake_manifest_strand_full_profile_carries_no_lens(
        self, tmp_path
    ):
        """`strand.wake_profile=full` is byte-identical to a resident's own
        inject stack, so nothing in it is profile-narrowed — no block should
        carry a `lens` value."""
        import json

        from brr.prompts import build_daemon_prompt_with_score
        from brr.run import Run
        from brr import run_context

        (tmp_path / ".brr").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".brr" / "config").write_text(
            "strand.wake_profile=full\n", encoding="utf-8"
        )
        _, score = build_daemon_prompt_with_score(
            "check the thing", "evt-mfst-4", "/tmp/resp.md", tmp_path,
            run_id="run-mfst-4",
            strand=True,
        )
        run = Run(
            id="run-mfst-4", event_id="evt-mfst-4", body="",
            source="test", status="running",
        )
        brr_dir = tmp_path / ".brr"
        path = run_context.write_wake_manifest(brr_dir, run, score)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert all(b["lens"] is None for b in manifest["blocks"])


