"""Tests for source-trust tiering (#517).

Covers the deterministic tier resolution per source × config matrix, the
tier → env routing (including the no-escalation pin and the fail-closed
refusal when solitary is unavailable), that owner-only paths are
unaffected at zero config, and that each gate stamps the tier from the
sender facts it already holds.
"""

from __future__ import annotations

import pytest

from brr import trust
from brr.run import Run


# ── tier resolution: source × stamp matrix ──────────────────────────────


@pytest.mark.parametrize("source", ["schedule", "cli", "respawn", "spawn", "cloud"])
def test_owner_only_sources_resolve_owner_without_a_stamp(source):
    assert trust.resolve_tier({"source": source}) == trust.OWNER


@pytest.mark.parametrize("source", ["github", "telegram", "slack", "some-future-gate", ""])
def test_ingress_sources_fail_closed_to_untrusted_without_a_stamp(source):
    # An ingress path that can carry strangers, with no gate authorization
    # stamp, must fail closed rather than inherit a trusted default.
    assert trust.resolve_tier({"source": source}) == trust.UNTRUSTED


@pytest.mark.parametrize("tier", [trust.OWNER, trust.COLLABORATOR, trust.UNTRUSTED])
def test_stamped_tier_wins_over_source_default(tier):
    # Even an owner-only source is capped by an explicit downgrade stamp,
    # and an ingress source is lifted by a gate's authorization stamp.
    assert trust.resolve_tier({"source": "github", "trust_tier": tier}) == tier
    assert trust.resolve_tier({"source": "schedule", "trust_tier": tier}) == tier


def test_unknown_stamp_value_falls_back_to_source_default():
    assert trust.resolve_tier({"source": "cli", "trust_tier": "bogus"}) == trust.OWNER
    assert trust.resolve_tier({"source": "github", "trust_tier": "bogus"}) == trust.UNTRUSTED


# ── tier → env routing ──────────────────────────────────────────────────


def test_owner_gets_configured_default_zero_config():
    d = trust.resolve_decision({"source": "schedule"}, {})
    assert d.tier == trust.OWNER
    assert d.env == "worktree"
    assert not d.refused


def test_owner_honours_event_environment_key():
    d = trust.resolve_decision({"source": "schedule", "environment": "host"}, {})
    assert d.env == "host"


def test_collaborator_zero_config_is_todays_default():
    d = trust.resolve_decision({"source": "github", "trust_tier": "collaborator"}, {})
    assert d.tier == trust.COLLABORATOR
    assert d.env == "worktree"
    assert not d.refused


def test_collaborator_env_override_caps_the_env():
    d = trust.resolve_decision(
        {"source": "github", "trust_tier": "collaborator", "environment": "host"},
        {"trust.collaborator_env": "solitary", "docker.image": "img"},
    )
    # The override wins over the event's own environment key.
    assert d.env == "solitary"


def test_untrusted_routes_to_solitary_when_available():
    d = trust.resolve_decision(
        {"source": "telegram", "trust_tier": "untrusted"},
        {"docker.image": "img"},
    )
    assert d.tier == trust.UNTRUSTED
    assert d.env == "solitary"
    assert not d.refused


def test_untrusted_env_key_never_escalates():
    # The no-escalation pin: an event-supplied environment must NEVER lift
    # an untrusted event out of its tier.
    d = trust.resolve_decision(
        {"source": "telegram", "trust_tier": "untrusted", "environment": "host"},
        {"docker.image": "img"},
    )
    assert d.env == "solitary"
    assert d.env != "host"


def test_untrusted_refuses_when_solitary_unavailable():
    # Fail closed: no docker.image means solitary cannot back the run.
    d = trust.resolve_decision({"source": "telegram", "trust_tier": "untrusted"}, {})
    assert d.refused
    assert d.env is None
    assert "solitary" in d.reason


def test_untrusted_refuse_mode():
    d = trust.resolve_decision(
        {"source": "telegram", "trust_tier": "untrusted"},
        {"docker.image": "img", "trust.untrusted": "refuse"},
    )
    assert d.refused
    assert d.reason == "trust.untrusted=refuse"


def test_untrusted_env_override_to_worktree_is_honoured():
    # An operator can explicitly opt untrusted down to a weaker env; it is
    # their call and requires no docker.image.
    d = trust.resolve_decision(
        {"source": "telegram", "trust_tier": "untrusted"},
        {"trust.untrusted_env": "worktree"},
    )
    assert not d.refused
    assert d.env == "worktree"


def test_trust_underscore_config_form_is_accepted():
    d = trust.resolve_decision(
        {"source": "telegram", "trust_tier": "untrusted"},
        {"trust_untrusted": "refuse"},
    )
    assert d.refused


# ── from_event integration ──────────────────────────────────────────────


def test_from_event_stamps_tier_on_meta():
    task = Run.from_event({"id": "e", "source": "github", "trust_tier": "collaborator"})
    assert task.meta["trust_tier"] == "collaborator"
    assert task.env == "worktree"
    assert "trust_refused" not in task.meta


def test_from_event_marks_refusal():
    task = Run.from_event({"id": "e", "source": "telegram", "trust_tier": "untrusted"})
    assert task.meta["trust_tier"] == "untrusted"
    assert task.meta.get("trust_refused")


def test_from_event_owner_paths_unaffected_zero_config():
    # Schedule / CLI / self-wake carry no stamp and must run exactly as today.
    for source in ("schedule", "cli", "respawn"):
        task = Run.from_event({"id": "e", "source": source, "body": "x"})
        assert task.meta["trust_tier"] == trust.OWNER
        assert task.env == "worktree"
        assert "trust_refused" not in task.meta


def test_from_event_untrusted_env_key_cannot_escalate():
    task = Run.from_event(
        {"id": "e", "source": "telegram", "trust_tier": "untrusted",
         "environment": "host"},
        {"docker.image": "img"},
    )
    assert task.env == "solitary"


# ── gate stamping ───────────────────────────────────────────────────────


def test_telegram_sender_tier_owner_vs_collaborator():
    from brr.gates import telegram

    state = {"paired_user_id": 111, "allowlist": [222]}
    assert telegram._sender_tier(state, 111) == trust.OWNER
    assert telegram._sender_tier(state, 222) == trust.COLLABORATOR
    assert telegram._sender_tier(state, 333) is None
    assert telegram._sender_tier(state, None) is None


def test_github_gate_stamps_collaborator(tmp_path, monkeypatch):
    from brr import protocol
    from brr.gates.github import state, loop, client

    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    state._save_state(brr_dir, {
        "token": "secret",
        "bot_login": "brr-bot",
        "repo": "owner/name",
        "triggers": {"mention": "@brr-bot"},
        "allowlist": ["Trusted-Outsider"],
    })
    monkeypatch.setattr(
        client, "_api_get",
        lambda token, path, params=None, **kwargs: [
            {
                "id": 502,
                "body": "@brr-bot do something",
                "user": {"login": "trusted-outsider"},
                "issue_url": "https://api.github.com/repos/owner/name/issues/7",
                "html_url": "https://github.com/owner/name/issues/7#issuecomment-502",
                "updated_at": "2026-06-01T00:00:00Z",
            },
        ] if path == "/repos/owner/name/issues/comments" else [],
    )

    loop._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    assert len(events) == 1
    assert events[0].get("trust_tier") == "collaborator"


def test_slack_gate_stamps_collaborator(tmp_path, monkeypatch):
    from brr import protocol
    from brr.gates import slack

    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    slack._save_state(brr_dir, {"token": "x", "channel": "C1", "oldest_ts": "0"})

    def fake_api(token, method, params=None):
        if method == "conversations.history":
            return {"messages": [{"text": "hello", "ts": "1.0", "user": "U1"}]}
        return {"ok": True}

    monkeypatch.setattr(slack, "_slack_api", fake_api)
    slack._loop_once(brr_dir, inbox, responses)

    events = protocol.list_pending(inbox)
    assert len(events) == 1
    assert events[0].get("trust_tier") == "collaborator"
