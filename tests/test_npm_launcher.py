import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
NPM = ROOT / "packaging" / "npm"


def test_uv_release_assets_are_pinned_for_supported_node_hosts():
    release = json.loads((NPM / "uv-assets.json").read_text())

    assert re.fullmatch(r"\d+\.\d+\.\d+", release["version"])
    assert release["python"] == "3.12"
    assert release["checksums"] == (
        f"https://github.com/astral-sh/uv/releases/download/"
        f"{release['version']}/sha256.sum"
    )
    assert set(release["assets"]) == {
        "darwin-arm64",
        "darwin-x64",
        "linux-arm64-gnu",
        "linux-arm64-musl",
        "linux-ia32-gnu",
        "linux-ia32-musl",
        "linux-x64-gnu",
        "linux-x64-musl",
        "win32-arm64",
        "win32-ia32",
        "win32-x64",
    }
    for asset in release["assets"].values():
        assert asset["archive"].startswith("uv-")
        assert asset["archive"].endswith((".tar.gz", ".zip"))
        assert re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])
        assert asset["size"] > 10_000_000


def test_uv_release_manifest_is_in_the_npm_tarball():
    package = json.loads((NPM / "package.json").read_text())
    assert "uv-assets.json" in package["files"]
