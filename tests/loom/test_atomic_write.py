"""Concurrent writers of one path: each lands whole, none crashes."""

import threading

from brr.loom.runtime.home import atomic_write


def test_concurrent_writers_of_one_path_all_land(tmp_path):
    path = tmp_path / "usage" / "claude.json"
    errors = []

    def write(n):
        try:
            for _ in range(200):
                atomic_write(path, f"writer {n}\n")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert path.read_text() in {f"writer {n}\n" for n in range(8)}
    assert [p.name for p in path.parent.iterdir()] == ["claude.json"]
