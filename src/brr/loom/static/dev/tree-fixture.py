"""Write fixtures/tree.json — the full tree the greybox lays out at real scale.

The loom feed carries only *visited* places (tree.repo / tree.home.places);
law 4 (rooms don't move) wants the layout to be a function of the whole tree,
so the fog can lift without moving a wall. This is that tree: `git ls-files`
of the repo and of the account home (dominion/, surface/) plus the knowledge
repo mounted as knowledge/. .gitignore decides what is not source — the row
is marked, never dropped, by not being here at all. Regenerate:

    python3 src/brr/loom/static/dev/tree-fixture.py [repo-root] [account-home]
"""
from __future__ import annotations
import json, os, subprocess, sys, datetime as dt

HOME_DIRS = ("dominion", "surface", "hearth", "account")
def ls(root: str) -> list[str]:
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]

def repo_name(root: str) -> str:
    r = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True, text=True)
    url = r.stdout.strip().rstrip("/")
    if r.returncode == 0 and url:
        return url.rsplit("/", 1)[-1].removesuffix(".git")
    return os.path.basename(root)

def main() -> None:
    repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    home = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("BRNRD_ACCOUNT_HOME", "")
    tree = {"captured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "repo": {"name": repo_name(repo), "files": sorted(ls(repo))}, "home": {"files": []}}
    if home and os.path.isdir(os.path.join(home, ".git")):
        files = [p for p in ls(home) if p.split("/", 1)[0] in HOME_DIRS]
        kn = os.path.join(home, "knowledge")
        if os.path.isdir(os.path.join(kn, ".git")):
            files += ["knowledge/" + p for p in ls(kn)]
        tree["home"]["files"] = sorted(files)
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "fixtures", "tree.json")
    with open(out, "w") as f:
        json.dump(tree, f, separators=(",", ":"))
    print(out, len(tree["repo"]["files"]), "repo files,", len(tree["home"]["files"]), "home files")

if __name__ == "__main__":
    main()
