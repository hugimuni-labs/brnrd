"""Grok Build's headless adapter.

Grok does not read a piped prompt (its headless guide: pass ``--prompt-file``).
brnrd's runner contract pipes the wake on stdin, so this process is the seam:
stdin goes to a temp file, then to ``grok --prompt-file``. The wake never
sits in argv.

The resident protonucleus rides ``--rules``, which Grok appends to its own
system prompt. Replacing that prompt would drop the tool instructions the
Shell needs to act. Model selection is the ``GROK_ACTIVE_MODEL`` environment
variable set by :func:`brr.runner_cores._cmd_with_model`, because the profile
command is this adapter rather than ``grok`` itself.

Hook config is the ``.claude/settings.local.json`` the worker writes for the
``grok`` flavour. Grok loads that Claude-compatible project file when the
folder is trusted, so the adapter passes ``--trust``. ``--permission-mode
bypassPermissions`` keeps a headless wake from stopping on a permission
prompt; deny rules and hooks still apply.

On success this process prints Grok's JSON envelope unchanged. The runner
reads ``sessionId``, ``usage``, ``modelUsage``, and ``total_cost_usd`` from
that object the way it reads Claude's result JSON. A held host seat resumes
by setting ``GROK_RESUME_SESSION`` to the UUID this envelope reported; the
adapter forwards it as ``--resume`` and does not put it on its own argv.
Quota is not read. Unknown capacity is not unlimited.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def final_reply(stdout: str) -> tuple[str, str | None]:
    """Unwrap ``--output-format json`` into the assistant text.

    A missing or blank ``text`` is a failed invocation. Grok's error object
    (``{"type":"error","message":...}``) is a failure even when a caller
    already treated a non-zero exit as one.
    """
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return "", "Grok returned invalid JSON"
    if not isinstance(payload, dict):
        return "", "Grok returned an unsupported JSON shape"
    if payload.get("type") == "error":
        message = payload.get("message")
        return "", str(message or "Grok returned an error")
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return "", "Grok returned no assistant reply"
    if not text.endswith("\n"):
        text += "\n"
    return text, None


def command(
    prompt_path: Path,
    *,
    model: str | None,
    rules: str,
    resume: str | None = None,
) -> list[str]:
    """Argv for one headless Grok invocation. The prompt is a file, not argv."""
    cmd = ["grok"]
    if resume:
        cmd += ["--resume", resume]
    if model:
        cmd += ["-m", model]
    cmd += [
        "--prompt-file", str(prompt_path),
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--trust",
        "--no-auto-update",
        "--rules", rules,
    ]
    return cmd


def main() -> int:
    """Fresh-process adapter so a catalog update works without daemon reload."""
    from .grok_status import valid_session_id
    from .runner import protonucleus_path

    env = dict(os.environ)
    model = (env.pop("GROK_ACTIVE_MODEL", "") or "").strip() or None
    resume = valid_session_id(env.pop("GROK_RESUME_SESSION", None))
    rules = protonucleus_path().read_text(encoding="utf-8").strip()
    root = Path(tempfile.mkdtemp(prefix="brnrd-grok-"))
    prompt_path = root / "prompt.txt"
    prompt_path.write_text(sys.stdin.read(), encoding="utf-8")
    try:
        result = subprocess.run(
            command(prompt_path, model=model, rules=rules, resume=resume),
            text=True, capture_output=True, env=env,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        return result.returncode if result.returncode > 0 else 128 - result.returncode
    _reply, error = final_reply(result.stdout)
    if error:
        print(error, file=sys.stderr)
        return 1
    # The runner reads the envelope. Printing the unwrapped reply here would
    # drop the session id, the model, and the token totals.
    sys.stdout.write(result.stdout if result.stdout.endswith("\n") else result.stdout + "\n")
    return 0
