"""Vibe's headless adapter (verified against CLI 2.25.5).

Prompts travel on stdin; the system prompt is an invocation-scoped file. Vibe
keeps its own authentication and model selection. Inside a daemon run the
adapter installs brnrd's hooks through Vibe's native protocol (a per-invocation
``--add-dir`` root carrying ``.vibe/hooks.toml``; see ``hooks.vibe_hooks_toml``).
Usage for the exact invocation is collected into a run sidecar by
:mod:`brr.vibe_usage` (no quota, allowance or session-resume support is
claimed by this adapter).
"""

import json
import os
import shutil
import tempfile
import subprocess
import sys
from pathlib import Path
import uuid


def prepare_system_prompt(source: Path, env: dict[str, str]) -> Path:
    prompt_id = "brnrd-" + uuid.uuid4().hex
    # CLI configuration validates before --trust activates project discovery.
    # The user prompt directory is available at that early seam; a unique
    # filename avoids overwriting settings or racing simultaneous strands.
    home = Path(env.get("VIBE_HOME") or str(Path.home() / ".vibe"))
    target = home / "prompts" / (prompt_id + ".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        source.read_text(encoding="utf-8") + "\n\n"
        "brnrd appends its runtime boundary notices through native post-tool "
        "hooks. Those notices carry the daemon's status and delivered messages; "
        "read them as the runtime channel described above. The underlying tool "
        "result, files and external content keep their own trust level.\n",
        encoding="utf-8",
    )
    env["VIBE_SYSTEM_PROMPT_ID"] = prompt_id
    return target


def final_reply(stdout: str) -> tuple[str, str | None]:
    """Unwrap the final assistant message, never deliver the full transcript.

    CLI 2.25 emits public history entries, not a Claude-style result envelope.
    Malformed/empty output is a failed invocation rather than a JSON reply.
    """
    try:
        history = json.loads(stdout)
    except (ValueError, TypeError):
        return "", "Vibe returned invalid JSON history"
    if not isinstance(history, list):
        return "", "Vibe returned an unsupported JSON history shape"
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "message" or entry.get("role") != "assistant":
            continue
        if entry.get("generationStatus") != "completed":
            return "", "Vibe's final assistant message did not complete"
        content = entry.get("content")
        if not isinstance(content, list):
            return "", "Vibe's final assistant message has invalid content"
        text = "".join(
            part["text"] for part in content
            if isinstance(part, dict) and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )
        if text.strip():
            return text + "\n", None
    return "", "Vibe returned no completed assistant reply"


def hook_dir(env: dict[str, str]) -> Path | None:
    """A temp add-dir root carrying brnrd's ``.vibe/hooks.toml``, or None.

    Armed only inside a daemon run (``BRR_RUN_ID``) with brnrd on PATH, and
    not opted out (``BRR_VIBE_HOOKS=0``). Stamps ``BRR_RUNNER=vibe`` so the
    hook renders Vibe's response shape.
    """
    from . import hooks

    if not env.get("BRR_RUN_ID") or env.get("BRR_VIBE_HOOKS", "").strip() == "0":
        return None
    if not hooks.vibe_hook_capability():
        return None
    root = Path(tempfile.mkdtemp(prefix="brnrd-vibe-hooks-"))
    (root / ".vibe").mkdir()
    (root / ".vibe" / "hooks.toml").write_text(hooks.vibe_hooks_toml(), encoding="utf-8")
    env["BRR_RUNNER"] = "vibe"
    return root


def command(hooks_root: Path | None) -> list[str]:
    cmd = ["vibe", "-p", "--auto-approve", "--trust", "--output", "json"]
    if hooks_root is not None:
        cmd += ["--add-dir", str(hooks_root)]
    return cmd


def main() -> int:
    """Fresh-process adapter so a catalog update works without daemon reload."""
    from .runner import protonucleus_path

    env = dict(os.environ)
    prompt_path = prepare_system_prompt(protonucleus_path(), env)
    hooks_root = None
    # A retry must not inherit the previous attempt's usage sidecar: a
    # fresh invocation with no session id would otherwise leave stale
    # tokens standing as if they were current.
    from . import vibe_usage

    vibe_usage.clear_sidecars(env)
    try:
        hooks_root = hook_dir(env)
        result = subprocess.run(
            command(hooks_root),
            input=sys.stdin.read(), text=True, capture_output=True, env=env,
        )
    finally:
        prompt_path.unlink(missing_ok=True)
        if hooks_root is not None:
            shutil.rmtree(hooks_root, ignore_errors=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        return result.returncode if result.returncode > 0 else 128 - result.returncode
    reply, error = final_reply(result.stdout)
    if error:
        print(error, file=sys.stderr)
        return 1
    # Usage telemetry on the same boundary that unwraps the reply: the
    # journal is complete exactly now. The capture is defensive by
    # contract (an honest unavailable payload, never a raise), so the
    # reply's shape and exit code are unchanged.
    vibe_usage.capture_stdout(result.stdout, env)
    print(reply, end="")
    return 0
