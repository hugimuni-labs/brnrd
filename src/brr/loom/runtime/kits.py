"""Install reviewed kit text from Git objects without checking out upstream code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

from .home import home_for
from .refusals import Parser, refuse

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
COMMIT = re.compile(r"[0-9a-f]{40}")
SOURCE = re.compile(r"https://github\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+")
SKILL_FILE = re.compile(r"skills/[a-z0-9-]+/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\.md")


def _manifest(path: Path) -> tuple[dict, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        source = data["source"]
        review = data["review"]
        description = data["description"]
        if (
            data["version"] != 1
            or not NAME.fullmatch(data["id"])
            or not SOURCE.fullmatch(source["url"])
            or not COMMIT.fullmatch(source["commit"])
            or not source["license"].strip()
            or not description.isprintable()
            or len(description) > 200
            or not review["by"].strip()
            or not review["notes"].strip()
            or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", review["at"])
            or data["script_count"] != 0
            or not isinstance(data["hands"], list)
            or not isinstance(data["mcp_servers"], list)
            or not isinstance(data["requirements"], dict)
        ):
            raise ValueError("expected a reviewed version 1 text kit")
        files = ["LICENSE"]
        for skill in data["skills"]:
            name = skill["id"]
            paths = skill["files"]
            if (not NAME.fullmatch(name) or not isinstance(paths, list)
                    or f"skills/{name}/SKILL.md" not in paths):
                raise ValueError("each skill needs its own SKILL.md")
            for file in paths:
                if not SKILL_FILE.fullmatch(file) or not file.startswith(f"skills/{name}/"):
                    raise ValueError("kit files must be Markdown within their skill")
                files.append(file)
        if len(files) < 2 or len(files) != len(set(files)):
            raise ValueError("expected at least one skill and unique file paths")
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError("invalid kit manifest") from exc
    return data, files


def _git(repo: Path, *args: str) -> bytes:
    # Ignore ambient Git configuration and credentials during public import.
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="https")
    return subprocess.run(
        ["git", "-C", str(repo), *args], env=env, check=True,
        capture_output=True, timeout=60,
    ).stdout


def _fetch(repo: Path, url: str, commit: str) -> None:
    _git(repo, "init", "--bare", "--quiet", "--template=")
    _git(repo, "-c", "http.followRedirects=false", "fetch", "--depth=1", "--no-tags",
         "--no-recurse-submodules", url, commit)
    if _git(repo, "rev-parse", "FETCH_HEAD^{commit}").decode().strip() != commit:
        raise ValueError("upstream did not return the reviewed commit")


def _install(root: Path, manifest: Path) -> Path:
    data, files = _manifest(manifest)
    source = data["source"]
    target = root / "kits" / data["id"] / source["commit"]
    if any(path.is_symlink() for path in (target.parent.parent, target.parent, target)):
        raise ValueError("kit directories must not be symlinks")
    target.parent.mkdir(parents=True, exist_ok=True)

    def installed() -> bool:
        if not target.exists():
            return False
        if _manifest(target / "manifest.json")[0] != data:
            raise ValueError("this kit version already has a different manifest")
        return True

    if installed():
        return target
    with TemporaryDirectory(prefix=".import-", dir=target.parent) as temporary:
        scratch = Path(temporary)
        repo = scratch / "source.git"
        repo.mkdir()
        _fetch(repo, source["url"], source["commit"])
        payload = scratch / "payload"
        payload.mkdir()
        for file in files:
            entry = _git(repo, "ls-tree", "-z", source["commit"], "--", file)
            if not entry.startswith(b"100644 blob ") or not entry.endswith(
                b"\t" + file.encode() + b"\0"
            ):
                raise ValueError(f"expected a regular, non-executable file at {file}")
            content = _git(repo, "show", f"{source['commit']}:{file}")
            if "\0" in content.decode("utf-8"):
                raise ValueError(f"expected text at {file}")
            destination = payload / file
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        (payload / "manifest.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        try:
            payload.rename(target)
        except OSError:
            if not installed():
                raise
    return target


def _installed(root: Path):
    for path in sorted((root / "kits").glob("*/*/manifest.json")):
        if not NAME.fullmatch(path.parent.parent.name) or not COMMIT.fullmatch(path.parent.name):
            continue
        if any(parent.is_symlink() for parent in (path, *list(path.parents)[:3])):
            continue
        try:
            data, files = _manifest(path)
        except (ValueError, OSError):
            continue
        if data["id"] == path.parent.parent.name and data["source"]["commit"] == path.parent.name:
            yield path, data, files


def wake_text(root: Path) -> str:
    lines = [
        f"kit {data['id']} @ {data['source']['commit'][:12]} — {data['description']}"
        for _, data, _ in _installed(root)
    ]
    if not lines:
        return ""
    return "\nInstalled kits (use `brnrd runtime kit list` for file paths)\n" + "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = Parser(verb="kit", prog="brnrd runtime kit")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="import a reviewed manifest from disk")
    importer.add_argument("manifest", type=Path)
    listing = commands.add_parser("list", help="show installed kits and skill paths")
    for command in (importer, listing):
        command.add_argument("--home")
    args = parser.parse_args(argv)
    try:
        root = home_for(args.home).root
        if args.command == "import":
            print(_install(root, args.manifest))
        else:
            for path, data, files in _installed(root):
                print(f"{data['id']} @ {data['source']['commit']} — {data['description']}")
                for file in files:
                    print(f"  {path.parent / file}")
    except subprocess.CalledProcessError as exc:
        return refuse("kit", exc.stderr.decode(errors="replace").strip() or str(exc))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return refuse("kit", exc)
    return 0
