from datetime import date
import subprocess

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from brr import account, constitution
from brr.cli import main
from brr.enable import enable_project

from _helpers import init_git_repo


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def _registry(result):
    return tomllib.loads(result.registry_path.read_text(encoding="utf-8"))


def test_committed_seed_is_agent_ready_and_visible(tmp_path):
    repo = tmp_path / "project"
    init_git_repo(repo)
    household = tmp_path / "brnrd"

    result = enable_project(repo, household_path=household)

    agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert result.agents_md == "created"
    assert constitution.verify(agents).ok
    assert "@AGENTS.md" in (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert (repo / ".brnrd-enabled").read_text(encoding="utf-8") == (
        f"seeding=committed enabled={date.today().isoformat()}\n"
    )
    row = _registry(result)["projects"][result.label]
    assert row["path"] == str(repo.resolve())
    assert row["seeding"] == "committed"
    assert row["knowledge"] == "household"
    assert row["mcp"] == []
    assert row["lanes"] == []
    assert result.knowledge_path.joinpath("index.md").exists()
    assert result.knowledge_path.joinpath("log.md").read_text(
        encoding="utf-8"
    ) == "# Log\n"
    assert "account/repos.json" in result.registry_path.read_text(
        encoding="utf-8"
    )
    status = _git(repo, "status", "--porcelain")
    assert "AGENTS.md" in status
    assert "CLAUDE.md" in status
    assert ".brnrd-enabled" not in status


def test_borrowed_seed_leaves_project_git_empty(tmp_path):
    repo = tmp_path / "borrowed"
    init_git_repo(repo)

    result = enable_project(
        repo,
        borrowed=True,
        household_path=tmp_path / "brnrd",
    )

    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "ls-files") == ""
    exclude = _git(repo, "rev-parse", "--git-path", "info/exclude").strip()
    excluded = (repo / exclude).resolve().read_text(encoding="utf-8")
    assert {"AGENTS.md", "CLAUDE.md", ".brnrd-enabled"} <= set(
        excluded.splitlines()
    )
    row = _registry(result)["projects"][result.label]
    assert row["seeding"] == "excluded"


def test_existing_agents_is_untouched_and_bridge_is_written(tmp_path):
    repo = tmp_path / "existing"
    init_git_repo(repo)
    original = b"my exact project contract\n"
    (repo / "AGENTS.md").write_bytes(original)

    result = enable_project(repo, household_path=tmp_path / "brnrd")

    assert result.agents_md == "existing"
    assert (repo / "AGENTS.md").read_bytes() == original
    assert result.bridges == ("claude",)
    assert (repo / "CLAUDE.md").exists()


def test_borrowed_seed_does_not_repair_existing_bridge(tmp_path):
    repo = tmp_path / "borrowed-existing"
    init_git_repo(repo)
    (repo / "AGENTS.md").write_text("contract\n", encoding="utf-8")
    bridge = b"upstream-owned bridge\n"
    (repo / "CLAUDE.md").write_bytes(bridge)

    enable_project(
        repo,
        borrowed=True,
        household_path=tmp_path / "brnrd",
    )

    assert (repo / "CLAUDE.md").read_bytes() == bridge


def test_registry_upsert_preserves_grants_and_round_trips(tmp_path):
    repo = tmp_path / "project"
    init_git_repo(repo)
    first = enable_project(repo, household_path=tmp_path / "brnrd")
    text = first.registry_path.read_text(encoding="utf-8")
    first.registry_path.write_text(
        text.replace("mcp = []", 'mcp = ["github"]')
        .replace("lanes = []", 'lanes = ["branch"]'),
        encoding="utf-8",
    )

    second = enable_project(
        repo,
        borrowed=True,
        household_path=tmp_path / "brnrd",
    )
    registry = _registry(second)

    assert list(registry["projects"]) == [second.label]
    assert registry["projects"][second.label]["seeding"] == "excluded"
    assert registry["projects"][second.label]["mcp"] == ["github"]
    assert registry["projects"][second.label]["lanes"] == ["branch"]
    assert (repo / ".brnrd-enabled").read_text(encoding="utf-8").startswith(
        "seeding=excluded "
    )


def test_household_symlink_created_but_foreign_path_is_left_alone(tmp_path):
    first_repo = tmp_path / "first"
    init_git_repo(first_repo)
    link = tmp_path / "household"
    first = enable_project(first_repo, household_path=link)

    assert first.household_link == "created"
    assert link.is_symlink()
    assert link.resolve() == account.context_home_root(
        account.resolve_context(first_repo)
    ).resolve()

    second_repo = tmp_path / "second"
    init_git_repo(second_repo)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    second = enable_project(second_repo, household_path=foreign)

    assert second.household_link == "existing-different"
    assert foreign.is_dir()
    assert not foreign.is_symlink()


def test_repo_kb_wins_over_household_slot(tmp_path):
    repo = tmp_path / "portable"
    init_git_repo(repo)
    (repo / "kb").mkdir()

    result = enable_project(repo, household_path=tmp_path / "brnrd")

    assert result.knowledge == "repo-kb"
    assert _registry(result)["projects"][result.label]["knowledge"] == "repo-kb"
    household_slot = account.repo_knowledge_path(
        account.resolve_context(repo), result.label
    )
    assert not household_slot.exists()


def test_cli_enable_end_to_end(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "cli-project"
    init_git_repo(repo)
    monkeypatch.setattr(
        "brr.enable._default_household_path",
        lambda: tmp_path / "brnrd",
    )

    assert main(["enable", str(repo)]) is None

    output = capsys.readouterr().out
    assert "AGENTS.md created" in output
    assert "mode: committed" in output
    assert "registry:" in output
    assert (repo / "AGENTS.md").exists()
