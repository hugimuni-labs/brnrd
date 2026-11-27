from brr.loom.runtime import scar_log


def test_rotation_keeps_an_append_to_the_detached_inode(tmp_path, monkeypatch):
    path = tmp_path / scar_log.LOG
    now = 2 * scar_log.WINDOW
    old = {"at": now - scar_log.WINDOW - 1, "call": "old", "paths": ["old.md"]}
    display = {"at": now - scar_log.WINDOW + 1, "call": "fresh", "paths": ["fresh.md"]}
    scar_log._append(path, old)
    scar_log._append(path, display)

    # Compaction runs after the writer opens the live file, just before
    # its write. The writer must retry against the replacement inode.
    write = scar_log.os.write

    def rotate_then_write(fd, data):
        assert scar_log.history(path, now) == (old["at"], [display])
        monkeypatch.setattr(scar_log.os, "write", write)
        return write(fd, data)

    monkeypatch.setattr(scar_log.os, "write", rotate_then_write)
    outcome = {"at": now, "call": "fresh", "failure": "blocked"}
    scar_log._append(path, outcome)
    archive = path.with_suffix(".archive.jsonl")
    assert old not in list(scar_log._rows(archive))
    assert scar_log.history(path, now) == (old["at"], [display, outcome])

    # The display falls out of the reporting window before rotation is due.
    archive_before = archive.read_bytes()
    assert scar_log.history(path, now + 2) == (old["at"], [outcome])
    assert archive.read_bytes() == archive_before
