"""The uninitialized-repo first wake — dispatch-time half of "the first run
takes it from there".

A cloud-only pairing has no door that can originate a message, so the
connect greeting (#1244 fork 2) never fires there; ``front_door`` truthfully
tells the person to message their bot — and the run that message woke used
to arrive with no word about setup. Measured live on a fresh install
(2026-08-19): the first run answered "The room is lit; I'm here" on the
cheapest core and "nothing appears to need intervention" on the strongest
one. The instructions were missing, not the capability.

These tests pin the two halves: the daemon predicate that decides a run is
the setup run, and the prompts builder that folds the init playbook +
adopter template into its task.
"""

from __future__ import annotations

from pathlib import Path

from brr import connect_greeting, daemon, prompts

from _helpers import init_git_repo


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    return repo


def _applies(event: dict, repo: Path, **overrides) -> bool:
    kwargs = dict(
        is_strand_run=False,
        is_home_root=False,
        correspondent_key="telegram:user-id:1",
    )
    kwargs.update(overrides)
    return daemon._uninitialized_first_wake_applies(event, repo, {}, **kwargs)


# ── the predicate ────────────────────────────────────────────────────


def test_owner_cloud_message_on_bare_repo_is_the_setup_run(tmp_path):
    repo = _repo(tmp_path)
    assert _applies({"source": "cloud", "body": "hey"}, repo) is True


def test_agents_md_present_ends_the_trigger_state(tmp_path):
    repo = _repo(tmp_path)
    (repo / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    assert _applies({"source": "cloud", "body": "hey"}, repo) is False


def test_no_correspondent_means_nobody_to_interview(tmp_path):
    """Schedule fires and spawn completions have no human on the other end;
    an interview task addressed to nobody would run its beats into a void."""
    repo = _repo(tmp_path)
    assert _applies({"source": "cloud"}, repo, correspondent_key="") is False


def test_untrusted_sender_never_authors_the_contract(tmp_path):
    """An ingress-gate event with no stamped tier fails closed (untrusted) —
    a stranger's answers must not become the repo contract."""
    repo = _repo(tmp_path)
    assert _applies({"source": "telegram", "body": "hi"}, repo) is False
    stamped = {"source": "telegram", "body": "hi", "trust_tier": "owner"}
    assert _applies(stamped, repo) is True


def test_strand_and_home_root_are_excluded(tmp_path):
    repo = _repo(tmp_path)
    event = {"source": "cloud", "body": "hey"}
    assert _applies(event, repo, is_strand_run=True) is False
    assert _applies(event, repo, is_home_root=True) is False


def test_greeting_event_is_not_double_wrapped(tmp_path):
    """The connect greeting's body already carries the same playbook."""
    repo = _repo(tmp_path)
    event = {"source": "telegram", "trust_tier": "owner",
             connect_greeting.GREETING_META_KEY: True}
    assert _applies(event, repo) is False


# ── the task builder ─────────────────────────────────────────────────


def test_uninitialized_wake_task_carries_playbook_and_template(tmp_path):
    repo = _repo(tmp_path)
    task = prompts.build_uninitialized_wake_task(repo)
    # The sentence the measured failure was missing: the setup IS the task.
    assert "this run is the setup run" in task.lower()
    assert "the setup is the task" in task.lower()
    # The reused init playbook and adopter template, not a restatement.
    assert "Init playbook" in task
    assert "adopter template" in task.lower()
    # It points at the message, which the bundle renders on its own.
    assert "Original event body" in task
    # The terminal-only machinery is explicitly disclaimed for a door wake.
    assert "do not emit `control:`" in task


def test_uninitialized_wake_task_renders_facts_block(tmp_path):
    repo = _repo(tmp_path)
    facts = prompts.collect_daemon_wake_init_facts(repo)
    # The daemon variant never restates runner detection — the wake's own
    # Runner catalog owns that surface (same trim the greeting uses).
    for key in ("runner_name", "detected_runners", "detected_shells",
                "missing_shells"):
        assert key not in facts
    task = prompts.build_uninitialized_wake_task(repo, facts=facts)
    assert "### Init facts" in task
    assert "Existing AGENTS.md: no" in task
