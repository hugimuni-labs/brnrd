"""Tests for the forge-state wake-snapshot facet (co-maintainer §5, #113)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr import forge_state, forges, prompts, run_context, worktree
from brr.run import Run

from _helpers import commit_files, init_git_repo


# ── parse_forge_thread ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "key, expected",
    [
        ("github:Gurio/brr:113", ("Gurio/brr", 113)),
        ("cloud:github:Gurio/brr#113:", ("Gurio/brr", 113)),
        ("cloud:github:Gurio/brr#42:topic-7", ("Gurio/brr", 42)),
        # nested-group repo path round-trips
        ("github:group/sub/repo:9", ("group/sub/repo", 9)),
        # non-forge keys yield None
        ("telegram:12345:", None),
        ("slack:C01:1700000000.1", None),
        ("cloud:telegram:999:", None),
        ("github:Gurio/brr", None),       # no number
        ("github:Gurio/brr:abc", None),   # non-numeric
        ("github:noslash:5", None),        # repo missing owner/repo shape
        ("", None),
    ],
)
def test_parse_forge_thread(key, expected):
    assert forge_state.parse_forge_thread(key) == expected


# ── forges.thread_url ────────────────────────────────────────────────


def test_thread_url_github():
    url = forges.thread_url("git@github.com:Gurio/brr.git", "Gurio/brr", 113)
    assert url == "https://github.com/Gurio/brr/issues/113"


def test_thread_url_uses_thread_repo_not_origin():
    # The repo a thread is about may differ from origin; the URL follows
    # the thread's repo while taking host/kind from the remote.
    url = forges.thread_url("git@github.com:Gurio/brr.git", "other/proj", 7)
    assert url == "https://github.com/other/proj/issues/7"


def test_thread_url_gitlab_template():
    url = forges.thread_url("git@gitlab.com:grp/proj.git", "grp/proj", 4)
    assert url == "https://gitlab.com/grp/proj/-/issues/4"


@pytest.mark.parametrize(
    "remote, expected",
    [
        ("git@github.com:Gurio/brr.git", "https://github.com/Gurio/brr/pull/4"),
        ("git@gitlab.com:Gurio/brr.git", "https://gitlab.com/Gurio/brr/-/merge_requests/4"),
    ],
)
def test_pull_request_url_uses_forge_native_path(remote, expected):
    assert forges.pull_request_url(remote, "Gurio/brr", 4) == expected


@pytest.mark.parametrize(
    "value",
    [
        "274",
        "#274",
        "https://github.com/Gurio/brr/pull/274",
        "https://gitlab.com/Gurio/brr/-/merge_requests/274",
        "https://bitbucket.org/Gurio/brr/pull-requests/274",
        "https://codeberg.org/Gurio/brr/pulls/274",
    ],
)
def test_parse_pull_request_number_accepts_explicit_native_forms(value):
    assert forges.parse_pull_request_number(value) == "274"


@pytest.mark.parametrize(
    "value",
    ["ea35206", "prefix 274", "not-a-url/pull/274", "https://x/pulls/274"],
)
def test_parse_pull_request_number_rejects_ambiguous_values(value):
    assert forges.parse_pull_request_number(value) is None


@pytest.mark.parametrize(
    "remote, repo, number",
    [
        ("git@github.com:Gurio/brr.git", "Gurio/brr", "nope"),  # bad number
        ("git@github.com:Gurio/brr.git", "noslash", 5),          # bad repo
        ("not a remote", "Gurio/brr", 5),                        # bad remote
        ("git@github.com:Gurio/brr.git", "Gurio/brr", 0),        # non-positive
    ],
)
def test_thread_url_none_cases(remote, repo, number):
    assert forges.thread_url(remote, repo, number) is None


# ── worktree.unpushed_commit_count ───────────────────────────────────


def _repo_with_remote(tmp_path: Path) -> Path:
    """A repo whose ``origin`` is a GitHub URL (for forge detection) and
    whose actual push target is a local bare ``store`` remote, so
    ``unpushed_commit_count`` sees real remote-tracking refs without a
    network.
    """
    store = tmp_path / "store.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(store)],
        check=True, stdout=subprocess.PIPE,
    )
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "one\n"})
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/Gurio/brr.git"],
        cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "store", str(store)],
        cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "push", "-u", "store", "main"],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return repo


def test_unpushed_commit_count_zero_after_push(tmp_path):
    repo = _repo_with_remote(tmp_path)
    assert worktree.unpushed_commit_count(repo) == 0


def test_unpushed_commit_count_counts_local_commits(tmp_path):
    repo = _repo_with_remote(tmp_path)
    commit_files(repo, {"b.txt": "two\n"}, message="local")
    commit_files(repo, {"c.txt": "three\n"}, message="local2")
    assert worktree.unpushed_commit_count(repo) == 2


def test_unpushed_commit_count_no_remote(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "x\n"})
    # No remote at all → every commit is "unpushed".
    assert worktree.unpushed_commit_count(repo) == 1


# ── worktree.uncommitted_file_count ──────────────────────────────────


def test_uncommitted_file_count_clean_tree(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "x\n"})
    assert worktree.uncommitted_file_count(repo) == 0


def test_uncommitted_file_count_counts_tracked_and_untracked(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "x\n"})
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")  # modified
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")  # untracked
    assert worktree.uncommitted_file_count(repo) == 2


# ── build_forge_state ────────────────────────────────────────────────


def _repo_with_worktree(tmp_path: Path) -> Path:
    repo = _repo_with_remote(tmp_path)
    # A brr-managed worktree under .brr/worktrees/<run-id>.
    run_id = "run-test-1"
    wt_path, branch = worktree.create(repo, run_id)
    # Add an unpushed commit on the worktree branch.
    commit_files(wt_path, {"feature.txt": "wip\n"}, message="feature")
    Run(
        id=run_id, event_id="evt-test", body="work", status="running",
        meta={"seed_ref": "main", "has_new_commit": True},
    ).save(repo / ".brr" / "runs")
    return repo


def test_build_forge_state_lists_worktrees(tmp_path):
    repo = _repo_with_worktree(tmp_path)
    facet = forge_state.build_forge_state(
        repo,
        related_threads=[],
        current_thread="github:Gurio/brr:113",
        current_run_id="run-test-1",
    )
    assert facet is not None
    worktrees = facet["worktrees"]
    by_branch = {w["branch"]: w for w in worktrees}
    assert "brr/run-test-1" in by_branch
    wt = by_branch["brr/run-test-1"]
    assert wt["unpushed"] == 1
    assert wt["current"] is True
    # forge branch URL derived from origin remote
    assert wt["branch_url"] == "https://github.com/Gurio/brr/tree/brr/run-test-1"


def test_build_forge_state_hides_commitless_placeholder_branch_url(tmp_path):
    repo = _repo_with_remote(tmp_path)
    run_id = "run-no-commit"
    worktree.create(repo, run_id)
    Run(
        id=run_id, event_id="evt-noop", body="audit", status="running",
        meta={"seed_ref": "main", "has_new_commit": False},
    ).save(repo / ".brr" / "runs")

    facet = forge_state.build_forge_state(
        repo, related_threads=[], current_thread="", current_run_id=run_id,
    )

    assert facet is not None
    entry = next(w for w in facet["worktrees"] if w["run_id"] == run_id)
    assert entry["branch"] == "brr/run-no-commit"
    assert "branch_url" not in entry


def test_build_forge_state_threads_cross_reference(tmp_path):
    repo = _repo_with_remote(tmp_path)
    facet = forge_state.build_forge_state(
        repo,
        related_threads=[{"conversation_key": "github:Gurio/brr:99"}],
        current_thread="github:Gurio/brr:113",
        current_run_id="",
    )
    assert facet is not None
    threads = facet["threads"]
    refs = {(t["repo"], t["number"]): t for t in threads}
    assert ("Gurio/brr", 113) in refs
    assert ("Gurio/brr", 99) in refs
    assert refs[("Gurio/brr", 113)]["current"] is True
    assert refs[("Gurio/brr", 99)]["current"] is False
    assert refs[("Gurio/brr", 113)]["url"] == "https://github.com/Gurio/brr/issues/113"


def test_build_forge_state_enriches_current_from_event_meta(tmp_path):
    repo = _repo_with_remote(tmp_path)
    facet = forge_state.build_forge_state(
        repo,
        related_threads=[],
        current_thread="github:Gurio/brr:113",
        current_run_id="",
        current_event_meta={
            "github_kind": "pull_request",
            "branch_target": "brr/feature-x",
            "github_pr_number": "113",
            "github_html_url": "https://github.com/Gurio/brr/pull/113#issuecomment-5",
        },
    )
    thread = facet["threads"][0]
    assert thread["kind"] == "pull_request"
    assert thread["branch_target"] == "brr/feature-x"
    assert thread["pr_number"] == "113"
    # exact comment URL wins over the template-derived issue URL
    assert thread["url"] == "https://github.com/Gurio/brr/pull/113#issuecomment-5"


def test_build_forge_state_keeps_only_prod_when_otherwise_empty(tmp_path):
    """A repo with no brr worktrees and a non-forge current thread has
    nothing worktree/thread-shaped to show — but `prod` always has
    *something* to say (2026-07-30 task), so the facet is never fully
    empty and the "prod:" line is never silently dropped."""
    repo = _repo_with_remote(tmp_path)
    facet = forge_state.build_forge_state(
        repo,
        related_threads=[{"conversation_key": "telegram:123:"}],
        current_thread="telegram:123:",
        current_run_id="",
    )
    assert facet is not None
    assert "worktrees" not in facet
    assert "threads" not in facet
    # No cloud gate configured in this bare test repo.
    assert facet["prod"] == {"configured": False}


# ── prompt rendering ─────────────────────────────────────────────────


def test_format_forge_state_renders_sections():
    facet = {
        "worktrees": [
            {
                "run_id": "task-1",
                "branch": "brr/feature",
                "unpushed": 2,
                "dirty": True,
                "current": True,
                "branch_url": "https://github.com/Gurio/brr/tree/brr/feature",
            }
        ],
        "threads": [
            {
                "conversation_key": "github:Gurio/brr:113",
                "repo": "Gurio/brr",
                "number": 113,
                "current": True,
                "kind": "issue",
                "url": "https://github.com/Gurio/brr/issues/113",
            }
        ],
    }
    rendered = prompts._format_forge_state(facet)
    assert "Forge state" in rendered
    assert "brr/feature" in rendered
    assert "2 unpushed" in rendered
    assert "uncommitted changes" in rendered
    assert "Gurio/brr#113" in rendered
    assert "this thread" in rendered


def test_format_forge_state_collapses_clean_pushed_branches():
    facet = {
        "worktrees": [
            {
                "run_id": "task-current",
                "branch": "brr/current",
                "unpushed": 0,
                "dirty": False,
                "current": True,
            },
            {
                "run_id": "task-clean-a",
                "branch": "brr/clean-a",
                "unpushed": 0,
                "dirty": False,
                "current": False,
            },
            {
                "run_id": "task-clean-b",
                "branch": "brr/clean-b",
                "unpushed": 0,
                "dirty": False,
                "current": False,
            },
            {
                "run_id": "task-wip",
                "branch": "brr/wip",
                "unpushed": 3,
                "dirty": True,
                "current": False,
            },
        ],
        "threads": [],
    }

    rendered = prompts._format_forge_state(facet)

    assert "Worktrees / branches: 4 total" in rendered
    assert "1 with unpushed commits (3 commits)" in rendered
    assert "1 dirty" in rendered
    assert "1 current" in rendered
    assert "brr/current" in rendered
    assert "brr/wip" in rendered
    assert "brr/clean-a" not in rendered
    assert "brr/clean-b" not in rendered
    assert "2 clean pushed branches omitted" in rendered


def test_run_context_forge_state_collapses_clean_pushed_branches():
    facet = {
        "worktrees": [
            {"branch": "brr/current", "current": True},
            {"branch": "brr/clean", "current": False},
        ],
        "threads": [],
    }

    rendered = run_context._render_forge_state(facet)

    assert "Worktrees / branches: 2 total" in rendered
    assert "brr/current" in rendered
    assert "brr/clean" not in rendered
    assert "1 clean pushed branch omitted" in rendered


# ── render_prod_line: fresh / stale / absent (2026-07-30 task) ───────


def test_render_prod_line_absent_states():
    assert forge_state.render_prod_line(None) == "prod: unknown — no cloud fingerprint yet"
    assert (
        forge_state.render_prod_line({"configured": False})
        == "prod: unknown — no cloud gate configured"
    )
    assert (
        forge_state.render_prod_line({"configured": True, "fingerprint": None})
        == "prod: unknown — no cloud fingerprint yet"
    )


def _prod_fixture(fetched_at: str) -> dict:
    return {
        "configured": True,
        "fingerprint": {
            "build": {
                "commit": "bebd5c1d1c3a4f5b",
                "built_at": "2026-07-30T10:19:01+00:00",
                "started_at": "2026-07-30T10:28:49+00:00",
            },
            "github": {
                "bot_login": "brnrd-bot",
                "app_slug": "brnrd-dev",
                "trigger_label": "brnrd",
                "trigger_aliases": ["brnrd", "brr"],
                "webhook_secret_set": True,
                "bot_token_set": True,
            },
            "fetched_at": fetched_at,
        },
    }


def test_render_prod_line_fresh_matches_the_spec_example_shape():
    from datetime import datetime, timezone

    now = datetime(2026, 7, 30, 10, 29, 30, tzinfo=timezone.utc)
    prod = _prod_fixture("2026-07-30T10:29:00+00:00")
    rendered = forge_state.render_prod_line(prod, now=now)
    assert rendered == (
        "prod: commit bebd5c1d · built 2026-07-30T10:19Z · up 10:28Z · "
        "call sign brnrd-bot · label brnrd · webhook secret set · bot token set"
    )


def test_render_prod_line_without_a_commit_omits_it():
    """An unidentified build says nothing where the sha would go.

    Until 2026-07-31 this case rendered ``tree <id>`` from the build-tree id
    the retired PaaS exported; that field is gone with the host, and an older
    backend still sending it is ignored rather than rendered. What must not
    happen either way is a *guessed* sha, so the rest of the line still has to
    arrive: the absence is visible, not a blank line.
    """
    from datetime import datetime, timezone

    prod = _prod_fixture("2026-07-30T10:29:00+00:00")
    prod["fingerprint"]["build"]["commit"] = None
    prod["fingerprint"]["build"]["tree_id"] = "bebd5c1d1c3a4f5b"
    rendered = forge_state.render_prod_line(
        prod, now=datetime(2026, 7, 30, 10, 29, 30, tzinfo=timezone.utc)
    )
    assert rendered == (
        "prod: built 2026-07-30T10:19Z · up 10:28Z · "
        "call sign brnrd-bot · label brnrd · webhook secret set · bot token set"
    )
    assert "bebd5c1d" not in rendered


def test_render_prod_line_stale_names_its_own_age():
    from datetime import datetime, timezone

    prod = _prod_fixture("2026-07-30T10:19:00+00:00")
    now = datetime(2026, 7, 30, 10, 40, 0, tzinfo=timezone.utc)  # 21 min old
    rendered = forge_state.render_prod_line(prod, now=now)
    assert rendered.startswith("prod: stale (21m old) — ")
    assert "commit bebd5c1d" in rendered


def test_format_forge_state_empty():
    assert prompts._format_forge_state(None) == ""
    assert prompts._format_forge_state({}) == ""


def test_format_forge_state_renders_prod_line_even_with_nothing_else():
    """No worktrees, no threads — the block still renders, for the one
    line that always has something to say (2026-07-30 task)."""
    rendered = prompts._format_forge_state(
        {"worktrees": [], "threads": [], "prod": {"configured": False}}
    )
    assert rendered == (
        "Forge state (local, network-free):\n"
        "- prod: unknown — no cloud gate configured"
    )


# ── #721: worktrees of this repo outside .brr/worktrees/ ─────────────


def test_summarize_splits_brr_from_external_worktrees():
    summary = forge_state.summarize_worktrees([
        {"run_id": "run-a", "branch": "brr/a", "kind": "brr", "current": False},
        {"branch": "brr/mood", "kind": "external", "path": "/tmp/brr-wt-mood",
         "dirty": True, "unpushed": 0, "current": False, "run_id": None},
        {"branch": "brr/other", "kind": "external", "path": "/tmp/brr-wt-other",
         "dirty": False, "unpushed": 0, "current": False, "run_id": None},
    ])

    assert summary["total"] == 3
    assert summary["brr_total"] == 1
    assert summary["external_total"] == 2
    # Only the one carrying work that would be lost with the directory.
    assert summary["external_at_risk"] == 1


def test_summarize_reads_a_missing_kind_as_brr_managed():
    """Silence must not reclassify. Every pre-#721 entry was brr-managed."""
    summary = forge_state.summarize_worktrees([
        {"run_id": "run-a", "branch": "brr/a", "current": False},
    ])
    assert summary["brr_total"] == 1
    assert summary["external_total"] == 0
    assert forge_state.external_worktree_note(summary) == ""


def _pushed_repo(tmp_path: Path) -> Path:
    """A repo whose one commit is reachable from a remote.

    Without this, ``unpushed_commit_count`` counts the seed commit itself —
    a repo with no remote has everything unpushed — and every worktree in the
    fixture would be "at risk" for a reason the test did not intend.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"file.txt": "init\n"})
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return repo


def _add_worktree(repo: Path, path: Path, branch: str) -> None:
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(path)],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def test_dirty_worktree_outside_brr_reaches_attention(tmp_path):
    """#721's whole point, driven through the real producer.

    A worktree of this repo that brnrd did not mint, holding uncommitted
    work. brnrd never created it, so brnrd never preserves it; under the
    ``host`` invariant it lives in ``/tmp``, which a reboot clears. It was
    invisible to this facet, and the facet's entire job is noticing exactly
    this.
    """
    repo = _pushed_repo(tmp_path)
    worktree.create(repo, "run-clean")

    foreign = tmp_path / "tmp-like" / "brr-wt-mood"
    _add_worktree(repo, foreign, "brr/mood")
    (foreign / "unsaved.txt").write_text("work nobody can see\n", encoding="utf-8")

    facet = forge_state.build_forge_state(repo, current_run_id="run-clean")
    summary = forge_state.summarize_worktrees(facet["worktrees"])

    assert summary["external_total"] == 1
    assert summary["external_at_risk"] == 1

    external = [wt for wt in summary["attention"] if wt.get("kind") == "external"]
    assert len(external) == 1
    assert external[0]["dirty"] is True
    # No run id to name it by, so the path is the identity — and the warning.
    assert external[0]["run_id"] is None
    assert forge_state.worktree_label(external[0]) == str(foreign)


def test_clean_worktree_outside_brr_stays_collapsed(tmp_path):
    """Widening the count must not widen the noise: only work at risk speaks."""
    repo = _pushed_repo(tmp_path)
    foreign = tmp_path / "tmp-like" / "brr-wt-quiet"
    _add_worktree(repo, foreign, "brr/quiet")

    facet = forge_state.build_forge_state(repo, current_run_id="run-none")
    summary = forge_state.summarize_worktrees(facet["worktrees"])

    assert summary["external_total"] == 1
    assert summary["external_at_risk"] == 0
    assert summary["attention"] == []
    assert summary["omitted"] == 1


_EXTERNAL_FACET = {
    "worktrees": [
        {"run_id": "run-a", "branch": "brr/a", "kind": "brr",
         "unpushed": 0, "dirty": False, "current": True},
        {"run_id": None, "branch": "brr/mood", "kind": "external",
         "path": "/tmp/brr-wt-mood", "unpushed": 0, "dirty": True,
         "current": False},
    ],
    "threads": [],
}


@pytest.mark.parametrize(
    "render",
    [prompts._format_forge_state, run_context._render_forge_state],
    ids=["wake-prompt", "run-context-file"],
)
def test_both_renderers_carry_the_outside_brr_count(render):
    """The two renderers are near-byte-identical copies of one block.

    A fact added to one of them only re-creates the defect it was added to
    fix — the count would go honest in the wake prompt and stay narrowed in
    the context file. Pin both from one rule so a third copy has to join it.
    """
    rendered = render(_EXTERNAL_FACET)

    assert "1 outside .brr/worktrees (1 with uncommitted or unpushed work)" in rendered
    # And the entry itself is reachable: named by path, since it has no run id.
    assert "/tmp/brr-wt-mood" in rendered
    assert "uncommitted changes" in rendered


@pytest.mark.parametrize(
    "render",
    [prompts._format_forge_state, run_context._render_forge_state],
    ids=["wake-prompt", "run-context-file"],
)
def test_neither_renderer_mentions_outside_brr_when_there_is_none(render):
    """All-brr is the common case; then the line is exactly what it always was."""
    facet = {
        "worktrees": [
            {"run_id": "run-a", "branch": "brr/a", "unpushed": 0,
             "dirty": False, "current": True},
        ],
        "threads": [],
    }
    rendered = render(facet)

    assert "Worktrees / branches: 1 total" in rendered
    assert "outside .brr/worktrees" not in rendered


@pytest.mark.parametrize(
    "render",
    [prompts._format_forge_state, run_context._render_forge_state],
    ids=["wake-prompt", "run-context-file"],
)
def test_both_renderers_carry_the_prod_line(render):
    """Same "one copy only re-creates the defect" pin as the worktree count
    above, applied to the prod fingerprint (2026-07-30 task) — both
    renderers call the one shared :func:`forge_state.render_prod_line`."""
    facet = {"worktrees": [], "threads": [], "prod": {"configured": False}}
    rendered = render(facet)
    assert "prod: unknown — no cloud gate configured" in rendered


# ── the App identity on the prod line (2026-07-31, the Scaleway cutover) ──
#
# The line reported `webhook secret set · bot token set` — two credentials,
# both true, neither one the credential a managed runner pushes with. The
# publishing lane had been dead for six hours behind that sentence.


def _prod_with_github(**github) -> dict:
    prod = _prod_fixture("2026-07-30T10:29:00+00:00")
    prod["fingerprint"]["github"].update(github)
    return prod


def test_render_prod_line_reports_app_auth_when_both_halves_are_set():
    from datetime import datetime, timezone

    now = datetime(2026, 7, 30, 10, 29, 30, tzinfo=timezone.utc)
    rendered = forge_state.render_prod_line(
        _prod_with_github(app_id_set=True, app_key_set=True), now=now
    )
    assert rendered.endswith("bot token set · app auth set")


def test_render_prod_line_names_the_missing_app_variable():
    """The remedy is the variable name — the operator sets exactly that."""
    from datetime import datetime, timezone

    now = datetime(2026, 7, 30, 10, 29, 30, tzinfo=timezone.utc)
    rendered = forge_state.render_prod_line(
        _prod_with_github(app_id_set=False, app_key_set=True), now=now
    )
    assert rendered.endswith("app auth unset — no BRNRD_GITHUB_APP_ID")

    rendered = forge_state.render_prod_line(
        _prod_with_github(app_id_set=False, app_key_set=False), now=now
    )
    assert rendered.endswith(
        "app auth unset — no BRNRD_GITHUB_APP_ID or "
        "BRNRD_GITHUB_APP_PRIVATE_KEY_B64"
    )


def test_render_prod_line_omits_app_auth_when_the_server_predates_it():
    """Absent is not `unset`: an older backend simply does not answer this
    question, and a `False` default would render a healthy prod as broken."""
    from datetime import datetime, timezone

    now = datetime(2026, 7, 30, 10, 29, 30, tzinfo=timezone.utc)
    rendered = forge_state.render_prod_line(
        _prod_fixture("2026-07-30T10:29:00+00:00"), now=now
    )
    assert "app auth" not in rendered
