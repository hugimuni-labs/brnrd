"""A resident's standing root moves; the event's project stays put."""
import json
from pathlib import Path

import pytest

from brr import account, daemon, envs, gitops, prompts, runner, worker
from brr.worker.prepare import execution_context
from _helpers import commit_files, init_git_repo, make_event


def _place(tmp_path):
    repo = tmp_path / 'repo'
    init_git_repo(repo)
    commit_files(repo, {'AGENTS.md': 'Project contract\n', 'README.md': 'project\n'})
    cfg = {
        'home.path': str(tmp_path / 'home'), 'home.kind': 'account',
        'account.id': 'acct-seat', 'repo.label': 'test/project',
        'sync.fetch_before_run': False, 'sync.fast_forward_default': False,
        'boot.mount': False,
    }
    (repo / '.brr/inbox').mkdir(parents=True)
    ctx = account.resolve_context(repo, cfg)
    return repo, cfg, ctx


@pytest.mark.parametrize('enabled,strand,tier,dominion_exists,moves', [
    (False, False, 'owner', True, False),
    (True, False, 'owner', True, True),
    (True, True, 'owner', True, False),
    (True, False, 'owner', False, False),
    (True, False, 'collaborator', True, False),
    (True, False, 'untrusted', True, False),
])
def test_real_environment_root_selection(tmp_path, enabled, strand, tier, dominion_exists, moves):
    repo, cfg, home = _place(tmp_path)
    cfg['seat.spawn_at_home'] = enabled
    dominion = account.home_dominion_path(home)
    if dominion_exists:
        dominion.mkdir()
    event = make_event(repo, eid='evt-root', source='telegram')
    task = daemon.Run.from_event(event, cfg)
    task.meta.update(trust_tier=tier, strand=strand, repo_label='test/project')
    plan = daemon.branching.resolve_publish_plan(repo, event, cfg)
    ctx = envs.HostEnv().prepare(task, repo, cfg, branch_plan=plan,
                                response_path=repo / '.brr/response.md')
    selected = execution_context(ctx, task, cfg, home)
    assert selected.cwd == (dominion if moves else repo)
    assert selected.repo_root == repo
    assert selected.runtime_dir == repo / '.brr'
    if not moves:
        assert selected is ctx
        assert 'execution_root' not in task.meta


@pytest.mark.parametrize("environment", ["host", "worktree"])
def test_worker_invokes_at_home_and_keeps_place_contract(tmp_path, monkeypatch, environment):
    repo, cfg, home = _place(tmp_path)
    dominion = account.home_dominion_path(home)
    dominion.mkdir()
    cfg['seat.spawn_at_home'] = True
    monkeypatch.setattr(daemon.runner, 'resolve_runner_profile',
                        lambda root, _overrides=None: daemon.runner.runner_profile('codex', root))
    monkeypatch.setattr(daemon.runner, 'available_runner_catalog', lambda *a, **k: [])
    event = make_event(repo, eid='evt-seat', source='telegram', environment=environment)
    p = worker.prepare(event, repo, repo / '.brr/responses', cfg, 0, account_context=home)
    assert isinstance(p, worker.Prepared)
    assert p.execution_root == dominion
    assert p.place_root == repo
    assert p.place_label == 'test/project'
    assert p.run_root == (repo if environment == 'host' else Path(p.task.meta['worktree_path']))
    assert p.run_root != dominion
    assert p.lane.env['BRR_SHARED_DIR'] == str(repo / '.brr')
    dx = worker.dispatch(p, worker.Attempt(n=1, lane=p.lane))
    assert isinstance(dx, worker.Dispatched)
    assert f'Execution root: {dominion}' in dx.prompt
    assert 'Place: test/project → places/project/' in dx.prompt
    assert f'Place contract (read explicitly): {p.run_root / "AGENTS.md"}' in dx.prompt

    seen = []
    def invoke(name, invocation, **kwargs):
        seen.append(invocation)
        assert invocation.cwd == dominion
        assert invocation.repo_root == repo
        assert invocation.publishing_brr_dir == repo / '.brr'
        return runner.RunnerResult(invocation=invocation, runner_name=name,
                                   command=['test'], stdout='done', stderr='', returncode=0,
                                   trace_dir=None, artifacts=[])
    monkeypatch.setattr(runner, 'invoke_runner', invoke)
    monkeypatch.setattr(daemon, '_capture_dominion', lambda *a, **k: None)
    worker.stream(p, dx)
    assert len(seen) == 1
    assert gitops.current_branch(repo) == 'main'


def test_codex_explicit_orientation_when_standing_elsewhere(tmp_path):
    repo, cfg, home = _place(tmp_path)
    dominion = account.home_dominion_path(home)
    dominion.mkdir()
    _, score = prompts.build_daemon_prompt_with_score(
        'work', 'evt-score', str(repo / 'response.md'), repo,
        execution_root=dominion, repo_label='test/project', runner_shell='codex',
    )
    assert str(repo / 'AGENTS.md') in [str(row.path) for row in score.orientation_set]
    usual = prompts.build_boot_score(repo, runner_shell='codex')
    assert str(repo / 'AGENTS.md') not in [str(row.path) for row in usual.orientation_set]


def test_portal_separates_project_readers_from_session_readers(tmp_path, monkeypatch):
    repo, cfg, home = _place(tmp_path)
    dominion = account.home_dominion_path(home)
    dominion.mkdir()
    (repo / 'README.md').write_text('project changed\n')
    (dominion / 'notebook.md').write_text('household changed\n')
    task = daemon.Run(id='run-roots', event_id='evt-hud', body='', source='telegram')
    task.meta['repo_label'] = 'test/project'
    outbox = repo / '.brr/outbox/evt-hud'
    seen = {}

    def observe(module, name, position):
        original = getattr(module, name)
        def wrapped(*args, **kwargs):
            seen[name] = args[position]
            return original(*args, **kwargs)
        monkeypatch.setattr(module, name, wrapped)

    for name, position in [('_collect_levels', 2), ('_collect_allowance_facet', 2),
                           ('_record_boot_cost', 2), ('_record_context_window', 1)]:
        observe(daemon, name, position)
    observe(daemon, '_scm_facet', 0)
    observe(daemon.relics, 'collection_scope', 1)
    observe(daemon.relics, 'live_summary', 0)
    path = daemon._write_live_portal_state(
        outbox, repo / '.brr/inbox', 'evt-hud', task, phase='running',
        work_dir=dominion, place_root=repo, cfg=cfg, brr_dir=repo / '.brr',
        account_context=home, repo_label='test/project', refresh_levels=False,
    )
    assert path.exists()
    for name in ('_collect_levels', '_collect_allowance_facet', '_record_boot_cost', '_record_context_window'):
        assert seen[name] == dominion
    for name in ('_scm_facet', 'collection_scope', 'live_summary'):
        assert seen[name] == repo
    assert json.loads(path.read_text())['scm']['branch'] == 'main'


def test_event_cannot_supply_execution_or_place_roots(tmp_path):
    forged = {key: str(tmp_path) for key in (
        "execution_root", "place_root", "place_label", "place_work_root",
    )}
    task = daemon.Run.from_event({
        "id": "evt-forged", "source": "telegram", "trust_tier": "owner", **forged,
    }, {})
    assert not forged.keys() & task.meta.keys()


def test_claude_loads_place_hooks_when_cwd_is_home(tmp_path):
    from brr.worker.prepare import runner_runtime

    repo, cfg, home = _place(tmp_path)
    dominion = account.home_dominion_path(home)
    dominion.mkdir()
    cfg.update({'seat.spawn_at_home': True, 'hooks.gate_command': 'pytest'})
    event = make_event(repo, eid='evt-hooks', environment='host')
    task = daemon.Run.from_event(event, cfg)
    plan = daemon.branching.resolve_publish_plan(repo, event, cfg)
    ctx = envs.HostEnv().prepare(task, repo, cfg, branch_plan=plan,
                                response_path=repo / '.brr/response.md')
    ctx = execution_context(ctx, task, cfg, home)
    runtime = runner_runtime(
        runner.runner_profile('claude', repo), task=task, eid='evt-hooks', env_ctx=ctx,
        context_path=repo / '.brr/context.md', outbox_dir=repo / '.brr/outbox',
        brr_dir=repo / '.brr', cfg=cfg, run_root=repo, repo_root=repo,
        emit=lambda *args, **kwargs: None,
    )
    assert runtime.hooks_installed
    assert runtime.extra_args == ['--settings', str(repo / '.claude/settings.local.json')]
    assert runtime.env['BRR_REPO_DIR'] == str(repo)
    assert (repo / '.claude/settings.local.json').is_file()
    assert not (dominion / '.claude').exists()
