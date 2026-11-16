"""Exercise durable bytes, hook capture and the verifier through their callers."""
import json
import shutil

import pytest

from brr.loom.runtime import audit, jack
from brr.loom.runtime.home import Home
from brr.loom.runtime.port import render_boundary


def test_encoding_golden():
    value = {"z": "é\n", "a": [None, True, 2]}
    assert audit.canonical(value) == b'{"a":[null,true,2],"z":"\\u00e9\\n"}'
    assert audit.digest(value) == 'f14b59b1539473fd2c2b50e66a97765b27a0528cdb542ac3aa70b1ba070d0460'
    assert audit.entry_hash(value) == '1e80ad4491904a5c1dce39471549355451fb0b1592ca31a7440af6d90c88faee'
    with pytest.raises(ValueError):
        audit.canonical(float('nan'))


def event(ident):
    return {"id": ident, "input_digest": audit.digest({"command": "echo secret"}),
            "output_digest": audit.digest(None), "source_digest": audit.digest(ident)}


def test_chain_epochs_and_tampering(tmp_path):
    home = Home(tmp_path, install="abcd")
    journal = audit.Journal(home)
    journal.append({"kind": "tool", "event": event("a")})
    journal.append({"kind": "tool", "event": event("b")})
    journal.close()
    journal = audit.Journal(home)
    journal.append({"kind": "tool", "event": event("c")})
    base, epoch = journal.base, journal.epoch
    journal.close()
    assert audit.verify(base)["entries"] == 3
    original = sorted(base.glob('*/entries.jsonl'))[0]
    for mutation in ("flip", "remove", "reorder", "last", "duplicate", "digest", "epoch"):
        copy = tmp_path / mutation / 'abcd'
        shutil.copytree(base, copy)
        target = copy / original.relative_to(base)
        lines = target.read_bytes().splitlines(keepends=True)
        if mutation == "flip":
            lines[1] = lines[1].replace(b'"id":"a"', b'"id":"x"')
        elif mutation == "remove":
            del lines[1]
        elif mutation == "last":
            lines.pop()
        elif mutation == "reorder":
            lines[1], lines[2] = lines[2], lines[1]
        elif mutation == "epoch":
            shutil.rmtree(copy / epoch.name)
        else:
            # Rehash to prove duplicate/digest checks do more than chain checks.
            row = json.loads(lines[2]); row.pop('hash')
            if mutation == "duplicate":
                row['event']['id'] = 'a'
            else:
                row['event']['input_digest'] = 'bad'
            row['hash'] = audit.entry_hash(row)
            lines[2] = audit.canonical(row) + b'\n'
        target.write_bytes(b''.join(lines))
        with pytest.raises((ValueError, OSError)):
            audit.verify(copy)


def test_writer_is_single_and_no_repair(tmp_path):
    home = Home(tmp_path, install="abcd")
    journal = audit.Journal(home)
    with pytest.raises(BlockingIOError):
        audit.Journal(home)
    journal.close()
    (journal.epoch / "entries.jsonl").write_bytes(b'broken\n')
    with pytest.raises(ValueError):
        audit.Journal(home)
    assert (journal.epoch / "entries.jsonl").read_bytes() == b'broken\n'


def room(home):
    place = home.root / 'rooms' / 's-abcd-aaaaaa'
    (place / 'port/in').mkdir(parents=True)
    (place / 'port/out').mkdir()
    (place / 'port/in/boundary.md').write_text(render_boundary(place.name, 3, 'act-chain', [], set(), {}))
    return place


def test_jack_pre_refusal_completion_and_off(tmp_path, monkeypatch, capsys):
    home = Home(tmp_path)
    place = room(home)
    payload = json.dumps({'tool_name': 'Bash', 'tool_use_id': 'call-1',
                          'cwd': '/work', 'tool_input': {'command': 'echo private-secret'},
                          'tool_response': 'private-result'})
    monkeypatch.setattr(jack, '_guard_warnings', lambda *a: ('', False))
    monkeypatch.setattr(jack, '_first_word', lambda *a, **kw: ('', False))
    jack.execute('pre', place, payload)
    assert not (home.root / 'audit').exists()
    (home.root / 'loom').mkdir(exist_ok=True)
    (home.root / 'loom/config.toml').write_text('audit = "on"\n')
    writer = audit.Writer(home)
    try:
        jack.execute('pre', place, payload)
        jack.execute('post', place, payload)
        monkeypatch.setattr(jack, '_guard_warnings', lambda *a: ('test refusal', True))
        denied = json.loads(jack.execute('pre', place, payload))
        assert denied['hookSpecificOutput']['permissionDecision'] == 'deny'
        assert audit.main(['verify', '--home', str(home.root)]) == 0
    finally:
        writer.close()
    data = b''.join(p.read_bytes() for p in writer.journal.base.glob('*/entries.jsonl'))
    rows = [json.loads(line) for line in data.splitlines()][1:]
    assert [r['event']['phase'] for r in rows] == ['pre', 'post', 'pre', 'refused']
    assert rows[0]['event']['wake'] == 3
    assert rows[0]['event']['call'] == 'call-1'
    assert b'private-secret' not in data and b'private-result' not in data
    assert audit.main(['verify', '--home', str(home.root)]) == 0
    # Missing writer: twice called, one visible warning, no refusal.
    audit.note(place, payload, 'pre')
    audit.note(place, payload, 'pre')
    assert capsys.readouterr().err.count('capture unavailable') == 1


def test_append_failure_never_acknowledged_or_repaired(tmp_path, monkeypatch):
    home = Home(tmp_path)
    place = room(home)
    (home.root / 'loom').mkdir(exist_ok=True)
    (home.root / 'loom/config.toml').write_text('audit = "on"\n')
    writer = audit.Writer(home)
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(writer.journal, 'append', fail)
    try:
        audit.note(place, json.dumps({'tool_name': 'Write', 'tool_use_id': 'a'}), 'pre')
        assert writer.error == 'disk full'
        assert audit.verify(writer.journal.base)['entries'] == 0
        assert (home.root / 'loom/audit-warning').exists()
    finally:
        writer.close()


def test_live_verifier_checks_a_prefix_while_writer_continues(tmp_path):
    journal = audit.Journal(Home(tmp_path, install="abcd"))
    try:
        journal.append({"kind": "tool", "event": event("a")})
        snapshot = audit.checkpoint(journal.base)
        journal.append({"kind": "tool", "event": event("b")})
        assert audit.verify(journal.base, snapshot)["entries"] == 1
        assert audit.verify(journal.base)["entries"] == 2
    finally:
        journal.close()


def test_unacknowledged_partial_append_stays_broken(tmp_path, monkeypatch):
    home = Home(tmp_path, install="abcd")
    journal = audit.Journal(home)
    def fail(*args):
        raise OSError("fsync failed")
    monkeypatch.setattr(audit.os, 'fsync', fail)
    with pytest.raises(OSError):
        journal.append({"kind": "tool", "event": event("a")})
    journal.close()
    with pytest.raises(ValueError):
        audit.Journal(home)
