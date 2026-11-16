"""Verified, commit-addressed board archives from the rolling prerelease.

Downloads never replace a good build until the complete archive is validated.
No credentials, Node, Docker, or GitHub API calls are needed by the installer.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path

BASE = "https://github.com/hugimuni-labs/brnrd/releases/download/board-builds"
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024


def download(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "brnrd-board"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("board asset exceeds size limit")
    return data


def fetch(commit: str, tree: str, destination: Path, get=download) -> None:
    """Populate an empty staging directory; the caller owns the final swap."""
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (commit, tree)):
        raise ValueError("board commit and tree must be full git hashes")
    name = f"board-{commit}.tar.gz"
    manifest = json.loads(get(f"{BASE}/{name}.json", 4096))
    if manifest.get("commit") != commit or manifest.get("tree") != tree:
        raise ValueError("board manifest does not match upstream commit/tree")
    data = get(f"{BASE}/{name}", MAX_ARCHIVE)
    if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
        raise ValueError("board archive digest mismatch")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if sum(member.size for member in members) > MAX_EXPANDED:
            raise ValueError("expanded board exceeds size limit")
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError("unsafe board archive member")
        destination.mkdir(parents=True, exist_ok=False)
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("missing board archive member")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    if not (destination / "200.html").is_file():
        raise ValueError("board archive has no 200.html")
    (destination / ".tree").write_text(tree + "\n")
