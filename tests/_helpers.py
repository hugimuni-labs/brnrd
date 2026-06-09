"""Shared test scaffolding.

Helpers in this module replace inline copies of the same setup that
were drifting across multiple test files. Each helper is a small,
explicit function — not a pytest fixture — so tests pass ``tmp_path``
to them rather than picking up implicit state.

See ``kb/research-test-suite-grooming-2026-05-16.md`` for the
motivation and the mapping of which helper subsumes which inline
copies.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable

from brr import envs
from brr.runner import RunnerResult


# ── git fixtures ────────────────────────────────────────────────────


def init_git_repo(repo: Path) -> None:
    """``git init -b main`` + configure a local identity.

    Subsequent commits are the caller's responsibility — many tests
    want either no commits, or a specific set of files in the seed.
    Use :func:`commit_files` for the common case.
    """
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo, check=True,
    )


def commit_files(
    repo: Path, files: dict[str, str], *, message: str = "init",
) -> str:
    """Write ``files`` into ``repo``, stage them, commit, return HEAD oid.

    ``files`` keys are relative paths from ``repo``; nested directories
    are created automatically.
    """
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, check=True, capture_output=True, text=True,
    )
    return head.stdout.strip()


# ── daemon-test scaffolding ─────────────────────────────────────────


def write_repo_scaffold(repo_root: Path) -> None:
    """Seed the minimal repo shape ``daemon._run_worker`` reads from.

    Creates ``AGENTS.md`` plus the ``.brr/inbox`` and ``.brr/responses``
    directories. Used by daemon-level tests that don't initialise a
    real git repo.
    """
    (repo_root / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    (repo_root / ".brr" / "inbox").mkdir(parents=True)
    (repo_root / ".brr" / "responses").mkdir(parents=True)


def make_event(
    repo_root: Path,
    *,
    eid: str,
    body: str = "raw event body",
    source: str = "telegram",
    **extra: Any,
) -> dict[str, Any]:
    """Write an inbox event file and return its in-memory dict.

    ``extra`` keys are merged into the returned dict but not into the
    on-disk frontmatter — tests that need them in the file should
    write the event themselves.
    """
    path = repo_root / ".brr" / "inbox" / f"{eid}.md"
    path.write_text(
        f"---\nid: {eid}\nstatus: pending\nsource: {source}\n---\n{body}\n",
        encoding="utf-8",
    )
    return {
        "id": eid,
        "status": "pending",
        "body": body,
        "source": source,
        "_path": path,
        **extra,
    }


InvokeFn = Callable[..., RunnerResult]


class StubWorktreeEnv:
    """Minimal env backend that the daemon worker can drive end-to-end.

    The factory parameter ``invoke_fn`` lets each test plug in the
    specific behaviour it cares about (write a response and succeed,
    fail on the first attempt, time out, etc.) without redefining the
    prepare / finalize plumbing every time.
    """

    name = "worktree"

    def __init__(self, *, invoke_fn: InvokeFn) -> None:
        self._invoke = invoke_fn

    def prepare(self, task, repo_root, cfg, *, branch_plan, response_path,
                outbox_path=None):
        return envs.RunContext(
            name=self.name,
            cwd=repo_root,
            repo_root=repo_root,
            runtime_dir=repo_root / ".brr",
            response_path_host=response_path,
            response_path_env=response_path,
            outbox_host=outbox_path,
            outbox_env=outbox_path,
            branch_name=f"brr/{task.id}",
            env_state={"worktree_path": str(repo_root)},
        )

    def invoke(self, ctx, runner_name, invocation, cfg=None, *, trace=False):
        return self._invoke(
            ctx, runner_name, invocation, cfg, trace=trace,
        )

    def finalize(self, _ctx, task, _tasks_dir):
        return task


def succeed_invoke(response: str = "all done\n") -> InvokeFn:
    """An ``invoke`` implementation that always writes ``response`` and
    returns a zero-exit ``RunnerResult``."""

    def _invoke(_ctx, runner_name, invocation, _cfg, *, trace=False):
        Path(invocation.response_path).parent.mkdir(
            parents=True, exist_ok=True,
        )
        Path(invocation.response_path).write_text(
            response, encoding="utf-8",
        )
        return RunnerResult(
            invocation=invocation, runner_name=runner_name,
            command=["mock"], stdout=response, stderr="",
            returncode=0, trace_dir=None, artifacts=[],
        )

    return _invoke


# ── brnrd backend scaffolding ───────────────────────────────────────


def brnrd_account_headers(
    app,
    *,
    github_id: str | None = None,
    login: str = "octocat",
    email: str | None = "octocat@example.com",
) -> dict[str, str]:
    """Seed a GitHub-backed brnrd account and return account auth headers."""
    from brnrd.oauth import GitHubIdentity
    from brnrd.routers.accounts import account_for_github_identity, issue_session_token

    if github_id is None:
        basis = email or login
        github_id = str(int(hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12], 16))

    with app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db,
            GitHubIdentity(github_id=github_id, login=login, email=email),
        )
        token = issue_session_token(db, account)
    return {"Authorization": f"Bearer {token}"}
