"""The test the original defect needed: a full capture cycle on a synthetic
account home never tracks a secret.

``dominion.commit()`` -> ``gitops.commit_all()`` runs ``git add -A`` on the
whole home root — it cannot tell a secret from a note. Two files land in
that tree by construction and carry credentials: ``account/gates/
cloud.json`` (the daemon's bearer token — 107 commits, live on
``hugimuni-labs/brnrd-home`` before this fix) and ``security.config`` (the
daemon-owned trust domain; empty of a secret today only by luck, per its own
module docstring).

This drives the real chain — ``account.resolve_context`` to build the home,
``gates.cloud._save_state`` to write a credential-shaped ``cloud.json`` the
way the running gate actually would, ``dominion.commit`` to run the same
capture the daemon runs after every thought — and checks the one property
that actually matters: what a full ``git ls-files`` / ``git show`` on the
result contains. The set of paths that must never appear is
``account.NEVER_TRACKED_FILES`` — the owning module's own list, not a copy
hand-written here (a hand-written copy is exactly the kind of second list
that drifts out from under the code it was meant to pin).
"""

from __future__ import annotations

import json
import subprocess

from brr import account, config as conf, dominion
from brr.gates import cloud

from _helpers import write_repo_scaffold


def test_full_capture_cycle_never_tracks_a_secret(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "account-home"

    # On-disk config, exactly as a connected install has it — not just an
    # in-memory dict — so every later call in this test (cloud._save_state,
    # dominion's own resolution) re-derives the same home independently,
    # the way separate daemon call sites really do.
    conf.write_config(
        repo,
        {"home.kind": "account", "home.path": str(home), "account.id": "acct-1"},
    )
    cfg = conf.load_config(repo)
    ctx = account.resolve_context(repo, cfg)

    brr_dir = repo / ".brr"
    # A credential-shaped cloud.json — pairing identity plus a live bearer
    # token, written the way the running gate actually persists it.
    live_token = "bd_live_secret_do_not_commit"
    cloud._save_state(
        brr_dir,
        {
            "brnrd_url": "https://brnrd.example",
            "token": live_token,
            "account_id": "acct-1",
            "repo_id": "proj-1",
            "daemon_name": "laptop",
            "since": 42,
            "capabilities": {"git_remote": "git@github.com:x/y.git"},
        },
    )

    # security.config: today it holds no secret in this repo either — the
    # point is that the *file* must never be tracked regardless of what it
    # currently holds.
    (home / "security.config").write_text(
        "docker.image=ghcr.io/x/y\n", encoding="utf-8"
    )

    # Ordinary dominion content, so the capture net has something to commit
    # in the same pass that reaches the whole home root.
    label = account.repo_label(repo, cfg)
    dominion_dir = account.repo_dominion_path(ctx, label)
    dominion_dir.mkdir(parents=True, exist_ok=True)
    (dominion_dir / "playbook.md").write_text("# memory\n", encoding="utf-8")

    committed = dominion.commit(dominion_dir, "test: full capture cycle")
    assert committed is True

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=home, check=True, capture_output=True, text=True,
    ).stdout.splitlines()

    for never in account.NEVER_TRACKED_FILES:
        assert never not in tracked, (
            f"{never!r} was captured by the dominion commit's git add -A — "
            "the generator this whole fix closes"
        )

    # The pairing identity legitimately stays tracked — but its committed
    # blob must never carry the secret, in the file or as a substring.
    assert "account/gates/cloud.json" in tracked
    blob = subprocess.run(
        ["git", "show", "HEAD:account/gates/cloud.json"],
        cwd=home, check=True, capture_output=True, text=True,
    ).stdout
    assert live_token not in blob
    committed_state = json.loads(blob)
    assert "token" not in committed_state
    assert committed_state["repo_id"] == "proj-1"
    assert committed_state["since"] == 42

    # And the live gate is still fully configured after the capture cycle —
    # the split must not have cost the running daemon its own credential.
    assert cloud.is_configured(brr_dir)
    assert cloud._load_state(brr_dir)["token"] == live_token
