"""Tests for the prompt-assembly module."""

import difflib

from brr import dominion
from brr.prompts import (
    SCHEDULE_TURN_DEDUP_RATIO,
    TrimResult,
    _build_context_block,
    _build_identity_core_block,
    _build_injected_blocks_with_contracts,
    _build_runner_policy_block,
    _build_work_surface_block,
    _build_work_surface_block_scored,
    _entry_key,
    _format_recent_conversation,
    _read_recent_log,
    _tail_trim_entries,
    _worst_trim,
    build_daemon_prompt,
    build_run_prompt,
    diffense_emit_enabled,
)


def _seed_pitfalls(repo_root, text: str) -> None:
    """Materialize a dominion dir with a ``pitfalls.md`` for prompt tests."""
    dom = dominion.dominion_path(repo_root)
    dom.mkdir(parents=True, exist_ok=True)
    (dom / "pitfalls.md").write_text(text, encoding="utf-8")


class TestContextInjection:
    def test_read_recent_log_missing(self, tmp_path):
        assert _read_recent_log(tmp_path).text == ""

    def test_read_recent_log_falls_back_to_home_knowledge(self, tmp_path):
        """A repo that migrated kb/ out per design-home-scopes-and-knowledge.md

        still gets its recent-activity block from home knowledge instead of
        going silent just because ``kb/log.md`` no longer lives in the tree.
        """
        from _helpers import init_git_repo

        repo = tmp_path / "repo"
        init_git_repo(repo)
        home = tmp_path / "home"
        (repo / ".brr").mkdir()
        (repo / ".brr" / "config").write_text(f"home.path={home}\n", encoding="utf-8")
        (home / "knowledge").mkdir(parents=True)
        (home / "knowledge" / "log.md").write_text(
            "# Activity Log\n\n## [2026-07-09] migrate | kb moved\n\nOut of the tree.\n",
            encoding="utf-8",
        )

        result = _read_recent_log(repo).text

        assert "## [2026-07-09]" in result
        assert "Out of the tree" in result

    def test_read_recent_log_prefers_repo_kb_when_both_exist(self, tmp_path):
        from _helpers import init_git_repo

        repo = tmp_path / "repo"
        init_git_repo(repo)
        home = tmp_path / "home"
        (repo / ".brr").mkdir()
        (repo / ".brr" / "config").write_text(f"home.path={home}\n", encoding="utf-8")
        (home / "knowledge").mkdir(parents=True)
        (home / "knowledge" / "log.md").write_text(
            "# Activity Log\n\n## [2026-01-01] plan | home only\n\nHome copy.\n",
            encoding="utf-8",
        )
        (repo / "kb").mkdir()
        (repo / "kb" / "log.md").write_text(
            "# Activity Log\n\n## [2026-07-09] plan | repo copy\n\nStill in the tree.\n",
            encoding="utf-8",
        )

        result = _read_recent_log(repo).text

        assert "repo copy" in result
        assert "home only" not in result

    def test_read_recent_log_basic(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "log.md").write_text(
            "# Activity Log\n\n"
            "## [2026-04-07] implement | Setup\n\nDid setup.\n\n"
            "## [2026-04-08] plan | Design\n\nDesigned stuff.\n"
        )
        result = _read_recent_log(tmp_path).text
        assert "## [2026-04-07]" in result
        assert "## [2026-04-08]" in result

    def test_read_recent_log_truncates(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        entries = "\n\n".join(
            f"## [2026-04-{i:02d}] implement | Entry {i}\n\nDid thing {i}."
            for i in range(1, 16)
        )
        (kb / "log.md").write_text(f"# Log\n\n{entries}\n")
        result = _read_recent_log(tmp_path, max_entries=3).text
        assert "Entry 13" in result
        assert "Entry 14" in result
        assert "Entry 15" in result
        assert "Entry 1\n" not in result

    def test_read_recent_log_byte_budget_stops_at_first_overflow(self, tmp_path):
        """A byte budget caps how much the conversation block can grow.

        Older entries are dropped (newest-first) once adding the next
        one would exceed the budget. The newest entry is always
        included so the most recent context never silently disappears,
        even if it alone exceeds the budget.
        """
        kb = tmp_path / "kb"
        kb.mkdir()
        bulk = "x" * 600
        entries = "\n\n".join(
            f"## [2026-04-{i:02d}] implement | Entry {i}\n\n{bulk}"
            for i in range(1, 6)
        )
        (kb / "log.md").write_text(f"# Log\n\n{entries}\n")
        # ~700 bytes per entry → ~1500-byte budget admits exactly 2.
        result = _read_recent_log(tmp_path, max_entries=10, max_bytes=1500).text
        assert "Entry 5" in result
        assert "Entry 4" in result
        assert "Entry 3" not in result
        assert "Entry 1" not in result
        # Oldest of the included pair comes first (natural reading order).
        assert result.index("Entry 4") < result.index("Entry 5")

    def test_read_recent_log_byte_budget_keeps_newest_even_when_oversized(
        self, tmp_path,
    ):
        """When the single newest entry exceeds the budget, brr still
        includes it. Silent dropping of the most recent context would
        be worse than a slightly oversized prompt."""
        kb = tmp_path / "kb"
        kb.mkdir()
        huge = "x" * 5000
        (kb / "log.md").write_text(
            "# Log\n\n## [2026-05-01] implement | Big\n\n" + huge + "\n"
        )
        result = _read_recent_log(tmp_path, max_bytes=512).text
        assert "Big" in result
        assert huge in result

    def test_context_block_empty(self, tmp_path):
        assert _build_context_block(tmp_path) == ""

    def test_context_block_with_log(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "log.md").write_text(
            "# Log\n\n## [2026-04-08] plan | Test\n\nTest entry.\n"
        )
        block = _build_context_block(tmp_path)
        assert "Recent Activity" in block
        assert "## [2026-04-08]" in block


# ── P1 — per-block content attestation ──────────────────────────────────
#
# review-boot-prompts-2026-07.md §P1: `_tail_trim_entries` already knows
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


def _ledger_entry(date: str, run_id: str, bulk: int) -> str:
    """One ``## `` decision-ledger-style entry, padded to a controllable size."""
    return f"## Decision ({date}, {run_id})\n\n" + ("x" * bulk)


class TestBlockAttestation:
    def test_fits_whole_is_untouched_no_attestation(self):
        """A block that fits whole is untouched — byte-identical, no claim.

        Pinned hard per the spec: this is the regression that would bite
        every wake, since almost every injected block never needs trimming.
        """
        content = "# Page\n\n## [2026-07-01] note | small\n\nfits fine.\n"
        result = _tail_trim_entries(content, max_bytes=10_000, source_hint="`x`")
        assert result.text == content
        assert result.newest_item is None
        assert result.oldest_item is None
        assert result.dropped is None
        assert result.source_newest is None
        assert result.stale is False

    def test_drifted_order_is_stale(self):
        """The real 2026-07-23 ledger-inversion bug, reproduced at day granularity.

        File order top-to-bottom: an entry dated 07-21, one dated 07-23 (the
        true newest), one dated 07-22 sitting *last* — exactly the ordering
        the real incident had (a numerically-earlier entry positioned below
        a later one). The budget admits only the bottom entry, so the trim
        keeps 07-22 as "newest" while the source's true newest (07-23) sat
        one heading above the cut.
        """
        e1 = _ledger_entry("2026-07-21", "run-260721-0900-aaaa", 200)
        e2 = _ledger_entry("2026-07-23", "run-260723-1342-isd1", 200)
        e3 = _ledger_entry("2026-07-22", "run-260723-1131-r0h4", 200)
        content = "\n\n".join([e1, e2, e3]) + "\n"
        budget = len(e3.encode("utf-8")) + 10  # admits only the bottom entry

        result = _tail_trim_entries(content, max_bytes=budget, source_hint="`surface/ledger/decisions.md`")

        assert result.newest_item == "2026-07-22"
        # Time rendered on the source side because the entry that *is* the
        # source's newest carries a corroborated run-id. The tail's own newest
        # (07-22, whose run-id contradicts its heading) still renders bare —
        # each side shows only the precision it actually has. The verdict is
        # the day comparison either way; this is display, not evidence.
        assert result.source_newest == "2026-07-23 13:42"
        assert result.dropped == 2
        assert result.stale is True
        assert "NOT current" in result.text
        assert "2026-07-23" in result.text  # the drifted-in source date is named

    def test_in_order_is_not_stale(self):
        """Ascending file order (the healthy, intended shape) never flags stale.

        Budget admits the bottom two entries; the newest kept (07-23) really
        is the source's newest, so the range stamp renders clean.
        """
        e1 = _ledger_entry("2026-07-21", "run-260721-0900-aaaa", 200)
        e2 = _ledger_entry("2026-07-22", "run-260722-1000-bbbb", 200)
        e3 = _ledger_entry("2026-07-23", "run-260723-1100-cccc", 200)
        content = "\n\n".join([e1, e2, e3]) + "\n"
        budget = len(e2.encode("utf-8")) + len(e3.encode("utf-8")) + 10

        result = _tail_trim_entries(content, max_bytes=budget, source_hint="`surface/ledger/decisions.md`")

        # Every entry carries a corroborated run-id time, so the comparison
        # ran at full precision and the rendered stamp says so.
        assert result.newest_item == "2026-07-23 11:00"
        assert result.oldest_item == "2026-07-22 10:00"
        assert result.source_newest == "2026-07-23 11:00"
        assert result.dropped == 1
        assert result.stale is False
        assert result.precise is True
        assert "the newest entry in the source" in result.text
        assert "NOT current" not in result.text

    def test_the_actual_2026_07_23_incident_same_day_inversion(self):
        """**The incident this whole feature is named after**, pinned literally.

        `run-260723-1131-r0h4` sat *below* `run-260723-1342-isd1` in the
        decision ledger. Both entries are dated 2026-07-23, so at day
        granularity `source_newest` and `newest_item` are the identical
        string and the drift is invisible. The first cut of this feature
        shipped exactly that blind spot — and worse than blind: its trim
        marker actively asserted "the newest entry in the source" over a
        tail that was not. A guard that reassures you about the one bug it
        was built for is worse than no guard.

        Run-id times make the two entries comparable, so this must be stale.
        """
        e1 = _ledger_entry("2026-07-23", "run-260723-1239-aaaa", 200)
        newer = _ledger_entry("2026-07-23", "run-260723-1342-isd1", 200)
        older = _ledger_entry("2026-07-23", "run-260723-1131-r0h4", 200)
        content = "\n\n".join([e1, newer, older]) + "\n"
        budget = len(older.encode("utf-8")) + 10

        result = _tail_trim_entries(content, max_bytes=budget, source_hint="`x`")

        assert result.stale is True, "the same-day inversion must be caught"
        assert result.precise is True
        assert result.newest_item == "2026-07-23 11:31"
        assert result.source_newest == "2026-07-23 13:42"
        # The alarm must be *readable*: a correct warning rendered as
        # "newest 2026-07-23 — source has 2026-07-23" is not much better
        # than the silence it replaced.
        assert "NOT current" in result.text
        assert "13:42" in result.text and "11:31" in result.text

    def test_same_day_without_times_refuses_to_reassure(self):
        """No corroborated times + a same-day tie ⇒ narrow the claim, don't make it.

        The trim cannot know whether the tail is current, so it says exactly
        that. It must not print "the newest entry in the source" — asserting
        something unverified is the defect P1 exists to abolish, and the guard
        is not exempt from it.
        """
        e1 = "## A (2026-07-23)\n\n" + ("x" * 200)
        e2 = "## B (2026-07-23)\n\n" + ("y" * 200)
        e3 = "## C (2026-07-23)\n\n" + ("z" * 200)
        content = "\n\n".join([e1, e2, e3]) + "\n"

        result = _tail_trim_entries(content, max_bytes=len(e3.encode()) + 10, source_hint="`x`")

        assert result.stale is False
        assert result.precise is False
        assert "the newest entry in the source" not in result.text
        assert "day precision" in result.text
        assert "NOT current" not in result.text

    # ── #670 — a run-id is a complete instant ────────────────────────────
    #
    # `_heading_time` used to return `None` whenever a run-id's date
    # disagreed with its heading's, so the entry counted as untimed and held
    # its whole cohort at day precision. The premise was right — "a heading
    # date paired with some other day's clock time is not a timestamp, it is
    # two facts glued together" — and the remedy dropped the wrong half:
    # `run-260723-2353` is *by itself* a complete instant, and ordering wants
    # the write time. The splice is refused where it was always the hazard
    # (display), not where it never was (ordering).
    #
    # These fixtures are the shape production actually emits: a run that
    # starts at 23:53 and writes its ledger entry after midnight dates the
    # heading by the day it is writing about. Driven on the live ledger
    # 2026-07-24: 15 such entries out of 177.

    def test_a_cross_midnight_runid_orders_by_its_own_instant(self):
        """The write instant comes from the run-id whole, heading date or not.

        Ordering key, not display: the 23:53 entry sorts *before* the 00:59
        one though both headings read 2026-07-24, and the heading date is
        what each still reports.
        """
        crossed = _entry_key(_ledger_entry("2026-07-24", "run-260723-2353-4hic", 10))
        after = _entry_key(_ledger_entry("2026-07-24", "run-260724-0059-pe1x", 10))

        assert (crossed.order_date, crossed.order_time) == ("2026-07-23", "2353")
        assert (after.order_date, after.order_time) == ("2026-07-24", "0059")
        assert crossed.order_date < after.order_date, "23:53 was written first"

        # Display never glues: the heading's date is the editorial fact, and
        # the run-id's clock may only sit beside a date it belongs to.
        assert crossed.shown_date == "2026-07-24"
        assert crossed.shown_time is None
        assert after.shown_time == "0059"

    def test_cross_midnight_entry_no_longer_holds_the_cohort_at_day_precision(self):
        """#670's acceptance, at the shape the live ledger has every day.

        Before: the 23:53 entry read as untimed, so the whole 2026-07-24
        cohort fell to day precision and the marker had to hedge — on an
        hourly-tick account, most days. After: every entry on the newest
        *write* date carries a clock, so the strong tier fires.
        """
        crossed = _ledger_entry("2026-07-24", "run-260723-2353-4hic", 200)
        early = _ledger_entry("2026-07-24", "run-260724-0059-pe1x", 200)
        latest = _ledger_entry("2026-07-24", "run-260724-1220-vzco", 200)
        content = "\n\n".join([crossed, early, latest]) + "\n"
        budget = len(early.encode("utf-8")) + len(latest.encode("utf-8")) + 10

        result = _tail_trim_entries(content, max_bytes=budget, source_hint="`x`")

        assert result.precise is True
        assert result.stale is False
        assert result.newest_item == "2026-07-24 12:20"
        assert result.oldest_item == "2026-07-24 00:59"
        assert "the newest entry in the source" in result.text
        assert "day precision" not in result.text

    def test_a_cross_midnight_tail_below_a_later_entry_is_caught_as_stale(self):
        """The inversion the old key was structurally blind to.

        The source's true newest was written at 00:59; the tail kept the
        entry written at 23:53 the previous evening. Both headings read
        2026-07-24, and under corroboration-gating the 23:53 entry was
        untimed — so ``precise`` was ``False``, the same-day tier could not
        run, and the trim reported "not known to be stale" over a tail that
        demonstrably was.
        """
        newer = _ledger_entry("2026-07-24", "run-260724-0059-pe1x", 200)
        older = _ledger_entry("2026-07-24", "run-260723-2353-4hic", 200)
        content = "\n\n".join([newer, older]) + "\n"
        budget = len(older.encode("utf-8")) + 10  # admits only the bottom entry

        result = _tail_trim_entries(content, max_bytes=budget, source_hint="`x`")

        assert result.precise is True
        assert result.stale is True, "an ordering the run-ids decide must be decided"
        assert "NOT current" in result.text
        # Constraint the fix must not break: the reported item is still the
        # *heading* date. The tail's newest renders bare — its clock belongs
        # to 2026-07-23 and may not be printed beside 2026-07-24.
        assert result.newest_item == "2026-07-24"
        assert result.source_newest == "2026-07-24 00:59"

    def test_a_heading_with_no_runid_at_all_is_still_untimed(self):
        """The residual #670 narrows *to*, and deliberately does not close.

        No run-id means no clock was ever recorded, and none may be
        invented. Such an entry on the newest write date still holds the
        cohort at day precision — the genuinely unrepairable legacy set the
        old docstring was describing, now the only thing left in it.
        """
        legacy = "## Legacy, no run-id (2026-07-24)\n\n" + ("w" * 200)
        timed = _ledger_entry("2026-07-24", "run-260724-1220-vzco", 200)
        content = "\n\n".join([legacy, timed]) + "\n"

        result = _tail_trim_entries(
            content, max_bytes=len(timed.encode("utf-8")) + 10, source_hint="`x`"
        )

        assert result.precise is False, "a missing clock is not a recoverable one"
        assert result.stale is False
        assert "day precision" in result.text

    def test_an_untimed_entry_on_an_older_date_does_not_disable_the_same_day_tier(self):
        """Precision is scoped to the cohort that can decide the tie.

        Time only ever breaks a same-date tie, so an entry dated *earlier*
        than the source's newest is settled by the day comparison and its
        missing run-id is irrelevant. Requiring the whole file to be timed was
        strictly stronger than the proof needs, and it was not a free
        conservatism: measured on the live decision ledger — 162 entries, 55
        untimed, but only 1 untimed on the newest date — whole-file scope
        pinned ``precise`` at ``False`` permanently. Legacy headings cannot be
        repaired without inventing timestamps, so the same-day tier could
        never turn on however disciplined later writing became.

        Here the tail keeps the 11:31 entry as "newest" while the 13:42 one
        sits one heading above the cut — the exact 2026-07-23 inversion this
        feature is named after. It must fire despite the untimed 2026-07-01
        entry further down the file.
        """
        legacy = "## Legacy, no run-id (2026-07-01)\n\n" + ("w" * 200)
        newer = "## B (2026-07-23, run-260723-1342-bbbb)\n\n" + ("y" * 200)
        older = "## A (2026-07-23, run-260723-1131-aaaa)\n\n" + ("z" * 200)
        content = "\n\n".join([legacy, newer, older]) + "\n"

        result = _tail_trim_entries(
            content, max_bytes=len(older.encode("utf-8")) + 10, source_hint="`x`"
        )

        assert result.precise is True, "an older untimed entry cannot decide a tie"
        assert result.stale is True
        assert "NOT current" in result.text
        assert "13:42" in result.text and "11:31" in result.text

    def test_undated_headings_not_attestable_no_crash(self):
        """A heading with no parseable date makes the whole trim not-attestable.

        Never guessed, never inferred from position (the playbook invariant
        this feature exists to satisfy). ``dropped`` alone survives — a
        count needs no date — but no date claim renders, and nothing raises.
        """
        e1 = "## Some title\n\n" + ("x" * 200)
        e2 = "## Another title\n\n" + ("y" * 200)
        content = e1 + "\n\n" + e2 + "\n"
        budget = len(e2.encode("utf-8")) + 10

        result = _tail_trim_entries(content, max_bytes=budget, source_hint="`x`")

        assert result.newest_item is None
        assert result.oldest_item is None
        assert result.source_newest is None
        assert result.stale is False
        assert result.dropped == 1
        assert "showing" not in result.text
        assert "cut to fit the wake budget" in result.text

    def test_no_headings_flat_cut_has_no_entry_attestation(self):
        """The flat byte-cut fallback (no ``## `` at all) carries no entry facts.

        A count of entries removed presupposes entries; a page with none
        falls back to the pre-existing flat tail cut, untouched by P1.
        """
        content = "just prose, no headings, " * 200
        result = _tail_trim_entries(content, max_bytes=100, source_hint="`x`")
        assert result.dropped is None
        assert result.newest_item is None
        assert result.stale is False

    def test_read_recent_log_reports_dropped_without_going_stale(self, tmp_path):
        """``_read_recent_log`` computes the same facts, but by construction
        never flags stale: it always walks newest-first off file *and*
        picks the true tail, so a healthy ascending log never drifts. (The
        residual risk this class guards is `_tail_trim_entries`'s pages —
        see ``review-boot-prompts-2026-07.md`` §P1.)
        """
        kb = tmp_path / "kb"
        kb.mkdir()
        entries = "\n\n".join(
            f"## [2026-04-{i:02d}] implement | Entry {i}\n\nDid thing {i}."
            for i in range(1, 6)
        )
        (kb / "log.md").write_text(f"# Log\n\n{entries}\n")
        result = _read_recent_log(tmp_path, max_entries=2)
        assert result.dropped == 3
        assert result.newest_item == "2026-04-05"
        assert result.source_newest == "2026-04-05"
        assert result.stale is False

    def test_worst_trim_prefers_stale_over_a_bigger_healthy_drop(self):
        """A stale page outranks a healthy one even when the healthy page
        dropped *more* entries — the alarm exists to catch staleness, and a
        "most dropped" tiebreak alone would silently prefer the wrong page.
        """
        healthy = TrimResult(
            text="h", newest_item="2026-07-23", oldest_item="2026-07-20",
            dropped=9, source_newest="2026-07-23",
        )
        # `stale` is a stored fact, not a re-derivation from these two display
        # dates — same-day drift is real and invisible at date granularity, so
        # the flag has to be set by whoever still held the times.
        stale = TrimResult(
            text="s", newest_item="2026-07-22", oldest_item="2026-07-22",
            dropped=1, source_newest="2026-07-23", stale=True,
        )
        assert _worst_trim([healthy, stale]) is stale
        assert _worst_trim([stale, healthy]) is stale  # order-independent

    def test_worst_trim_falls_back_to_most_dropped_when_nothing_is_stale(self):
        small = TrimResult(text="a", dropped=1)
        big = TrimResult(text="b", dropped=5)
        assert _worst_trim([small, big]) is big

    def test_worst_trim_empty_when_nothing_was_trimmed(self):
        untouched = TrimResult(text="whole")
        result = _worst_trim([untouched])
        assert result.text == ""
        assert result.dropped is None


class TestPromptBuilding:
    def test_run_prompt_includes_identity_core_before_dominion_and_task(
        self, tmp_path,
    ):
        dom = dominion.dominion_path(tmp_path)
        dom.mkdir(parents=True)
        (dom / "self-inject").write_text("full playbook.md\n", encoding="utf-8")
        (dom / "playbook.md").write_text("# Living Playbook\n", encoding="utf-8")

        prompt = build_run_prompt("do something", tmp_path)

        assert "Resident Identity Core" in prompt
        assert "product-owned identity contract" in prompt
        assert "Voice And The Seam" in prompt
        assert "fluency: weave | prose" in prompt
        assert "Your dominion (working memory)" in prompt
        assert prompt.index("Resident Identity Core") < prompt.index(
            "Your dominion (working memory)"
        )
        assert prompt.index("Resident Identity Core") < prompt.index("Task:")

    def test_identity_core_ignores_runtime_prompt_override(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "identity-core.md").write_text(
            "# Custom Core\n\nRuntime override.", encoding="utf-8"
        )

        block = _build_identity_core_block(tmp_path)
        assert "Resident Identity Core" in block
        assert "Runtime override" not in block

    def test_run_prompt_includes_context(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "log.md").write_text(
            "# Log\n\n## [2026-04-08] fix | Bug fix\n\nFixed a bug.\n"
        )
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_run_prompt("do something", tmp_path)
        assert "Bug fix" in prompt
        assert "do something" in prompt

    def test_run_prompt_injects_kb_health_when_findings(self, tmp_path, monkeypatch):
        """A non-clean deterministic preflight rides into the wake prompt
        so the resident folds kb fixes into its own thought (replacing
        the retired post-task kb-maintenance spawn)."""
        from brr import kb_preflight

        prompts_dir = tmp_path / ".brr" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "run.md").write_text("You are an agent.")
        monkeypatch.setattr(
            kb_preflight, "scan",
            lambda _root, _kb_dir=None: [
                kb_preflight.Finding(
                    type="missing-from-index",
                    target="kb/decision-orphan.md",
                    description="needs an index entry",
                ),
            ],
        )

        prompt = build_run_prompt("do something", tmp_path)
        assert "kb health (deterministic preflight)" in prompt
        assert "missing-from-index" in prompt
        assert "kb/decision-orphan.md" in prompt

    def test_run_prompt_omits_kb_health_when_clean(self, tmp_path, monkeypatch):
        """A clean preflight is silent — no wake-time tax."""
        from brr import kb_preflight

        prompts_dir = tmp_path / ".brr" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "run.md").write_text("You are an agent.")
        monkeypatch.setattr(kb_preflight, "scan", lambda _root, _kb_dir=None: [])

        prompt = build_run_prompt("do something", tmp_path)
        assert "kb health" not in prompt

    def test_run_prompt_kb_health_disabled_with_never(self, tmp_path, monkeypatch):
        """``kb_maintenance=never`` opts out of the wake-time inject even
        when the preflight has findings."""
        from brr import config as conf
        from brr import kb_preflight

        prompts_dir = tmp_path / ".brr" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "run.md").write_text("You are an agent.")
        monkeypatch.setattr(
            kb_preflight, "scan",
            lambda _root, _kb_dir=None: [
                kb_preflight.Finding(
                    type="broken-link", target="kb/x.md",
                    description="dangling reference",
                ),
            ],
        )
        monkeypatch.setattr(
            conf, "load_config", lambda _root: {"kb_maintenance": "never"},
        )

        prompt = build_run_prompt("do something", tmp_path)
        assert "kb health" not in prompt

    def test_diffense_emit_enabled_defaults_off(self):
        # Off by default so routine or chat-only wakes do not pay the prompt
        # and review-pack tax; opt in explicitly when the surface is wanted.
        assert not diffense_emit_enabled({})
        assert not diffense_emit_enabled(None)
        assert diffense_emit_enabled({"diffense.emit_pack": True})
        assert not diffense_emit_enabled({"diffense.emit_pack": False})
        assert diffense_emit_enabled({"diffense_emit_pack": True})
        assert not diffense_emit_enabled({"diffense_emit_pack": False})

    def test_daemon_prompt_includes_diffense_pack_when_enabled(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runtime_dir="/repo/.brr",
            diffense=True,
        )
        assert "Review pack (diffense)" in prompt
        assert "brnrd review --check" in prompt
        # The heavy publish plumbing is now inspected, not injected: the
        # block points at `brnrd docs review-pack` instead of re-narrating
        # the relay/gist/frontmatter procedure every diffense wake.
        assert "brnrd docs review-pack" in prompt
        # The pack path is explicit and absolute in the shared runtime dir
        # so it survives worktree teardown.
        assert "Review pack path: /repo/.brr/diffense/task-9/pack.json" in prompt

    def test_daemon_prompt_omits_diffense_pack_when_not_requested(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runtime_dir="/repo/.brr",
        )
        assert "Review pack (diffense)" not in prompt
        assert "Review pack path" not in prompt

    def test_daemon_prompt_surfaces_available_update(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            update_available="update available: 0.1.0 → 0.2.0",
        )

        assert "- update available: 0.1.0 → 0.2.0" in prompt

    def test_daemon_prompt_worker_excludes_resident_stack(self, tmp_path):
        # A pitfall would normally surface for a matching task — confirm
        # the worker path skips the injected blocks entirely, not just the
        # ones that happen to be empty in this fixture.
        _seed_pitfalls(
            tmp_path,
            "## Blind retry\ntrigger: docker\n"
            "Rebuild the image before you trust the cache.\n",
        )
        prompt = build_daemon_prompt(
            "rebuild the docker image and ship", "evt-1", "/tmp/resp.md",
            tmp_path,
            run_id="task-9",
            worker=True,
        )
        assert "Resident Identity Core" not in prompt
        assert "Pitfalls that match this task" not in prompt
        assert "Rebuild the image before you trust the cache." not in prompt
        assert "bounded, single-purpose thought" in prompt
        assert "next-move contract" in prompt
        # Mechanics still ride — a worker wake is still under the daemon.
        assert "single-flight" in prompt

    def test_daemon_prompt_default_keeps_resident_stack(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
        )
        assert "Resident Identity Core" in prompt
        assert "bounded, single-purpose thought" not in prompt

    def test_daemon_prompt_surfaces_runner_medium(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runner_medium="codex",
        )
        assert "- Requested Runner: codex" in prompt

    def test_daemon_prompt_declares_web_research_for_claude_shell(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runner_shell="claude",
        )
        assert "- Web research: native via WebSearch/WebFetch" in prompt
        assert "server-side" in prompt
        assert "solitary egress boundary" in prompt

    def test_daemon_prompt_declares_web_research_for_codex_shell(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runner_shell="codex",
        )
        assert "- Web research: native via web.run, default-on" in prompt
        assert "server-side" in prompt

    def test_daemon_prompt_marks_web_research_undeclared_for_unknown_shell(
        self, tmp_path
    ):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runner_shell="my-custom-cli",
        )
        assert "- Web research: not declared for this Shell" in prompt
        assert "rather than guessing" in prompt

    def test_daemon_prompt_marks_web_research_undeclared_without_shell(
        self, tmp_path
    ):
        # No attested Shell at all is the same epistemic state as a custom
        # one: the wake must not assume it can verify a changing fact.
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
        )
        assert "- Web research: not declared for this Shell" in prompt

    def test_daemon_prompt_worker_still_sees_web_capability(self, tmp_path):
        # Workers skip the resident inject stack but still get the bundle —
        # the capability declaration must survive that path.
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            worker=True,
            runner_shell="codex",
        )
        assert "- Web research: native via web.run" in prompt

    def test_daemon_prompt_omits_runner_medium_when_absent(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
        )
        assert "- Runner:" not in prompt

    def test_daemon_prompt_surfaces_runner_quota_when_known(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runner_medium="codex",
            runner_quota="weekly 0% - resets 2026-06-17T01:29Z",
        )
        assert "- Requested Runner: codex" in prompt
        assert "- Quota: weekly 0% - resets 2026-06-17T01:29Z" in prompt

    def test_daemon_prompt_surfaces_repo_label(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            repo_label="Gurio/brr",
        )

        assert "- Repo: Gurio/brr" in prompt

    def test_daemon_prompt_surfaces_runner_mandate_catalog(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runner_medium="codex-mini",
            runner_catalog=[
                {
                    "name": "codex-mini",
                    "shell": "codex",
                    "model": "gpt-5.4-mini",
                    "class": "economy",
                    "cost_rank": 20,
                    "quota_source": "codex-local",
                    "selected": True,
                    "availability": "available",
                },
                {
                    "name": "claude-bare-api-only-sonnet",
                    "shell": "claude",
                    "model": "claude-sonnet-4-6",
                    "class": "balanced",
                    "cost_rank": 30,
                    "auth_variant": "anthropic-api-key",
                    "selected": False,
                    "availability": "available",
                },
            ],
        )

        assert "### Runner catalog" in prompt
        assert (
            "- selected codex-mini: shell=codex, core=gpt-5.4-mini, "
            "class=economy, cost_rank=20, quota=codex-local"
        ) in prompt
        assert "claude-bare-api-only-sonnet" in prompt
        assert "auth=anthropic-api-key" in prompt
        assert "cmd=" not in prompt
        # The catalog is pre-filtered to invokable profiles; a redundant
        # ``availability=available`` on every line is exactly the bloat the
        # renderer now suppresses. Only anomalies get the field.
        assert "availability=available" not in prompt

    def test_runner_catalog_renders_only_unusual_availability(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
            runner_catalog=[
                {
                    "name": "claude-bare-api-only",
                    "shell": "claude",
                    "availability": "missing-auth",
                },
            ],
        )
        assert "availability=missing-auth" in prompt

    # ── quota level on catalog rows (#632) ───────────────────────────────

    def test_catalog_known_level_appended_after_quota_source(self, tmp_path):
        """A catalog row with quota_level renders it parenthetically after the pool name."""
        from brr.prompts import _render_runner_catalog

        rows = [
            {
                "name": "claude-sonnet",
                "shell": "claude",
                "model": "claude-sonnet-4-7",
                "class": "balanced",
                "cost_rank": 30,
                "quota_source": "claude-local",
                "quota_level": "82%",
                "availability": "available",
            }
        ]
        rendered = _render_runner_catalog(rows)
        assert len(rendered) == 1
        assert "quota=claude-local (82%)" in rendered[0]

    def test_catalog_absent_level_renders_byte_identically(self, tmp_path):
        """A row without quota_level renders exactly as before — no suffix, no placeholder.

        Assert on *absence of* a parenthesised suffix after the pool name, not on a
        specific placeholder string: any placeholder would also fail this test, which
        is the point (the fixture must not silently become 'legal').
        """
        from brr.prompts import _render_runner_catalog

        rows = [
            {
                "name": "codex-full",
                "shell": "codex",
                "model": "gpt-5.6-sol",
                "class": "strong",
                "cost_rank": 45,
                "quota_source": "codex-local",
                "availability": "available",
            }
        ]
        rendered = _render_runner_catalog(rows)
        assert len(rendered) == 1
        line = rendered[0]
        assert "quota=codex-local" in line
        # No parenthesised suffix after the pool name in any form.
        assert "quota=codex-local (" not in line

    def test_catalog_exhausted_pool_no_availability_change_no_x_mark(self, tmp_path):
        """Exhausted quota_level is informational only — availability= must not flip."""
        from brr.prompts import _render_runner_catalog

        rows = [
            {
                "name": "codex-full",
                "shell": "codex",
                "model": "gpt-5.6-sol",
                "class": "strong",
                "cost_rank": 45,
                "quota_source": "codex-local",
                "quota_level": "exhausted, resets Jul 28",
                "availability": "available",
            }
        ]
        rendered = _render_runner_catalog(rows)
        assert len(rendered) == 1
        line = rendered[0]
        # Level is rendered inline
        assert "quota=codex-local (exhausted, resets Jul 28)" in line
        # availability="available" stays suppressed → no ✗ prefix
        assert not line.startswith("- ✗")
        # No availability= field at all
        assert "availability=" not in line

    def test_catalog_mixed_known_and_absent_pool(self, tmp_path):
        """Mixed catalog: known level on one pool, absent on another — each renders correctly."""
        from brr.prompts import _render_runner_catalog

        rows = [
            {
                "name": "codex-full",
                "shell": "codex",
                "quota_source": "codex-local",
                "quota_level": "exhausted, resets Jul 28",
                "availability": "available",
            },
            {
                "name": "claude-sonnet",
                "shell": "claude",
                "quota_source": "claude-local",
                # No quota_level — reading absent for this pool
                "availability": "available",
            },
        ]
        rendered = _render_runner_catalog(rows)
        assert len(rendered) == 2
        codex_line = next(l for l in rendered if "codex-full" in l)
        claude_line = next(l for l in rendered if "claude-sonnet" in l)
        assert "quota=codex-local (exhausted, resets Jul 28)" in codex_line
        assert "quota=claude-local" in claude_line
        assert "quota=claude-local (" not in claude_line

    def test_catalog_multiple_profiles_same_pool_all_carry_level(self, tmp_path):
        """Multiple profiles on the same pool all render with the same level label.

        This pins the 'one read per pool, not per profile' invariant: the fixture
        sets the same quota_level on all rows (as the enrichment would), and the
        renderer must propagate it to each rendered line.
        """
        from brr.prompts import _render_runner_catalog

        level = "53%"
        rows = [
            {
                "name": f"codex-model-{i}",
                "shell": "codex",
                "quota_source": "codex-local",
                "quota_level": level,
                "availability": "available",
            }
            for i in range(3)
        ]
        rendered = _render_runner_catalog(rows)
        assert len(rendered) == 3
        for line in rendered:
            assert f"quota=codex-local ({level})" in line

    def test_daemon_prompt_includes_outbox_contract_when_given(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            run_id="task-9",
        )
        assert "/repo/.brr/outbox/evt-1" in prompt
        assert "mid-thought" in prompt
        assert "/repo/.brr/outbox/evt-1/inbox.json" in prompt
        assert "/repo/.brr/outbox/evt-1/portal-state.json" in prompt
        assert "BRR_PORTAL_STATE" in prompt
        assert "change_token" in prompt
        assert "plan / todo boundaries" in prompt
        assert "immediately before a terminal closeout" in prompt
        assert "after the runner has already returned" in prompt
        assert "statically dispatched by the daemon" in prompt
        assert "nobody re-runs you to extract a sentence" in prompt
        assert "`gate: forge` is the explicit PR handoff" in prompt
        assert "does not own PR creation" in prompt

    def test_daemon_prompt_carries_kb_url_portal_fact(self, tmp_path):
        base = "https://github.test/knowledge/blob/main/repos/Gurio__brr/"
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            run_id="task-9",
            kb_base_url=base,
        )
        assert f"kb page URL base: {base}" in prompt
        assert "link only after the knowledge commit is pushed" in prompt

    def test_daemon_prompt_maps_codex_channels_to_brr_portals(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            run_id="task-9",
            runner_medium="codex",
        )
        assert "codex Shell:" in prompt
        assert "runner-local under brr" in prompt
        assert ".card" in prompt
        assert "plain current-thread fallback" in prompt

    def test_daemon_prompt_omits_outbox_contract_without_path(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-9",
        )
        # The standing outbox rules now ride unconditionally in
        # daemon-substrate's delivery-portals block (contract-compression
        # pass), so the outbox-specific absence pin is the live value
        # bullet the bundle renders only when a path exists.
        assert "- outbox:" not in prompt

    def test_daemon_prompt_states_budget_and_keepalive(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            budget_seconds=3600,
            run_id="task-9",
        )
        assert "Budget:" in prompt
        assert "60m" in prompt
        # The extension how-to is anchored on the agent's outbox path.
        assert "/repo/.brr/outbox/evt-1/.keepalive" in prompt

    def test_daemon_prompt_omits_budget_without_value(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            run_id="task-9",
        )
        assert "Budget:" not in prompt
        # daemon-substrate names `.keepalive` as a standing rule; the
        # absence pin is the live path bullet, rendered only with a budget.
        assert "/repo/.brr/outbox/evt-1/.keepalive" not in prompt

    def test_daemon_prompt_includes_driver_manual(self, tmp_path):
        """The daemon path injects brr's driver's manual — the daemon-only
        machinery (single-flight, capture net, self-scheduled wakes) the
        host-agnostic playbook deliberately leaves out."""
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path, run_id="task-9",
        )
        assert "How the daemon drives you" in prompt
        assert "single-flight" in prompt
        assert "schedule.md" in prompt  # self-scheduled wakes live here now

    def test_run_prompt_omits_driver_manual(self, tmp_path):
        """`brnrd run` is a one-shot: no daemon to fire schedules or drain an
        outbox, so it doesn't carry the driver's manual."""
        prompt = build_run_prompt("ship it", tmp_path)
        assert "How the daemon drives you" not in prompt
        assert "schedule.md" not in prompt

    def test_prompts_include_weave_register(self, tmp_path):
        """Both runner paths carry the working-register contract (weave.md):
        the resident's dense native notation for the surfaces only it and
        the machinery read. Host-agnostic, so the one-shot path gets it too."""
        assert "your working register" in build_run_prompt("ship it", tmp_path)
        prompt = build_daemon_prompt(
            "ship it", "evt-1", "/tmp/resp.md", tmp_path, run_id="task-9",
        )
        assert "your working register" in prompt

    def test_daemon_prompt_lists_pending_events_and_fold_in_contract(self, tmp_path):
        prompt = build_daemon_prompt(
            "work on A", "evt-A", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-A",
            run_id="task-A",
            pending_events=[
                {"id": "evt-B", "source": "telegram",
                 "summary": "quick question about X"},
            ],
        )
        assert "Inbox — other pending events" in prompt
        assert "evt-B" in prompt
        assert "quick question about X" in prompt
        assert "Every listed event is yours" in prompt
        # The fold-in contract names the frontmatter handle.
        assert "event: <id>" in prompt
        assert "Own every" in prompt
        assert "worker capacity and quota are healthy" in prompt
        assert "spawn:" in prompt
        assert "portal-state.json" in prompt
        assert "inbox.json" in prompt
        assert "snapshot from when you woke" not in prompt

    def test_daemon_prompt_omits_inbox_when_no_pending_events(self, tmp_path):
        prompt = build_daemon_prompt(
            "work on A", "evt-A", "/tmp/resp.md", tmp_path,
            outbox_path="/repo/.brr/outbox/evt-A", run_id="task-A",
        )
        assert "other pending events" not in prompt

    def test_daemon_prompt_lists_present_thoughts(self, tmp_path):
        prompt = build_daemon_prompt(
            "work on A", "evt-A", "/tmp/resp.md", tmp_path,
            run_id="task-A",
            present=[
                {"kind": "session", "stream": "telegram:9:", "run_id": "task-Z"},
            ],
        )
        assert "Also awake right now" in prompt
        assert "session" in prompt
        assert "telegram:9:" in prompt
        # The framing names reconciliation-by-judgement, not locking.
        assert "reconcile" in prompt.lower()

    def test_daemon_prompt_omits_presence_when_alone(self, tmp_path):
        prompt = build_daemon_prompt(
            "work on A", "evt-A", "/tmp/resp.md", tmp_path, run_id="task-A",
        )
        assert "Also awake right now" not in prompt

    def test_daemon_prompt_injects_pitfall_when_trigger_hits(self, tmp_path):
        _seed_pitfalls(
            tmp_path,
            "## Blind retry\ntrigger: docker\n"
            "Rebuild the image before you trust the cache.\n",
        )
        prompt = build_daemon_prompt(
            "rebuild the docker image and ship", "evt-A", "/tmp/resp.md",
            tmp_path, run_id="task-A",
        )
        assert "Pitfalls that match this task" in prompt
        assert "Blind retry" in prompt
        assert "Rebuild the image before you trust the cache." in prompt

    def test_daemon_prompt_omits_pitfall_when_no_trigger_match(self, tmp_path):
        _seed_pitfalls(
            tmp_path,
            "## Blind retry\ntrigger: docker\nRebuild first.\n",
        )
        prompt = build_daemon_prompt(
            "update the readme wording", "evt-A", "/tmp/resp.md",
            tmp_path, run_id="task-A",
        )
        assert "Pitfalls that match this task" not in prompt

    def test_daemon_prompt_matches_pitfall_against_event_body(self, tmp_path):
        _seed_pitfalls(
            tmp_path,
            "## Billing math\ntrigger: invoice\nProrate on the day boundary.\n",
        )
        # The trigger is absent from the task summary but present in the
        # original event text — both feed the matcher.
        prompt = build_daemon_prompt(
            "handle the request", "evt-A", "/tmp/resp.md", tmp_path,
            run_id="task-A",
            event_body="the invoice total looks wrong for mid-month signups",
        )
        assert "Pitfalls that match this task" in prompt
        assert "Prorate on the day boundary." in prompt

    def test_daemon_prompt_includes_branch_and_runtime_paths(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="run-123",
            source="telegram",
            environment="docker",
            branch_name="feat/task-abstraction",
            seed_ref="feat/task-abstraction",
            branch_source="event:target_branch",
            branch_setup_notice="target branch held elsewhere; using run branch",
            runtime_dir="/repo/.brr",
            context_path="/repo/.brr/runs/run-123/context.md",
        )
        assert "Run ID: run-123" in prompt
        assert "Legacy task id" not in prompt
        assert f"Execution root: {tmp_path}" in prompt
        assert "Seed ref: feat/task-abstraction" in prompt
        assert "Current branch: feat/task-abstraction" in prompt
        assert (
            "Branch setup: target branch held elsewhere; using run branch"
            in prompt
        )
        assert "Shared runtime dir: /repo/.brr" in prompt
        assert "Run context file: /repo/.brr/runs/run-123/context.md" in prompt
        assert "- stdout capture: /tmp/resp.md" in prompt
        assert "fix it" in prompt
        assert "kb/log-" not in prompt
        assert "gh pr create" not in prompt

    def test_daemon_prompt_includes_mode_block(self, tmp_path):
        """The Mode block names the stage, source, environment, and
        runtime-recovery surface so the runner can identify "where am
        I?" from the bundle alone without opening the run context file
        on every task."""
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-123",
            source="telegram",
            environment="docker",
            context_path="/repo/.brr/runs/task-123/context.md",
        )
        assert "### Mode" in prompt
        assert "Stage: brnrd daemon run" in prompt
        assert "Source: telegram" in prompt
        assert "Environment: docker" in prompt
        assert "Delivery: situational outputs captured by brr" in prompt
        # Runtime-recovery line points at the context file and frames it
        # as opt-in detail, not routine reading.
        assert (
            "Runtime recovery: /repo/.brr/runs/task-123/context.md"
            in prompt
        )
        assert "open only if" in prompt

    def test_daemon_prompt_mode_block_drops_missing_fields(self, tmp_path):
        """Source, environment, and runtime-recovery lines disappear
        when the daemon couldn't determine them. Stage and Delivery are
        always present because they're invariant for this builder."""
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "do thing", "evt-9", "/tmp/r.md", tmp_path,
            run_id="task-9",
        )
        assert "### Mode" in prompt
        assert "Stage: brnrd daemon run" in prompt
        assert "Delivery: situational outputs captured by brr" in prompt
        assert "Source:" not in prompt
        assert "Environment:" not in prompt
        assert "Runtime recovery:" not in prompt

    def test_daemon_prompt_names_host_publication_ownership(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-123",
            environment="host",
        )

        assert "Environment: host — shared checkout" in prompt
        assert "host finalization does not publish commits" in prompt
        assert "own the push / PR handoff" in prompt

    def test_daemon_prompt_describes_preserved_run_branch(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-123",
            branch_name="brr/task-123",
            seed_ref="main",
            branch_source="fallback:preserve",
            host_context_branch="feature/host",
        )

        assert "Seed ref: main" in prompt
        assert "Branch source: fallback:preserve" in prompt
        assert "Host context branch: feature/host" in prompt
        # No target branch → nudge the agent to rename the brr/<run-id>
        # placeholder to something descriptive.
        assert "themed work ⇒ rename" in prompt
        assert "brr/<short-slug>" in prompt
        # The forge-locked `gh pr create` nudge is gone — brr now emits
        # a forge URL in the response card automatically, and PR
        # creation is forge-specific behaviour that doesn't belong in
        # the default prompt.
        assert "gh pr create" not in prompt

    def test_daemon_prompt_warns_against_local_paths_in_chat_reply(self, tmp_path):
        """The agent shouldn't tell the remote user to click on a
        worktree path that only exists on the host running brr.
        Telegram in particular doesn't render those as links and the
        user can't reach them anyway."""
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-123",
            branch_name="brr/task-123",
            seed_ref="main",
        )

        assert "chat client" in prompt
        assert "basename only" in prompt
        assert ".brr/worktrees/" in prompt  # cited as the bad pattern
        assert "forge-hosted branch URL" in prompt

    def test_daemon_prompt_with_recent_conversation(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        recent = [
            {
                "ts": "2026-05-05T20:00:00Z",
                "kind": "event",
                "event_id": "evt-prev",
                "source": "telegram",
                "summary": "earlier ping",
            },
            {
                "ts": "2026-05-05T20:00:05Z",
                "kind": "update",
                "type": "done",
                "run_id": "task-prev",
            },
        ]

        prompt = build_daemon_prompt(
            "fix it", "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-123",
            branch_name="brr/task-123",
            seed_ref="feat/task",
            runtime_dir="/repo/.brr",
            recent_conversation=recent,
            event_body="please fix the login flow",
        )
        assert "Run Context Bundle" in prompt
        assert "Recent in this conversation" in prompt
        assert "earlier ping" in prompt
        assert "task-prev" in prompt
        assert "update done" in prompt
        assert "Original event body" in prompt
        assert "please fix the login flow" in prompt
        assert "Run ID: task-123" in prompt
        assert f"Execution root: {tmp_path}" in prompt
        assert "Seed ref: feat/task" in prompt
        assert "Workstream" not in prompt
        assert "Triage" not in prompt

    def test_daemon_prompt_lists_event_attachments_for_read(self, tmp_path):
        image_path = tmp_path / "evt-1.attachments" / "photo.jpg"
        prompt = build_daemon_prompt(
            "look at this", "evt-1", "/tmp/resp.md", tmp_path,
            event_body="look at this",
            event_attachments=[image_path],
        )
        assert "Original event body" in prompt
        assert "look at this" in prompt
        assert "Attachments" in prompt
        assert str(image_path) in prompt

    def test_daemon_prompt_shows_attachments_section_with_empty_body(self, tmp_path):
        # A bare photo with no caption: body is empty, but the image is
        # still the whole point of the event — the section must still
        # render so the attachment isn't silently invisible.
        image_path = tmp_path / "evt-1.attachments" / "photo.jpg"
        prompt = build_daemon_prompt(
            "task", "evt-1", "/tmp/resp.md", tmp_path,
            event_body="",
            event_attachments=[image_path],
        )
        assert "Original event body" in prompt
        assert str(image_path) in prompt

    def test_daemon_prompt_with_communication_snapshot(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "follow up",
            "evt-2",
            "/tmp/resp.md",
            tmp_path,
            communication_snapshot={
                "current_thread": "cloud:telegram:77:",
                "correspondent_key": "telegram:user-id:42",
                "related_threads": [
                    {
                        "conversation_key": "telegram:77:",
                        "source": "telegram",
                        "kind": "gate_thread",
                        "record_count": 4,
                        "dialogue_count": 2,
                        "latest_ts": "2026-05-05T20:01:00Z",
                    },
                    {
                        "conversation_key": "cloud:telegram:77:",
                        "source": "cloud/telegram",
                        "kind": "gate_thread",
                        "record_count": 1,
                        "dialogue_count": 1,
                        "latest_ts": "2026-05-05T20:02:00Z",
                    },
                ],
                "history_groups": [
                    {
                        "label": "telegram thread telegram:77:",
                        "path": "/repo/.brr/runs/task/history/gate.jsonl",
                        "record_count": 4,
                    },
                ],
                "recent_turns": [
                    {
                        "ts": "2026-05-05T20:00:00Z",
                        "kind": "event",
                        "source": "telegram",
                        "conversation_key": "telegram:77:",
                        "body": "prior ask",
                    },
                    {
                        "ts": "2026-05-05T20:01:00Z",
                        "kind": "artifact",
                        "artifact_kind": "response",
                        "label": "response:evt-prev",
                        "body": "prior answer",
                    },
                ],
            },
        )

        assert "Communication snapshot" in prompt
        assert "Current thread: `cloud:telegram:77:`" in prompt
        assert "Correspondent: `telegram:user-id:42`" in prompt
        assert "Related input threads" in prompt
        assert "On-demand grouped history" in prompt
        assert "/repo/.brr/runs/task/history/gate.jsonl" in prompt
        assert "Recent turns (woven, oldest first)" in prompt
        assert "prior ask" in prompt
        assert "prior answer" in prompt

    def test_daemon_prompt_history_group_truncation_stays_truthful(self, tmp_path):
        # #500: a bounded per-run history copy must say so — the pointer
        # can't claim "untruncated" once older records were dropped, and
        # it must name where the rest of the thread actually lives.
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "follow up",
            "evt-2",
            "/tmp/resp.md",
            tmp_path,
            communication_snapshot={
                "current_thread": "telegram:77:",
                "history_groups": [
                    {
                        "label": "telegram thread telegram:77:",
                        "path": "/repo/.brr/runs/task/history/gate.jsonl",
                        "record_count": 400,
                        "total_record_count": 4321,
                        "truncated": True,
                        "store_path": "/repo/.brr/conversations/telegram__77__",
                    },
                ],
            },
        )

        assert "latest 400 of 4321 records" in prompt
        assert "/repo/.brr/conversations/telegram__77__" in prompt
        assert "untruncated" not in prompt
        assert "truncated to the latest" in prompt

    def test_daemon_prompt_renders_reader_model_from_snapshot(self, tmp_path):
        # #217 v1: `fluency` in the communication snapshot renders a Reader
        # fluency line — `weave` licenses register-density replies; other
        # values unfold to plain language; absent means no line (`prose` is
        # the default and needs no announcement). Renamed from
        # `user_commitment: full | profane` 2026-07-23: `full` read as an
        # amount, and the field names the reader's language, not the volume.
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        base = {
            "current_thread": "telegram:77:",
            "correspondent_key": "telegram:user-id:42",
        }
        weave = build_daemon_prompt(
            "hi", "evt-2", "/tmp/resp.md", tmp_path,
            communication_snapshot={**base, "fluency": "weave"},
        )
        assert "Reader fluency: `fluency: weave`" in weave
        assert "register" in weave
        # The line must never read as a licence for length (2026-07-23).
        assert "still the delta" in weave

        prose = build_daemon_prompt(
            "hi", "evt-2", "/tmp/resp.md", tmp_path,
            communication_snapshot={**base, "fluency": "prose"},
        )
        assert "Reader fluency: `fluency: prose`" in prose
        assert "plain language" in prose
        assert "never longer" in prose

        unset = build_daemon_prompt(
            "hi", "evt-2", "/tmp/resp.md", tmp_path,
            communication_snapshot=dict(base),
        )
        assert "Reader fluency" not in unset

    def test_daemon_prompt_renders_prior_failure_facet(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "any update?",
            "evt-2",
            "/tmp/resp.md",
            tmp_path,
            communication_snapshot={
                "current_thread": "telegram:10:",
                "prior_failure": {
                    "reason": "Credit balance is too low",
                    "stage": "run",
                    "attempts": 3,
                    "exit_code": 1,
                    "ts": "2026-06-14T16:00:00Z",
                    "event_id": "evt-old",
                },
                "related_threads": [],
                "recent_turns": [],
            },
        )

        assert "Prior run on this thread failed (operational)" in prompt
        assert "Credit balance is too low" in prompt
        assert "3 attempt(s)" in prompt
        assert "This wake lands after that interruption" in prompt

    def test_daemon_prompt_renders_woven_dialogue_bodies(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        recent = [
            {
                "ts": "2026-05-05T20:00:00Z",
                "kind": "event",
                "source": "telegram",
                "body": "first line\nsecond line",
                "summary": "first line second line",
            },
            {
                "ts": "2026-05-05T20:01:00Z",
                "kind": "artifact",
                "artifact_kind": "response",
                "label": "response:evt-prev",
                "body": "agent reply\nwith detail",
                "path": "/tmp/evt-prev.md",
            },
        ]

        prompt = build_daemon_prompt(
            "next thing", "evt-2", "/tmp/resp.md", tmp_path,
            recent_conversation=recent,
        )

        assert "user (telegram):\n  first line\n  second line" in prompt
        assert "agent (response:evt-prev):\n  agent reply\n  with detail" in prompt
        assert "/tmp/evt-prev.md" not in prompt

    def test_daemon_prompt_does_not_repeat_identical_event_body(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        body = "long telegram request"
        prompt = build_daemon_prompt(
            body, "evt-1", "/tmp/resp.md", tmp_path,
            run_id="task-123",
            event_body=body,
        )

        assert "Original event body" in prompt
        assert body in prompt
        assert f"Run: {body}" not in prompt

    def test_daemon_prompt_without_recent_conversation(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")

        prompt = build_daemon_prompt(
            "do thing", "evt-9", "/tmp/r.md", tmp_path,
            run_id="task-9",
        )
        assert "Workstream" not in prompt
        assert "Recent in this conversation" not in prompt
        assert "Original event body" not in prompt

    def test_bundled_daemon_prompt_points_at_portals_not_dead_commands(self, tmp_path):
        prompt = build_daemon_prompt(
            "do thing",
            "evt-9",
            "/tmp/r.md",
            tmp_path,
            run_id="task-9",
            context_path="/repo/.brr/runs/task-9/context.md",
        )
        assert "Run context file: /repo/.brr/runs/task-9/context.md" in prompt
        assert "brr inspect" not in prompt
        assert "brr stream" not in prompt
        # The portals manual is inspected, not injected: the daemon prompt
        # carries a one-line pointer to it (the protocol choreography lives
        # there, not re-narrated in full on every wake).
        assert "brnrd docs portals" in prompt

    def test_daemon_prompt_frames_delivery_as_conversational(self, tmp_path):
        prompt = build_daemon_prompt(
            "ship it",
            "evt-1",
            "/tmp/resp.md",
            tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            run_id="task-9",
        )
        # Pending-event ownership is single-sourced in daemon-substrate's
        # portals block since the P2 dedup (run.md Delivery is a pointer now).
        assert "Own every pending event" in prompt
        assert "card + mid-thought replies" in prompt
        assert "waiting in the dark" in prompt

    def test_delivery_contract_carries_portal_model_summary(self, tmp_path):
        # The portal-grammar summary (inbound/outbound/parked) now rides in
        # daemon-substrate's delivery-portals block (contract-compression
        # pass); the bundle's Delivery contract carries only live values plus
        # the pointer at the standing rules and the manual. Both halves must
        # name the manual as the pull-only full reference — the anti-drift
        # link the maintainer asked for. The reciprocal half is pinned in
        # test_docs.py (test_portals_manual_links_back_to_delivery_contract);
        # keep the two tests in step so contract and manual can't silently
        # diverge.
        prompt = build_daemon_prompt(
            "ship it",
            "evt-1",
            "/tmp/resp.md",
            tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            run_id="task-9",
        )
        assert "portals" in prompt
        for form in ("inbound", "outbound", "parked"):
            assert form in prompt
        assert "Standing rules" in prompt
        assert "brnrd docs portals" in prompt

    def test_daemon_prompt_carries_next_move_and_linger(self, tmp_path):
        # A1/#211 + B5/#216: the delivery-portals block carries the compact
        # next-move rule (four closeout states, manufactured options named
        # as the failure mode) and the post-delivery linger contract
        # (backoff inside the provider cache window, dispatch-or-explicit-
        # defer ownership for unrelated pending work), including the daemon-owned attending
        # floor. Full contracts live in the portals manual (pinned in
        # test_docs.py).
        prompt = build_daemon_prompt(
            "ship it",
            "evt-1",
            "/tmp/resp.md",
            tmp_path,
            outbox_path="/repo/.brr/outbox/evt-1",
            run_id="task-9",
        )
        assert "next move" in prompt
        for state in (
            "done — receipt",
            "continuing — what's next",
            "blocked — what's needed",
        ):
            assert state in prompt
        assert "manufactured options are the failure" in prompt
        assert "linger" in prompt
        assert "delivered · attending" in prompt
        assert "backoff 30s → cap 240s" in prompt
        assert "worker capacity and quota are healthy" in prompt
        assert "queue never starves" in prompt


# ── Phase 3 guardrails: revisit-signal handling ──────────────────────


def _read_bundled_run_prompt() -> str:
    """Read the bundled prompt directly so we pin its shipped content."""
    from pathlib import Path

    import brr

    return (Path(brr.__file__).parent / "prompts" / "run.md").read_text(
        encoding="utf-8",
    )


def _read_bundled_daemon_substrate() -> str:
    from pathlib import Path

    import brr

    return (
        Path(brr.__file__).parent / "prompts" / "daemon-substrate.md"
    ).read_text(encoding="utf-8")


def test_kb_link_contract_uses_portal_url_with_basename_fallback():
    run_prompt = _read_bundled_run_prompt()
    substrate = _read_bundled_daemon_substrate()

    assert "link the kb\n  URL when the portal provides one" in run_prompt
    assert "otherwise name the file by basename only" in run_prompt
    assert "link a kb page with the kb URL the portal provides" in substrate
    assert "when none is\n  available, use its basename only" in substrate


def test_recent_conversation_renders_dedup_provenance():
    """Issue #338: a woven turn deduped across mirrored gates must still

    name every pipe it arrived on — `duplicate_conversation_keys`
    (attached by `conversations._dedupe_woven_records`) renders as
    `also-on=` alongside the existing `thread=` label, so a reader never
    loses the fact that both gates carried the exchange.
    """
    recent = [
        {
            "ts": "2026-07-20T04:44:00Z",
            "kind": "event",
            "source": "telegram",
            "correspondent_key": "telegram:user-id:42",
            "conversation_key": "telegram:10:",
            "duplicate_conversation_keys": ["cloud:telegram:10:"],
            "body": "hello",
        },
    ]
    block = _format_recent_conversation(recent)
    assert "thread=telegram:10:" in block
    assert "also-on=cloud:telegram:10:" in block


def _read_bundled_agents_md() -> str:
    from pathlib import Path

    import brr

    return (Path(brr.__file__).parent / "AGENTS.md").read_text(encoding="utf-8")


class TestScheduleTurnDedup:
    """Issue #576: a recurring `schedule.md` entry re-enters the conversation
    store as a `source: schedule` turn every time it fires, so an unbounded
    number of near-identical copies of the same entry accrete into "Recent
    turns" — and the current firing is *also* rendered separately as
    "### Original event body". Near-duplicate `schedule` turns collapse to
    a one-line stub; genuinely different content (schedule or otherwise)
    is left alone.
    """

    # Single-line by design: a real entry is markdown with linebreaks, but
    # `_format_turn` re-indents multi-line bodies, which would break the
    # exact-substring assertions below without adding coverage of the
    # ratio-based collapse itself. No leading/trailing whitespace: bodies
    # are rendered via `.strip()`'d copies (`_conversation_body`), so a
    # fixture with edge whitespace would never literally appear in the
    # rendered prompt.
    _ONE_FIRING = (
        "director-tick 5h cadence. Dispatch authority granted: spawn "
        "workers for bounded plan items without waiting for a reply. "
        "Merge authority history withdrawn 2026-07-05, see decision "
        "ledger for the incident. Notify bar widened 2026-06-20 to cover "
        "schedule-sourced turns. Cadence history 30m to 2h on 2026-05-01 "
        "then 5h on 2026-06-10 after quota pressure."
    )
    _ENTRY_BODY = " ".join([_ONE_FIRING] * 3)

    def _prompt(self, tmp_path, *, recent, event_body):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.")
        return build_daemon_prompt(
            event_body,
            "evt-1",
            "/tmp/resp.md",
            tmp_path,
            run_id="task-1",
            recent_conversation=recent,
            event_body=event_body,
        )

    def test_identical_schedule_body_collapses_against_event_body(self, tmp_path):
        recent = [{
            "ts": "2026-07-20T04:44:00Z",
            "kind": "event",
            "source": "schedule",
            "schedule_id": "director-tick",
            "body": self._ENTRY_BODY,
        }]
        prompt = self._prompt(tmp_path, recent=recent, event_body=self._ENTRY_BODY)

        # Only the "### Original event body" copy remains in full.
        assert prompt.count(self._ENTRY_BODY) == 1
        assert (
            "identical to this run's event body — not repeated" in prompt
        )
        # The firing itself must still be nameable: timestamp survives.
        assert "2026-07-20T04:44:00Z" in prompt

    def test_genuinely_different_schedule_turn_is_not_collapsed(self, tmp_path):
        other_entry = "wholly unrelated kb-gc entry: prune stale worktrees nightly."
        recent = [{
            "ts": "2026-07-20T04:44:00Z",
            "kind": "event",
            "source": "schedule",
            "schedule_id": "kb-gc",
            "body": other_entry,
        }]
        prompt = self._prompt(tmp_path, recent=recent, event_body=self._ENTRY_BODY)

        assert other_entry in prompt
        assert "not repeated" not in prompt

    def test_near_miss_under_threshold_is_not_collapsed(self, tmp_path):
        # Flip enough characters to land just under the dedup ratio; assert
        # the fixture actually lands there rather than assuming it does, so
        # a future ratio-formula change can't silently invalidate the test.
        chars = list(self._ENTRY_BODY)
        step = max(1, len(chars) // 40)
        for i in range(0, len(chars), step):
            chars[i] = "#"
        near_miss = "".join(chars)
        ratio = difflib.SequenceMatcher(None, near_miss, self._ENTRY_BODY).ratio()
        assert ratio < SCHEDULE_TURN_DEDUP_RATIO, (
            f"fixture ratio {ratio} is not below the threshold; adjust the "
            "flip density"
        )

        recent = [{
            "ts": "2026-07-20T04:44:00Z",
            "kind": "event",
            "source": "schedule",
            "schedule_id": "director-tick",
            "body": near_miss,
        }]
        prompt = self._prompt(tmp_path, recent=recent, event_body=self._ENTRY_BODY)

        assert near_miss in prompt
        assert "not repeated" not in prompt

    def test_non_schedule_source_is_never_collapsed(self, tmp_path):
        # A user repeating themselves is signal, not scheduler noise — must
        # never collapse even when byte-identical to the event body.
        recent = [{
            "ts": "2026-07-20T04:44:00Z",
            "kind": "event",
            "source": "telegram",
            "body": self._ENTRY_BODY,
        }]
        prompt = self._prompt(tmp_path, recent=recent, event_body=self._ENTRY_BODY)

        assert prompt.count(self._ENTRY_BODY) == 2
        assert "not repeated" not in prompt

    def test_repeated_schedule_firings_collapse_against_each_other(self, tmp_path):
        # Two prior firings, near-identical to each other but not to the
        # current (unrelated) event body: the first renders in full, the
        # second collapses against it.
        firing_1 = self._ENTRY_BODY
        firing_2 = self._ENTRY_BODY + " Minor accreted note from this firing."
        current_body = "a wholly different current event, e.g. a telegram ping"
        recent = [
            {
                "ts": "2026-07-15T04:44:00Z",
                "kind": "event",
                "source": "schedule",
                "schedule_id": "director-tick",
                "body": firing_1,
            },
            {
                "ts": "2026-07-20T04:44:00Z",
                "kind": "event",
                "source": "schedule",
                "schedule_id": "director-tick",
                "body": firing_2,
            },
        ]
        prompt = self._prompt(tmp_path, recent=recent, event_body=current_body)

        assert firing_1 in prompt
        assert firing_2 not in prompt
        assert (
            "identical to the 2026-07-15T04:44:00Z firing above — not "
            "repeated" in prompt
        )

    def test_realistic_fixture_shrinks_and_names_all_firings(self, tmp_path):
        """Reproduce the #576 shape: two prior firings of one entry plus the
        same body as the current event. The rendered bundle should shrink
        substantially relative to a naive three-copy render, while every
        firing's timestamp is still present.
        """
        current_body = self._ENTRY_BODY
        recent = [
            {
                "ts": "2026-07-15T04:44:00Z",
                "kind": "event",
                "source": "schedule",
                "schedule_id": "director-tick",
                "body": self._ENTRY_BODY,
            },
            {
                "ts": "2026-07-20T04:44:00Z",
                "kind": "event",
                "source": "schedule",
                "schedule_id": "director-tick",
                "body": self._ENTRY_BODY,
            },
        ]

        # Baseline: the same two bodies, but sourced from something other
        # than `schedule` so nothing collapses — this reproduces the
        # pre-#576-fix "verbatim every time" rendering for a size
        # comparison, without needing to disable the fix under test.
        naive_recent = [dict(r, source="telegram") for r in recent]
        naive_block = _format_recent_conversation(naive_recent, event_body=current_body)
        collapsed_block = _format_recent_conversation(recent, event_body=current_body)

        assert len(collapsed_block) < len(naive_block) * 0.1
        for ts in ("2026-07-15T04:44:00Z", "2026-07-20T04:44:00Z"):
            assert ts in collapsed_block

        full_prompt = self._prompt(tmp_path, recent=recent, event_body=current_body)
        assert "Original event body" in full_prompt
        # All three firings named: two collapsed recent turns (by
        # timestamp) plus the current firing rendered once, in full, as
        # the event body.
        assert full_prompt.count(self._ENTRY_BODY) == 1


class TestRevisitSignalGuardrails:
    """Pin the prompt + AGENTS.md guidance for design-loaded / "reconsider"
    tasks. Stance refined 2026-06-20 (see `kb/log.md`): the default is
    *reconcile and act in the same thought*, not surface-and-wait; a
    chat-only reply is reserved for a genuine fork, where it still must be
    authorized so the diff-as-receipt rule can't force a half-fitting
    commit. Both failure modes — path-of-least-resistance compliance and
    aloof bounce-back — are guarded. See `kb/design-git-layer-rework.md`
    Phase 3 for the original revisit-signal rationale."""

    def test_run_prompt_mentions_revisit_signals(self):
        prompt = _read_bundled_run_prompt()
        # Section header that gates the guidance.
        assert "When the task asks you to reconsider" in prompt
        # The trigger is ownership intent, not a brittle keyword list:
        # the stance lives in the resident playbook and AGENTS.md →
        # Stewardship, which this section leans on instead of
        # re-enumerating trigger phrases.
        assert "judgement on the substance" in prompt
        assert "trust the intent rather than scanning for trigger words" in prompt

    def test_run_prompt_biases_to_resolve_and_act(self):
        prompt = _read_bundled_run_prompt()
        # The default on a clear, reversible reconsider is to resolve it
        # in-thread, not to park it for a second "go do that" event.
        assert "this same thought" in prompt
        assert "round-trip" in prompt
        assert "Stewardship" in prompt

    def test_run_prompt_authorizes_no_commit_for_genuine_fork(self):
        prompt = _read_bundled_run_prompt()
        # The chat-only-reply outcome must stay named for the genuine-fork
        # case so the diff-as-receipt rule doesn't force a half-fitting
        # commit when there is no clear edit yet.
        assert "chat-only reply" in prompt
        assert "the complete task" in prompt

    def test_agents_md_self_review_contains_contradiction_check(self):
        agents = _read_bundled_agents_md()
        # The self-review bullet maps onto Stewardship and now catches
        # both failure modes, not just compliance.
        assert "reconcile it against the current state" in agents
        assert "aloof bounce-back" in agents
        assert "Stewardship" in agents


class TestDaemonModeGuardrails:
    """Pin the run.md changes that route daemon runners through the
    Run Context Bundle's Mode block and treat the run context file as
    recovery detail rather than routine reading.  See
    the earlier editor-orientation research and
    `kb/plan-agent-orientation-layering.md`."""

    def test_run_prompt_names_mode_block_and_recovery_role(self):
        prompt = _read_bundled_run_prompt()
        # AGENTS.md remains the entry point, but whether a wake already
        # carries it is Shell-dependent (codex reads it natively; claude
        # does not) — verified live 2026-07-11 on a claude-fable daemon
        # wake whose context had no AGENTS.md block. The old "injected in
        # most daemon wakes" wording taught residents to skip the contract
        # they never received.
        assert "Shell-dependent" in prompt
        assert "not guaranteed" in prompt
        assert "open it before touching" in prompt
        assert "Read the `AGENTS.md` playbook at the repo root" not in prompt
        # The bundle is the authoritative "where am I?" (its Mode block).
        assert "mode, run metadata" in prompt
        # Injected Recent Activity counts toward the kb/log.md step so
        # daemon runs don't re-read the log when the prompt already
        # carries an extract. Checked as separate anchors so the
        # paragraph can rewrap without breaking the guardrail.
        assert "Recent Activity (from kb/log.md)" in prompt
        assert "the log startup read" in prompt
        assert "only for older history" in prompt
        # The run context file is recovery detail, not routine reading.
        assert "runtime-recovery context file" in prompt
        assert "only for what the" in prompt


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
        assert "The shape of the context itself" in prompt
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


class TestWorkSurfaceInjection:
    def test_points_to_one_surface_when_empty(self, tmp_path):
        _seed_account_home(tmp_path)
        result = _build_work_surface_block(tmp_path)
        assert "Work surface" in result
        assert "surface/index.md" in result

    def test_discovers_every_markdown_file_with_index_first(self, tmp_path):
        home = _seed_account_home(tmp_path)
        surface = home / "surface"
        (surface / "plans" / "local__default").mkdir(parents=True)
        (surface / "index.md").write_text("# Start here", encoding="utf-8")
        (surface / "workflow.md").write_text("# Workflow", encoding="utf-8")
        (surface / "plans" / "local__default" / "active.md").write_text(
            "# Plan\n\nship it", encoding="utf-8"
        )

        result = _build_work_surface_block(tmp_path)

        assert result.index("### index.md") < result.index("### plans/local__default/active.md")
        assert "### workflow.md" in result
        assert "ship it" in result

    def test_surface_block_rides_in_daemon_prompt(self, tmp_path):
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.", encoding="utf-8")
        home = _seed_account_home(tmp_path)
        surface = home / "surface"
        surface.mkdir()
        (surface / "index.md").write_text("One orientation root.", encoding="utf-8")

        prompt = build_daemon_prompt("fix it", "evt-1", "/tmp/r.md", tmp_path)

        assert "Work surface" in prompt
        assert "One orientation root" in prompt

    def test_scored_variant_attests_the_stale_page_among_several(self, tmp_path):
        """``work-surface`` aggregates many independently-trimmed pages into
        one ``ContractEntry`` (P1 move 1). When several pages are cut, the
        block attests to the **stale** one — the whole point of the alarm —
        not an arbitrary healthy one that happened to iterate later.

        Each page's own content exceeds ``_MAX_ACCRETING_BLOCK_BYTES``
        (8192), which caps *that page's* trim allowance regardless of the
        total surface budget — the default total budget (48000) is left in
        place so neither page's trimmed block gets skipped for exceeding
        what's left of the *shared* remainder (the failure mode this test
        first hit: too tight a shared budget silently drops the stale page
        instead of attesting to it).
        """
        home = _seed_account_home(tmp_path)
        surface = home / "surface"
        surface.mkdir()
        (surface / "index.md").write_text("# Start here", encoding="utf-8")

        # A healthy, in-order, trimmed page.
        healthy = "\n\n".join([
            _ledger_entry("2026-07-21", "run-260721-0900-aaaa", 6000),
            _ledger_entry("2026-07-22", "run-260722-1000-bbbb", 6000),
        ])
        (surface / "plan.md").write_text(healthy, encoding="utf-8")

        # A drifted, stale, trimmed page (the ledger-inversion class).
        drifted = "\n\n".join([
            _ledger_entry("2026-07-23", "run-260723-1342-isd1", 6000),
            _ledger_entry("2026-07-22", "run-260723-1131-r0h4", 6000),
        ])
        ledger_dir = surface / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "decisions.md").write_text(drifted, encoding="utf-8")

        result, _whole = _build_work_surface_block_scored(tmp_path)

        assert result.stale is True
        assert result.newest_item == "2026-07-22"
        assert result.source_newest == "2026-07-23 13:42"

    def test_stale_page_flows_through_to_the_contract_entry(self, tmp_path):
        """End-to-end: a stale surface page's attestation reaches the
        ``ContractEntry`` the kernel alarm (P1 move 4a) reads.
        """
        home = _seed_account_home(tmp_path)
        surface = home / "surface"
        ledger_dir = surface / "ledger"
        ledger_dir.mkdir(parents=True)
        drifted = "\n\n".join([
            _ledger_entry("2026-07-23", "run-260723-1342-isd1", 6000),
            _ledger_entry("2026-07-22", "run-260723-1131-r0h4", 6000),
        ])
        (ledger_dir / "decisions.md").write_text(drifted, encoding="utf-8")

        _, contracts, _whole = _build_injected_blocks_with_contracts(tmp_path)
        by_key = {c.block_key: c for c in contracts}

        assert by_key["work-surface"].stale is True
        assert by_key["work-surface"].newest_item == "2026-07-22"
        assert by_key["work-surface"].source_newest == "2026-07-23 13:42"
        # Every other, non-chronological block stays untouched — defaults.
        assert by_key["identity-core"].stale is False
        assert by_key["identity-core"].newest_item is None


# ── CS6 — runner policy injection ─────────────────────────────────────


class TestRunnerPolicyInjection:
    """CS6: stored runner policy from the account dominion is injected when
    present; silent when absent — standing preferences without ambient noise."""

    def test_absent_when_no_policy_file(self, tmp_path):
        _seed_account_home(tmp_path)
        assert _build_runner_policy_block(tmp_path) == ""

    def test_injects_repo_policy_when_present(self, tmp_path):
        home = _seed_account_home(tmp_path)
        policy_dir = home / "runner-policy" / "local__default"
        policy_dir.mkdir(parents=True)
        (policy_dir / "policy.md").write_text(
            "Prefer haiku for quick tasks.", encoding="utf-8"
        )

        result = _build_runner_policy_block(tmp_path)

        assert "Stored runner policy" in result
        assert "Prefer haiku" in result

    def test_injects_account_policy_when_present(self, tmp_path):
        home = _seed_account_home(tmp_path)
        acct_dir = home / "runner-policy" / "_account"
        acct_dir.mkdir(parents=True)
        (acct_dir / "policy.md").write_text(
            "Escalate to opus for design reviews.", encoding="utf-8"
        )

        result = _build_runner_policy_block(tmp_path)

        assert "Stored runner policy" in result
        assert "Escalate to opus" in result

    def test_includes_both_repo_and_account_policies(self, tmp_path):
        home = _seed_account_home(tmp_path)
        repo_dir = home / "runner-policy" / "local__default"
        repo_dir.mkdir(parents=True)
        (repo_dir / "policy.md").write_text("Repo policy.", encoding="utf-8")
        acct_dir = home / "runner-policy" / "_account"
        acct_dir.mkdir(parents=True)
        (acct_dir / "policy.md").write_text("Account policy.", encoding="utf-8")

        result = _build_runner_policy_block(tmp_path)

        assert "Repo policy" in result
        assert "Account policy" in result

    def test_absent_when_policy_file_is_empty(self, tmp_path):
        home = _seed_account_home(tmp_path)
        policy_dir = home / "runner-policy" / "local__default"
        policy_dir.mkdir(parents=True)
        (policy_dir / "policy.md").write_text("   ", encoding="utf-8")

        assert _build_runner_policy_block(tmp_path) == ""

    def test_policy_block_rides_in_daemon_prompt(self, tmp_path):
        """CS6 runner policy appears in the assembled daemon prompt."""
        prompts = tmp_path / ".brr" / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "run.md").write_text("You are an agent.", encoding="utf-8")
        home = _seed_account_home(tmp_path)
        policy_dir = home / "runner-policy" / "local__default"
        policy_dir.mkdir(parents=True)
        (policy_dir / "policy.md").write_text(
            "Use haiku for cheap tasks.", encoding="utf-8"
        )

        prompt = build_daemon_prompt("quick thing", "evt-1", "/tmp/r.md", tmp_path)

        assert "Stored runner policy" in prompt
        assert "Use haiku" in prompt


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

    assert "run-prior · done · finished · claude-opus · pushed" in block
    assert "Landing the edge writer." in block
    # The shape, not the territory: section names without their contents.
    assert "also in that body: Arc · Open" in block
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


# ── _kb_ownership_signal — orphan naming (#649) ───────────────────────────────


def _make_stats(**kw):
    """Minimal GraphStats factory so tests don't import the dataclass everywhere."""
    from brr.kb_health import GraphStats

    defaults = dict(total_pages=10, total_bytes=1000, log_bytes=5000, log_entry_count=100)
    defaults.update(kw)
    return GraphStats(**defaults)


def test_kb_ownership_signal_names_one_orphan():
    """A single orphan shows its basename in the signal."""
    from brr.prompts import _kb_ownership_signal

    stats = _make_stats(peer_orphans=["kb/distinctive-orphan.md"])
    out = _kb_ownership_signal([], stats)
    assert "distinctive-orphan.md" in out
    # The name must not survive as a host path.
    assert "/kb/distinctive-orphan.md" not in out


def test_kb_ownership_signal_names_from_the_list_not_a_count():
    """A distinctive filename must appear verbatim — counting alone cannot catch this."""
    from brr.prompts import _kb_ownership_signal

    stats = _make_stats(peer_orphans=["kb/xyzzy-sentinel-page.md"])
    out = _kb_ownership_signal([], stats)
    assert "xyzzy-sentinel-page.md" in out


def test_kb_ownership_signal_three_orphans_no_truncation():
    """Exactly 3 orphans: all names shown, no ellipsis."""
    from brr.prompts import _kb_ownership_signal

    stats = _make_stats(peer_orphans=["kb/alpha.md", "kb/beta.md", "kb/gamma.md"])
    out = _kb_ownership_signal([], stats)
    assert "alpha.md" in out
    assert "beta.md" in out
    assert "gamma.md" in out
    assert "more" not in out


def test_kb_ownership_signal_five_orphans_truncated():
    """5 orphans: first 3 basenames shown, then '… and 2 more'; 4th and 5th absent."""
    from brr.prompts import _kb_ownership_signal

    stats = _make_stats(
        peer_orphans=["kb/a.md", "kb/b.md", "kb/c.md", "kb/d.md", "kb/e.md"]
    )
    out = _kb_ownership_signal([], stats)
    assert "a.md" in out
    assert "b.md" in out
    assert "c.md" in out
    assert "… and 2 more" in out
    assert "d.md" not in out
    assert "e.md" not in out


def test_kb_ownership_signal_zero_orphans_size_pressure_byte_identical():
    """Zero orphans + size pressure only must produce the exact pre-#649 string.

    Pinned so the orphan-naming change cannot silently widen the common path.
    """
    from brr.prompts import _kb_ownership_signal

    stats = _make_stats(peer_orphans=[])
    out = _kb_ownership_signal(["x", "y"], stats)
    expected = (
        "**Ownership signal** — 2 page(s)/log over a size threshold. Not a list of "
        "pages to trim: a byte count cannot tell a load-bearing page from bloat — you "
        "can. The graph is 10 pages, log 5,000 B over 100 entries. Read this as the kb "
        "asking for a maintenance *round* — promote what's load-bearing, breadcrumb "
        "what's spent, cut what's dead, relink the orphans. Worker-delegable; worth a "
        "dedicated pass, not a per-wake reflex to shorten the longest file. Full graph "
        "shape on demand: `brnrd kb`."
    )
    assert out == expected


def test_kb_ownership_signal_zero_orphans_zero_pressure_returns_empty():
    """When there is nothing to report the function must return the empty string."""
    from brr.prompts import _kb_ownership_signal

    stats = _make_stats(peer_orphans=[])
    assert _kb_ownership_signal([], stats) == ""


# ── kb mirror state on the wake surface (#667) ───────────────────────
#
# Driven through `_build_kb_health_block` — the function the wake actually
# calls — against real checkouts on disk, not against `_kb_mirror_signal` with
# a hand-made state object. The defect #667 names lives in what the *wake*
# does or doesn't say, so the helper in isolation is not the caller that
# matters. `kb_preflight.scan` is stubbed to `[]` in these so the only thing
# that can put the block on screen is the mirror; the mirror path itself —
# checkout, fetch, refs, counts — stays real end to end.


class TestKbMirrorSignal:
    def _repo_with_kb(self, tmp_path, monkeypatch):
        """A repo whose `.brnrd-kb/` is a real clone of a real account repo."""
        import subprocess

        from brr import kb_preflight, knowledge

        repo = tmp_path / "repo"
        (repo / ".brr" / "prompts").mkdir(parents=True)
        (repo / ".brr" / "prompts" / "run.md").write_text("You are an agent.")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        home = tmp_path / "home"
        krepo = home / "knowledge"
        krepo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=krepo, check=True)
        (krepo / "index.md").write_text("v1\n", encoding="utf-8")
        self._commit(krepo, "seed")
        (repo / ".brr" / "config").write_text(
            f"home.path={home}\n", encoding="utf-8"
        )
        cfg = {"home.path": str(home)}
        checkout = knowledge.ensure_checkout(repo, cfg)
        self._only_the_mirror_may_speak(monkeypatch)
        return repo, krepo, checkout, cfg

    @staticmethod
    def _only_the_mirror_may_speak(monkeypatch):
        """Silence this block's *other* two contributors.

        Both are real collaborators of `_build_kb_health_block` and both would
        otherwise put the block on screen for reasons that have nothing to do
        with the mirror — the ownership signal in particular fires the moment
        a fast-forward lands a page nothing links to yet, which is exactly
        what the current-mirror fixture does on purpose. Stubbing them is what
        makes "the block is empty" mean "the mirror said nothing"; the mirror's
        own path stays real from checkout to count.
        """
        from brr import kb_health, kb_preflight

        monkeypatch.setattr(kb_preflight, "scan", lambda _root, _kb=None: [])
        monkeypatch.setattr(
            kb_health, "compute_graph_stats",
            lambda _root, _kb=None: kb_health.GraphStats(total_pages=0, total_bytes=0),
        )

    @staticmethod
    def _commit(repo, message):
        import subprocess

        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", message],
            cwd=repo, check=True,
        )

    def _fall_behind(self, repo, krepo, checkout, cfg, *, clean):
        """Reach a genuinely-behind mirror the way production reaches it."""
        import subprocess

        from brr import knowledge

        (krepo / "late-page.md").write_text("account-side\n", encoding="utf-8")
        self._commit(krepo, "add late-page.md")
        (checkout / "index.md").write_text("mid-write\n", encoding="utf-8")
        knowledge.ensure_checkout(repo, cfg)  # real fetch, real ff-skip
        if clean:
            subprocess.run(
                ["git", "checkout", "--", "index.md"], cwd=checkout, check=True
            )
        assert not (checkout / "late-page.md").exists()  # guard the fixture

    def test_behind_and_clean_gets_a_line_naming_the_count(
        self, tmp_path, monkeypatch
    ):
        """The headline case: one line, the real count, and why `git status`
        told the resident nothing."""
        from brr.prompts import _build_kb_health_block

        repo, krepo, checkout, cfg = self._repo_with_kb(tmp_path, monkeypatch)
        self._fall_behind(repo, krepo, checkout, cfg, clean=True)

        block = _build_kb_health_block(repo)

        assert "kb health" in block
        assert "1 commit behind" in block
        assert "`origin/main`" in block
        assert "`git status` there reads clean" in block

    def test_behind_and_dirty_gets_a_different_sentence(
        self, tmp_path, monkeypatch
    ):
        """Dirty is a different next action — commit or discard — so it must
        not be told with the clean sentence."""
        from brr.prompts import _build_kb_health_block

        repo, krepo, checkout, cfg = self._repo_with_kb(tmp_path, monkeypatch)
        self._fall_behind(repo, krepo, checkout, cfg, clean=False)

        block = _build_kb_health_block(repo)

        assert "1 commit behind" in block
        assert "uncommitted work" in block
        assert "reads clean" not in block

    def test_diverged_gets_a_third_sentence(self, tmp_path, monkeypatch):
        """Behind *and* ahead never fast-forwards on its own; saying "the
        next capture catches it up" here would be a lie."""
        from brr import knowledge
        from brr.prompts import _build_kb_health_block

        repo, krepo, checkout, cfg = self._repo_with_kb(tmp_path, monkeypatch)
        (krepo / "account-side.md").write_text("theirs\n", encoding="utf-8")
        self._commit(krepo, "account-side work")
        (checkout / "checkout-side.md").write_text("ours\n", encoding="utf-8")
        self._commit(checkout, "checkout-side work")
        knowledge.ensure_checkout(repo, cfg)  # fetch, then ff-only refuses

        block = _build_kb_health_block(repo)

        assert "diverged" in block
        assert "1 ahead" in block
        assert "uncommitted work" not in block

    def test_current_mirror_is_silent(self, tmp_path, monkeypatch):
        """**The bar most likely to regress.** A current mirror renders
        nothing — no line, no empty section, no block at all. #623: a guard
        that fires every wake for a non-reason stops being read, and takes
        the wakes where it *is* a reason down with it."""
        from brr import knowledge
        from brr.prompts import _build_kb_health_block

        repo, krepo, checkout, cfg = self._repo_with_kb(tmp_path, monkeypatch)
        (krepo / "late-page.md").write_text("account-side\n", encoding="utf-8")
        self._commit(krepo, "add late-page.md")
        knowledge.ensure_checkout(repo, cfg)  # clean, so it fast-forwards
        assert (checkout / "late-page.md").exists()  # guard: genuinely current
        assert knowledge.mirror_state(repo).status == knowledge.MIRROR_CURRENT

        assert _build_kb_health_block(repo) == ""

    def test_absent_mirror_is_silent_but_not_indistinguishable(
        self, tmp_path, monkeypatch
    ):
        """A repo with no `.brnrd-kb/` renders nothing — it has no mirror to
        be stale. But *silent* is a rendering choice, and the code underneath
        must still tell "absent" from "0 behind": collapsing them is the
        exact class of defect (`active_kb_dir`'s `None`) this repo keeps
        paying for. Assert both halves, or the second one rots."""
        from brr import knowledge
        from brr.prompts import _build_kb_health_block, _kb_mirror_signal

        repo = tmp_path / "repo"
        (repo / ".brr" / "prompts").mkdir(parents=True)
        (repo / ".brr" / "prompts" / "run.md").write_text("You are an agent.")
        self._only_the_mirror_may_speak(monkeypatch)
        assert not (repo / knowledge.CHECKOUT_DIRNAME).exists()

        state = knowledge.mirror_state(repo)

        assert _build_kb_health_block(repo) == ""       # renders as nothing…
        assert _kb_mirror_signal(state) == ""
        assert state.status == knowledge.MIRROR_ABSENT  # …but is not "fine"
        assert state.status != knowledge.MIRROR_CURRENT
        assert state.absent_reason

    def test_behind_mirror_reaches_the_assembled_wake_prompt(
        self, tmp_path, monkeypatch
    ):
        """End to end through `build_run_prompt`: a block that renders but
        never gets injected is the same guardrail failure one layer up."""
        repo, krepo, checkout, cfg = self._repo_with_kb(tmp_path, monkeypatch)
        self._fall_behind(repo, krepo, checkout, cfg, clean=True)

        prompt = build_run_prompt("do something", repo)

        assert "1 commit behind" in prompt
    # ── the mirror of the wrong repository (#676) ────────────────────
    #
    # A checkout cloned from another repo is 0-behind and used to render as
    # nothing at all. The wake needs the *pair* of paths, because that is the
    # only reading that distinguishes this from a healthy mirror — `git
    # status` reads clean there, and so does the count.

    def _account_moves_to(self, repo, tmp_path, name="real-home"):
        """Resolution moves to a second real account repo; checkout untouched.

        This is the live 2026-07-09 shape (a `.brnrd-kb` left pointing at an
        early decoy account slot after `cloud.json` started carrying the real
        one), and it is reached the way production reaches it — nothing here
        rewrites the checkout's remote by hand.
        """
        import subprocess

        home = tmp_path / name
        krepo = home / "knowledge"
        krepo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=krepo, check=True)
        (krepo / "index.md").write_text("the real account\n", encoding="utf-8")
        self._commit(krepo, "seed the real account")
        (repo / ".brr" / "config").write_text(
            f"home.path={home}\n", encoding="utf-8"
        )
        return krepo

    def test_a_mirror_of_another_repo_names_both_paths(self, tmp_path, monkeypatch):
        """Both paths, or the line is unactionable: "the mirror is wrong" is
        not a next move, and the reader cannot recover either path from a
        checkout that reads healthy by every other handle."""
        from brr.prompts import _build_kb_health_block

        repo, decoy, checkout, cfg = self._repo_with_kb(tmp_path, monkeypatch)
        real = self._account_moves_to(repo, tmp_path)

        block = _build_kb_health_block(repo)

        assert str(decoy) in block   # where it actually points
        assert str(real) in block    # where this account's pages live
        assert "wrong repository" in block
        assert "brnrd kb" in block   # the one path that repairs it
        assert "behind" not in block  # a count here would be true and useless

    def test_a_mirror_of_another_repo_reaches_the_assembled_wake_prompt(
        self, tmp_path, monkeypatch
    ):
        """Same one-layer-up guardrail as the behind case: rendered is not
        injected."""
        repo, decoy, checkout, cfg = self._repo_with_kb(tmp_path, monkeypatch)
        real = self._account_moves_to(repo, tmp_path)

        prompt = build_run_prompt("do something", repo)

        assert str(decoy) in prompt
        assert str(real) in prompt

    def test_a_correctly_pointed_mirror_still_says_nothing(
        self, tmp_path, monkeypatch
    ):
        """The identity check's own #623 bar, on the surface that matters: the
        wake sees an empty block for a checkout that is where it belongs, even
        though the block now asks one more question than it used to."""
        from brr import knowledge
        from brr.prompts import _build_kb_health_block

        repo, krepo, checkout, cfg = self._repo_with_kb(tmp_path, monkeypatch)
        state = knowledge.mirror_state(repo, cfg)
        assert state.status == knowledge.MIRROR_CURRENT  # guard: identity asked
        assert state.status != knowledge.MIRROR_ELSEWHERE

        assert _build_kb_health_block(repo) == ""
