"""Runner abstraction and implementations.

This module defines a minimal interface for executors and provides
placeholders for concrete implementations.  A runner is responsible for
invoking an AI coding tool (Codex CLI, Claude Code, Gemini CLI or a custom
script) to perform a task or an analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any


@dataclass
class Runner:
    """Base class for all runners."""
    name: str

    def run(self, prompt: str) -> str:
        """Run the given prompt and return a string result.

        Subclasses should override this method.  It may raise if the tool
        fails.  For now this is a stub.
        """
        raise NotImplementedError


class CodexRunner(Runner):
    def __init__(self) -> None:
        super().__init__(name="codex")

    def run(self, prompt: str) -> str:
        # Placeholder: call Codex CLI with the prompt.  For now, just echo
        print(f"[codex] would run prompt:\n{prompt}\n")
        return "{}"


class ShellRunner(Runner):
    def __init__(self, command: str) -> None:
        super().__init__(name="shell")
        self.command = command

    def run(self, prompt: str) -> str:
        # Placeholder: call external command with prompt via stdin
        print(f"[shell] would run {self.command} with prompt:\n{prompt}\n")
        return "{}"


import shutil


# Executors to try, in order of preference.
_AUTO_DETECT_ORDER = ["claude", "codex", "gemini"]


def get_default_runner() -> Runner:
    """Return the default runner instance.

    When the executor is set to 'auto' (the default), brr probes for
    available CLI tools in order: claude, codex, gemini.  If none are
    found it falls back to CodexRunner as a stub.
    """
    for name in _AUTO_DETECT_ORDER:
        if shutil.which(name):
            return ShellRunner(command=name)
    return CodexRunner()


def run_task(instruction: str) -> None:
    """Run a user instruction via the default runner.

    This stub prints the instruction and returns.  A real implementation
    will prepare the prompt with current state and call the runner.
    """
    runner = get_default_runner()
    print(f"[brr] running task: {instruction}")
    # In a full implementation, we'd construct a prompt using AGENTS.md,
    # agent_state.md and the instruction, then call runner.run().
    _ = runner.run(instruction)


def run_adoption_prompt(runner: Runner) -> Optional[Any]:
    """Ask the runner to perform adoption analysis.

    Constructs a prompt from `prompts/init_adopt.md` and passes it to the
    runner.  Returns the parsed JSON structure or None on failure.
    """
    prompt_path = Path(__file__).resolve().parent.parent.parent / "prompts" / "init_adopt.md"
    if not prompt_path.exists():
        print(f"[brr] adoption prompt not found at {prompt_path}")
        return None
    prompt = prompt_path.read_text(encoding="utf-8")
    result = runner.run(prompt)
    # TODO: parse JSON from result
    return None