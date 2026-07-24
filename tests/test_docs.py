"""Tests for bundled docs module."""

from __future__ import annotations

from brr import docs


def test_list_topics_includes_bundled():
    topics = docs.list_topics()
    assert "active-task" in topics
    assert "execution-map" in topics
    assert "internals" in topics
    assert "portals" in topics
    assert "review-pack" in topics


def test_review_pack_topic_carries_publish_plumbing():
    # The heavy publish procedure moved out of the injected diffense block
    # into this inspected topic (cost-aware-cockpit de-firehose).
    text = docs.read_topic("review-pack")
    assert text is not None
    assert "--pr-body --relay" in text
    assert "gate: forge" in text
    assert "diffense.emit_pack" in text
    assert "not diffense-owned" in text


def test_portals_topic_covers_protocol_and_choreography():
    text = docs.read_topic("portals")
    assert text is not None
    # The control-file cheatsheet…
    assert ".card" in text
    assert "portal-state.json" in text
    assert "context/quota/spend resource meter" in text
    assert "BRR_PORTAL_STATE" in text
    assert "gate: forge" in text
    assert "explicit PR handoff" in text
    assert "not diffense-owned" in text
    # …and the average-run choreography.
    assert "schedule.md" in text
    assert "plan or execute" in text.lower() or "plan-vs-execute" in text.lower()
    assert "Stay in the conversation" in text


def test_portals_manual_links_back_to_delivery_contract():
    # The manual and the injected delivery contract are a matched pair: the
    # contract names the inbound/outbound/parked forms hot, the manual defines
    # them. The manual must say so explicitly so an editor of either reconciles
    # the other and they don't drift. The reciprocal half is pinned in
    # test_prompts.py (test_delivery_contract_carries_portal_model_summary).
    text = docs.read_topic("portals")
    assert text is not None
    for form in ("inbound", "outbound", "parked"):
        assert form in text
    assert "injected summary" in text
    assert "delivery contract" in text


def test_portals_manual_defines_next_move_contract():
    # A1/#211: an addressed reply ends with the next move — four states,
    # options only at genuine forks. The manual holds the full contract;
    # the compact rule rides in daemon-substrate's delivery-portals block
    # (pinned in test_prompts.py::test_daemon_prompt_carries_next_move_and_linger).
    text = docs.read_topic("portals")
    assert text is not None
    assert "The next move" in text
    for state in ("done —", "continuing —", "blocked —"):
        assert state in text
    assert "genuine fork" in text
    assert "2–4 numbered options" in text
    assert "Manufacturing options is the failure mode" in text


def test_portals_manual_defines_post_delivery_linger():
    # B5/#216: the linger is a named contract — outbox delivery first,
    # keepalive-held slot, TTL-aware exponential backoff, dispatch-or-explicit-
    # defer ownership for unrelated pending work, bounded horizon — plus the daemon-owned
    # attending floor for post-return safety.
    text = docs.read_topic("portals")
    assert text is not None
    assert "post-delivery linger" in text
    assert "delivered · attending" in text
    assert "delivery.post_delivery_attend_seconds" in text
    assert "cap at ~240s" in text
    assert "Any other pending event ends" in text
    assert "spawn.max_concurrent" in text
    assert "queue never starves" in text
    assert "10–15 minutes past the last delivery" in text


def test_read_topic_bundled_returns_content():
    text = docs.read_topic("execution-map")
    assert text is not None
    assert "Execution Map" in text


def test_read_topic_unknown_returns_none():
    assert docs.read_topic("does-not-exist") is None


def test_read_topic_rejects_traversal():
    assert docs.read_topic("../pyproject") is None
    assert docs.read_topic(".hidden") is None
    assert docs.read_topic("") is None


def test_read_topic_override_wins(tmp_path):
    overrides = tmp_path / ".brr" / "docs"
    overrides.mkdir(parents=True)
    (overrides / "execution-map.md").write_text("# custom override")

    text = docs.read_topic("execution-map", repo_root=tmp_path)
    assert text == "# custom override"


def test_list_topics_includes_override_additions(tmp_path):
    overrides = tmp_path / ".brr" / "docs"
    overrides.mkdir(parents=True)
    (overrides / "repo-specific.md").write_text("# repo specific")

    topics = docs.list_topics(repo_root=tmp_path)
    assert "repo-specific" in topics
    assert "execution-map" in topics  # bundled still listed


def test_format_listing_marks_overrides(tmp_path):
    overrides = tmp_path / ".brr" / "docs"
    overrides.mkdir(parents=True)
    (overrides / "execution-map.md").write_text("# custom")

    listing = docs.format_listing(repo_root=tmp_path)
    assert "execution-map" in listing
    assert "(overridden)" in listing


def test_read_topic_uses_shared_runtime_override_for_worktree(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    overrides = repo / ".brr" / "docs"
    overrides.mkdir(parents=True)
    (overrides / "execution-map.md").write_text("# worktree override", encoding="utf-8")
    worktree = repo / ".brr" / "worktrees" / "task-1"
    subprocess.run(
        ["git", "worktree", "add", "-b", "brr/task-1", str(worktree), "HEAD"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )

    try:
        text = docs.read_topic("execution-map", repo_root=worktree)
        assert text == "# worktree override"
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo, check=True)
        subprocess.run(["git", "branch", "-D", "brr/task-1"], cwd=repo, check=True, stdout=subprocess.PIPE)
