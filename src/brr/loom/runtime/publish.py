"""``publish``: the one door a project checkout's commits leave the machine through.

The strand's label is read here, at push time, because a body can become
tainted after it starts. The push goes to the source the seat declares on
main, never to the checkout's own ``origin``: a body can rewrite its
checkout's remotes, and the home's token would follow them.

A tainted strand cannot push, so its opener does it in two calls. ``--from
<thread>`` copies the worker's branch into the opener's own checkout, under
the worker's branch name, and pushes nothing. ``--from <thread> --commit
<sha>`` pushes that copy if it is still at the commit the opener named. The
push never reads the worker's checkout, so a commit the worker adds after
the opener read the diff does not ride along.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .config import ConfigError, load_config, resolve_secret
from .home import check_thread_id, home_of_room, strand_of_room
from .ledger import Fact, append, read_facts
from .selfrepo import SelfError, _env_without_pin, _segment, git, resolve_clone

_PUSH_TIMEOUT = 600
_SHA = re.compile(r"[0-9a-f]{7,64}")
_TOKEN_ENV = "BRNRD_FORGE_TOKEN"
# git runs a helper that starts with ``!`` through sh. The value comes only
# from the push environment, never argv, config or a file written by us.
_TOKEN_HELPER = (
    '!f() { test "$1" = get || exit 0; echo username=x-access-token; '
    f'printf "password=%s\\n" "${_TOKEN_ENV}"; }}; f'
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


def push_command(source: str, branch: str, forge_token: str | None,
                 ) -> tuple[list[str], dict[str, str], str, str]:
    """``(git arguments, environment, lane, note)`` for one push of ``branch``."""
    refspec = f"refs/heads/{branch}:refs/heads/{branch}"
    return remote_command(source, ["push", "--", source, refspec], forge_token)


def remote_command(source: str, tail: list[str], forge_token: str | None,
                   ) -> tuple[list[str], dict[str, str], str, str]:
    """``(git arguments, environment, lane, note)`` for one git call that
    talks to ``source``; ``tail`` is the call itself (``push``, ``ls-remote``,
    ``fetch``), so every read of the source goes out the push's lane.

    With a token, git reads no machine or user config: a ``url.*.insteadOf``
    there would turn the https push into an ssh push under the machine
    owner's key, and a helper there would answer before the token's. The
    token's helper is scoped to the source's host, so a redirect to another
    host is handed nothing.
    """
    env = _plain_env()
    if _is_local(source):
        return list(tail), env, "local", ""
    if forge_token:
        parts = urlsplit(source)
        if parts.scheme != "https" or not parts.hostname:
            raise PublishError(
                f"publish: the home's token is only sent over https, and this repo's "
                f"source is {source}. Pushing it would go out under this machine's own "
                f"keys. Declare the https URL in the seat's repo: line."
            )
        try:
            token = resolve_secret(forge_token)
        except ConfigError as exc:
            raise PublishError(f"publish: forge_token {forge_token}: {exc}") from None
        host = parts.hostname + (f":{parts.port}" if parts.port else "")
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env[_TOKEN_ENV] = token
        args = [
            "-c", "credential.helper=",
            "-c", f"credential.https://{host}.helper=",
            "-c", f"credential.https://{host}.helper={_TOKEN_HELPER}",
            *tail,
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
    note = ("publish: no forge_token in loom/config.toml; used this machine's "
            f"own git setup (git user {who or 'unset'})")
    return [*args, *tail], env, "machine", note


def read_command(root: Path, source: str, tail: list[str],
                 ) -> tuple[list[str], dict[str, str] | None]:
    """``(git arguments, environment)`` for a loom read of ``source``.

    An https source goes out the push's token lane when the home has a
    token, so a private repo the bot can push is one the loom can read. Any
    other source, or no token, reads as before: ``env`` is ``None``.
    """
    token = load_config(root).forge_token
    if not token or urlsplit(source).scheme != "https":
        return list(tail), None
    try:
        args, env, _lane, _note = remote_command(source, tail, token)
    except PublishError as exc:
        raise SelfError(str(exc)) from None
    return args, env


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


def _uncovered(checkout: Path, revisions: list[str], covered: list[str]) -> list[tuple[str, str]]:
    """The one reachability comparison: commits not held by any covered ref."""
    text = git(checkout, "log", "--format=%H%x09%s", *revisions, "--not", *covered).stdout
    return [tuple(line.split("\t", 1)) for line in text.splitlines() if "\t" in line]


def _ahead(checkout: Path, revisions: list[str], covered: list[str]) -> int:
    """The count the bounce and the send guard have always used: a comparison
    git cannot make (a missing branch) counts as nothing ahead, as before."""
    try:
        return len(_uncovered(checkout, revisions, covered))
    except SelfError:
        return 0


def unpublished(facts, thread: str, name: str, checkout: Path) -> tuple[str, int] | None:
    """``(tip, commits)`` when ``loom/<thread>`` in ``checkout`` holds commits
    that no ``branch.published`` fact and no ``origin`` ref covers. Else none.
    """
    if not (checkout / ".git").exists():
        return None
    tip = _tip(checkout, f"refs/heads/loom/{thread}")
    if tip is None:
        return None
    known = [str((fact.data or {}).get("commit")) for fact in facts
             if fact.kind == "branch.published" and (fact.data or {}).get("thread") == thread
             and (fact.data or {}).get("repo") == name]
    if tip in known:
        return None
    # A published commit this checkout never fetched is not a name git knows.
    held = [commit for commit in known if _tip(checkout, commit)]
    ahead = _ahead(checkout, [tip], [*held, "--remotes=origin"])
    return (tip, ahead) if ahead else None


def unlanded(home, facts, strand: str, thread: str) -> list[str]:
    """What a room holds that has not left it, one line per repo. Read for a
    dead body's bounce; the room is kept, so each line names where to look."""
    from .adapters import repos, seat_text
    room = home.room(strand).resolve()
    lines: list[str] = []
    try:
        clone = room / "self"
        if (clone / ".git").exists():
            ahead = _ahead(clone, [f"refs/heads/strand/{strand}"], ["--remotes=origin"])
            if ahead:
                lines.append(f"self: {ahead} commit(s) on strand/{strand} not on main, in {clone}")
        for name, _source in repos(seat_text(home, thread)):
            left = unpublished(facts, thread, name, room / name)
            if left:
                lines.append(
                    f"repo {name}: {left[1]} commit(s) on loom/{thread} not published, "
                    f"tip {left[0][:12]}, in {room / name}; its opener publishes them "
                    f"with `publish {name} --from {thread}`"
                )
    except (SelfError, OSError, ValueError) as exc:
        lines.append(f"unpublished commits not read: {exc}")
    return lines


def _gen(facts, strand: str) -> int | None:
    found = None
    for fact in facts:
        if fact.kind == "lease" and (fact.data or {}).get("strand") == strand:
            found = (fact.data or {}).get("gen")
    return found if isinstance(found, int) else None


@dataclass(frozen=True)
class Fetched:
    """A worker's branch, copied into the opener's checkout and not pushed."""
    thread: str
    repo: str
    branch: str
    commit: str
    #: Commits on the branch that no ``origin`` ref holds.
    ahead: int
    checkout: Path


@dataclass(frozen=True)
class _Caller:
    home: object
    facts: list
    room: Path
    strand: str
    thread: str
    name: str
    checkout: Path


def _caller(room: Path | str, name: str) -> _Caller:
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
    return _Caller(home, facts, room, strand, thread, name, checkout)


def _tainted(who: _Caller) -> bool:
    from .port import _current_label
    return bool(_current_label(resolve_clone(who.room), who.strand).taint)


def _tip(checkout: Path, ref: str) -> str | None:
    proc = git(checkout, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _opener(home, thread: str) -> str | None:
    from .lifecycle import read_thread
    opener = str(read_thread(home, thread).get("opened-by", ""))
    return opener.removeprefix("thread:") if opener.startswith("thread:") else None


def _opened(home, own: str, worker: str) -> bool:
    """``own`` opened ``worker``, or opened the thread that did, and so on up."""
    seen: set[str] = set()
    current: str | None = worker
    while current and current not in seen:
        seen.add(current)
        current = _opener(home, current)
        if current == own:
            return True
    return False


def _worker_checkout(who: _Caller, worker: str) -> Path | None:
    """The newest room of ``worker`` that has this repo. A molt's successor
    starts from its predecessor's branch, so the newest holds the most."""
    from .project import fold
    strands = [str(fact.data["strand"]) for fact in fold(who.facts).accepted
               if fact.kind == "lease" and str(fact.data.get("thread")) == worker]
    rooms = [who.home.room(strand).resolve() / who.name for strand in reversed(strands)]
    rooms = [path for path in rooms if (path / ".git").exists()]
    # A body that died is followed by a fresh room that never saw its commits.
    for path in rooms:
        if unpublished(who.facts, worker, who.name, path):
            return path
    return rooms[0] if rooms else None


def _for_worker(who: _Caller, worker: str) -> tuple[str, str]:
    """``(branch, source)`` once this caller may publish ``worker``'s branch."""
    try:
        check_thread_id(worker)
    except ValueError as exc:
        raise PublishError(f"publish: {exc}") from exc
    if worker == who.thread:
        raise PublishError(f"publish: {worker} is this room's own thread; leave out --from")
    if _tainted(who):
        raise PublishError(
            "publish: this strand has read untrusted text, so it cannot push, "
            "for itself or for a worker."
        )
    if not _opened(who.home, who.thread, worker):
        raise PublishError(
            f"publish: thread {who.thread} did not open {worker}; a worker's branch "
            f"is published by the thread that opened it"
        )
    source = _declared(who.home, worker, who.name)
    try:
        own = _declared(who.home, who.thread, who.name)
    except PublishError:
        own = None
    if own != source:
        raise PublishError(
            f"publish: {who.checkout} is not a checkout of {worker}'s {who.name} "
            f"({source}); this thread's seat declares {own or 'no repo of that name'}"
        )
    return f"loom/{worker}", source


def fetch_from(room: Path | str, name: str, worker: str) -> Fetched:
    """Copy ``loom/<worker>`` from the worker's room into this room's checkout.

    Nothing is pushed. The copy keeps the worker's branch name: one thread,
    one branch, and the worker's next clean body starts from it once it is
    on the source.
    """
    who = _caller(room, name)
    branch, _source = _for_worker(who, worker)
    theirs = _worker_checkout(who, worker)
    if theirs is None or _tip(theirs, f"refs/heads/{branch}") is None:
        raise PublishError(f"publish: no room of thread {worker} has {name} on {branch}")
    git(who.checkout, "fetch", "--no-tags", "--", str(theirs),
        f"+refs/heads/{branch}:refs/heads/{branch}")
    commit = _tip(who.checkout, f"refs/heads/{branch}")
    assert commit is not None
    ahead = git(who.checkout, "rev-list", "--count", f"refs/heads/{branch}",
                "--not", "--remotes=origin").stdout.strip()
    return Fetched(worker, name, branch, commit, int(ahead or 0), who.checkout)


def publish(room: Path | str, name: str, *, worker: str | None = None,
            commit: str | None = None) -> Published:
    """Push ``loom/<thread>`` of ``<room>/<name>`` to its declared source.

    Refuses a tainted strand, a branch carrying another author, and a repo
    the thread's seat does not declare. Records ``branch.published`` after
    the push, never before. With ``worker``, the branch is that thread's as
    ``fetch_from`` left it here, and ``commit`` must name its tip.
    """
    from .loom import _commit_identity

    who = _caller(room, name)
    checkout, strand = who.checkout, who.strand
    if worker is not None:
        branch, source = _for_worker(who, worker)
        thread = worker
        tip = _tip(checkout, f"refs/heads/{branch}")
        if tip is None or not commit or not _SHA.fullmatch(commit):
            raise PublishError(
                f"publish: fetch {branch} and read it before pushing it:\n"
                f"  publish {name} --from {worker}"
            )
        if _tip(checkout, commit) != tip:
            raise PublishError(
                f"publish: {branch} in this room is at {tip}, not {commit}. "
                f"Push the commit you read, or fetch again with: publish {name} --from {worker}"
            )
        commit = tip
    else:
        thread, branch = who.thread, f"loom/{who.thread}"
        if commit:
            raise PublishError("publish: --commit goes with --from")
        commit = _tip(checkout, f"refs/heads/{branch}")
        if commit is None:
            raise PublishError(f"publish: {checkout} has no branch {branch}")
        if _tainted(who):
            opener = _opener(who.home, thread)
            raise PublishError(
                f"publish: this strand has read untrusted text, so it cannot push. "
                f"Commit your work, then send your opener a clean letter that names "
                f"this checkout:\n"
                f"  send --to {'thread:' + opener if opener else '<opener>'} "
                f"--clean --kind done --ref {name}\n"
                f"The opener reads the diff and publishes it with "
                f"`publish {name} --from {thread}`. The branch "
                f"is {branch} at {commit} in {checkout}."
            )
        source = _declared(who.home, thread, name)

    from .guards import for_room, violations
    broken = violations(for_room(who.room, thread)[0], who.room, repo=name, commit=commit)
    if broken:
        raise PublishError(f"publish: a path guard refuses {branch} at {commit}:\n" + "\n".join(broken))

    email = _commit_identity(who.room)[1]
    others = _other_authors(checkout, branch, email)
    if others:
        raise PublishError(
            f"publish: {branch} carries commits whose author is not {email}:\n"
            + "".join(f"  {line}\n" for line in others)
            + "Rewrite them under this home's author, or leave them out."
        )

    args, env, lane, note = push_command(source, branch,
                                         load_config(who.home.root).forge_token)
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
    gen = _gen(who.facts, strand)
    if gen is not None:
        data["gen"] = gen
    append(who.home, Fact(kind="branch.published", by=f"strand:{strand}", data=data,
                          id=f"published:{thread}:{name}:{commit}"))
    return Published(thread, name, branch, commit, lane, note)


def _remote(who: _Caller, source: str, tail: list[str]) -> tuple[str, str]:
    """``(stdout, note)`` of one git call to ``source`` over the push's lane."""
    args, env, _lane, note = remote_command(source, tail,
                                            load_config(who.home.root).forge_token)
    try:
        proc = subprocess.run(["git", *args], cwd=who.checkout, env=env,
                              capture_output=True, text=True, timeout=_PUSH_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise PublishError(f"publish: git {tail[0]} did not finish in {_PUSH_TIMEOUT}s") from exc
    if proc.returncode != 0:
        raise PublishError((proc.stderr or proc.stdout).rstrip() or f"publish: git {tail[0]} failed")
    return proc.stdout, note


def pr_url(source: str, number: int) -> str:
    """The pull request's page when the source is an https URL; else empty."""
    if urlsplit(source).scheme != "https":
        return ""
    return source.rstrip("/").removesuffix(".git") + f"/pull/{number}"


def _pr_head(who: _Caller, source: str, number: int) -> tuple[str, str]:
    """``(head, note)``: the source's ``refs/pull/N/head``, once it is a
    commit publish pushed for this thread's repo."""
    out, note = _remote(who, source, ["ls-remote", "--", source, f"refs/pull/{number}/head"])
    head = out.split("\t", 1)[0].strip() if out.strip() else ""
    if not head:
        raise PublishError(f"publish: {source} has no pull request {number} "
                           f"(no refs/pull/{number}/head)")
    pushed = {str((fact.data or {}).get("commit")) for fact in who.facts
              if fact.kind == "branch.published" and (fact.data or {}).get("thread") == who.thread
              and (fact.data or {}).get("repo") == who.name}
    if head not in pushed:
        raise PublishError(
            f"publish: pull request {number}'s head is {head[:12]}, which publish never "
            f"pushed for thread {who.thread}'s {who.name}; a receipt names this thread's "
            f"own published work"
        )
    return head, note


def _merge_of(who: _Caller, source: str, head: str, number: int) -> str:
    """The merge commit on the source's default branch whose second parent is ``head``."""
    _remote(who, source, ["fetch", "--no-tags", "--", source, "HEAD"])
    main = _tip(who.checkout, "FETCH_HEAD")
    if main is None:
        raise PublishError(f"publish: fetching {source} left no FETCH_HEAD")
    text = git(who.checkout, "log", "--merges", "--format=%H %P%x09%s", main, f"^{head}").stdout
    for line in text.splitlines():
        hashes, _tab, subject = line.partition("\t")
        commits = hashes.split()
        if len(commits) >= 3 and commits[2] == head:
            if not re.search(rf"#{number}\b", subject):
                raise PublishError(
                    f"publish: {commits[0][:12]} merges {head[:12]}, but its subject does not "
                    f"name #{number}: {subject}"
                )
            return commits[0]
    raise PublishError(
        f"publish: no merge commit with second parent = head {head[:12]} on {source}'s "
        f"default branch. A squash or rebase merge leaves none; merge pull request "
        f"{number} with a merge commit, or record nothing."
    )


@dataclass(frozen=True)
class Claimed:
    """A pull request receipt the source has shown, now a fact."""
    fact: Fact
    note: str = ""


def claim_pr(room: Path | str, name: str, number: int, *, merged: bool = False) -> Claimed:
    """``pr.opened`` (or with ``merged``, ``pr.merged``) for this room's own thread.

    git on the declared source is the evidence: the pull request's head ref
    must be a commit this thread published, and a merge needs a merge commit
    on the source's default branch with that head as its second parent.
    """
    who = _caller(room, name)
    if number <= 0:
        raise PublishError(f"publish: {number} is not a pull request number")
    source = _declared(who.home, who.thread, name)
    head, note = _pr_head(who, source, number)
    data = {"thread": who.thread, "repo": name, "number": number, "head": head}
    if merged:
        data["merge_commit"] = _merge_of(who, source, head, number)
        fact = Fact(kind="pr.merged", by=f"strand:{who.strand}", data=data,
                    id=f"pr.merged:{who.thread}:{name}:{number}")
    else:
        data["url"] = pr_url(source, number)
        fact = Fact(kind="pr.opened", by=f"strand:{who.strand}", data=data,
                    id=f"pr.opened:{who.thread}:{name}:{number}:{head}")
    return Claimed(append(who.home, fact), note)


def _arguments(parser) -> None:
    parser.add_argument("name", help="the repo's directory name in the room")
    parser.add_argument("--room")
    parser.add_argument("--from", dest="worker", metavar="THREAD",
                        help="a thread this one opened: copy its branch here, push nothing")
    parser.add_argument("--commit", help="with --from: push the copy, if it is at this commit")
    parser.add_argument("--pr", type=int, metavar="N",
                        help="record pull request N of this thread's pushed branch as opened")
    parser.add_argument("--merged", type=int, metavar="N",
                        help="record pull request N as merged, by its merge commit on the source")


def cmd_publish(args) -> int:
    from .verbs_loom import _room
    try:
        if args.pr is not None or args.merged is not None:
            if args.worker or args.commit or (args.pr is not None and args.merged is not None):
                raise PublishError("publish: --pr and --merged each go alone, for this thread")
            got = claim_pr(_room(args.room), args.name,
                           args.pr if args.merged is None else args.merged,
                           merged=args.merged is not None)
            if got.note:
                sys.stderr.write(got.note + "\n")
            data = got.fact.data
            sys.stdout.write(f"{got.fact.kind} {data['repo']}#{data['number']} "
                             f"{data.get('merge_commit') or data['head']}"
                             + (f" {data['url']}" if data.get("url") else "") + "\n")
            return 0
        if args.worker and not args.commit:
            got = fetch_from(_room(args.room), args.name, args.worker)
            sys.stdout.write(
                f"{got.branch} {got.commit} fetched, not pushed: {got.ahead} commit(s) "
                f"the source does not have.\n"
                f"Read them: git -C {got.checkout} log -p {got.branch} --not --remotes=origin\n"
                f"Then: publish {got.repo} --from {got.thread} --commit {got.commit}\n"
            )
            return 0
        done = publish(_room(args.room), args.name, worker=args.worker, commit=args.commit)
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
