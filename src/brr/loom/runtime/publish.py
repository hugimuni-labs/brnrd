"""``publish``: the one door a project checkout's commits leave the machine through.

The strand's label is read here, at push time, because a body can become
tainted after it starts. The push goes to the source the seat declares on
main, never to the checkout's own ``origin``: a body can rewrite its
checkout's remotes, and the home's token would follow them.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .config import load_config
from .home import home_of_room, strand_of_room
from .ledger import Fact, append, read_facts
from .selfrepo import SelfError, _env_without_pin, _segment, git, resolve_clone

_PUSH_TIMEOUT = 600
_TOKEN_ENV = "BRNRD_FORGE_TOKEN_FILE"
# git runs a helper that starts with ``!`` through sh. The token is read from
# the file when git asks, so it is in no argv and no config.
_TOKEN_HELPER = (
    '!f() { test "$1" = get || exit 0; echo username=x-access-token; '
    f'echo "password=$(cat "${_TOKEN_ENV}")"; }}; f'
)
_GIT_CONFIG_ENV = ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
                   "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")


class PublishError(RuntimeError):
    """``publish`` did not push. The text is what the body reads."""


@dataclass(frozen=True)
class Published:
    thread: str
    repo: str
    branch: str
    commit: str
    #: How the push was authorized: ``token``, ``local``, or ``machine``.
    lane: str
    #: One line for stderr when the lane needs saying. Empty otherwise.
    note: str = ""


def _plain_env() -> dict[str, str]:
    """The body's environment without the git config a strand's command line pins."""
    env = _env_without_pin()
    for key in list(env):
        if key in _GIT_CONFIG_ENV or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            del env[key]
    return env


def _is_local(source: str) -> bool:
    return source.startswith("/") or source.startswith("file://")


def push_command(source: str, branch: str, token_file: str | None,
                 ) -> tuple[list[str], dict[str, str], str, str]:
    """``(git arguments, environment, lane, note)`` for one push of ``branch``.

    With a token, git reads no machine or user config: a ``url.*.insteadOf``
    there would turn the https push into an ssh push under the machine
    owner's key, and a helper there would answer before the token's. The
    token's helper is scoped to the source's host, so a redirect to another
    host is handed nothing.
    """
    refspec = f"refs/heads/{branch}:refs/heads/{branch}"
    env = _plain_env()
    if _is_local(source):
        return ["push", "--", source, refspec], env, "local", ""
    if token_file:
        parts = urlsplit(source)
        if parts.scheme != "https" or not parts.hostname:
            raise PublishError(
                f"publish: the home's token is only sent over https, and this repo's "
                f"source is {source}. Pushing it would go out under this machine's own "
                f"keys. Declare the https URL in the seat's repo: line."
            )
        if not Path(token_file).is_file():
            raise PublishError(f"publish: forge_token_file {token_file} is not a file")
        host = parts.hostname + (f":{parts.port}" if parts.port else "")
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env[_TOKEN_ENV] = token_file
        args = [
            "-c", "credential.helper=",
            "-c", f"credential.https://{host}.helper=",
            "-c", f"credential.https://{host}.helper={_TOKEN_HELPER}",
            "push", "--", source, refspec,
        ]
        return args, env, "token", ""
    # No token: the machine's own setup, said out loud. The checkout's empty
    # ``credential.helper`` clears the machine's helpers, so they are named
    # again on the command line, after it.
    args: list[str] = []
    for scope in ("--system", "--global"):
        proc = git(Path.cwd(), "config", scope, "--null", "--get-regexp",
                   r"^credential\.(.*\.)?helper$", check=False, env=env)
        if proc.returncode != 0:
            continue
        for entry in proc.stdout.split("\x00"):
            key, newline, value = entry.partition("\n")
            if key and newline and value:
                args += ["-c", f"{key}={value}"]
    who = git(Path.cwd(), "config", "--global", "user.email", check=False, env=env).stdout.strip()
    note = ("publish: no forge_token_file in loom/config.toml; pushed with this machine's "
            f"own git setup (git user {who or 'unset'})")
    return [*args, "push", "--", source, refspec], env, "machine", note


def _declared(home, thread: str, name: str) -> str:
    from .adapters import repos, seat_text
    for found, source in repos(seat_text(home, thread)):
        if found == name:
            return source
    raise PublishError(
        f"publish: thread {thread}'s seat on main declares no repo named {name}"
    )


def _other_authors(checkout: Path, branch: str, email: str) -> list[str]:
    """Commits on ``branch`` no ``origin`` ref holds whose author is not ``email``."""
    proc = git(checkout, "log", "--format=%h %ae", f"refs/heads/{branch}",
               "--not", "--remotes=origin")
    return [line for line in proc.stdout.splitlines()
            if line.strip() and line.split(" ", 1)[1] != email]


def _gen(facts, strand: str) -> int | None:
    found = None
    for fact in facts:
        if fact.kind == "lease" and (fact.data or {}).get("strand") == strand:
            found = (fact.data or {}).get("gen")
    return found if isinstance(found, int) else None


def publish(room: Path | str, name: str) -> Published:
    """Push ``loom/<thread>`` of ``<room>/<name>`` to its declared source.

    Refuses a tainted strand, a branch carrying another author, and a repo
    the thread's seat does not declare. Records ``branch.published`` after
    the push, never before.
    """
    from .loom import _commit_identity
    from .port import _current_label
    from .project import sender_threads

    room = Path(room).resolve()
    if _segment(name) is None or name == "self":
        raise PublishError(f"publish: {name!r} is not a project repo in this room")
    try:
        home = home_of_room(room)
    except ValueError as exc:
        raise PublishError(f"publish: {exc}") from exc
    strand = strand_of_room(room)
    checkout = room / name
    if not (checkout / ".git").exists():
        raise PublishError(f"publish: no checkout at {checkout}")
    facts = read_facts(home)
    thread = sender_threads(facts).get(strand)
    if thread is None:
        raise PublishError(f"publish: room {strand} has no thread")
    branch = f"loom/{thread}"
    tip = git(checkout, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}",
              check=False)
    if tip.returncode != 0:
        raise PublishError(f"publish: {checkout} has no branch {branch}")
    commit = tip.stdout.strip()

    if _current_label(resolve_clone(room), strand).taint:
        raise PublishError(
            f"publish: this strand has read untrusted text, so it cannot push. "
            f"Commit your work, then send your opener a clean letter that names "
            f"this checkout:\n"
            f"  send --to <opener> --clean --kind done --ref {name}\n"
            f"The opener reads the diff and publishes from its own room. The branch "
            f"is {branch} at {commit} in {checkout}."
        )

    email = _commit_identity(room)[1]
    others = _other_authors(checkout, branch, email)
    if others:
        raise PublishError(
            f"publish: {branch} carries commits whose author is not {email}:\n"
            + "".join(f"  {line}\n" for line in others)
            + "Rewrite them under this home's author, or leave them out."
        )

    source = _declared(home, thread, name)
    args, env, lane, note = push_command(source, branch, load_config(home.root).forge_token_file)
    try:
        proc = subprocess.run(
            ["git", *args], cwd=checkout, env=env, capture_output=True, text=True,
            timeout=_PUSH_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise PublishError(f"publish: the push did not finish in {_PUSH_TIMEOUT}s") from exc
    if proc.returncode != 0:
        # git's own words, unchanged: a paraphrase turns a refusal into a guess.
        raise PublishError((proc.stderr or proc.stdout).rstrip() or "publish: push failed")
    # The push went to a URL, so git moved no remote-tracking ref.
    git(checkout, "update-ref", f"refs/remotes/origin/{branch}", commit)
    data = {"thread": thread, "repo": name, "branch": branch, "commit": commit,
            "strand": strand}
    gen = _gen(facts, strand)
    if gen is not None:
        data["gen"] = gen
    append(home, Fact(kind="branch.published", by=f"strand:{strand}", data=data,
                      id=f"published:{thread}:{name}:{commit}"))
    return Published(thread, name, branch, commit, lane, note)


def _arguments(parser) -> None:
    parser.add_argument("name", help="the repo's directory name in the room")
    parser.add_argument("--room")


def cmd_publish(args) -> int:
    from .verbs_loom import _room
    try:
        done = publish(_room(args.room), args.name)
    except (PublishError, SelfError, ValueError, OSError) as exc:
        sys.stderr.write(str(exc).rstrip("\n") + "\n")
        return 1
    if done.note:
        sys.stderr.write(done.note + "\n")
    sys.stdout.write(f"{done.branch} {done.commit}\n")
    return 0


def add_publish_parser(subparsers) -> None:
    parser = subparsers.add_parser("publish")
    _arguments(parser)
    parser.set_defaults(func=cmd_publish)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime publish")
    _arguments(parser)
    return cmd_publish(parser.parse_args(argv))
