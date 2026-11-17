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


def test_writer_is_single_and_a_broken_epoch_never_stops_capture(tmp_path):
    home = Home(tmp_path, install="abcd")
    journal = audit.Journal(home)
    with pytest.raises(BlockingIOError):
        audit.Journal(home)
    journal.close()
    (journal.epoch / "entries.jsonl").write_bytes(b'broken\n')
    successor = audit.Journal(home)
    successor.append({"kind": "tool", "event": event("after")})
    successor.close()
    assert (journal.epoch / "entries.jsonl").read_bytes() == b'broken\n'
    assert successor.found["epoch"] == journal.epoch.name
    with pytest.raises(ValueError, match=journal.epoch.name):
        audit.verify(successor.base)


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
    # A writer that closed took its endpoint: no capture running, nothing to warn of.
    capsys.readouterr()  # A slow runner may have warned above on an ack past the 5 ms budget.
    audit.note(place, payload, 'pre')
    assert 'capture unavailable' not in capsys.readouterr().err


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


def test_unclean_stop_opens_a_new_epoch_that_says_so(tmp_path, monkeypatch):
    """The crash window: the row is fsynced, the head is not yet replaced."""
    home = Home(tmp_path, install="abcd")
    journal = audit.Journal(home)
    journal.append({"kind": "tool", "event": event("a")})
    real = audit._replace
    def crash(path, value):
        if path.name == "head.json":
            raise OSError("power lost")
        real(path, value)
    monkeypatch.setattr(audit, "_replace", crash)
    with pytest.raises(OSError):
        journal.append({"kind": "tool", "event": event("b")})
    journal.close()
    monkeypatch.setattr(audit, "_replace", real)
    old = (journal.epoch / "entries.jsonl").read_bytes()
    with pytest.raises(ValueError, match="no later epoch has recorded"):
        audit.verify(journal.base)
    successor = audit.Journal(home)
    successor.append({"kind": "tool", "event": event("c")})
    successor.close()
    assert (journal.epoch / "entries.jsonl").read_bytes() == old
    first = json.loads((successor.epoch / "entries.jsonl").read_bytes().splitlines()[0])
    committed = json.loads((journal.epoch / "head.json").read_bytes())
    assert first["found"] == {"epoch": journal.epoch.name, "committed": committed,
                              "size": len(old), "listed": True}
    assert first["previous_epoch"] == committed["hash"]
    result = audit.verify(journal.base)
    assert result["entries"] == 2  # a and c; b was never acknowledged
    [report] = result["unclean"]
    assert report["epoch"] == journal.epoch.name and report["tail_bytes"] > 0
    # A third clean start links on; the unclean epoch is still reported, still checked after.
    audit.Journal(home).close()
    assert audit.verify(journal.base)["unclean"] == [report]
    # Anything changed in the unclean epoch after it was found is not unclean, it is broken.
    with open(journal.epoch / "entries.jsonl", "ab") as stream:
        stream.write(b"more\n")
    with pytest.raises(ValueError, match="changed after"):
        audit.verify(journal.base)


def test_unacknowledged_partial_append_is_reported_unclean(tmp_path, monkeypatch):
    home = Home(tmp_path, install="abcd")
    journal = audit.Journal(home)
    def fail(*args):
        raise OSError("fsync failed")
    monkeypatch.setattr(audit.os, 'fsync', fail)
    with pytest.raises(OSError):
        journal.append({"kind": "tool", "event": event("a")})
    journal.close()
    monkeypatch.undo()
    successor = audit.Journal(home)
    successor.close()
    assert [u["epoch"] for u in audit.verify(home.root / "audit/abcd")["unclean"]] == [journal.epoch.name]


def test_crash_before_the_manifest_or_the_first_head(tmp_path, monkeypatch):
    home = Home(tmp_path, install="abcd")
    audit.Journal(home).close()
    real = audit._replace
    def crash(path, value):
        if path.name == "epochs.json":
            raise OSError("power lost")
        real(path, value)
    monkeypatch.setattr(audit, "_replace", crash)
    with pytest.raises(OSError):
        audit.Journal(home)  # entry 0 written, manifest never replaced
    monkeypatch.undo()
    orphan = audit.Journal(home)
    orphan.close()
    assert orphan.found["listed"] is False
    (orphan.base / f"{3:020d}-deadbeef").mkdir()  # mkdir, then a crash before any head
    last = audit.Journal(home)
    last.close()
    assert last.found["committed"] is None
    result = audit.verify(last.base)
    assert result["epochs"] == 5 and len(result["unclean"]) == 2


def test_start_reads_no_history(tmp_path, monkeypatch):
    home = Home(tmp_path, install="abcd")
    journal = audit.Journal(home)
    for i in range(50):
        journal.append({"kind": "tool", "event": event(str(i))})
    journal.close()
    def walked(*args):
        raise AssertionError("start-up walked the history")
    monkeypatch.setattr(audit, "_rows", walked)
    monkeypatch.setattr(audit, "verify", walked)
    successor = audit.Journal(home)
    successor.close()
    assert successor.found is None


def test_bad_requests_never_poison_the_writer(tmp_path, monkeypatch):
    import socket
    # What is tested is the writer's state after bad requests, not the runner's speed: the 5 ms
    # production deadline (connect, send, fsync, ack) misses on a loaded CI runner (brnrd#2758).
    monkeypatch.setattr(audit, "DEADLINE", 1.0)
    home = Home(tmp_path)
    place = room(home)
    writer = audit.Writer(home)
    endpoint = json.loads((home.root / "loom/audit-endpoint.json").read_bytes())
    payload = json.dumps({'tool_name': 'Bash', 'tool_use_id': 'x'})
    def send(raw, close=True, wait=1.0):
        with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=wait) as conn:
            conn.sendall(raw)
            if close:
                conn.shutdown(socket.SHUT_WR)
            try:
                return conn.recv(3)
            except OSError:
                return b""
    try:
        good = {"token": endpoint["token"], "event": event("e")}
        for raw in (b"garbage\n", b'{"token":', b'[1]\n', b'{"token":7}\n',
                    audit.canonical(dict(good, token="0" * 64)) + b"\n",
                    audit.canonical({"token": endpoint["token"], "event": {"id": "bad"}}) + b"\n",
                    b'{"token":"' + endpoint["token"].encode() + b'","event":NaN}\n'):
            assert send(raw) != b"ok\n"
        send(audit.canonical(good)[:20], close=False, wait=.05)  # half-sent, then left hanging
        assert writer.error is None
        audit.note(place, payload, "pre")
    finally:
        writer.close()
    assert audit.verify(writer.journal.base)["entries"] == 1
    assert not (home.root / "loom/audit-warning").exists()


def test_a_second_outage_warns_again(tmp_path, capsys):
    home = Home(tmp_path)
    place = room(home)
    payload = json.dumps({'tool_name': 'Bash'})
    (home.root / "loom").mkdir(exist_ok=True)
    (home.root / "loom/audit-endpoint.json").write_text('{"port": 1, "token": "x"}')
    audit.note(place, payload, "pre")
    audit.note(place, payload, "pre")
    assert capsys.readouterr().err.count("capture unavailable") == 1
    writer = audit.start(home, True)
    writer.close()
    assert not (home.root / "loom/audit-warning").exists()
    (home.root / "loom/audit-endpoint.json").write_text('{"port": 1, "token": "x"}')
    audit.note(place, payload, "pre")
    assert capsys.readouterr().err.count("capture unavailable") == 1


def test_start_off_removes_a_dead_endpoint_and_starts_nothing(tmp_path):
    home = Home(tmp_path)
    (home.root / "loom").mkdir(parents=True)
    (home.root / "loom/audit-endpoint.json").write_text('{"port": 1, "token": "x"}')
    capture = audit.start(home, False)
    assert capture.writer is None
    capture.check()
    assert not (home.root / "loom/audit-endpoint.json").exists()
    assert not (home.root / "audit").exists()


def test_a_failed_start_and_a_stopped_writer_are_a_loom_degraded_step(tmp_path, monkeypatch):
    """Through the guard the loop uses: degraded while capture is absent, recovered at a good start."""
    from brr.loom.runtime.health import Health
    from brr.loom.runtime.ledger import read_facts
    home = Home(tmp_path)
    (home.root / "loom").mkdir(parents=True)
    log = lambda *a: None
    def kinds():
        return [(f.kind, f.data["step"]) for f in read_facts(home) if f.kind.startswith("loom.") and f.data.get("step") == "audit"]
    real = audit.Journal
    def refuse(*a):
        raise OSError("disk full")
    monkeypatch.setattr(audit, "Journal", refuse)
    capture = audit.start(home, True)
    Health(home, read_facts(home), log).guard("audit", capture.check, backoff=False)
    assert kinds() == [("loom.degraded", "audit")]
    monkeypatch.setattr(audit, "Journal", real)
    capture = audit.start(home, True)  # the next start comes up
    try:
        health = Health(home, read_facts(home), log)
        health.guard("audit", capture.check, backoff=False)
        assert kinds()[-1] == ("loom.recovered", "audit")
        def fail(*a):
            raise OSError("disk full")
        monkeypatch.setattr(capture.writer.journal, "append", fail)
        audit.note(room(home), json.dumps({"tool_name": "Write"}), "pre")
        health.guard("audit", capture.check, backoff=False)
        assert kinds()[-1] == ("loom.degraded", "audit")
    finally:
        capture.close()
