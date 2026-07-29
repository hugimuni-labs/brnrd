"""Native, local-first project enablement.

``brnrd enable`` lays down only convention files that ordinary agent shells
already understand, then records the project in the account household. The
borrowed-repo path keeps every addition clone-local via git's info/exclude;
the consent ledger remains in the household rather than in the project.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib

from . import account, constitution, gitops


REGISTRY_HEADER = """\
# Human-readable consent ledger for projects enabled in this household.
# The daemon's mechanical project view remains in account/repos.json.
"""


@dataclass(frozen=True)
class EnableResult:
    repo_root: Path
    label: str
    agents_md: str
    bridges: tuple[str, ...]
    seeding: str
    registry_path: Path
    knowledge: str
    knowledge_path: Path
    household_link: str
    household_path: Path


def _toml_string(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _toml_array(values: object) -> str:
    if not isinstance(values, list):
        values = []
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _render_registry(projects: dict[str, dict[str, Any]]) -> str:
    """Render the deliberately small registry schema as TOML."""
    lines = [REGISTRY_HEADER.rstrip(), ""]
    order = (
        "path",
        "upstream",
        "tier",
        "seeding",
        "knowledge",
        "enabled",
        "mcp",
        "lanes",
    )
    for project_label in sorted(projects):
        row = projects[project_label]
        lines.append(f"[projects.{_toml_string(project_label)}]")
        keys = [key for key in order if key in row]
        keys.extend(sorted(set(row) - set(order)))
        for key in keys:
            value = row[key]
            rendered = (
                _toml_array(value)
                if isinstance(value, list)
                else _toml_string(value)
            )
            lines.append(f"{key} = {rendered}")
        lines.append("")
    return "\n".join(lines)


def _load_projects(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid consent registry: {path}") from exc
    projects = raw.get("projects")
    if not isinstance(projects, dict):
        return {}
    return {
        str(project_label): dict(row)
        for project_label, row in projects.items()
        if isinstance(row, dict)
    }


def _write_registry(
    path: Path,
    *,
    project_label: str,
    repo_root: Path,
    upstream: str | None,
    seeding: str,
    knowledge: str,
    enabled: str,
) -> None:
    projects = _load_projects(path)
    previous = projects.get(project_label, {})
    row: dict[str, Any] = {
        "path": str(repo_root),
        "tier": previous.get("tier", "local"),
        "seeding": seeding,
        "knowledge": knowledge,
        "enabled": enabled,
        # Re-enabling is never permission escalation. Future explicit grant
        # flags may change these; this verb only preserves or initializes.
        "mcp": previous.get("mcp", []),
        "lanes": previous.get("lanes", []),
    }
    if upstream:
        row["upstream"] = upstream
    projects[project_label] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_render_registry(projects), encoding="utf-8")
    tmp.replace(path)


def _household_link(path: Path, home_root: Path) -> str:
    if path.exists() or path.is_symlink():
        if path.is_symlink():
            try:
                if path.resolve() == home_root.resolve():
                    return "existing"
            except OSError:
                pass
        return "existing-different"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(home_root, target_is_directory=True)
    return "created"


def _default_household_path() -> Path:
    return Path.home() / "brnrd"


def enable_project(
    repo_root: Path,
    *,
    borrowed: bool = False,
    label: str | None = None,
    shells: list[str] | None = None,
    household_path: Path | None = None,
) -> EnableResult:
    """Enable one git project and register its local consent grant."""
    repo_root = repo_root.expanduser().resolve()
    shells = list(shells) if shells is not None else ["claude"]
    enabled = date.today().isoformat()
    seeding = "excluded" if borrowed else "committed"

    agents_path = repo_root / constitution.CONTRACT_FILE
    if agents_path.exists() or agents_path.is_symlink():
        agents_md = "existing"
    else:
        template = constitution.TEMPLATE_PATH.read_text(encoding="utf-8")
        verification = constitution.verify_template()
        if not verification.ok:
            raise ValueError(
                f"cannot seed {constitution.CONTRACT_FILE}: invalid template"
            )
        agents_path.write_text(constitution.stamp(template), encoding="utf-8")
        agents_md = "created"

    bridge_shells = shells
    if borrowed:
        # A borrowed checkout admits additions only. write_bridges can repair a
        # hand-authored file, so pass it only shells whose bridge is absent.
        bridge_shells = []
        for shell in shells:
            bridge = constitution.bridge_filename(shell)
            if bridge is None:
                bridge_shells.append(shell)
                continue
            target = repo_root / bridge
            if not target.exists() and not target.is_symlink():
                bridge_shells.append(shell)
    bridge_names = tuple(constitution.write_bridges(repo_root, bridge_shells))

    created_paths: list[Path] = []
    if agents_md == "created":
        created_paths.append(agents_path)
    for shell in bridge_names:
        bridge = constitution.bridge_filename(shell)
        if bridge is not None:
            created_paths.append(repo_root / bridge)

    marker = repo_root / ".brnrd-enabled"
    marker_owned = False
    if marker.is_file() and not marker.is_symlink():
        marker_owned = bool(re.fullmatch(
            r"seeding=(?:committed|excluded) enabled=\d{4}-\d{2}-\d{2}\n?",
            marker.read_text(encoding="utf-8"),
        ))
    if marker_owned or (not marker.exists() and not marker.is_symlink()):
        marker.write_text(
            f"seeding={seeding} enabled={enabled}\n",
            encoding="utf-8",
        )
    gitops.exclude_from_git(repo_root, marker.name)
    if borrowed:
        for path in created_paths:
            gitops.exclude_from_git(
                repo_root,
                path.relative_to(repo_root).as_posix(),
            )

    ctx = account.resolve_context(repo_root)
    project_label = label or account.repo_label(repo_root)
    account.register_repo(
        ctx,
        repo_root,
        label=project_label,
        make_default=False,
    )

    if (repo_root / "kb").is_dir():
        knowledge = "repo-kb"
        knowledge_path = repo_root / "kb"
    else:
        knowledge = "household"
        knowledge_path = account.repo_knowledge_path(ctx, project_label)
        if not knowledge_path.exists():
            knowledge_path.mkdir(parents=True)
            (knowledge_path / "index.md").write_text(
                f"# {project_label}\n\n"
                "Map of this project's knowledge; see ../../AGENTS.md for "
                "the rules.\n\n"
                "## Subjects\n",
                encoding="utf-8",
            )
            (knowledge_path / "log.md").write_text(
                "# Log\n",
                encoding="utf-8",
            )

    upstream = gitops.remote_url(repo_root, "origin")
    registry_path = account.context_home_root(ctx) / "account" / "registry.toml"
    _write_registry(
        registry_path,
        project_label=project_label,
        repo_root=repo_root,
        upstream=upstream,
        seeding=seeding,
        knowledge=knowledge,
        enabled=enabled,
    )

    household_path = household_path or _default_household_path()
    link_state = _household_link(
        household_path,
        account.context_home_root(ctx),
    )
    return EnableResult(
        repo_root=repo_root,
        label=project_label,
        agents_md=agents_md,
        bridges=bridge_names,
        seeding=seeding,
        registry_path=registry_path,
        knowledge=knowledge,
        knowledge_path=knowledge_path,
        household_link=link_state,
        household_path=household_path,
    )
