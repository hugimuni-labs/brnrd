"""The standalone home's contracts, including real subprocess hook/MCP framing."""

from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from brr import dominion, knowledge
from brr.self import checkpoint, encode, init_home, wake
from brr.self.cli import main
from brr.self.home import resolve_home
from brr.self.mcp import serve
from brr.self.memory import note, obligations
from brr.self.recall import index_path, recall


@pytest.fixture
def home(tmp_path):
    home = tmp_path / 'agent home'
    init_home(home, tmp_path / 'project')
    return home


def test_init_preserves_seeds_and_foreign_configuration(tmp_path):
    home, project = tmp_path / 'agent home', tmp_path / 'project'
    (project / '.claude').mkdir(parents=True)
    settings = {'permissions': {'allow': ['Read']}, 'hooks': {'Stop': [
        {'hooks': [{'type': 'command', 'command': 'existing-stop'}]}]}}
    (project / '.claude/settings.json').write_text(json.dumps(settings))
    (project / '.mcp.json').write_text(json.dumps({'mcpServers': {'other': {'command': 'other'}}}))
    (project / 'CLAUDE.md').write_text('# Existing instructions\n')
    created = init_home(home, project)
    assert {'identity.md', 'notebook.md', 'playbook.md', 'pitfalls.md', 'now.md', 'self-inject'} <= set(created)
    for directory in ('kb', 'obligations', 'body', 'bench', 'journal/checkpoints'):
        assert (home / directory).is_dir()
    (home / 'identity.md').write_text('Edited identity')
    (home / 'playbook.md').write_text('')
    first = (project / '.claude/settings.json').read_text()
    assert init_home(home, project) == []
    assert (home / 'identity.md').read_text() == 'Edited identity'
    assert (home / 'playbook.md').read_text() == ''
    assert (project / '.claude/settings.json').read_text() == first
    merged = json.loads(first)
    assert merged['permissions'] == settings['permissions']
    assert merged['hooks']['Stop'][0] == settings['hooks']['Stop'][0]
    assert len(merged['hooks']['Stop']) == 2
    imports = (project / 'CLAUDE.md').read_text()
    assert imports.startswith('# Existing instructions\n')
    assert imports == '# Existing instructions\n'
    assert 'other' in json.loads((project / '.mcp.json').read_text())['mcpServers']
    command = merged['hooks']['SessionStart'][0]['hooks'][0]['command']
    assert shlex.split(command)[-3:] == ['--home', str(home), '--hook']
    assert resolve_home(project=project) == home


@pytest.mark.parametrize('file,content', [
    ('.claude/settings.json', 'not json'),
    ('.claude/settings.json', '{"hooks": []}'),
    ('.claude/settings.json', '{"hooks": {"Stop": {}}}'),
    ('.mcp.json', '{"mcpServers": {"self": {"command": "unrelated"}}}'),
])
def test_init_conflicts_leave_home_and_configuration_alone(tmp_path, file, content):
    project, home = tmp_path / 'project', tmp_path / 'home'
    p = project / file
    p.parent.mkdir(parents=True)
    p.write_text(content)
    with pytest.raises(ValueError):
        init_home(home, project)
    assert not home.exists()
    assert p.read_text() == content


def test_init_refuses_second_home(tmp_path):
    project = tmp_path / 'project'
    init_home(tmp_path / 'one', project)
    with pytest.raises(ValueError, match='another self home'):
        init_home(tmp_path / 'two', project)
    assert not (tmp_path / 'two').exists()


def test_home_precedence(home, monkeypatch):
    project = home.parent / 'project'
    monkeypatch.setenv('BRNRD_SELF_HOME', str(home.parent / 'env'))
    assert resolve_home(project=project) == home.parent / 'env'
    assert resolve_home(home, project) == home


def test_wake_reuses_selector_and_names_omissions(home, monkeypatch):
    (home / 'identity.md').write_text('Identity stays visible')
    (home / 'notebook.md').write_text('# Large notebook\n\n' + 'long ' * 1000)
    (home / 'self-inject').write_text('full now.md\nfull notebook.md\nfull playbook.md\nfull missing.md\nexec nope\n')
    (home / 'pitfalls.md').write_text('## Deployment guard\ntrigger: deploy\nCheck rollback first.\n\n## Other\ntrigger: database\nDifferent lesson.')
    (home / 'kb/rollout.md').write_text('deploy rollback safely')
    calls = []
    original = dominion.resolve_self_inject
    def selected(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(dominion, 'resolve_self_inject', selected)
    text = wake(home, 'deploy', budget_bytes=2400)
    assert calls[0][0] == (home,)
    assert calls[0][1]['budget_bytes'] == 1600
    assert 'Identity stays visible' in text
    assert 'self-inject overflow' in text
    assert 'full playbook.md' in text
    assert 'full missing.md (missing' in text
    assert 'exec nope (missing' in text
    assert 'Deployment guard' in text and 'Check rollback first.' in text
    assert '1 trigger misses' in text
    assert 'Other (trigger did not match)' not in text
    assert 'kb/: 1 pages outside situational slice' in text
    assert 'kb/rollout.md:1: deploy rollback safely' in text
    assert 'brnrd agent inject' not in text
    assert (home / 'wake.md').read_text() == text


def test_tiny_wake_keeps_identity_and_pitfall_names(home):
    (home / 'pitfalls.md').write_text('## Important\ntrigger: deploy\nDo the check.')
    text = wake(home, 'deploy', budget_bytes=0)
    assert '# Identity' in text
    assert 'Important (pitfall budget)' in text
    assert 'self-inject budget exhausted' in text
    with pytest.raises(ValueError):
        wake(home, budget_bytes=-1)


def test_encode_redacts_and_does_not_save_payload(home):
    payload = {'tool_name': 'Bash', 'tool_input': {
        'command': 'TOKEN=not-for-journal curl example.com',
        'description': 'Check availability using api_key=private-value'},
        'session_id': 's1', 'tool_use_id': 't1',
        'tool_response': 'private raw response'}
    row = encode(home, payload)
    assert row['why'] == 'Check availability using api_key=<redacted>'
    assert row['act']['detail'] == 'TOKEN=<redacted> curl example.com'
    journal = next((home / 'journal').glob('*.jsonl')).read_text()
    assert 'private-value' not in journal and 'private raw response' not in journal
    assert 'not-for-journal' not in journal
    assert json.loads(journal) == row
    assert set(row) == {'at', 'act', 'why', 'session_id', 'tool_use_id'}


def test_encode_comment_reason_and_absent_reason(home):
    row = encode(home, {'tool_name': 'exec_command', 'tool_input': {'cmd': '# why: Verify the fix\npytest'}})
    assert row['why'] == 'Verify the fix'
    assert encode(home, {'tool_name': 'Read', 'tool_input': {'file_path': 'test.py'}})['why'] is None
    with pytest.raises(ValueError, match='tool_name'):
        encode(home, {'tool_input': {}})


def test_parallel_capture_rows_remain_parseable(home):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: encode(home, {'tool_name': 'Bash', 'tool_input': {
            'command': 'true', 'description': f'Check {i}'}}), range(24)))
    rows = [json.loads(line) for line in next((home / 'journal').glob('*.jsonl')).read_text().splitlines()]
    assert len(rows) == 24 and len({r['why'] for r in rows}) == 24


def test_checkpoint_retains_authored_advancement_and_obligations(home):
    orientation = '# Now\nGoal: build the thing\n- [x] tested\n- [ ] review\nFork: API choice\n'
    (home / 'now.md').write_text(orientation)
    (home / 'obligations/pr.md').write_text('state: open\nPublish review PR')
    path = checkpoint(home, {'hook_event_name': 'PreCompact', 'session_id': 's1'})
    data = json.loads(path.read_text())
    assert data['orientation'] == orientation
    assert data['obligations'] == [{'path': 'obligations/pr.md', 'text': 'state: open\nPublish review PR'}]
    assert data['event'] == 'PreCompact' and data['session_id'] == 's1'
    (home / 'now.md').write_text('Updated')
    assert data == json.loads(path.read_text())
    assert checkpoint(home) != path


def test_recall_scoped_to_home_and_exact_search(home, monkeypatch):
    (home / 'kb/decision.md').write_text('# Decision\nThe house is blue.\n')
    def forbidden(*args, **kwargs):
        pytest.fail('Standalone recall must not discover account sources')
    monkeypatch.setattr(knowledge, 'sources', forbidden)
    hits = recall(home, 'HOUSE')
    assert len(hits) == 1 and hits[0].line_no == 2
    assert hits[0].path == home / 'kb/decision.md'
    assert hits[0].source == 'self knowledge'
    assert knowledge.search(home, 'house', search_sources=[]) == []
    assert recall(home, '') == []


def test_fts_cache_external_refreshes_edits_and_deletions(home, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    page = home / 'kb/deploy.md'
    page.write_text('blue dependable deployment\n')
    hits = recall(home, 'deployment blue')
    assert len(hits) == 1 and hits[0].source == 'self knowledge (FTS5)'
    assert index_path(home).is_file()
    assert not index_path(home).is_relative_to(home)
    page.write_text('green dependable deployment\n')
    assert recall(home, 'deployment blue') == []
    assert recall(home, 'deployment green')[0].path == page
    page.unlink()
    assert recall(home, 'deployment green') == []
    monkeypatch.setenv('XDG_CACHE_HOME', str(home / 'cache'))
    with pytest.raises(ValueError, match='outside'):
        recall(home, 'green deployment')


def test_note_kinds_are_fixed_and_obligations_authored(home):
    note(home, 'obligation', '# Ship\nstate: open')
    assert obligations(home)[0]['text'] == '\n# Ship\nstate: open\n'
    with pytest.raises(ValueError):
        note(home, '../identity', 'replace')
    with pytest.raises(ValueError):
        note(home, 'notebook', '')


def test_cli_hook_outputs_and_error_handling(home, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"hook_event_name": "SessionStart"}'))
    assert main(['wake', '--home', str(home), '--hook']) == 0
    hook = json.loads(capsys.readouterr().out)['hookSpecificOutput']
    assert hook['hookEventName'] == 'SessionStart'
    assert hook['additionalContext'] == (home / 'wake.md').read_text()
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"tool_name":"Bash","tool_input":{"command":"true","description":"Prove capture"}}'))
    assert main(['encode', '--home', str(home), '--hook']) == 0
    assert capsys.readouterr().out == ''
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"hook_event_name": "Stop"}'))
    assert main(['checkpoint', '--home', str(home), '--hook']) == 0
    assert capsys.readouterr().out == ''
    monkeypatch.setattr(sys, 'stdin', io.StringIO('[]'))
    assert main(['encode', '--home', str(home)]) == 1
    assert 'JSON object' in capsys.readouterr().err


def rpc(ident, method, params=None):
    return {'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params or {}}


def test_mcp_lifecycle_tools_and_errors(home):
    messages = [rpc(1, 'tools/list'), rpc(2, 'initialize', {'protocolVersion': '2025-06-18'}),
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                rpc(3, 'tools/list'), rpc(4, 'tools/call', {'name': 'note', 'arguments': {
                    'kind': 'knowledge', 'text': 'The proof is a receipt.'}}),
                rpc(5, 'tools/call', {'name': 'recall', 'arguments': {'query': 'receipt'}}),
                rpc(6, 'tools/call', {'name': 'obligations'}),
                rpc(7, 'tools/call', {'name': 'note', 'arguments': {'kind': '../identity', 'text': 'bad'}}),
                rpc(8, 'tools/call', {'name': 'recall', 'arguments': {'query': []}}),
                rpc(9, 'unknown'), rpc(10, 'ping')]
    stream = io.StringIO('\n'.join(json.dumps(m) for m in messages) + '\n{\n[]\n')
    out = io.StringIO()
    serve(home, stream, out)
    replies = [json.loads(s) for s in out.getvalue().splitlines()]
    assert len(replies) == 12
    assert replies[0]['error']['code'] == -32000
    assert replies[1]['result']['protocolVersion'] == '2025-06-18'
    assert {t['name'] for t in replies[2]['result']['tools']} == {'recall', 'note', 'obligations', 'consolidate', 'proposals'}
    assert json.loads(replies[4]['result']['content'][0]['text'])[0]['path'] == 'kb/notes.md'
    assert replies[6]['result']['isError'] and replies[7]['result']['isError']
    assert replies[8]['error']['code'] == -32601
    assert replies[9]['result'] == {}
    assert replies[10]['error']['code'] == -32700
    assert replies[11]['error']['code'] == -32600


def test_mcp_real_subprocess_stdio(home):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    wire = '\n'.join(json.dumps(m) for m in [rpc(1, 'initialize'), rpc(2, 'tools/list'),
                        rpc(3, 'tools/call', {'name': 'obligations'})]) + '\n'
    result = subprocess.run([sys.executable, '-m', 'brr.self', 'mcp', '--home', str(home)],
                            input=wire, text=True, capture_output=True, env=env, timeout=15)
    assert result.returncode == 0 and result.stderr == ''
    replies = [json.loads(s) for s in result.stdout.splitlines()]
    assert [r['id'] for r in replies] == [1, 2, 3]
    assert replies[2]['result']['content'][0]['text'] == '[]'


def test_hook_caps_via_existing_selector_with_visible_omissions(home, monkeypatch, capsys):
    (home / 'notebook.md').write_text('# Notebook\n\n' + 'filler ' * 1335 + '\n\nLate fact\n')
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    assert main(['wake', '--home', str(home), '--hook']) == 0
    text = json.loads(capsys.readouterr().out)['hookSpecificOutput']['additionalContext']
    assert len(text) < 10000
    assert 'memory budget reduced from 16384 to 8192' in text
    assert 'notebook.md' in text and 'self-inject overflow' in text
    assert 'Use recall for the long tail.' in text
    assert text == (home / 'wake.md').read_text()


def test_hook_oversized_identity_points_to_full_file(home, monkeypatch, capsys):
    identity = '# Identity\n' + 'identity ' * 2000
    (home / 'identity.md').write_text(identity)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    assert main(['wake', '--home', str(home), '--hook']) == 0
    text = json.loads(capsys.readouterr().out)['hookSpecificOutput']['additionalContext']
    assert len(text) < 10000 and 'Read that file before acting' in text
    assert 'This pointer is not' in text
    assert identity in (home / 'wake.md').read_text()


def test_hook_ceiling_counts_astral_characters_as_two_units(home, monkeypatch, capsys):
    # JavaScript strings count astral glyphs as surrogate pairs. Python len
    # alone would admit this oversized floor even though the native hook
    # cannot receive it intact.
    (home / 'identity.md').write_text('# Identity\n' + '🌱' * 5000)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    assert main(['wake', '--home', str(home), '--hook']) == 0
    text = json.loads(capsys.readouterr().out)['hookSpecificOutput']['additionalContext']
    assert 'Read that file before acting' in text
    assert len(text.encode('utf-16-le')) // 2 < 10000
    assert '🌱' * 5000 in (home / 'wake.md').read_text()


def test_reinit_removes_only_its_generated_import(home):
    project = home.parent / 'project'
    marker = '<!-- brnrd self: generated wake, authored home -->'
    own = '@' + str(home / 'wake.md').replace(' ', r'\ ')
    other = '@other/wake.md'
    text = f'# User instructions\n{own}\n{marker}\n{other}\n{marker}\n{own}\nKeep this.\n'
    (project / 'CLAUDE.md').write_text(text)
    init_home(home, project)
    assert (project / 'CLAUDE.md').read_text() == f'# User instructions\n{own}\n{marker}\n{other}\nKeep this.\n'
    init_home(home, project)
    assert (project / 'CLAUDE.md').read_text().endswith('Keep this.\n')


def test_wake_omissions_scale_with_situation_not_home(home):
    for i in range(300):
        (home / 'kb' / f'page-{i}.md').write_text('unrelated information')
    (home / 'pitfalls.md').write_text('\n'.join(
        f'## Lesson {i}\ntrigger: deploy safely {i}\nCheck it.' for i in range(70)))
    text = wake(home, 'deploy safely', budget_bytes=0)
    outside = text.split('## Outside this wake')[1]
    assert 'kb/: 301 pages' in outside
    assert '70 trigger misses' in outside
    assert outside.count('pitfalls.md: Lesson') == 10
    assert 'Lesson 10 (' not in outside
    assert 'page-299.md' not in outside
    assert len(outside) < 1600


def limb(home, tmp_path, files=None, raw=None):
    """A real command checks stdin framing, without standing in for model quality."""
    script = tmp_path / 'limb.py'
    response = raw if raw is not None else json.dumps({
        'files': files or {'notebook.md': '# Notebook\n\nKeep calls with their reasons.\n'},
        'rationale': 'Keep supported calls inside the wake budget\nThe journal gives reasons, not inferred successes.'})
    script.write_text('import json, sys\n'
                      'value = sys.stdin.read()\n'
                      'assert "journal" in value and "standing_memory_budget_bytes" in value\n'
                      f'print({response!r})\n')
    (home / '.self.json').write_text(json.dumps({'consolidate': {
        'command': shlex.join([sys.executable, str(script)])}}))


def test_consolidation_proposes_and_explicit_curation_marks_input(home, tmp_path):
    from brr.self import consolidate, curate, proposals
    encode(home, {'tool_name': 'Bash', 'tool_input': {'command': 'printf first', 'description': 'Verify one receipt'}})
    checkpoint(home)
    original = (home / 'notebook.md').read_text()
    limb(home, tmp_path)
    proposal = consolidate(home)
    manifest = json.loads((proposal / 'input.json').read_text())
    assert manifest['journal'][0]['why'] == 'Verify one receipt'
    assert len(manifest['checkpoints']) == 1
    assert manifest['sizes_bytes']['notebook.md'] == len(original.encode())
    assert (home / 'notebook.md').read_text() == original
    assert [p['path'] for p in proposals(home)] == [str(proposal)]
    # A boundary landing after consolidation must survive the curation mark.
    encode(home, {'tool_name': 'Bash', 'tool_input': {'command': 'printf later', 'description': 'Later boundary'}})
    row = curate(home, proposal, accept=True, files=['notebook.md'])
    assert row['through'] == manifest['through']
    assert (home / 'notebook.md').read_text() == '# Notebook\n\nKeep calls with their reasons.\n'
    assert proposals(home) == []
    second = consolidate(home)
    replay = json.loads((second / 'input.json').read_text())
    assert [r['why'] for r in replay['journal']] == ['Later boundary']
    assert replay['checkpoints'] == []
    with pytest.raises(ValueError, match='already curated'):
        curate(home, proposal, accept=True)


def test_curate_identity_requires_explicit_choice_and_reject_keeps_mark(home, tmp_path):
    from brr.self import consolidate, curate, proposals
    limb(home, tmp_path, {'identity.md': '# New identity', 'notebook.md': '# Rewritten notebook'})
    first = consolidate(home)
    before = (home / 'identity.md').read_text()
    with pytest.raises(ValueError, match='--identity'):
        curate(home, first, accept=True)
    assert (home / 'identity.md').read_text() == before
    assert len(proposals(home)) == 1
    curate(home, first, accept=True, files=['notebook.md'])
    assert (home / 'identity.md').read_text() == before
    second = consolidate(home)
    curate(home, second, accept=True, files=['identity.md'], identity=True)
    assert (home / 'identity.md').read_text() == '# New identity'
    third = consolidate(home)
    with pytest.raises(ValueError, match='Reject requires'):
        curate(home, third)
    curate(home, third, why='The evidence does not justify another rewrite')
    fourth = consolidate(home)
    manifests = [json.loads((p / 'input.json').read_text()) for p in (third, fourth)]
    assert manifests[0]['since'] == manifests[1]['since']


@pytest.mark.parametrize('files,raw', [
    ({'../outside.md': 'bad'}, None), ({'journal/overwrite.md': 'bad'}, None),
    ({'./identity.md': 'bad'}, None), ({'notebook.md': 17}, None),
    (None, 'not JSON'), (None, '{"files": {}, "rationale": ""}'),
])
def test_bad_limb_outputs_cannot_change_authored_files(home, tmp_path, files, raw):
    from brr.self import consolidate, proposals
    before = (home / 'notebook.md').read_text()
    limb(home, tmp_path, files, raw)
    with pytest.raises(ValueError):
        consolidate(home)
    assert (home / 'notebook.md').read_text() == before
    assert proposals(home) == []
    assert len(list((home / 'proposals').glob('*/input.json'))) == 1


def test_stale_and_escaping_acceptance_is_refused_before_any_writes(home, tmp_path):
    from brr.self import consolidate, curate
    limb(home, tmp_path, {'playbook.md': '# New playbook', 'notebook.md': '# New notebook'})
    proposal = consolidate(home)
    old = (home / 'playbook.md').read_text()
    (home / 'notebook.md').write_text('A newer authored call')
    with pytest.raises(ValueError, match='changed since proposal'):
        curate(home, proposal, accept=True)
    assert (home / 'playbook.md').read_text() == old
    assert not (home / 'journal/curation.jsonl').exists()
    with pytest.raises(ValueError, match='directory in this home'):
        curate(home, tmp_path, accept=True)


def test_curate_commits_only_selected_produce_in_home_despite_git_pin(home, tmp_path, monkeypatch):
    from brr.self import consolidate, curate
    from brr.self.consolidation import _git
    assert _git(home, 'init').returncode == 0
    assert _git(home, 'config', 'user.name', 'Test self').returncode == 0
    assert _git(home, 'config', 'user.email', 'test@example.invalid').returncode == 0
    assert _git(home, 'add', '.').returncode == 0
    assert _git(home, 'commit', '-m', 'Seed authored home').returncode == 0
    limb(home, tmp_path)
    proposal = consolidate(home)
    (home / 'unrelated.md').write_text('Do not commit this')
    assert _git(home, 'add', 'unrelated.md').returncode == 0
    monkeypatch.setenv('GIT_DIR', str(tmp_path / 'wrong.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(tmp_path / 'wrong-tree'))
    row = curate(home, proposal.name, accept=True)
    assert row['commit'] == _git(home, 'rev-parse', 'HEAD').stdout.strip()
    changed = _git(home, 'show', '--format=', '--name-only', 'HEAD').stdout.splitlines()
    assert set(changed) == {'notebook.md', 'journal/curation.jsonl'}
    assert 'Accepted: notebook.md' in _git(home, 'log', '-1', '--format=%B').stdout
    assert 'A  unrelated.md' in _git(home, 'status', '--porcelain').stdout


def test_consolidate_mcp_and_curate_cli_are_distinct(home, tmp_path, capsys):
    from brr.self.mcp import call_tool, TOOLS
    limb(home, tmp_path)
    result = call_tool(home, 'consolidate', {'since': 'last'})
    proposal = json.loads(result['content'][0]['text'])['path']
    assert len(json.loads(call_tool(home, 'proposals', {})['content'][0]['text'])) == 1
    assert 'curate' not in {tool['name'] for tool in TOOLS}
    assert main(['curate', '--home', str(home), proposal, '--accept', 'notebook.md']) == 0
    assert json.loads(capsys.readouterr().out)['decision'] == 'accept'
    assert main(['consolidate', '--home', str(home), '--since', 'not ISO']) == 1
    assert 'ISO timestamp' in capsys.readouterr().err


def test_wake_and_consolidation_share_configured_budget(home, tmp_path, capsys):
    from brr.self import consolidate
    limb(home, tmp_path)
    config = json.loads((home / '.self.json').read_text())
    config['wake_budget_bytes'] = 0
    (home / '.self.json').write_text(json.dumps(config))
    assert main(['wake', '--home', str(home)]) == 0
    assert 'self-inject budget exhausted' in capsys.readouterr().out
    proposal = consolidate(home)
    assert json.loads((proposal / 'input.json').read_text())['wake_budget_bytes'] == 0
