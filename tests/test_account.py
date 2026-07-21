import json
import subprocess

from brr import account

from _helpers import write_repo_scaffold


def test_resolve_context_creates_account_home_and_registry(tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    write_repo_scaffold(repo_a)
    write_repo_scaffold(repo_b)
    home = tmp_path / "account-home"

    ctx = account.resolve_context(
        repo_a,
        {
            "repo.label": "Gurio/a",
            "home.kind": "account",
            "home.path": str(home),
            "account.id": "acct-1",
            "account.repo.Gurio/b": str(repo_b),
            "account.default_repo": "Gurio/b",
        },
    )

    assert ctx.dominion_repo == home
    assert ctx.home_root == home
    assert ctx.kind == "account"
    assert ctx.account_id == "acct-1"
    assert ctx.default_repo.root == repo_b
    assert ctx.dispatch_inbox == home / "dispatch" / "inbox"
    assert ctx.responses_dir == home / "dispatch" / "responses"
    assert ctx.runs_dir == home / "runs"
    registry = json.loads((home / "account" / "repos.json").read_text())
    assert registry["home_kind"] == "account"
    assert registry["home_id"] == "acct-1"
    assert registry["default_repo"] == "Gurio/b"
    assert {item["label"] for item in registry["repos"]} == {"Gurio/a", "Gurio/b"}
    assert (home / ".gitignore").read_text(encoding="utf-8").splitlines() == [
        "/dispatch/inbox/",
        "/dispatch/responses/",
        "/knowledge/",
        "*.tmp",
    ]


def test_gitignore_backfills_missing_rules_on_a_pre_existing_home(tmp_path):
    """A home created before a rule existed shouldn't carry a stale .gitignore.

    ``resolve_context`` only calls ``_write_gitignore`` at first creation —
    the write itself has to be an append-what's-missing, not a one-shot
    "skip if the file exists", or every home created before ``/knowledge/``
    was added would keep a nested, un-ignored knowledge git repo forever.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "account-home"
    home.mkdir(parents=True)
    (home / ".gitignore").write_text(
        "/dispatch/inbox/\n/dispatch/responses/\n*.tmp\n", encoding="utf-8"
    )

    account.resolve_context(
        repo,
        {
            "home.kind": "account",
            "home.path": str(home),
            "account.id": "acct-1",
        },
    )

    lines = (home / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/knowledge/" in lines
    assert lines.count("/knowledge/") == 1


def test_resolve_context_migrates_legacy_authored_roots_into_surface(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "account-home"
    (home / "plans" / "Gurio__brr").mkdir(parents=True)
    (home / "plans" / "Gurio__brr" / "active.md").write_text("plan", encoding="utf-8")
    (home / "ledger").mkdir()
    (home / "ledger" / "decisions.md").write_text("decision", encoding="utf-8")
    (home / "workflow.md").write_text("workflow", encoding="utf-8")

    account.resolve_context(repo, {"home.path": str(home), "repo.label": "Gurio/brr"})

    assert (home / "surface" / "plans" / "Gurio__brr" / "active.md").read_text() == "plan"
    assert (home / "surface" / "ledger" / "decisions.md").read_text() == "decision"
    assert (home / "surface" / "workflow.md").read_text() == "workflow"
    assert not (home / "plans").exists()
    assert not (home / "ledger").exists()
    assert not (home / "workflow.md").exists()


def test_migration_deletes_legacy_directory_skeletons(tmp_path):
    """A pre-surface daemon re-creates plans/ as empty dirs — not history.

    Regression for the 2026-07-18 `brnrd up` brick: the wake migrated the
    live home while the old daemon still ran; the old ``resolve_context``
    mkdir'd ``plans/`` again, and the migration then refused to boot over a
    file-less husk.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "account-home"
    (home / "surface" / "plans" / "Gurio__brr").mkdir(parents=True)
    (home / "surface" / "plans" / "Gurio__brr" / "active.md").write_text("plan", encoding="utf-8")
    (home / "plans" / "Gurio__brr").mkdir(parents=True)  # husk, no files
    (home / "ledger").mkdir()  # husk, no files

    account.resolve_context(repo, {"home.path": str(home), "repo.label": "Gurio/brr"})

    assert not (home / "plans").exists()
    assert not (home / "ledger").exists()
    assert (home / "surface" / "plans" / "Gurio__brr" / "active.md").read_text() == "plan"


def test_migration_collision_with_content_warns_but_boots(tmp_path, capsys):
    """Real content on both sides must never brick the daemon boot."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "account-home"
    (home / "surface" / "plans" / "Gurio__brr").mkdir(parents=True)
    (home / "surface" / "plans" / "Gurio__brr" / "active.md").write_text("new", encoding="utf-8")
    (home / "plans" / "Gurio__brr").mkdir(parents=True)
    (home / "plans" / "Gurio__brr" / "active.md").write_text("old", encoding="utf-8")

    ctx = account.resolve_context(repo, {"home.path": str(home), "repo.label": "Gurio/brr"})

    assert ctx.enabled
    assert (home / "plans" / "Gurio__brr" / "active.md").read_text() == "old"
    assert (home / "surface" / "plans" / "Gurio__brr" / "active.md").read_text() == "new"
    assert "work-surface migration" in capsys.readouterr().out


def test_migration_removes_identical_legacy_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "account-home"
    (home / "surface").mkdir(parents=True)
    (home / "surface" / "workflow.md").write_text("same", encoding="utf-8")
    (home / "workflow.md").write_text("same", encoding="utf-8")

    account.resolve_context(repo, {"home.path": str(home), "repo.label": "Gurio/brr"})

    assert not (home / "workflow.md").exists()
    assert (home / "surface" / "workflow.md").read_text() == "same"


def test_work_surface_files_ignores_symlinked_directories(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "account-home"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("leak", encoding="utf-8")

    ctx = account.resolve_context(repo, {"home.path": str(home), "repo.label": "Gurio/brr"})
    surface = account.work_surface_path(ctx)
    (surface / "evil").symlink_to(outside, target_is_directory=True)

    names = [p.name for p in account.work_surface_files(ctx)]
    assert "secret.md" not in names
    assert "index.md" in names


def test_corpus_files_joins_surface_knowledge_and_runs(tmp_path):
    """One layered corpus: authored surface, knowledge, then whole run nodes.

    The runs layer reads state, body, and messages from the private side of the
    ACL (home repo, not shared knowledge repo).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "account-home"
    ctx = account.resolve_context(repo, {"home.path": str(home), "repo.label": "Gurio/brr"})

    surface = account.work_surface_path(ctx)
    (surface / "index.md").write_text("# Work surface", encoding="utf-8")
    (surface / "workflow.md").write_text("gating", encoding="utf-8")

    knowledge_root = account.knowledge_path(ctx)
    kb = knowledge_root / account.REPOS_PATH / "Gurio__brr"
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "design.md").write_text("# Design", encoding="utf-8")

    # Run messages now live in home/runs/<slug>/<run-id>/messages/
    from brr import message_store as ms
    ms_dir = ms.run_messages_dir(ctx, "Gurio/brr", "run-x")
    ms_dir.mkdir(parents=True, exist_ok=True)
    (ms_dir.parent / "state.md").write_text("# State", encoding="utf-8")
    (ms_dir.parent / "body.md").write_text("## Now\n\nDone", encoding="utf-8")
    (ms_dir / "000001-terminal.md").write_text(
        "---\nstatus: delivered\n---\n\nreply body\n", encoding="utf-8",
    )

    # Hardening: a symlinked directory in the knowledge layer must not leak.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("leak", encoding="utf-8")
    (kb / "evil").symlink_to(outside, target_is_directory=True)

    files = account.corpus_files(ctx)
    layers = [f.layer for f in files]
    assert layers == sorted(layers, key=["authored", "knowledge", "runs"].index)  # grouped in order
    paths = [f.path for f in files]
    assert paths[0] == "surface/index.md"  # index leads its layer
    assert "surface/workflow.md" in paths
    assert "knowledge/repos/Gurio__brr/design.md" in paths
    # The run's message appears under runs/ (the new home-relative location).
    assert any(p.startswith("runs/") and "run-x" in p for p in paths)
    assert "runs/Gurio__brr/run-x/state.md" in paths
    assert "runs/Gurio__brr/run-x/body.md" in paths
    assert not any("secret" in p for p in paths)
    # Each layer appears contiguously in reading order.
    assert [f.layer for f in files if f.path.startswith("knowledge/repos/")] == ["knowledge"]


def test_default_home_is_repo_derived_project_home(monkeypatch, tmp_path):
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)

    ctx = account.resolve_context(repo)

    assert ctx.kind == "project"
    assert ctx.account_id == ""
    assert ctx.dominion_repo.parent.parent == state_home / "brnrd" / "projects"
    assert ctx.dominion_repo.name == "home"
    assert ctx.dominion_repo.parent.name.startswith("repo-")


def test_project_home_uses_path_hash_for_same_basename(monkeypatch, tmp_path):
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    repo_a = tmp_path / "a" / "repo"
    repo_b = tmp_path / "b" / "repo"
    repo_a.mkdir(parents=True)
    repo_b.mkdir(parents=True)
    write_repo_scaffold(repo_a)
    write_repo_scaffold(repo_b)

    ctx_a = account.resolve_context(repo_a)
    ctx_b = account.resolve_context(repo_b)

    assert ctx_a.dominion_repo != ctx_b.dominion_repo
    assert ctx_a.dominion_repo.parent.name.startswith("repo-")
    assert ctx_b.dominion_repo.parent.name.startswith("repo-")


def test_repo_dominion_path_is_repo_tagged(tmp_path):
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        runs_dir=tmp_path / "home" / "runs",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.repo_dominion_path(ctx, "Gurio/brr") == (
        tmp_path / "home" / "repos" / "Gurio__brr" / "dominion"
    )


def test_event_repo_label_accepts_repo_label_metadata():
    assert account.event_repo_label({"repo_label": "Gurio/brr"}) == "Gurio/brr"


def test_cloud_gate_state_migrates_to_account_home(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    legacy = repo / ".brr" / "gates" / "cloud.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "account_id": "acct-1",
                "repo_id": "repo-old",
                "token": "secret",
                "brnrd_url": "https://brnrd.example",
            }
        ),
        encoding="utf-8",
    )
    home = tmp_path / "account-home"

    ctx = account.resolve_context(
        repo,
        {
            "home.kind": "account",
            "home.path": str(home),
            "account.id": "acct-1",
        },
    )

    destination = account.cloud_gate_state_path(ctx)
    assert json.loads(destination.read_text(encoding="utf-8"))["token"] == "secret"
    assert not legacy.exists()


def test_run_state_blob_url_none_for_local_only_dominion(tmp_path):
    """A purely-local account dominion (no remote) yields no web URL, so callers
    fall back to a non-path label rather than leaking a host path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    ctx = account.resolve_context(
        repo, {"home.path": str(tmp_path / "home")},
    )
    doc = ctx.runs_dir / "local" / "run" / "state.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("x", encoding="utf-8")
    assert account.run_state_blob_url(ctx, doc) is None


def test_run_state_blob_url_projects_to_forge_remote(tmp_path):
    """Once the dominion tracks a forge-hosted remote, a run state doc gets a
    stable blob URL derived from the remote and the doc's repo-relative path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "home"
    ctx = account.resolve_context(repo, {"home.path": str(home)})
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:Gurio/account.git"],
        cwd=home, check=True,
    )
    doc = ctx.runs_dir / "Gurio__brr" / "run-260630" / "state.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("x", encoding="utf-8")
    url = account.run_state_blob_url(ctx, doc)
    assert url == (
        "https://github.com/Gurio/account/blob/main/"
        "runs/Gurio__brr/run-260630/state.md"
    )


def test_resolve_context_migrates_run_state_into_the_run_folder(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "home"
    legacy = home / "run-state" / "Gurio__brr" / "run-old.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("---\nstatus: done\n---\n", encoding="utf-8")

    ctx = account.resolve_context(
        repo, {"home.path": str(home), "repo.label": "Gurio/brr"},
    )

    state = account.run_dir(ctx, "Gurio/brr", "run-old") / "state.md"
    assert state.read_text(encoding="utf-8") == "---\nstatus: done\n---\n"
    assert not (home / "run-state").exists()


# ── CS5 — inter-run plan home ─────────────────────────────────────────


def test_resolve_context_creates_and_seeds_work_surface(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "home"

    account.resolve_context(repo, {"home.path": str(home)})

    assert (home / "surface").is_dir()
    index = (home / "surface" / "index.md").read_text(encoding="utf-8")
    assert "# Work surface" in index
    assert "plans/repo/active.md" in index


def test_active_plan_path_is_repo_tagged(tmp_path):
    """CS5: the active plan path is tagged by repo label inside plans/."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        runs_dir=tmp_path / "home" / "runs",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.active_plan_path(ctx, "Gurio/brr") == (
        tmp_path / "home" / "surface" / "plans" / "Gurio__brr" / "active.md"
    )


def test_cross_repo_plans_path(tmp_path):
    """CS5: cross-repo plans live under plans/_cross-repo/."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        runs_dir=tmp_path / "home" / "runs",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.cross_repo_plans_path(ctx) == (
        tmp_path / "home" / "surface" / "plans" / "_cross-repo"
    )


# ── CS6 — runner policy home ──────────────────────────────────────────


def test_runner_policy_path_is_repo_tagged(tmp_path):
    """CS6: the runner policy path is tagged by repo label."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        runs_dir=tmp_path / "home" / "runs",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.runner_policy_path(ctx, "Gurio/brr") == (
        tmp_path / "home" / "runner-policy" / "Gurio__brr" / "policy.md"
    )


def test_account_runner_policy_path(tmp_path):
    """CS6: the account-wide runner policy lives under runner-policy/_account/."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        runs_dir=tmp_path / "home" / "runs",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.account_runner_policy_path(ctx) == (
        tmp_path / "home" / "runner-policy" / "_account" / "policy.md"
    )


def test_runner_policy_proposals_path(tmp_path):
    """CS6b: pending policy proposals live under runner-policy/_proposals/."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        runs_dir=tmp_path / "home" / "runs",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.runner_policy_proposals_path(ctx) == (
        tmp_path / "home" / "runner-policy" / "_proposals"
    )


# ── CS7 — decision ledger home ────────────────────────────────────────


def test_decisions_ledger_path(tmp_path):
    """CS7: the decision ledger lives at ledger/decisions.md."""
    ctx = account.AccountContext(
        account_id="default",
        dominion_repo=tmp_path / "home",
        dispatch_inbox=tmp_path / "home" / "dispatch" / "inbox",
        responses_dir=tmp_path / "home" / "dispatch" / "responses",
        runs_dir=tmp_path / "home" / "runs",
        repos={},
        default_repo=account.AccountRepo(label="Gurio/brr", root=tmp_path),
    )

    assert account.decisions_ledger_path(ctx) == (
        tmp_path / "home" / "surface" / "ledger" / "decisions.md"
    )


# ── Repo relabel ─────────────────────────────────────────────────────


def _relabel_home(tmp_path, label="Gurio/brr"):
    """An account home with every slug-keyed scope populated for *label*."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    home = tmp_path / "account-home"
    ctx = account.resolve_context(
        repo,
        {
            "repo.label": label,
            "home.kind": "account",
            "home.path": str(home),
            "account.id": "acct-1",
        },
    )
    for scope, path, _home in account.relabel_scopes(ctx, label):
        path.mkdir(parents=True, exist_ok=True)
        (path / "witness.md").write_text(scope, encoding="utf-8")
    return ctx, repo


def test_relabel_scopes_covers_every_slug_keyed_path(tmp_path):
    """The scope list is the contract: a slug-keyed dir missing from it is a
    scope that silently fails to migrate. Pin the set so adding one elsewhere
    without adding it here trips a test rather than eating a resident's memory.
    """
    ctx, _repo = _relabel_home(tmp_path)
    scopes = {scope for scope, _path, _home in account.relabel_scopes(ctx, "Gurio/brr")}
    assert scopes == {
        "dominion", "surface-plans", "runner-policy", "runs", "knowledge", "replies",
    }


def test_relabel_moves_every_scope_and_rekeys_the_registry(tmp_path):
    ctx, _repo = _relabel_home(tmp_path)

    moves = account.relabel_repo(ctx, "Gurio/brr", "hugimuni-labs/brnrd")

    assert len(moves) == 6
    for scope, path, _home in account.relabel_scopes(ctx, "Gurio/brr"):
        assert not path.exists(), f"{scope} left behind at the old slug"
    for scope, path, _home in account.relabel_scopes(ctx, "hugimuni-labs/brnrd"):
        assert (path / "witness.md").read_text(encoding="utf-8") == scope

    registry = json.loads(
        (account.context_home_root(ctx) / account.REGISTRY_PATH).read_text()
    )
    labels = [entry["label"] for entry in registry["repos"]]
    assert labels == ["hugimuni-labs/brnrd"]
    assert registry["default_repo"] == "hugimuni-labs/brnrd"
    index = (account.work_surface_path(ctx) / "index.md").read_text(encoding="utf-8")
    assert "plans/hugimuni-labs__brnrd/active.md" in index
    assert "plans/Gurio__brr/active.md" not in index


def test_relabel_dry_run_touches_nothing(tmp_path):
    ctx, _repo = _relabel_home(tmp_path)

    moves = account.relabel_repo(
        ctx, "Gurio/brr", "hugimuni-labs/brnrd", dry_run=True
    )

    assert len(moves) == 6
    for _scope, path, _home in account.relabel_scopes(ctx, "Gurio/brr"):
        assert path.exists(), "dry run moved something"
    for _scope, path, _home in account.relabel_scopes(ctx, "hugimuni-labs/brnrd"):
        assert not path.exists()


def test_relabel_preserves_a_non_default_repos_registry_entry(tmp_path):
    """Relabelling repo B must not steal the default from repo A."""
    ctx, _repo = _relabel_home(tmp_path, label="Gurio/brr")
    registry_path = account.context_home_root(ctx) / account.REGISTRY_PATH
    account._write_registry(
        registry_path,
        {
            "Gurio/brr": account.AccountRepo(label="Gurio/brr", root=tmp_path / "repo"),
            "other/keeper": account.AccountRepo(
                label="other/keeper", root=tmp_path / "keeper"
            ),
        },
        "other/keeper",
        account_id="acct-1",
        home_kind="account",
        home_id="acct-1",
    )

    account.relabel_repo(ctx, "Gurio/brr", "hugimuni-labs/brnrd")

    registry = json.loads(registry_path.read_text())
    labels = sorted(entry["label"] for entry in registry["repos"])
    assert labels == ["hugimuni-labs/brnrd", "other/keeper"]
    assert registry["default_repo"] == "other/keeper"


def test_relabel_refuses_to_merge_into_a_populated_destination(tmp_path):
    """Two histories under one slug is data loss wearing a migration's coat."""
    ctx, _repo = _relabel_home(tmp_path)
    occupied = account.repo_knowledge_path(ctx, "hugimuni-labs/brnrd")
    occupied.mkdir(parents=True, exist_ok=True)
    (occupied / "someone-elses.md").write_text("prior", encoding="utf-8")

    try:
        account.relabel_repo(ctx, "Gurio/brr", "hugimuni-labs/brnrd")
    except account.RelabelError as exc:
        assert "not empty" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RelabelError")

    # And it refused *before* moving anything — no half-migration.
    for _scope, path, _home in account.relabel_scopes(ctx, "Gurio/brr"):
        assert path.exists()


def test_relabel_rejects_a_no_op_and_a_slug_collision(tmp_path):
    ctx, _repo = _relabel_home(tmp_path)

    for old, new, expected in (
        ("Gurio/brr", "Gurio/brr", "already the label"),
        ("", "x/y", "required"),
        # "Gurio/brr" and "Gurio__brr" both slug to "Gurio__brr" — distinct
        # labels, one directory. Moving would mean moving onto itself.
        ("Gurio/brr", "Gurio__brr", "slug to"),
    ):
        try:
            account.plan_relabel(ctx, old, new)
        except account.RelabelError as exc:
            assert expected in str(exc), f"{old!r}->{new!r}: {exc}"
        else:  # pragma: no cover
            raise AssertionError(f"expected RelabelError for {old!r} -> {new!r}")


def test_relabel_skips_scopes_that_do_not_exist(tmp_path):
    """A home that never grew a runner policy still relabels cleanly."""
    ctx, _repo = _relabel_home(tmp_path)
    import shutil

    shutil.rmtree(account.runner_policy_path(ctx, "Gurio/brr").parent)

    moves = account.relabel_repo(ctx, "Gurio/brr", "hugimuni-labs/brnrd")

    assert {move.scope for move in moves} == {
        "dominion", "surface-plans", "runs", "knowledge", "replies",
    }


# ── Detecting a repo that moved without a relabel ─────────────────────


def test_detect_relabelled_repo_fires_when_memory_is_stranded(tmp_path):
    """The move happened, the migration didn't: memory sits under the old label."""
    ctx, repo = _relabel_home(tmp_path, label="Gurio/brr")

    # The remote now derives a new label; the registry still knows the old one.
    # NB: resolve_context auto-registers the current repo, so after a real move
    # *both* labels point at this root — that is the state to detect, not an
    # obstacle to it.
    assert account.detect_relabelled_repo(ctx, repo, "hugimuni-labs/brnrd") == "Gurio/brr"


def test_detect_relabelled_repo_silent_once_the_relabel_has_run(tmp_path):
    ctx, repo = _relabel_home(tmp_path, label="Gurio/brr")

    account.relabel_repo(ctx, "Gurio/brr", "hugimuni-labs/brnrd")

    assert account.detect_relabelled_repo(ctx, repo, "hugimuni-labs/brnrd") is None


def test_detect_relabelled_repo_silent_on_a_healthy_repo(tmp_path):
    ctx, repo = _relabel_home(tmp_path, label="Gurio/brr")

    assert account.detect_relabelled_repo(ctx, repo, "Gurio/brr") is None


def test_detect_relabelled_repo_ignores_a_sibling_repo_under_the_same_home(tmp_path):
    """An account home hosts many repos, each with its own memory. Another
    label having memory is the normal case — only the *same tree* under another
    label means a move. Without the root check this would false-positive on
    every multi-repo home, which is the shape that makes a warning ignorable."""
    ctx, repo = _relabel_home(tmp_path, label="Gurio/brr")
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    repos = dict(ctx.repos)
    repos["other/project"] = account.AccountRepo(label="other/project", root=sibling)
    ctx = account.HomeContext(
        account_id=ctx.account_id,
        dominion_repo=ctx.dominion_repo,
        dispatch_inbox=ctx.dispatch_inbox,
        responses_dir=ctx.responses_dir,
        runs_dir=ctx.runs_dir,
        repos=repos,
        default_repo=ctx.default_repo,
        kind=ctx.kind,
        home_id=ctx.home_id,
        home_root=ctx.home_root,
    )
    for _scope, path, _home in account.relabel_scopes(ctx, "other/project"):
        path.mkdir(parents=True, exist_ok=True)
        (path / "witness.md").write_text("x", encoding="utf-8")

    # The sibling has memory of its own, at a different root. That must not
    # make the (healthy) main repo look like it moved.
    assert account.detect_relabelled_repo(ctx, repo, "Gurio/brr") is None
    # Nor may it strand a genuinely fresh repo at a third root.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert account.detect_relabelled_repo(ctx, fresh, "other/fresh") is None


def test_detect_relabelled_repo_silent_when_the_old_label_holds_nothing(tmp_path):
    """A stale registry entry over an empty home is bookkeeping, not a loss."""
    ctx, repo = _relabel_home(tmp_path, label="Gurio/brr")
    import shutil

    for _scope, path, _home in account.relabel_scopes(ctx, "Gurio/brr"):
        if path.is_dir():
            shutil.rmtree(path)

    assert account.detect_relabelled_repo(ctx, repo, "hugimuni-labs/brnrd") is None
