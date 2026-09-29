"""Vibe's headless Tier-1 adapter (verified against CLI 2.25.5).

Prompts travel on stdin; the system prompt is an invocation-scoped file. Vibe
keeps its own authentication and model selection. No native hooks, quota
collector or session-resume support is claimed by this adapter.
"""

import json
import os
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
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
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


def main() -> int:
    """Fresh-process adapter so a catalog update works without daemon reload."""
    from .runner import protonucleus_path

    env = dict(os.environ)
    prompt_path = prepare_system_prompt(protonucleus_path(), env)
    try:
        result = subprocess.run(
            ["vibe", "-p", "--auto-approve", "--trust", "--output", "json"],
            input=sys.stdin.read(), text=True, capture_output=True, env=env,
        )
    finally:
        prompt_path.unlink(missing_ok=True)
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
    print(reply, end="")
    return 0
