"""Publish a built board to the single rolling prerelease; retain 200 commits."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True)


def main():
    commit = os.environ["BOARD_COMMIT"]
    tree = subprocess.check_output(["git", "rev-parse", f"{commit}:src/frontend"], text=True).strip()
    repo = os.environ["GITHUB_REPOSITORY"]
    try:
        gh("release", "view", "board-builds", "--repo", repo)
    except subprocess.CalledProcessError:
        try:
            gh("release", "create", "board-builds", "--repo", repo, "--target", commit,
               "--prerelease", "--latest=false", "--title", "Board builds",
               "--notes", "Commit-addressed board assets for checkout installs. The newest 200 builds are retained.")
        except subprocess.CalledProcessError:
            # Another green commit may have created it concurrently.
            gh("release", "view", "board-builds", "--repo", repo)
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / f"board-{commit}.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for path in sorted(Path("src/frontend/build").iterdir()):
                output.add(path, arcname=path.name)
        manifest = archive.with_name(archive.name + ".json")
        manifest.write_text(json.dumps({"commit": commit, "tree": tree,
                                       "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}) + "\n")
        gh("release", "upload", "board-builds", str(archive), str(manifest), "--repo", repo, "--clobber")
    # Pagination is essential once more than fifty pairs have been published.
    release = json.loads(gh("api", f"repos/{repo}/releases/tags/board-builds"))
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/releases/{release['id']}/assets?per_page=100"))
    assets = [asset for page in pages for asset in page]
    archives = sorted((a for a in assets if a["name"].startswith("board-") and a["name"].endswith(".tar.gz")),
                      key=lambda a: (a["created_at"], a["id"]), reverse=True)
    remove = {a["name"] for a in archives[200:]}
    remove |= {name + ".json" for name in remove}
    for asset in assets:
        if asset["name"] in remove:
            gh("api", "--method", "DELETE", f"repos/{repo}/releases/assets/{asset['id']}")


if __name__ == "__main__":
    main()
