"""Vibe's whoami-cache plan collector and its daemon wiring.

The cache carries plan metadata but no monthly allowance. Unknown binds no
numeric quota; stale facts keep their own date, and secrets stay private.
"""
import json
import time

import pytest

from brr import facets, runner_quota, vibe_status


def _entry(stored_at, plan_type="chat", plan_name="INDIVIDUAL", customer="94039872-70f3-4c90-a810-ce51706f4f82"):
    return {
        "stored_at_timestamp": stored_at,
        "payload": {
            "plan_type": plan_type,
            "plan_name": plan_name,
            "prompt_switching_to_pro_plan": False,
            "organization_kind": "S",
            "customer_id": customer,
            "api_base": "https://api.mistral.ai",
            "vibe_base": "https://chat.mistral.ai",
        },
    }


def _write_cache(home, entries):
    home.mkdir(parents=True, exist_ok=True)
    (home / "whoami_cache.json").write_text(json.dumps(entries), encoding="utf-8")


@pytest.mark.parametrize("name", ["vibe", "Vibe", "vibe-pro"])
def test_supported_matches_vibe_flavours(name):
    assert vibe_status.supported(name)


@pytest.mark.parametrize("name", [None, "", "codex", "claude", "codex-vibeless", "vibeless"])
def test_supported_rejects_other_shells(name):
    assert not vibe_status.supported(name)


def test_load_levels_reports_cached_plan_and_unavailable_allowance(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    at = int(time.time()) - 60
    _write_cache(tmp_path, {"2f966a92630978610867251fc16b30ab": _entry(at)})

    levels = vibe_status.load_levels()
    quota = levels["quota"]
    summary = quota["summary"]
    assert "INDIVIDUAL" in summary and "chat" in summary
    # Cached metadata, explicitly not an active-billing claim.
    assert "cached" in summary and "not active billing" in summary
    assert "unavailable from this cache" in summary
    # No guessed number: no percent, no reset, no "unlimited".
    assert "%" not in summary and "unlimited" not in summary
    # The measurement date is the cache entry's own clock.
    assert quota["updated_at"] == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))
    assert levels["source"] == "vibe-whoami-cache"


def test_load_levels_two_unrelated_cached_keys_read_ambiguous(tmp_path, monkeypatch):
    """Regression for the parent's review (2026-09-29): the cache is keyed by
    a credential hash, and the active credential cannot be proven passively —
    so two unrelated cached keys must never resolve to "the freshest entry is
    this account". Plan facts of either entry are claimed for neither."""
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    _write_cache(tmp_path, {
        "2f966a92630978610867251fc16b30ab": _entry(1000, plan_name="OLDKEY"),
        "f00dcafef00dcafef00dcafef00dcafe": _entry(int(time.time()), plan_name="NEWKEY"),
    })
    levels = vibe_status.load_levels()
    summary = levels["quota"]["summary"]
    assert "ambiguous" in summary and "2 cached credentials" in summary
    assert "unavailable from this cache" in summary
    # Neither entry's plan is claimed — not even the freshest one's.
    assert "NEWKEY" not in summary and "OLDKEY" not in summary
    assert "unproven" in summary
    # The ambiguous reading binds nothing, same as the unknown one.
    assert runner_quota.binding_quota_remaining_pct(levels) is None


def test_load_levels_stale_entry_is_marked_not_served_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    at = int(time.time()) - int(vibe_status.WHOAMI_TTL_SECONDS) - 300
    _write_cache(tmp_path, {"2f966a92630978610867251fc16b30ab": _entry(at)})

    quota = vibe_status.load_levels()["quota"]
    assert "(stale)" in quota["summary"]
    assert quota["updated_at"] == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


@pytest.mark.parametrize("body", ["", "not json", "[]", '{"key": null}', "{}",
                                  '{"key": {"stored_at_timestamp": 1, "payload": {}}}'])
def test_missing_or_planless_cache_yields_no_reading(tmp_path, monkeypatch, body):
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    (tmp_path / "whoami_cache.json").write_text(body, encoding="utf-8")
    assert vibe_status.load_levels() is None


def test_no_cache_file_yields_no_reading(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    assert vibe_status.load_levels() is None


def test_levels_never_expose_key_hash_or_customer_id(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    _write_cache(tmp_path, {"2f966a92630978610867251fc16b30ab": _entry(int(time.time()))})

    rendered = json.dumps(vibe_status.load_levels())
    assert "2f966a92630978610867251fc16b30ab" not in rendered
    assert "94039872-70f3-4c90-a810-ce51706f4f82" not in rendered
    assert "customer_id" not in rendered
    assert "api.mistral.ai" not in rendered


def test_unknown_allowance_binds_no_quota_number(tmp_path, monkeypatch):
    """The whole point of the unknown floor: a summary-only reading must not
    feed ``binding_quota_remaining_pct`` a number it can pace or refuse on."""
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    _write_cache(tmp_path, {"k": _entry(int(time.time()))})
    levels = vibe_status.load_levels()
    assert runner_quota.binding_quota_remaining_pct(levels) is None
    assert runner_quota.binding_quota_bucket(levels) is None


# ── the daemon's public collect path ─────────────────────────────────────────

def test_daemon_collect_supported_renders_known_quota_and_unimplemented_rest(
    tmp_path, monkeypatch,
):
    from brr import daemon

    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    _write_cache(tmp_path, {"k": _entry(int(time.time()))})
    levels, slots = daemon._collect_levels("vibe", None)
    assert slots == frozenset({"quota"})
    res = facets.build(levels=levels, levels_collector=slots)
    assert res["quota"]["status"] == "known"
    assert "unavailable from this cache" in res["quota"]["summary"]
    assert res["spend"]["status"] == "unimplemented"
    assert res["context_window"]["status"] == "unimplemented"


def test_daemon_collect_missing_cache_reads_absent(tmp_path, monkeypatch):
    from brr import daemon

    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    levels, slots = daemon._collect_levels("vibe", tmp_path, refresh=False)
    assert levels is None
    assert slots == frozenset({"quota"})
    res = facets.build(levels=levels, levels_collector=slots)
    assert res["quota"]["status"] == "absent"


def test_daemon_collect_error_cache_reads_absent(tmp_path, monkeypatch):
    """A corrupt cache is an honest empty, never a crash on the collect path."""
    from brr import daemon

    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    _write_cache(tmp_path, {"k": _entry(1)})
    (tmp_path / "whoami_cache.json").write_text("{corrupt", encoding="utf-8")
    levels, slots = daemon._collect_levels("vibe", tmp_path)
    assert levels is None
    assert facets.build(levels=levels, levels_collector=slots)["quota"]["status"] == "absent"


def test_daemon_collect_stale_reading_is_marked(tmp_path, monkeypatch):
    from brr import daemon

    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    at = int(time.time()) - int(vibe_status.WHOAMI_TTL_SECONDS) - 300
    _write_cache(tmp_path, {"k": _entry(at)})
    levels, _ = daemon._collect_levels("vibe", tmp_path)
    assert "(stale)" in levels["quota"]["summary"]


def test_quota_summary_parses_to_no_chip_bucket():
    """The hooks chip parses ``<label> N% left`` clauses; an unknown-allowance
    summary must render no bucket — checked here through the same seam the
    boundary line reads, so the two cannot drift apart silently."""
    from brr import hooks

    facet = {"quota": {"status": "known",
                       "summary": "vibe cached plan INDIVIDUAL / chat (cached metadata, not active billing); remaining allowance unavailable from this cache; cached 2026-09-29T20:35:11Z"}}
    assert hooks._quota_buckets(facet) == []


@pytest.mark.parametrize("timestamp", [float("inf"), float("nan"), 10**400, 1e30])
def test_invalid_cache_date_preserves_plan_without_fabricating_date(tmp_path, timestamp):
    _write_cache(tmp_path, {"key": _entry(timestamp)})
    levels = vibe_status.load_levels({"VIBE_HOME": str(tmp_path)})
    assert "INDIVIDUAL" in levels["quota"]["summary"]
    assert "updated_at" not in levels["quota"]
    assert "updated_at" not in levels


def test_schema_changed_plan_objects_are_not_rendered(tmp_path):
    _write_cache(tmp_path, {"key": _entry(100, plan_name={"secret": "private"}, plan_type=[])})
    assert vibe_status.load_levels({"VIBE_HOME": str(tmp_path)}) is None
