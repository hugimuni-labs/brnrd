"""The note-surface registry and its deterministic preflight.

Two things these tests are built to hold, and one they deliberately don't.

**Every check must be able to go red.** A deterministic check that is
silent when clean is indistinguishable, from the outside, from a check
that is silent always — which is how #985 sat inert for nine days looking
perfectly filed. So each check here is exercised in both directions: the
broken surface produces the finding, and the healthy surface produces
nothing at all. The second half is the one most likely to regress into a
guard that fires every wake for a non-reason.

**Every check must survive its own blind spot.** Each has a sanity
assertion — the state where the check itself has stopped working (a parser
that indexes nothing, a manifest that resolves to nothing, a signature
scope naming a section that no longer exists) — and that state is tested
too, because a no-op check reports clean.

What they don't do is assert on the operator's live account. The fixtures
build their own dominion, work surface, and git repo under ``tmp_path``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr import dominion, notes, notes_preflight


# ── fixtures ─────────────────────────────────────────────────────────


def _repo(tmp_path: Path) -> Path:
    """A repo whose account home points somewhere empty under tmp_path.

    Without the home pin, ``resident_dominion_candidates`` resolves the
    operator's real dominion and these tests would read live resident
    memory — which is both wrong and unstable.
    """
    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".brr" / "config").write_text(
        f"home.path={tmp_path / 'home'}\n", encoding="utf-8"
    )
    return repo


def _dominion(repo: Path) -> Path:
    path = dominion.dominion_path(repo)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(cwd),
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


def _commit(repo: Path, path: Path, text: str, date: str, message: str) -> None:
    path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    stamp = f"{date}T12:00:00+00:00"
    subprocess.run(
        ["git", "commit", "-m", message, f"--date={stamp}"],
        cwd=repo, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repo),
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
        },
    )


# ── the registry ─────────────────────────────────────────────────────


class TestRegistry:
    def test_every_entry_can_be_located(self):
        """The registry's own sanity assertion.

        An entry with no resolver is a surface nothing can ever find, and
        it renders in ``brnrd notes`` identically to a file that simply
        isn't there — the exact silent-narrowing this module exists to
        end. Held here rather than trusted.
        """
        assert notes.unresolvable_keys() == ()

    def test_keys_are_unique(self):
        keys = [s.key for s in notes.registry()]
        assert len(keys) == len(set(keys))

    def test_every_entry_names_who_reads_it(self):
        """"Who reads this" is the field a resident cannot get anywhere
        else — the grammar is half an answer without it."""
        for surface in notes.registry():
            assert surface.readers, surface.key
            assert surface.role, surface.key
            assert surface.grammar, surface.key

    def test_every_root_is_a_known_root(self):
        for surface in notes.registry():
            assert surface.root in notes.ROOT_ORDER, surface.key

    def test_trait_lookup_is_how_a_check_selects_its_targets(self):
        """A check joins on a trait, never on a filename.

        The point is that a second signed page enrols itself in the
        signature check by declaring the trait — no edit to the check, and
        no divergence between what the check covers and what the class
        contains.
        """
        signed = notes.with_trait("signatures")
        assert [s.key for s in signed] == ["workflow"]
        assert notes.with_trait("no-such-trait") == ()

    def test_resolution_survives_a_repo_with_no_account_home(self, tmp_path):
        """Nothing raises, everything reports honestly as absent."""
        repo = _repo(tmp_path)
        rows = notes.resolve(repo)
        assert len(rows) == len(notes.registry())
        assert all(not row.note for row in rows)

    def test_a_resolved_row_carries_bytes_and_mtime(self, tmp_path):
        repo = _repo(tmp_path)
        dom = _dominion(repo)
        (dom / "playbook.md").write_text("## One\nbody\n", encoding="utf-8")
        row = next(
            r for r in notes.resolve(repo, keys=["playbook"])
        )
        assert row.exists
        assert row.bytes == len("## One\nbody\n")
        assert row.mtime is not None


# ── check 1: the pitfall store ───────────────────────────────────────


class TestPitfallStore:
    def test_a_triggerless_entry_is_named(self, tmp_path):
        repo = _repo(tmp_path)
        dom = _dominion(repo)
        (dom / "pitfalls.md").write_text(
            "## Seeing unshipped frontend\nRead the probe first.\n",
            encoding="utf-8",
        )
        findings = notes_preflight.check_pitfall_store(dom, "account:x")
        assert [f.type for f in findings] == ["inert-pitfall"]
        assert "Seeing unshipped frontend" in findings[0].target
        assert "account:x" in findings[0].description

    def test_a_healthy_store_is_silent(self, tmp_path):
        """The bar most likely to regress: a clean store renders nothing."""
        repo = _repo(tmp_path)
        dom = _dominion(repo)
        (dom / "pitfalls.md").write_text(
            "## Docker rebuild\ntrigger: docker\nRebuild first.\n\n"
            "## Quota flicker\ntrigger: quota, budget\nCheck the provider.\n",
            encoding="utf-8",
        )
        assert notes_preflight.check_pitfall_store(dom) == []

    def test_the_measured_failure_the_wrong_key_spelling(self, tmp_path):
        """#985 verbatim: ``**Trigger:**`` where the parser reads ``trigger:``.

        The entry looks filed — heading, bold label, prose — and matches
        nothing forever. This is the whole reason the check exists, so it
        is asserted as its own case rather than folded into the generic
        triggerless one.
        """
        repo = _repo(tmp_path)
        dom = _dominion(repo)
        (dom / "pitfalls.md").write_text(
            "## The demoted lesson\n"
            "**Trigger:** prompt-contract, boot surface\n"
            "Demoting a lesson out of the playbook loses its trigger line.\n",
            encoding="utf-8",
        )
        findings = notes_preflight.check_pitfall_store(dom)
        assert [f.type for f in findings] == ["inert-pitfall"]

    def test_a_heading_the_parser_never_indexes_is_reported(
        self, tmp_path, monkeypatch
    ):
        """The diff, not the parametrisation.

        The check compares the file's ``## `` headings against what
        ``parse_pitfalls`` actually returned. Simulating a parser that
        drops one entry proves the diff is real — a check that iterated
        over the parser's own list could not see this at all.
        """
        repo = _repo(tmp_path)
        dom = _dominion(repo)
        (dom / "pitfalls.md").write_text(
            "## Kept\ntrigger: a\nbody\n\n## Dropped\ntrigger: b\nbody\n",
            encoding="utf-8",
        )
        from brr import pitfalls as pitfalls_mod

        real = pitfalls_mod.parse_pitfalls
        monkeypatch.setattr(
            pitfalls_mod, "parse_pitfalls",
            lambda d: [p for p in real(d) if p.title != "Dropped"],
        )
        findings = notes_preflight.check_pitfall_store(dom)
        assert [f.type for f in findings] == ["unindexed-pitfall-section"]
        assert "Dropped" in findings[0].target

    def test_the_sanity_assertion_headings_but_no_entries(
        self, tmp_path, monkeypatch
    ):
        """The state where this check has gone blind reports *itself*.

        A grammar change or parser regression that indexes nothing would
        make a naive check pass over an empty set and report clean. Here
        it produces one ``error`` and suppresses the per-entry findings,
        which would be meaningless.
        """
        repo = _repo(tmp_path)
        dom = _dominion(repo)
        (dom / "pitfalls.md").write_text(
            "## One\ntrigger: a\nbody\n\n## Two\nbody\n", encoding="utf-8",
        )
        from brr import pitfalls as pitfalls_mod

        monkeypatch.setattr(pitfalls_mod, "parse_pitfalls", lambda d: [])
        findings = notes_preflight.check_pitfall_store(dom)
        assert [f.type for f in findings] == ["pitfall-store-unreadable"]
        assert findings[0].severity == "error"

    def test_no_file_at_all_is_not_a_finding(self, tmp_path):
        repo = _repo(tmp_path)
        assert notes_preflight.check_pitfall_store(_dominion(repo)) == []


# ── check 2: eviction preview ────────────────────────────────────────


class TestSelfInjectEviction:
    def _dom_with_manifest(self, tmp_path: Path, body: str) -> Path:
        repo = _repo(tmp_path)
        dom = _dominion(repo)
        (dom / "playbook.md").write_text(body, encoding="utf-8")
        (dom / dominion.SELF_INJECT_FILE).write_text(
            "# a comment\nfull playbook.md\n", encoding="utf-8",
        )
        return dom

    def test_within_budget_is_silent(self, tmp_path):
        dom = self._dom_with_manifest(tmp_path, "## One\nshort\n")
        assert notes_preflight.check_self_inject_eviction(
            dom, budget_bytes=20_480,
        ) == []

    def test_over_budget_names_the_bytes_and_the_sections(self, tmp_path):
        """The #1020 case, one wake earlier.

        The numbers come from ``resolve_self_inject_digest``'s own
        ``InjectOverflow`` record — not from re-doing the arithmetic here
        — and the collapsed section names are read back out of the banner
        the real collapser wrote, so a finding names the sections the wake
        will actually lose.
        """
        body = (
            "preamble\n\n"
            "## Invariants\n" + ("keep this. " * 40) + "\n\n"
            "## Middle\n" + ("middling. " * 60) + "\n\n"
            "## The bottom section\n" + ("evicted. " * 60) + "\n"
        )
        dom = self._dom_with_manifest(tmp_path, body)
        findings = notes_preflight.check_self_inject_eviction(
            dom, budget_bytes=900, label="account:x",
        )
        assert [f.type for f in findings] == ["eviction-preview"]
        text = findings[0].description
        assert "over its 900 B ceiling" in text
        assert "The bottom section" in text
        assert "account:x" in text

    def test_the_return_value_is_a_pair_not_a_string(self, tmp_path):
        """A guard against the bug the ticket named in advance.

        ``_collapse_markdown_to_budget`` and ``resolve_self_inject_digest``
        both return ``(text, extra)``. An ``out == text`` comparison
        against the tuple is always ``False``, so a checker written that
        way passes silently on every input. The overflow *record* is the
        only honest signal, and this pins that it is what the check reads.
        """
        dom = self._dom_with_manifest(tmp_path, "## One\n" + ("x " * 4000))
        digest, overflow = dominion.resolve_self_inject_digest(
            dom, budget_bytes=500,
        )
        assert isinstance(digest, str)
        assert overflow is not None
        assert overflow.total_dropped_bytes > 0

    def test_the_sanity_assertion_entries_but_no_digest(self, tmp_path):
        """A manifest that resolves to nothing is reported, not called clean.

        Nothing overflowed — because nothing was spent. Every entry was
        skipped (missing path here; in the wild also an unknown mode, or
        an ``exec`` entry, which is parsed and deliberately not run).
        """
        repo = _repo(tmp_path)
        dom = _dominion(repo)
        (dom / dominion.SELF_INJECT_FILE).write_text(
            "full nowhere.md\n", encoding="utf-8",
        )
        findings = notes_preflight.check_self_inject_eviction(dom)
        assert [f.type for f in findings] == ["self-inject-empty"]
        assert findings[0].severity == "error"

    def test_an_empty_manifest_is_not_a_finding(self, tmp_path):
        repo = _repo(tmp_path)
        dom = _dominion(repo)
        (dom / dominion.SELF_INJECT_FILE).write_text(
            "# only comments\n\n", encoding="utf-8",
        )
        assert notes_preflight.check_self_inject_eviction(dom) == []


class TestWorkSurfaceEviction:
    """Driven through the real assembler, at a real budget.

    The check reads back the two markers
    ``prompts._build_work_surface_block_scored`` renders when a page did
    not fit — the per-page placeholder and the exhausted-budget line, both
    of which #1020 made name their pages. That read-back is the coupling
    most likely to rot, so it is exercised end to end rather than against
    a hand-written sample of the marker text.
    """

    @staticmethod
    def _home(tmp_path: Path, budget: int) -> Path:
        home = tmp_path / "acct-home"
        home.mkdir(parents=True)
        (tmp_path / ".brr").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".brr" / "config").write_text(
            f"home.path={home}\nrepo.label=local/default\n"
            f"dominion.surface_inject_budget_bytes={budget}\n",
            encoding="utf-8",
        )
        surface = home / "surface"
        surface.mkdir()
        return surface

    def test_a_healthy_budget_is_silent(self, tmp_path):
        surface = self._home(tmp_path, 40_000)
        (surface / "index.md").write_text("# Index\n\nsmall\n", encoding="utf-8")
        (surface / "zzz-other.md").write_text(
            "## Delivery\n\nshort\n", encoding="utf-8",
        )
        assert notes_preflight.check_work_surface_eviction(tmp_path) == []

    def test_an_evicted_page_is_named_on_its_own_row(self, tmp_path):
        """One finding per page, targeted at that page.

        A single finding listing three pages is a line a reader can
        neither verify nor act on page by page; the per-page target is
        what lets `brnrd notes` put the verdict on the evicted page's own
        row instead of a bare ``surface/``.
        """
        surface = self._home(tmp_path, 900)
        (surface / "index.md").write_text(
            "# Index\n\n" + ("the opening page's content " * 60),
            encoding="utf-8",
        )
        (surface / "zzz-other.md").write_text(
            "## Delivery\n\n" + ("later page content " * 40), encoding="utf-8",
        )
        findings = notes_preflight.check_work_surface_eviction(tmp_path)
        assert findings
        assert all(f.type == "eviction-preview" for f in findings)
        assert any("zzz-other.md" in f.target for f in findings)


# ── check 3: signatures ──────────────────────────────────────────────


_SIGNED_PAGE = """\
# Workflow

## Autonomy

- reversible calls are the resident's to take.

## Gating and merges

- PRs stay the delivery vehicle.

## Progress cadence

- a card is a live surface.

## Signatures

    signed-by: maintainer
    date: 2026-07-16
    scope: Autonomy; Gating and merges — progress visibility over gating
    basis: "work gating is not the best way anyway"
"""


class TestSignatureParsing:
    def test_the_four_keys_parse_without_a_model(self):
        records = notes_preflight.parse_signatures(_SIGNED_PAGE)
        assert len(records) == 1
        record = records[0]
        assert record.signed_by == "maintainer"
        assert record.date == "2026-07-16"
        assert record.sections == ("autonomy", "gating and merges")

    def test_a_continuation_line_appends_to_the_open_key(self):
        text = (
            "## Signatures\n\n"
            "    signed-by: both\n"
            "    date: 2026-07-24\n"
            "    scope: Delivery and ceremony — the message economy\n"
            "      block, wrapped across two lines\n"
            "    basis: agreed in thread\n"
        )
        record = notes_preflight.parse_signatures(text)[0]
        assert "wrapped across two lines" in record.scope
        assert record.sections == ("delivery and ceremony",)

    def test_a_retracted_record_stops_covering_anything(self):
        """The live file keeps a retracted signature on purpose — "the
        shortest proof the block works". A parser that counted it as live
        coverage would report a retracted clause as agreed."""
        text = (
            "## Signatures\n\n"
            "    signed-by: both\n"
            "    date: 2026-07-25\n"
            "    scope: Gating and merges — a bot PR stays a draft\n"
            "    basis: agreed, then not\n"
            "    RETRACTED 2026-07-25 by both — see the strikethrough\n"
        )
        assert notes_preflight.parse_signatures(text)[0].retracted is True

    def test_a_heading_parenthetical_does_not_break_the_match(self):
        """Headings carry parentheticals a scope line never repeats."""
        assert (
            notes_preflight._normalise_section(
                "Orchestration (2026-08-01, from the maintainer's steer)"
            ) == "orchestration"
        )


class TestSignatureFindings:
    def test_an_unsigned_section_is_reported_flatly(self, tmp_path):
        path = tmp_path / "workflow.md"
        path.write_text(_SIGNED_PAGE, encoding="utf-8")
        findings = notes_preflight.check_signatures(path)
        assert [f.type for f in findings] == ["unsigned-clause"]
        assert "Progress cadence" in findings[0].target

    def test_the_signatures_block_itself_is_not_a_clause(self, tmp_path):
        path = tmp_path / "workflow.md"
        path.write_text(_SIGNED_PAGE, encoding="utf-8")
        targets = [f.target for f in notes_preflight.check_signatures(path)]
        assert not any("Signatures" in t for t in targets)

    def test_a_fully_signed_page_is_silent(self, tmp_path):
        page = _SIGNED_PAGE.replace(
            "scope: Autonomy; Gating and merges — progress visibility",
            "scope: Autonomy; Gating and merges; Progress cadence — all of it",
        )
        path = tmp_path / "workflow.md"
        path.write_text(page, encoding="utf-8")
        assert notes_preflight.check_signatures(path) == []

    def test_the_sanity_assertion_a_scope_naming_no_section(self, tmp_path):
        """A renamed section silently un-covers itself.

        Without this the clause would simply be reported as never-signed
        and the *reason* — that a signature still points at its old title —
        would be invisible. It outranks the unsigned finding it explains,
        which is why it is an ``error``.
        """
        page = _SIGNED_PAGE.replace("## Autonomy", "## Autonomy and scope")
        path = tmp_path / "workflow.md"
        path.write_text(page, encoding="utf-8")
        findings = notes_preflight.check_signatures(path)
        top = findings[0]
        assert top.type == "signature-scope-unmatched"
        assert top.severity == "error"
        assert "autonomy" in top.description

    def test_a_page_with_sections_and_no_records_says_so(self, tmp_path):
        path = tmp_path / "workflow.md"
        path.write_text("## One\n\nbody\n\n## Two\n\nbody\n", encoding="utf-8")
        findings = notes_preflight.check_signatures(path)
        assert [f.type for f in findings] == ["signature-scope-unmatched"]

    def test_a_rewrite_after_the_signature_is_stale(self, tmp_path):
        """The predicate, held against real git history."""
        repo = tmp_path / "home"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        path = repo / "workflow.md"
        _commit(repo, path, _SIGNED_PAGE, "2026-07-16", "sign it")
        _commit(
            repo, path,
            _SIGNED_PAGE.replace(
                "- reversible calls are the resident's to take.",
                "- reversible calls are the maintainer's to take.",
            ),
            "2026-07-20", "reword the signed clause",
        )
        findings = notes_preflight.check_signatures(
            path, repo_dir=repo, rel_path="workflow.md",
        )
        stale = [f for f in findings if f.type == "stale-signature"]
        assert [f.target for f in stale] == ["workflow.md §Autonomy"]
        assert "1 line" in stale[0].description
        assert "2026-07-20" in stale[0].description

    def test_an_append_beside_signed_text_is_not_stale(self, tmp_path):
        """The 2026-07-25 amendment, enforced.

        As first written the predicate could not tell an append from a
        rewrite, so one new clause staled every prior signature in the
        section — "a guard that fires constantly for a non-reason, which is
        how a guard stops being read." A pure-addition commit must leave
        the signature standing.
        """
        repo = tmp_path / "home"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        path = repo / "workflow.md"
        _commit(repo, path, _SIGNED_PAGE, "2026-07-16", "sign it")
        _commit(
            repo, path,
            _SIGNED_PAGE.replace(
                "- reversible calls are the resident's to take.",
                "- reversible calls are the resident's to take.\n"
                "- and a brand new bullet, added beside it.",
            ),
            "2026-07-20", "append a clause",
        )
        findings = notes_preflight.check_signatures(
            path, repo_dir=repo, rel_path="workflow.md",
        )
        assert [f for f in findings if f.type == "stale-signature"] == []

    def test_no_git_means_undetermined_never_clean(self, tmp_path):
        """A staleness verdict with no history behind it is not produced."""
        path = tmp_path / "workflow.md"
        path.write_text(_SIGNED_PAGE, encoding="utf-8")
        findings = notes_preflight.check_signatures(
            path, repo_dir=tmp_path, rel_path="workflow.md",
        )
        assert [f for f in findings if f.type == "stale-signature"] == []


# ── the scan and its wake block ──────────────────────────────────────


class TestScanAndBlock:
    def test_a_clean_account_renders_no_block(self, tmp_path):
        """Silent when clean — the whole contract in one assertion."""
        from brr.prompts import _build_notes_health_block

        repo = _repo(tmp_path)
        assert _build_notes_health_block(repo) == ""

    def test_kb_maintenance_never_silences_it(self, tmp_path):
        from brr.prompts import _build_notes_health_block

        repo = _repo(tmp_path)
        (repo / ".brr" / "config").write_text(
            f"home.path={tmp_path / 'home'}\nkb_maintenance=never\n",
            encoding="utf-8",
        )
        dom = _dominion(repo)
        (dom / "pitfalls.md").write_text("## Inert\nno trigger\n", encoding="utf-8")
        assert _build_notes_health_block(repo) == ""

    def test_findings_reach_the_rendered_block(self, tmp_path):
        from brr.prompts import _build_notes_health_block

        repo = _repo(tmp_path)
        dom = _dominion(repo)
        (dom / "pitfalls.md").write_text(
            "## Half-drafted\nbody, no triggers yet\n", encoding="utf-8",
        )
        block = _build_notes_health_block(repo)
        assert "notes health" in block
        assert "inert-pitfall" in block
        assert "Half-drafted" in block

    def test_findings_sort_errors_before_advisories(self, tmp_path):
        repo = _repo(tmp_path)
        dom = _dominion(repo)
        (dom / "pitfalls.md").write_text("## Inert\nbody\n", encoding="utf-8")
        (dom / dominion.SELF_INJECT_FILE).write_text(
            "full nowhere.md\n", encoding="utf-8",
        )
        findings = notes_preflight.scan(repo)
        assert [f.severity for f in findings] == ["error", "info"]


# ── the CLI ──────────────────────────────────────────────────────────


class TestNotesCommand:
    def test_the_map_lists_every_registered_surface(self, tmp_path, capsys):
        from brr import cli

        repo = _repo(tmp_path)
        _git(repo, "init", "-q", "-b", "main")
        args = cli.build_parser().parse_args(["notes"])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cli, "_repo_root", lambda: repo)
            args.func(args)
        out = capsys.readouterr().out
        for surface in notes.registry():
            assert surface.key in out

    def test_an_unknown_surface_names_the_known_ones(self, tmp_path, capsys):
        from brr import cli

        repo = _repo(tmp_path)
        _git(repo, "init", "-q", "-b", "main")
        args = cli.build_parser().parse_args(["notes", "nope"])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cli, "_repo_root", lambda: repo)
            assert args.func(args) == 1
        out = capsys.readouterr().out
        assert "no surface named 'nope'" in out
        assert "playbook" in out

    def test_one_surface_prints_grammar_readers_and_budget(
        self, tmp_path, capsys,
    ):
        from brr import cli

        repo = _repo(tmp_path)
        _git(repo, "init", "-q", "-b", "main")
        args = cli.build_parser().parse_args(["notes", "pitfalls"])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cli, "_repo_root", lambda: repo)
            assert args.func(args) == 0
        out = capsys.readouterr().out
        assert "trigger:" in out
        assert "brr.pitfalls.parse_pitfalls" in out
        assert "verdict   clean" in out

    def test_check_exits_nonzero_when_something_is_wrong(
        self, tmp_path, capsys,
    ):
        from brr import cli

        repo = _repo(tmp_path)
        _git(repo, "init", "-q", "-b", "main")
        dom = _dominion(repo)
        (dom / "pitfalls.md").write_text("## Inert\nbody\n", encoding="utf-8")
        args = cli.build_parser().parse_args(["notes", "check"])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cli, "_repo_root", lambda: repo)
            assert args.func(args) == 1
        assert "inert-pitfall" in capsys.readouterr().out

    def test_json_is_machine_readable(self, tmp_path, capsys):
        import json

        from brr import cli

        repo = _repo(tmp_path)
        _git(repo, "init", "-q", "-b", "main")
        args = cli.build_parser().parse_args(["notes", "--json"])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cli, "_repo_root", lambda: repo)
            args.func(args)
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["surfaces"]) == len(notes.registry())
