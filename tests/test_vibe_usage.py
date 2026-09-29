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
    # meta.json's active_model is configured, not observed: no model claim.
    assert payload["model"] is None
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


# ── numeric defenses ───────────────────────────────────────────────────

def test_inconsistent_tokens_degrade_to_absent(tmp_path):
    unified_session(tmp_path, "session-a", token_usage={
        # cached over input: not usage, corruption.
        "cachedInputTokens": 9000, "inputTokens": 100,
        "outputTokens": 3, "totalTokens": 103,
    })
    payload = vibe_usage.collect("session-a", {"VIBE_HOME": str(tmp_path)})
    # The model still proves the session; the corrupted tokens stay absent.
    assert payload["session_tokens"] is None
    unified_session(tmp_path, "session-b", token_usage={
        # total off the input+output sum: same verdict.
        "cachedInputTokens": 10, "inputTokens": 100,
        "outputTokens": 3, "totalTokens": 999,
    })
    payload = vibe_usage.collect("session-b", {"VIBE_HOME": str(tmp_path)})
    assert payload["session_tokens"] is None


def test_nonfinite_prices_and_costs_are_absent(tmp_path):
    legacy_session(tmp_path, "session-a", stats={
        "session_prompt_tokens": 100, "session_cached_tokens": 10,
        "session_completion_tokens": 5, "context_tokens": 50,
        "input_price_per_million": float("nan"),
        "output_price_per_million": float("inf"),
        "cached_input_price_per_million": -1.0,
        "session_cost": -0.5,
    })
    payload = vibe_usage.collect("session-a", {"VIBE_HOME": str(tmp_path)})
    assert payload["available"] is True  # tokens still usable
    assert payload["pricing"] is None
    assert payload["list_price_cost_usd"] is None


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
    # The outbox keeps the fixed name; the shared copy is run-scoped and
    # a reader must pass the matching run_id.
    loaded = vibe_usage.load_sidecar(outbox)
    assert loaded["session_id"] == "session-1"
    assert loaded["available"] is True
    assert loaded["session_tokens"]["total"] == 7249
    assert (shared / vibe_usage.SIDECAR_NAME).exists() is False
    shared_loaded = vibe_usage.load_sidecar(shared, run_id="run-x")
    assert shared_loaded["session_id"] == "session-1"
    assert shared_loaded["run_id"] == "run-x"


def test_concurrent_runs_keep_their_own_shared_records(tmp_path):
    """Two children, one shared dir: each keeps its own run-scoped fact."""
    home = tmp_path / "vibe-home"
    unified_session(home, "session-a", token_usage={
        "cachedInputTokens": 10, "inputTokens": 100,
        "outputTokens": 5, "totalTokens": 105,
    }, model="model-a")
    unified_session(home, "session-b", token_usage={
        "cachedInputTokens": 20, "inputTokens": 200,
        "outputTokens": 6, "totalTokens": 206,
    }, model="model-b")
    shared = tmp_path / "shared"
    for run_id, sid in (("run-a", "session-a"), ("run-b", "session-b")):
        payload = vibe_usage.capture_stdout(history(sid), {
            "VIBE_HOME": str(home),
            "BRR_SHARED_DIR": str(shared),
            "BRR_RUN_ID": run_id,
        })
        assert payload is not None
    a = vibe_usage.load_sidecar(shared, run_id="run-a")
    b = vibe_usage.load_sidecar(shared, run_id="run-b")
    assert a["model"] == "model-a" and a["session_tokens"]["total"] == 105
    assert b["model"] == "model-b" and b["session_tokens"]["total"] == 206
    # A reader with the wrong run_id gets nothing, not someone else's fact.
    assert vibe_usage.load_sidecar(shared, run_id="run-c") is None


def test_clear_sidecars_removes_this_run_stale_snapshot(tmp_path):
    outbox = tmp_path / "outbox"
    shared = tmp_path / "shared"
    stale = {"session_id": "old", "run_id": "run-x", "available": True}
    vibe_usage.write_sidecar(outbox, stale)
    vibe_usage.write_sidecar(
        shared, stale, name=vibe_usage.shared_sidecar_name("run-x"))
    vibe_usage.clear_sidecars({
        "BRR_OUTBOX_DIR": str(outbox),
        "BRR_SHARED_DIR": str(shared),
        "BRR_RUN_ID": "run-x",
    })
    assert not (outbox / vibe_usage.SIDECAR_NAME).exists()
    assert not (shared / vibe_usage.shared_sidecar_name("run-x")).exists()


def test_session_id_rejects_path_traversal(tmp_path):
    """A hostile coordinate never reaches the filesystem."""
    evil = json.dumps([{"sessionId": "../../etc", "type": "message"}])
    assert vibe_usage.extract_session_id(evil) is None
    assert vibe_usage.capture_stdout(evil, {
        "BRR_OUTBOX_DIR": str(tmp_path / "outbox"),
    }) is None
    payload = vibe_usage.collect("../escape")
    assert payload["available"] is False
    assert payload["reason"] == "no valid session id"
    assert not (tmp_path / "escape").exists()


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


def test_main_clears_stale_sidecar_before_invoking(tmp_path, monkeypatch):
    """A retry that yields no session id must not serve the last attempt."""
    from brr import runner, vibe_runner

    source = tmp_path / "system.md"
    source.write_text("runtime")
    outbox = tmp_path / "outbox"
    stale = {"session_id": "previous-attempt", "available": True,
             "session_tokens": {"input": 1, "cached": 1, "output": 1, "total": 2}}
    vibe_usage.write_sidecar(outbox, stale)
    monkeypatch.setenv("VIBE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))
    monkeypatch.setenv("BRR_VIBE_HOOKS", "0")
    monkeypatch.setattr(runner, "protonucleus_path", lambda: source)
    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"read": lambda self: "wake"})())

    def fake_run(cmd, input, text, capture_output, env):  # noqa: ANN001
        # A completed reply that declares no session id: the invocation
        # succeeds, there is just no exact coordinate to persist.
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=json.dumps([{"type": "message", "role": "assistant",
                                "generationStatus": "completed",
                                "content": [{"type": "text", "text": "done"}]}]),
            stderr="",
        )

    monkeypatch.setattr(vibe_runner.subprocess, "run", fake_run)
    assert vibe_runner.main() == 0
    assert not (outbox / vibe_usage.SIDECAR_NAME).exists()


def test_telemetry_failure_never_breaks_a_good_reply(tmp_path, monkeypatch, capsys):
    """collect raising OSError must not change the adapter's exit code."""
    from brr import runner, vibe_runner

    source = tmp_path / "system.md"
    source.write_text("runtime")
    outbox = tmp_path / "outbox"
    monkeypatch.setenv("VIBE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(outbox))
    monkeypatch.setenv("BRR_VIBE_HOOKS", "0")
    monkeypatch.setattr(runner, "protonucleus_path", lambda: source)
    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"read": lambda self: "wake"})())

    def fake_run(cmd, input, text, capture_output, env):  # noqa: ANN001
        return subprocess.CompletedProcess(
            cmd, 0, stdout=history("session-1", "done"), stderr="",
        )

    def exploding_collect(session_id, env=None):  # noqa: ANN001
        raise OSError("journal dir vanished mid-scan")

    monkeypatch.setattr(vibe_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(vibe_usage, "collect", exploding_collect)
    assert vibe_runner.main() == 0
    assert capsys.readouterr().out == "done\n"
    assert not (outbox / vibe_usage.SIDECAR_NAME).exists()


def test_runner_attests_only_its_own_invocation(tmp_path):
    from brr import runner

    vibe_usage.write_sidecar(tmp_path, {"run_id": "run-a", "model": "glm-5-3"})
    env = {"BRR_OUTBOX_DIR": str(tmp_path), "BRR_RUN_ID": "run-a"}
    assert runner._process_runner_stdout("vibe", "final reply", env) == (
        "final reply", "glm-5-3", False,
    )
    env["BRR_RUN_ID"] = "run-b"
    assert runner._process_runner_stdout("vibe", "final reply", env) == (
        "final reply", None, False,
    )


def test_daemon_delivers_journal_tokens_to_ledger_without_double_count(tmp_path):
    from brr import daemon, run_ledger

    home = tmp_path / "vibe"
    outbox = tmp_path / "outbox"
    unified_session(home, "session-a")
    vibe_usage.capture_stdout(history("session-a"), {
        "VIBE_HOME": str(home), "BRR_OUTBOX_DIR": str(outbox),
        "BRR_RUN_ID": "run-a",
    })
    levels, slots = daemon._collect_levels("vibe-glm-5-3", outbox, refresh=False)
    assert run_ledger.token_fields(levels) == {
        "tokens_input": 78, "tokens_output": 3, "tokens_cache_read": 7168,
        "tokens_cache_creation": 0, "context_window_used": None,
    }
    assert slots == frozenset()
    assert "quota" not in levels
    assert "context_window" not in levels
    assert "spend" not in levels  # no prices persisted in the unified journal


def test_daemon_never_borrows_another_vibe_run_from_shared_dir(tmp_path):
    from brr import daemon

    vibe_usage.write_sidecar(tmp_path, {
        "available": True, "session_tokens": {
            "input": 100, "cached": 50, "output": 10, "total": 110,
        },
    })
    assert daemon._collect_levels("vibe", None, shared_dir=tmp_path) == (
        None, frozenset(),
    )
