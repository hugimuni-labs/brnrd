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


# ── session id extraction ───────────────────────────────────────────────


# ── unified collection ──────────────────────────────────────────────────


# ── legacy collection ───────────────────────────────────────────────────


# ── honest degradation ──────────────────────────────────────────────────


# ── numeric defenses ───────────────────────────────────────────────────


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


