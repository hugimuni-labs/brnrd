"""Runner abstraction and implementations.

This module defines a minimal interface for executors and provides
placeholders for concrete implementations.  A runner is responsible for
invoking an AI coding tool (Codex CLI, Claude Code, Gemini CLI or a custom
script) to perform a task or an analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def get_default_runner() -> Runner:
    """Return the default runner instance.

    Currently this always returns a CodexRunner.  In the future this
    function will inspect the repository's AGENTS.md header to select an
    executor.
    """
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

    This stub constructs a prompt from `prompts/init_adopt.md` and passes it
    to the runner.  It returns the parsed JSON structure or None if
    unimplemented.
    """
    try:
        prompt_path = __import__("importlib.resources").files(__package__).joinpath("../prompts/init_adopt.md")
    except Exception:
        # Python <3.9 fallback
        from pkg_resources import resource_filename
        prompt_path = resource_filename(__package__, "../prompts/init_adopt.md")
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt = f.read()
    result = runner.run(prompt)
    # TODO: parse JSON from result
    return None