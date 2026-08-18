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

import re
import subprocess
from pathlib import Path

import pytest

from brr import dominion, notes, notes_preflight


# ── fixtures ─────────────────────────────────────────────────────────


def _repo(tmp_path: Path, *, seed_roots: bool = True) -> Path:
    """A repo with its own account home under tmp_path.

    Two reasons for the home pin. Without it,
    ``resident_dominion_candidates`` resolves the *operator's* real
    dominion and these tests read live resident memory — wrong and
    unstable. And since 2026-08-07 the scan reports a durable root it
    could not read (:func:`notes_preflight.check_roots`). *seed_roots*
    materialises the three durable roots **with a registered surface in
    each** — an empty directory is not a populated root, since that is the
    #1193 fingerprint — which is what most tests here mean by clean. Pass
    ``False`` for an account nothing has ever written to, which reports as
    a *denominator* rather than as a finding.
    """
    repo = tmp_path / "repo"
    (repo / ".brr").mkdir(parents=True)
    (repo / ".brr" / "config").write_text(
        f"home.path={tmp_path / 'home'}\n", encoding="utf-8"
    )
    if seed_roots:
        # Each durable root gets one *registered* surface with content in
        # it. An empty directory is not a populated root — `check_roots`
        # reports "resolved and holds none of its surfaces" as loudly as a
        # missing one, because that is the #1193 fingerprint. So a fixture
        # that means "a healthy account" has to actually put something in
        # each root, and the surfaces chosen here are the inert ones no
        # check reads.
        dom = repo / ".brr" / "dominion"
        dom.mkdir(parents=True, exist_ok=True)
        (dom / "thread-of-record.md").write_text("seed\n", encoding="utf-8")
        surface = tmp_path / "home" / "surface"
        surface.mkdir(parents=True, exist_ok=True)
        (surface / "index.md").write_text("# Surface\n\nseed\n", encoding="utf-8")
        (tmp_path / "home" / "knowledge").mkdir(parents=True, exist_ok=True)
        from brr import knowledge

        kb = knowledge.active_kb_dir(repo)
        if kb is not None:
            Path(kb).mkdir(parents=True, exist_ok=True)
            (Path(kb) / "index.md").write_text("# kb\n", encoding="utf-8")
    return repo


def _dominion(repo: Path) -> Path:
    """The dominion directory *the resolver actually picks* for this repo.

    Not ``dominion.dominion_path`` unconditionally: with an account home
    present, ``resident_dominion_candidates`` prefers the account-scoped
    path over the legacy repo-local one, and a test that seeds the loser
    is testing a directory the code will never read — the exact
    write-into-the-void this module exists to catch, committed by its own
    fixture.
    """
    legacy = dominion.dominion_path(repo)
    legacy.mkdir(parents=True, exist_ok=True)
    return notes.resolve_roots(repo).dominion or legacy


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

    def test_the_budget_is_the_one_the_wake_actually_spends(self, tmp_path):
        """The preview must not measure against a budget nobody spends.

        `adopt.py` writes `dominion.inject_budget_bytes` into every fresh
        install, so a check reading `DEFAULT_INJECT_BUDGET_BYTES` instead
        reports "everything fits" about a digest that is losing sections
        every wake — #1020, reproduced inside the check written to prevent
        it. Both the current key and its legacy spelling resolve, and a
        junk value falls back rather than raising on the wake path.
        """
        body = "preamble\n\n## Keep\n" + ("x " * 300) + "\n\n## Drop\n" + ("y " * 300)
        dom = self._dom_with_manifest(tmp_path, body)
        repo = dom.parent.parent.parent  # .brr/dominion -> .brr -> repo

        assert dominion.inject_budget_bytes({}) == \
            dominion.DEFAULT_INJECT_BUDGET_BYTES
        assert dominion.inject_budget_bytes(
            {"dominion.inject_budget_bytes": 900}) == 900
        assert dominion.inject_budget_bytes(
            {"dominion_inject_budget_bytes": "900"}) == 900
        assert dominion.inject_budget_bytes(
            {"dominion.inject_budget_bytes": "not a number"}
        ) == dominion.DEFAULT_INJECT_BUDGET_BYTES

        # Default budget: the digest fits, so nothing to report.
        assert notes_preflight.check_self_inject_eviction(dom, cfg={}) == []
        # The configured budget the wake would actually spend: it does not.
        findings = notes_preflight.check_self_inject_eviction(
            dom, cfg={"dominion.inject_budget_bytes": 500},
        )
        assert [f.type for f in findings] == ["eviction-preview"]
        assert "500 B ceiling" in findings[0].description
        assert repo.is_dir()

    def test_scan_threads_the_configured_budget_through(self, tmp_path):
        body = "preamble\n\n## Keep\n" + ("x " * 300) + "\n\n## Drop\n" + ("y " * 300)
        dom = self._dom_with_manifest(tmp_path, body)
        repo = tmp_path / "repo"
        (repo / ".brr" / "config").write_text(
            f"home.path={tmp_path / 'home'}\n"
            "dominion.inject_budget_bytes=500\n",
            encoding="utf-8",
        )
        assert dom.is_dir()
        types = [f.type for f in notes_preflight.scan(repo)]
        assert "eviction-preview" in types

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

    def test_a_page_name_with_a_space_is_not_silently_missed(self, tmp_path):
        """``### {relative}`` carries the raw path, spaces included.

        A `\\S+` page group matched the first word and then failed the
        newline, so exactly the pages with a space in the name went
        unreported — silently, which is the class of miss this whole
        module exists to end.
        """
        surface = self._home(tmp_path, 900)
        (surface / "index.md").write_text(
            "# Index\n\n" + ("the opening page's content " * 60),
            encoding="utf-8",
        )
        (surface / "zzz other.md").write_text(
            "## Delivery\n\n" + ("later page content " * 40), encoding="utf-8",
        )
        findings = notes_preflight.check_work_surface_eviction(tmp_path)
        assert any("zzz other.md" in f.target for f in findings)


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
        assert stale[0].severity == "warning"
        assert "1 line" in stale[0].description
        assert "2026-07-20" in stale[0].description
        # #1476: the finding quotes the removed (old) line rather than
        # delegating to a `git show` — a rename commit prints the whole
        # file as an addition, so that delegation is not evidence at all.
        assert "the resident's to take" in stale[0].description
        assert "git show" not in stale[0].description

    def test_a_genuine_multiline_rewrite_of_both_signed_text_stays_warning(
        self, tmp_path,
    ):
        """#1476 shape 2: no rename anywhere in the commit ⇒ `warning`
        stands, and the quote carries every replaced line, not just the
        first — the reader is meant to be able to judge the whole clause
        from the finding, not just its opening word.
        """
        repo = tmp_path / "home"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        # A second signer covering the same clause — the real #1476 shape
        # ("both" signed §Gating and merges) — so `who` names two signers.
        # The clause itself starts multi-line so the *rewrite* removes more
        # than one line (a single-line original can only ever contribute
        # one `-` line to the diff, however long its replacement is).
        page = _SIGNED_PAGE.replace(
            "## Gating and merges\n\n- PRs stay the delivery vehicle.\n",
            "## Gating and merges\n\n"
            "- PRs stay open until review clears,\n"
            "  and a bot-authored PR cannot approve its own diff,\n"
            "  so the draft/ready split is structural, not a preference.\n",
        ).replace(
            "    basis: \"work gating is not the best way anyway\"\n",
            "    basis: \"work gating is not the best way anyway\"\n"
            "    signed-by: resident\n"
            "    date: 2026-07-17\n"
            "    scope: Gating and merges — same clause, second signer\n"
            "    basis: agreed\n",
        )
        path = repo / "workflow.md"
        _commit(repo, path, page, "2026-07-16", "sign it")
        _commit(
            repo, path,
            page.replace(
                "- PRs stay open until review clears,\n"
                "  and a bot-authored PR cannot approve its own diff,\n"
                "  so the draft/ready split is structural, not a preference.\n",
                "- ready-for-review is the verdict; nothing else moves a PR\n"
                "  out of draft.\n",
            ),
            "2026-07-25", "rewrite the gating clause",
        )
        findings = notes_preflight.check_signatures(
            path, repo_dir=repo, rel_path="workflow.md",
        )
        stale = [f for f in findings if f.type == "stale-signature"]
        assert [f.target for f in stale] == ["workflow.md §Gating and merges"]
        assert stale[0].severity == "warning"
        assert "3 lines" in stale[0].description
        assert "maintainer" in stale[0].description
        assert "resident" in stale[0].description
        assert "PRs stay open until review clears" in stale[0].description

    def test_the_quote_fence_outruns_backticks_inside_the_removed_line(
        self, tmp_path,
    ):
        """The quote is arbitrary file content, and CommonMark closes a code
        span on a backtick string of **equal** length — so a fixed ``-wide
        fence spills a removed line that carries its own ``double`` span
        into the description as markup. The fence must outrun the longest
        run inside it. Driven through the real caller because the bug is in
        the rendered description, which is the only thing a wake reads.
        """
        repo = tmp_path / "home"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        clause = "- reversible calls are the ``resident``'s to take."
        page = _SIGNED_PAGE.replace(
            "- reversible calls are the resident's to take.", clause,
        )
        path = repo / "workflow.md"
        _commit(repo, path, page, "2026-07-16", "sign it")
        _commit(
            repo, path,
            page.replace(clause, "- reversible calls are the operator's."),
            "2026-07-20", "rewrite it",
        )

        findings = notes_preflight.check_signatures(
            path, repo_dir=repo, rel_path="workflow.md",
        )
        stale = [f for f in findings if f.type == "stale-signature"]
        assert len(stale) == 1
        desc = stale[0].description
        assert clause in desc

        # The property, not the implementation: the fence run that opens the
        # quote is strictly longer than every backtick run inside it.
        opener = re.search(r"the removed text: (`+)", desc)
        assert opener is not None, desc
        inner = max(len(m) for m in re.findall(r"`+", clause))
        assert len(opener.group(1)) > inner, (opener.group(1), inner)

    def test_a_rename_softens_severity_and_names_the_old_path(self, tmp_path):
        """#1476 shape 1: a rename that fixes a path line inside a signed
        section ⇒ the finding survives (never suppressed), drops to
        `info`, names the rename, and quotes the removed line.
        """
        repo = tmp_path / "home"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        old_path = repo / "old_workflow.md"
        _commit(repo, old_path, _SIGNED_PAGE, "2026-07-16", "sign it")

        # A real move: `git mv` plus, in the same commit, a self-referential
        # path fixup inside the already-signed §Autonomy section — the
        # shape a repo reorganisation actually produces.
        _git(repo, "mv", "old_workflow.md", "workflow.md")
        new_path = repo / "workflow.md"
        new_path.write_text(
            _SIGNED_PAGE.replace(
                "- reversible calls are the resident's to take.",
                "- reversible calls (see old_workflow.md) are the "
                "resident's to take.",
            ),
            encoding="utf-8",
        )
        _git(repo, "add", "-A")
        stamp = "2026-07-20T12:00:00+00:00"
        subprocess.run(
            ["git", "commit", "-m", "move the file, fix the self-reference",
             f"--date={stamp}"],
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

        findings = notes_preflight.check_signatures(
            new_path, repo_dir=repo, rel_path="workflow.md",
        )
        stale = [f for f in findings if f.type == "stale-signature"]
        assert [f.target for f in stale] == ["workflow.md §Autonomy"]
        assert stale[0].severity == "info"
        assert "old_workflow.md" in stale[0].description
        assert "resident's to take." in stale[0].description

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

    def test_an_inherited_git_pin_cannot_redirect_the_history_walk(
        self, tmp_path, monkeypatch,
    ):
        """The wake-path hazard, held.

        Under a brnrd wake ``GIT_DIR`` / ``GIT_WORK_TREE`` are pinned to the
        run's own worktree, and a bare ``git`` obeys the pin over ``cwd=``.
        This check asks a *different* repository — the account home — for a
        section's history, so an unscrubbed subprocess would confidently
        answer for the wrong tree: `git log -L` on a path that does not
        exist there returns nothing, and "nothing was ever rewritten" is
        indistinguishable from "everything is signed and current".

        ``gitops.explicit_repo_env()`` is the scrub. This pins that it is
        actually applied, by setting the pin and asserting the finding
        still lands.
        """
        pin = tmp_path / "elsewhere"
        pin.mkdir()
        _git(pin, "init", "-q", "-b", "main")

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

        monkeypatch.setenv("GIT_DIR", str(pin / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(pin))

        assert notes_preflight._git_location(path)[0] == repo
        stale = [
            f for f in notes_preflight.check_signatures(
                path, repo_dir=repo, rel_path="workflow.md",
            )
            if f.type == "stale-signature"
        ]
        assert [f.target for f in stale] == ["workflow.md §Autonomy"]

    def test_an_uncommitted_edit_cannot_shift_the_walk(self, tmp_path):
        """The working tree's line numbers are not HEAD's.

        ``git log -L<start>,<end>`` resolves its range against HEAD and
        *clamps* an out-of-range end rather than erroring — so feeding it
        numbers counted from the file on disk produces a confident wrong
        answer with no signal at all. An uncommitted maintainer edit to
        ``workflow.md`` is that file's normal state between capture
        commits: one added section at the top shifts every range down, and
        the rewrite in §Gating and merges gets reported against §Autonomy.

        Here the rewrite is in §Gating and merges, and an uncommitted
        6-line preface sits above it. The finding must still name §Gating
        and merges, and must not name §Autonomy.
        """
        repo = tmp_path / "home"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        path = repo / "workflow.md"
        _commit(repo, path, _SIGNED_PAGE, "2026-07-16", "sign it")
        rewritten = _SIGNED_PAGE.replace(
            "- PRs stay the delivery vehicle.",
            "- PRs stay the delivery vehicle, reworded.",
        )
        _commit(repo, path, rewritten, "2026-07-20", "reword a signed clause")

        # …and now an uncommitted six-line section above everything.
        path.write_text(
            "# Workflow\n\n## Preface\n\nnew\nlines\nhere\nuncommitted\n\n"
            + rewritten.split("# Workflow\n", 1)[1],
            encoding="utf-8",
        )

        stale = [
            f for f in notes_preflight.check_signatures(
                path, repo_dir=repo, rel_path="workflow.md",
            )
            if f.type == "stale-signature"
        ]
        assert [f.target for f in stale] == ["workflow.md §Gating and merges"]

    def test_a_section_not_yet_at_head_is_undetermined(self, tmp_path):
        """An uncommitted new section has no history to judge it by."""
        repo = tmp_path / "home"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        path = repo / "workflow.md"
        _commit(repo, path, _SIGNED_PAGE, "2026-07-16", "sign it")
        path.write_text(
            _SIGNED_PAGE + "\n## Brand new\n\nnever committed\n",
            encoding="utf-8",
        )
        findings = notes_preflight.check_signatures(
            path, repo_dir=repo, rel_path="workflow.md",
        )
        assert [f for f in findings if f.type == "stale-signature"] == []
        # …but it is still reported as binding nobody, which needs no git.
        assert any(
            f.type == "unsigned-clause" and "Brand new" in f.target
            for f in findings
        )

    def test_no_git_means_undetermined_never_clean(self, tmp_path):
        """A staleness verdict with no history behind it is not produced."""
        path = tmp_path / "workflow.md"
        path.write_text(_SIGNED_PAGE, encoding="utf-8")
        findings = notes_preflight.check_signatures(
            path, repo_dir=tmp_path, rel_path="workflow.md",
        )
        assert [f for f in findings if f.type == "stale-signature"] == []


# ── the scan and its wake block ──────────────────────────────────────


class TestTheScanKnowsWhatItCouldNotSee:
    """The guard the other three checks needed, turned on their author.

    Measured 2026-08-07 on a live account: `brnrd notes check` printed
    *"all registered surfaces clean"* and `--json` printed `[]`, against an
    account the same code had measured at five findings minutes earlier —
    because 17 of 22 surfaces had not resolved and the checks had iterated
    over nothing. A clean verdict about an empty set is exactly the
    silent-narrowing every check here exists to catch, and it happened one
    layer out, in the scan itself.
    """

    def test_an_unconfigured_repo_says_nothing(self, tmp_path):
        """No brnrd here ⇒ no claim to fall short of ⇒ no line."""
        repo = tmp_path / "bare"
        (repo / ".brr").mkdir(parents=True)
        (repo / ".brr" / "config").write_text(
            "dominion.enabled=false\n", encoding="utf-8",
        )
        roots = notes.resolve_roots(repo)
        if roots.account_enabled:
            pytest.skip("this environment resolves an account for a bare repo")
        assert notes_preflight.check_roots(roots) == []

    def test_a_fresh_account_is_a_denominator_not_an_accusation(self, tmp_path):
        """Nothing written anywhere yet is not a blind spot.

        A fresh adopter's first wake has no dominion, no surface, no kb —
        because it has not written one, not because the scan failed. An
        `error` on day one is a guard firing for a non-reason on the wake
        least able to judge it. The honest report there is the *scope
        line*, which is unconditional.
        """
        repo = _repo(tmp_path, seed_roots=False)
        findings, scope = notes_preflight.scan_scoped(repo)
        assert findings == []
        assert scope.unresolved_roots == ()
        assert scope.located < scope.registered

    def test_every_root_missing_under_a_used_account_names_the_home(
        self, tmp_path,
    ):
        """`home-unresolved` is reachable for a caller holding only roots.

        `scan` always passes rows, so on that path the per-root findings
        below carry the report; this pins that the blanket form still
        renders, and still names #1193, for anything that cannot.
        """
        repo = _repo(tmp_path, seed_roots=False)
        roots = notes.resolve_roots(repo)
        findings = notes_preflight.check_roots(roots)
        assert [f.type for f in findings] == ["home-unresolved"]
        assert findings[0].severity == "error"
        assert str(tmp_path / "home") in findings[0].target
        assert "#1193" in findings[0].description

    def test_one_missing_root_names_that_root(self, tmp_path):
        """A partial blind spot is per-root, not the blanket finding.

        Seeding ``home/surface`` also resolves the dominion root — the
        account-root candidate *is* the home directory, same as the wake's
        own ``_build_dominion_block`` resolves it — so what is left missing
        is the kb, and that is what must be named.
        """
        repo = _repo(tmp_path, seed_roots=False)
        surface = tmp_path / "home" / "surface"
        surface.mkdir(parents=True)
        (surface / "index.md").write_text("# Surface\n", encoding="utf-8")
        (repo / ".brr" / "dominion").mkdir(parents=True)
        (repo / ".brr" / "dominion" / "playbook.md").write_text(
            "## One\nbody\n", encoding="utf-8",
        )
        findings = notes_preflight.scan(repo)
        types = [f.type for f in findings]
        assert "home-unresolved" not in types
        named = [f for f in findings if f.type.startswith("surface-root-")]
        assert [f.target for f in named] == [notes.ROOT_KNOWLEDGE]

    def test_the_clean_path_still_carries_its_denominator(self, tmp_path):
        """`scan` can return `[]`; `scan_scoped` can never return no scope."""
        repo = _repo(tmp_path)
        findings, scope = notes_preflight.scan_scoped(repo)
        assert findings == []
        assert scope.registered == len(notes.registry())
        assert scope.whole
        assert f"of {scope.registered} registered surfaces" in scope.line()

    def test_an_unread_root_on_a_used_account_says_so_in_its_scope(
        self, tmp_path,
    ):
        """The #1193 shape: the account is in use, and a root came back empty."""
        repo = _repo(tmp_path, seed_roots=False)
        surface = tmp_path / "home" / "surface"
        surface.mkdir(parents=True)
        (surface / "index.md").write_text("# Surface\n", encoding="utf-8")
        _findings, scope = notes_preflight.scan_scoped(repo)
        assert not scope.whole
        assert {r.split(" ")[0] for r in scope.unresolved_roots} == {
            notes.ROOT_DOMINION, notes.ROOT_KNOWLEDGE,
        }
        assert "unresolved roots" in scope.line()

    def test_the_cli_never_prints_a_bare_clean(self, tmp_path, capsys):
        from brr import cli

        repo = _repo(tmp_path, seed_roots=False)
        surface = tmp_path / "home" / "surface"
        surface.mkdir(parents=True)
        (surface / "index.md").write_text("# Surface\n", encoding="utf-8")
        _git(repo, "init", "-q", "-b", "main")
        args = cli.build_parser().parse_args(["notes", "check"])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cli, "_repo_root", lambda: repo)
            assert args.func(args) == 1
        out = capsys.readouterr().out
        assert "registered surfaces located" in out
        assert "surface-root-" in out

    def test_the_json_check_shape_carries_scope(self, tmp_path, capsys):
        """A consumer reading a bare `[]` cannot tell healthy from unread."""
        import json

        from brr import cli

        repo = _repo(tmp_path)
        _git(repo, "init", "-q", "-b", "main")
        args = cli.build_parser().parse_args(["notes", "check", "--json"])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(cli, "_repo_root", lambda: repo)
            assert args.func(args) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["findings"] == []
        assert payload["scope"]["registered"] == len(notes.registry())
        assert payload["scope"]["unresolved_roots"] == []

    def test_the_wake_block_leads_with_its_scope(self, tmp_path):
        from brr.prompts import _build_notes_health_block

        repo = _repo(tmp_path, seed_roots=False)
        surface = tmp_path / "home" / "surface"
        surface.mkdir(parents=True)
        (surface / "index.md").write_text("# Surface\n", encoding="utf-8")
        block = _build_notes_health_block(repo)
        scope_at = block.index("_Scope:")
        findings_at = block.index("## Findings")
        assert scope_at < findings_at, "the denominator must precede the list"


# ── check 4: never-linked home (#1423) ────────────────────────────────


class TestNeverLinked:
    """The deterministic twin of the wake-time banners.

    Same predicate, same source of truth (:func:`brr.dominion.never_linked`
    / :func:`brr.knowledge.never_linked`) — see ``TestNeverLinkedBanner`` in
    ``test_prompts.py`` for the rendered-prose side of this. Needs a *real*
    git-backed dominion (``dominion.ensure_dominion``), not the synthetic
    plain-directory fixture the rest of this module uses — the check reads
    actual git history, and a directory with no ``.git`` is correctly
    invisible to it.
    """

    @staticmethod
    def _repo_with_dominion(tmp_path):
        import _helpers

        repo = tmp_path / "repo"
        _helpers.init_git_repo(repo)
        _helpers.commit_files(repo, {"README.md": "x\n"})
        path = dominion.ensure_dominion(repo, push=False)
        return repo, path

    def test_absent_at_birth(self, tmp_path):
        repo, path = self._repo_with_dominion(tmp_path)
        dominion.mark_never_linked(path.parent)

        assert notes_preflight.check_never_linked(repo, {}) == []

    def test_names_the_dominion_once_real_content_follows(self, tmp_path):
        repo, path = self._repo_with_dominion(tmp_path)
        dominion.mark_never_linked(path.parent)
        notes_preflight.check_never_linked(repo, {})  # anchors the baseline

        (path / "pain.md").write_text("slow rebuild keeps biting\n", encoding="utf-8")
        assert dominion.commit(path, "capture", remote=None, push=False) is True

        findings = notes_preflight.check_never_linked(repo, {})
        assert [f.type for f in findings] == ["never-linked"]
        assert str(path) in findings[0].target
        assert "brnrd home link" in findings[0].description
        assert findings[0].severity == "warning"

    def test_silent_once_a_remote_is_wired(self, tmp_path):
        repo, path = self._repo_with_dominion(tmp_path)
        dominion.mark_never_linked(path.parent)
        notes_preflight.check_never_linked(repo, {})
        (path / "pain.md").write_text("more\n", encoding="utf-8")
        dominion.commit(path, "capture", remote=None, push=False)
        assert notes_preflight.check_never_linked(repo, {}) != []

        remote = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote)], cwd=path, check=True,
        )
        # A clean-tree capture still clears the marker: linking is a fact
        # about the remote, not about whether this particular call had
        # something new to commit.
        assert dominion.commit(
            path, "capture", remote="origin", branch="brr-home", push=True,
        ) is False

        assert notes_preflight.check_never_linked(repo, {}) == []

    def test_reaches_the_full_scan(self, tmp_path):
        repo, path = self._repo_with_dominion(tmp_path)
        dominion.mark_never_linked(path.parent)
        notes_preflight.check_never_linked(repo, {})
        (path / "pain.md").write_text("more\n", encoding="utf-8")
        dominion.commit(path, "capture", remote=None, push=False)

        findings = notes_preflight.scan(repo, {})
        assert any(f.type == "never-linked" for f in findings)


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

    def test_two_surfaces_named_index_md_do_not_share_a_verdict(self):
        """Attribution is by path suffix, not basename.

        ``surface/index.md`` and the kb's ``index.md`` are two registered
        surfaces with one basename. Keyed by basename, one surface's
        finding paints a severity mark on the other's row and makes a
        healthy surface exit non-zero — a false alarm on a page nobody
        touched, which is how a map stops being read.
        """
        from brr import cli, kb_preflight

        finding = kb_preflight.Finding(
            type="eviction-preview", target="surface/index.md",
            description="dropped", severity="warning",
        )
        verdicts = cli._notes_verdicts([finding])

        surface_row = notes.Resolved(
            surface=notes.get("surface-index"),
            paths=(Path("/home/acct/surface/index.md"),),
        )
        kb_row = notes.Resolved(
            surface=notes.get("kb-index"),
            paths=(Path("/home/acct/knowledge/repos/x/index.md"),),
        )
        assert cli._notes_match_verdicts(surface_row, verdicts) == [finding]
        assert cli._notes_match_verdicts(kb_row, verdicts) == []

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
