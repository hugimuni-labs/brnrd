"""Exercise downloaded bytes and extraction, including hostile archives."""
import hashlib
import io
import json
import tarfile

import pytest

from brr.loom.runtime import board_asset

COMMIT, TREE = "a" * 40, "b" * 40


def payload(name="200.html", kind=None):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        if kind:
            member.type = kind
            member.linkname = "../outside"
            archive.addfile(member)
        else:
            member.size = 2
            archive.addfile(member, io.BytesIO(b"ok"))
    return data.getvalue()


def getter(data, **overrides):
    manifest = {"commit": COMMIT, "tree": TREE, "sha256": hashlib.sha256(data).hexdigest(), **overrides}
    return lambda url, limit: json.dumps(manifest).encode() if url.endswith(".json") else data


def test_fetch_writes_verified_board_and_tree(tmp_path):
    destination = tmp_path / "next"
    board_asset.fetch(COMMIT, TREE, destination, getter(payload()))
    assert (destination / "200.html").read_text() == "ok"
    assert (destination / ".tree").read_text().strip() == TREE


@pytest.mark.parametrize("overrides", [{"sha256": "wrong"}, {"commit": "c" * 40}, {"tree": "d" * 40}])
def test_reject_mismatched_manifest_before_writing(tmp_path, overrides):
    destination = tmp_path / "next"
    with pytest.raises(ValueError):
        board_asset.fetch(COMMIT, TREE, destination, getter(payload(), **overrides))
    assert not destination.exists()


@pytest.mark.parametrize("name,kind", [("../outside", None), ("/outside", None), ("link", tarfile.SYMTYPE), ("hard", tarfile.LNKTYPE)])
def test_archive_cannot_escape_staging(tmp_path, name, kind):
    destination = tmp_path / "next"
    with pytest.raises(ValueError, match="unsafe"):
        board_asset.fetch(COMMIT, TREE, destination, getter(payload(name, kind)))
    assert not destination.exists()
    assert not (tmp_path / "outside").exists()


def test_missing_shell_rejected(tmp_path):
    with pytest.raises(ValueError, match="200.html"):
        board_asset.fetch(COMMIT, TREE, tmp_path / "next", getter(payload("other")))


def test_publisher_uploads_matching_pair_and_prunes_only_old_pairs(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'publish_board.py'
    spec = importlib.util.spec_from_file_location('publish_board_test', script)
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    monkeypatch.chdir(tmp_path)
    build = tmp_path / 'src/frontend/build'
    build.mkdir(parents=True)
    (build / '200.html').write_text('board')
    monkeypatch.setenv('BOARD_COMMIT', COMMIT)
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/repo')
    monkeypatch.setattr(publisher.subprocess, 'check_output', lambda *a, **k: TREE + '\n')
    assets = []
    for index in range(201):
        name = f'board-{index:040x}.tar.gz'
        for suffix in ('', '.json'):
            assets.append({'name': name + suffix, 'created_at': f'{index:06}', 'id': len(assets)})
    assets.append({'name': 'unrelated', 'created_at': '0', 'id': 999})
    deleted, uploaded = [], []

    def gh(*args):
        if args[:2] == ('release', 'upload'):
            archive, manifest = Path(args[3]), Path(args[4])
            data = archive.read_bytes()
            uploaded.append(json.loads(manifest.read_text()))
            assert uploaded[0] == {'commit': COMMIT, 'tree': TREE, 'sha256': hashlib.sha256(data).hexdigest()}
        elif args[:2] == ('api', '--paginate'):
            return json.dumps([assets[:100], assets[100:]])
        elif args[:3] == ('api', '--method', 'DELETE'):
            deleted.append(args[-1])
        elif args[0] == 'api':
            return json.dumps({'id': 42})
        return ''

    monkeypatch.setattr(publisher, 'gh', gh)
    publisher.main()
    assert len(uploaded) == 1
    assert deleted == ['repos/owner/repo/releases/assets/0', 'repos/owner/repo/releases/assets/1']
