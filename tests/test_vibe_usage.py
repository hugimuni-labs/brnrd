"""vibe_usage: exact-session collection from Vibe's own journal."""
import json
import os
import subprocess
import sys

from brr import vibe_usage


def history(session_id="d27b24f2-97aa-9f27-2ff4-305b523279b6", text="ok"):
    return json.dumps([
        {"type": "message", "role": "user", "generationStatus": "completed",
         "sessionId": session_id,
         "content": [{"type": "text", "text": "hi"}]},
        {"type": "message", "role": "assistant", "generationStatus": "completed",
         "sessionId": session_id,
         "content": [{"type": "text", "text": text}]},
    ])


def unified_session(home, session_id="d27b24f2-97aa-9f27-2ff4-305b523279b6",
                    generation="0000000000000018",
                    token_usage=None, context_usage=None, model="glm-5-3",
                    mtime=None):
    """The journal shape captured from installed Vibe 2.25.5 (unified)."""
    if token_usage is None:
        token_usage = {"cachedInputTokens": 7168, "inputTokens": 7246,
                       "outputTokens": 3, "totalTokens": 7249}
    root = home / "logs" / "session" / "unified" / session_id
    gen = root / "generations" / generation
    gen.mkdir(parents=True)
    (gen / "projection-state.json").write_text(json.dumps({
        "session_id": session_id,
        "snapshot": {"session": {
            "tokenUsage": token_usage, "contextUsage": context_usage,
        }},
    }), encoding="utf-8")
    if model is not None:
        (gen / "runtime-state.json").write_text(json.dumps({
            "session_metadata": {"active_model": model},
        }), encoding="utf-8")
    if mtime is not None:
        for path in sorted(root.rglob("*")):
            os.utime(path, (mtime, mtime))
    return root


def legacy_session(home, session_id, stats=None, active_model="mistral-medium-3.5"):
    """The meta.json shape captured from installed Vibe 2.25.5 (legacy)."""
    if stats is None:
        stats = {
            "session_prompt_tokens": 362298, "session_completion_tokens": 4027,
            "session_cached_tokens": 294656, "context_tokens": 70044,
            "input_price_per_million": 1.5, "output_price_per_million": 7.5,
            "cached_input_price_per_million": 0.15,
            "session_cost": 0.1758639,
        }
    root = home / "logs" / "session"
    root.mkdir(parents=True, exist_ok=True)
    directory = root / ("session_20260929_191511_" + session_id[:8])
    directory.mkdir()
    (directory / "meta.json").write_text(json.dumps({
        "session_id": session_id,
        "config": {"active_model": active_model},
        "stats": stats,
    }), encoding="utf-8")
    return directory


# ── session id extraction ───────────────────────────────────────────────

def test_extract_session_id_reads_the_cli_history():
    assert vibe_usage.extract_session_id(
        history()
    ) == "d27b24f2-97aa-9f27-2ff4-305b523279b6"


def test_extract_never_guesses_an_ambiguous_or_absent_id():
    mixed = json.dumps([
        {"sessionId": "session-a", "type": "message"},
        {"sessionId": "session-b", "type": "message"},
    ])
    assert vibe_usage.extract_session_id(mixed) is None
    assert vibe_usage.extract_session_id("not json") is None
    assert vibe_usage.extract_session_id("[]") is None
    assert vibe_usage.extract_session_id(
        json.dumps([{"type": "message"}])
    ) is None


# ── unified collection ──────────────────────────────────────────────────

def test_collect_unified_matches_the_observed_demo_turn(tmp_path):
    payload = vibe_usage.collect(
        "d27b24f2-97aa-9f27-2ff4-305b523279b6",
        {"VIBE_HOME": str(tmp_path)},
    )
    assert payload["available"] is False  # no journal built yet
    unified_session(tmp_path)
    payload = vibe_usage.collect(
        "d27b24f2-97aa-9f27-2ff4-305b523279b6",
        {"VIBE_HOME": str(tmp_path)},
    )
    assert payload["available"] is True
    assert payload["source"] == "unified"
    assert payload["model"] == "glm-5-3"
    # The numbers the real demo turn recorded (run-260929-2156-cwgm trace).
    assert payload["session_tokens"] == {
        "input": 7246, "cached": 7168, "output": 3, "total": 7249,
    }
    assert payload["context_tokens"] is None  # contextUsage absent here
    # The unified journal persists no prices: cost degrades, never invents.
    assert payload["pricing"] is None
    assert payload["list_price_cost_usd"] is None


def test_collect_selects_the_exact_session_never_the_newest(tmp_path):
    """Two concurrent sessions, both on disk, one newer: id decides."""
    unified_session(tmp_path, "session-a", token_usage={
        "cachedInputTokens": 10, "inputTokens": 100,
        "outputTokens": 5, "totalTokens": 105,
    }, model="model-a", mtime=1000)
    unified_session(tmp_path, "session-b", token_usage={
        "cachedInputTokens": 20, "inputTokens": 200,
        "outputTokens": 6, "totalTokens": 206,
    }, model="model-b", mtime=2_000_000_000)  # far newer by mtime
    env = {"VIBE_HOME": str(tmp_path)}
    picked = vibe_usage.collect("session-a", env)
    assert picked["model"] == "model-a"
    assert picked["session_tokens"]["total"] == 105
    other = vibe_usage.collect("session-b", env)
    assert other["model"] == "model-b"
    assert other["session_tokens"]["total"] == 206


def test_latest_generation_is_sequence_order_not_mtime(tmp_path):
    unified_session(tmp_path, "session-a", generation="0000000000000005",
                    model="old")
    unified_session(tmp_path, "session-a", generation="0000000000000018",
                    model="glm-5-3", mtime=1000)
    # Sequence 5 carries the later mtime; the higher sequence must win.
    payload = vibe_usage.collect("session-a", {"VIBE_HOME": str(tmp_path)})
    assert payload["model"] == "glm-5-3"


# ── legacy collection ───────────────────────────────────────────────────

def test_collect_legacy_reads_stats_prices_and_list_price_cost(tmp_path):
    legacy_session(tmp_path, "1120bd2a-b57f-bd65-bf67-f96071569a89")
    payload = vibe_usage.collect(
        "1120bd2a-b57f-bd65-bf67-f96071569a89", {"VIBE_HOME": str(tmp_path)},
    )
    assert payload["available"] is True
    assert payload["source"] == "legacy"
    assert payload["model"] == "mistral-medium-3.5"
    assert payload["session_tokens"] == {
        "input": 362298, "cached": 294656, "output": 4027, "total": 366325,
    }
    assert payload["context_tokens"] == {"total": 70044}
    assert payload["pricing"] == {
        "input_per_million": 1.5, "output_per_million": 7.5,
        "cached_input_per_million": 0.15,
    }
    # Vibe's own list-price arithmetic, not a subscription bill.
    assert payload["list_price_cost_usd"] == 0.1758639


def test_collect_legacy_matches_by_meta_id_not_directory_prefix(tmp_path):
    """A different session sharing the 8-char name prefix must not match."""
    legacy_session(tmp_path, "1120bd2a-b57f-bd65-bf67-f96071569a89")
    payload = vibe_usage.collect(
        "1120bd2a-0000-0000-0000-000000000000", {"VIBE_HOME": str(tmp_path)},
    )
    assert payload["available"] is False
    assert payload["model"] is None
    assert payload["reason"]


# ── honest degradation ──────────────────────────────────────────────────

def test_missing_journal_degrades_to_unknown(tmp_path):
    payload = vibe_usage.collect("no-such-session", {"VIBE_HOME": str(tmp_path)})
    assert payload["available"] is False
    assert payload["reason"]
    assert payload["model"] is None
    assert payload["session_tokens"] is None
    assert payload["list_price_cost_usd"] is None


def test_malformed_projection_degrades_without_inventing(tmp_path):
    root = unified_session(tmp_path, "session-a", model=None)
    gen = root / "generations" / "0000000000000018"
    (gen / "projection-state.json").write_text("{ not json", encoding="utf-8")
    payload = vibe_usage.collect("session-a", {"VIBE_HOME": str(tmp_path)})
    assert payload["available"] is False
    assert payload["model"] is None


def test_missing_runtime_state_keeps_tokens_and_degrades_model(tmp_path):
    unified_session(tmp_path, "session-a", model=None)  # no runtime-state.json
    payload = vibe_usage.collect("session-a", {"VIBE_HOME": str(tmp_path)})
    assert payload["available"] is True
    assert payload["session_tokens"]["total"] == 7249
    assert payload["model"] is None  # absent, never a fake model


# ── sidecar persistence ─────────────────────────────────────────────────

def test_capture_stdout_writes_outbox_and_shared_sidecars(tmp_path):
    home = tmp_path / "vibe-home"
    unified_session(home, "session-1")
    outbox = tmp_path / "outbox"
    shared = tmp_path / "shared"
    payload = vibe_usage.capture_stdout(history("session-1"), {
        "VIBE_HOME": str(home),
        "BRR_OUTBOX_DIR": str(outbox),
        "BRR_SHARED_DIR": str(shared),
        "BRR_RUN_ID": "run-x",
    })
    assert payload is not None and payload["run_id"] == "run-x"
    for directory in (outbox, shared):
        loaded = vibe_usage.load_sidecar(directory)
        assert loaded["session_id"] == "session-1"
        assert loaded["available"] is True
        assert loaded["session_tokens"]["total"] == 7249


def test_capture_without_a_session_id_writes_nothing(tmp_path):
    outbox = tmp_path / "outbox"
    assert vibe_usage.capture_stdout(json.dumps([{"type": "message"}]), {
        "BRR_OUTBOX_DIR": str(outbox),
    }) is None
    assert not (outbox / vibe_usage.SIDECAR_NAME).exists()


def test_capture_persists_the_coordinate_even_when_usage_is_unknown(tmp_path):
    outbox = tmp_path / "outbox"
    payload = vibe_usage.capture_stdout(history("session-1"), {
        "VIBE_HOME": str(tmp_path / "empty-home"),
        "BRR_OUTBOX_DIR": str(outbox),
    })
    assert payload is not None
    assert payload["available"] is False
    assert payload["reason"]
    assert vibe_usage.load_sidecar(outbox)["session_id"] == "session-1"


# ── adapter wiring ──────────────────────────────────────────────────────

def test_main_captures_usage_without_touching_the_reply(
        tmp_path, monkeypatch):
    from brr import runner, vibe_runner

    home = tmp_path / "vibe-home"
    unified_session(home, "session-1")
    source = tmp_path / "system.md"
    source.write_text("runtime")
    outbox = tmp_path / "outbox"
    monkeypatch.setenv("VIBE_HOME", str(home))
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))
    monkeypatch.setenv("BRR_VIBE_HOOKS", "0")
    monkeypatch.setattr(runner, "protonucleus_path", lambda: source)
    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"read": lambda self: "wake"})())
    stdout = history("session-1", "done")

    def fake_run(cmd, input, text, capture_output, env):  # noqa: ANN001
        assert cmd[0] == "vibe"
        return subprocess.CompletedProcess(
            cmd, 0, stdout=stdout, stderr="",
        )

    monkeypatch.setattr(vibe_runner.subprocess, "run", fake_run)
    assert vibe_runner.main() == 0
    sidecar = vibe_usage.load_sidecar(outbox)
    assert sidecar["session_id"] == "session-1"
    assert sidecar["available"] is True
    assert sidecar["session_tokens"]["total"] == 7249
