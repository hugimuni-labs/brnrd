"""Status — show project state at a glance."""

from __future__ import annotations

from pathlib import Path


def get_status() -> str:
    from . import gitops
    from . import config as conf
    from . import daemon as daemon_mod

    try:
        repo_root = gitops.ensure_git_repo()
    except (RuntimeError, SystemExit):
        return "[brr] not in a git repo"

    brr_dir = repo_root / ".brr"
    lines = [f"repo: {repo_root}"]

    cfg = conf.load_config(repo_root)
    runner_name = cfg.get("runner", "auto")
    lines.append(f"runner: {runner_name}")

    pid = daemon_mod.read_pid(brr_dir)
    lines.append(f"daemon: {'running (pid ' + str(pid) + ')' if pid else 'stopped'}")

    agents = repo_root / "AGENTS.md"
    lines.append(f"AGENTS.md: {'yes' if agents.exists() else 'missing'}")

    kb_dir = repo_root / "kb"
    if kb_dir.exists():
        pages = [f.name for f in kb_dir.iterdir() if f.suffix == ".md"]
        lines.append(f"kb/: {len(pages)} page(s)")
    else:
        lines.append("kb/: missing")

    log = repo_root / "kb" / "log.md"
    if log.exists():
        recent = _recent_log(log, 5)
        if recent:
            lines.append("")
            lines.append("recent activity:")
            lines.extend(f"  {r}" for r in recent)

    return "\n".join(lines)


def _recent_log(path: Path, n: int) -> list[str]:
    """Extract the last *n* log entry headings."""
    text = path.read_text(encoding="utf-8")
    entries = [
        line.strip()
        for line in text.splitlines()
        if line.startswith("## [")
    ]
    return entries[-n:]
