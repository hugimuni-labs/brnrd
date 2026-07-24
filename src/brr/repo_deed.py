"""The deed — a README seeded into every repo brnrd founds.

brnrd creates exactly two personal artifacts on the user's behalf: the
dominion (the resident's working memory, born silently by
``account.resolve_context``) and the knowledge base (what the work taught,
born by ``knowledge.ensure_checkout`` or ``home_link.link_home``). The
ownership mechanics were always user-correct — user's credentials, user's
GitHub login, forced-private, brnrd's App owns nothing — but every one of
those facts was true and unsaid: the user first met their agent's memory
as an unexplained path string.

The deed is the artifact that does the ceremony's work when nobody is
watching: a README written at birth stating what the repo is, who writes
and reads it, where it lives and what the cloud mirrors, and how to
leave. Deliberately **self-contained** rather than a pointer to hosted
docs — the repo must explain itself to a stranger reading it in a year
(including the owner's own future self auditing what an AI has been
writing into their account) with no brnrd running at all; a hosted link
rots and hides the answer behind brnrd's uptime.

Write-if-absent, checked only at true birth (a fresh ``git init``) or at
the link seam: an owner who edits or deletes their deed is exercising
exactly the ownership the deed asserts, and is never overwritten.

The bounded-mirror wording below mirrors ``SECURITY.md`` § What dashboard
publishing mirrors (the authority): a connected account mirrors a bounded
render cache of this content — work surface, knowledge pages, run nodes
from the last 14 days — plus six further publish lanes, while these repos
remain the durable copies. It used to say the dashboard kept *only* the
corpus cache; that was one of seven lanes (#417), and a deed written into
an owner's own repo is the wrong place to under-state what leaves.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

DEED_FILENAME = "README.md"

# Fallback commit identity, used only when the plain commit fails (no git
# user configured on the machine) — the deed must still land at birth.
_FALLBACK_IDENT = [
    "-c", "user.name=brnrd",
    "-c", "user.email=brnrd@users.noreply.github.com",
]

_SLOT_TITLES = {
    "dominion": "Your brnrd resident's memory",
    "knowledge": "Your brnrd knowledge base",
}

_WHAT_THIS_IS = {
    "dominion": """\
This repo is the **working memory** of your brnrd resident — the standing
agent that works your repositories. Its notes, plans, run records,
schedule, and self-orientation live here as plain files. brnrd created
this repo for you; **you own it**, in the ordinary git sense: it sits in
your filesystem (and, if linked, under your GitHub login), and nothing in
it is beyond your edit.""",
    "knowledge": """\
This repo is the **knowledge base** your brnrd resident distills from
working your repositories — durable pages recording what each project
taught it: designs, decisions, pitfalls, and plans. brnrd created this
repo for you; **you own it**, in the ordinary git sense: it sits in your
filesystem (and, if linked, under your GitHub login), and nothing in it
is beyond your edit.""",
}

_WHO_WRITES = {
    "dominion": """\
The brnrd daemon commits here after every thought (its capture net), and
the resident reads it on every wake; the dashboard renders parts of it
when you connect an account. You can read and edit anything at any time —
your edits are just commits, and the agent works with what it finds.""",
    "knowledge": """\
The resident writes pages here as work closes out, and reads them back on
later wakes; the dashboard renders them when you connect an account. You
can read, edit, or prune anything — your edits are just commits, and the
agent works with what it finds.""",
}

_SHARED_TAIL = """\
## Where it lives, and what the cloud sees

Local-first: the durable copy is this repo, on your machine. If you link
it to GitHub (`brnrd home link`), the remote is a **private** repo under
*your* GitHub login, created with *your* credentials — brnrd's GitHub App
never owns or holds it, and brnrd refuses to push this content to a
public repo. If you connect a brnrd account, the hosted dashboard keeps a
**bounded render cache** of this content — the authored work surface,
knowledge pages, and run nodes from the last 14 days — alongside other
publish lanes covering runs, quota and review state, and disconnecting
purges it; this repo remains the durable copy either way. `SECURITY.md`
in the brnrd source repo has the full lane-by-lane inventory and is the
authority on those bounds.

## How to leave

It's plain git — no export, no support ticket:

- **Copy it:** `git clone` this repo anywhere.
- **Move it:** `git remote set-url origin <wherever>` — brnrd follows
  your remote config.
- **End it:** delete the repo (and the GitHub remote, if you made one).
"""


def deed_text(slot: str) -> str:
    """Return the full deed README for *slot* (``dominion``|``knowledge``)."""
    title = _SLOT_TITLES[slot]
    return (
        f"# {title}\n\n"
        f"{_WHAT_THIS_IS[slot]}\n\n"
        f"## Who writes here, who reads it\n\n"
        f"{_WHO_WRITES[slot]}\n\n"
        f"{_SHARED_TAIL}"
    )


def founding_commit_message(slot: str) -> str:
    """The birth commit's message — names what was founded, for whom.

    Replaces the anonymous ``brnrd: seed <dirname>`` that used to found
    these repos: a founding commit is the one line of history every later
    reader sees first, so it should say what came into being.
    """
    if slot == "dominion":
        return "brnrd: found this dominion — the resident's working memory, deeded to its owner"
    return "brnrd: found this knowledge base — what the work taught, deeded to its owner"


def write_deed(repo_path: Path, slot: str) -> bool:
    """Write the deed README into *repo_path* iff absent. Returns whether written.

    Never overwrites: an existing README — brnrd's or the owner's — is the
    owner's to keep.
    """
    deed = repo_path / DEED_FILENAME
    if deed.exists():
        return False
    try:
        deed.write_text(deed_text(slot), encoding="utf-8")
    except OSError:
        return False
    return True


def _git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )


def _commit_deed(repo_path: Path, message: str) -> bool:
    """Best-effort commit of the just-written deed. Returns success."""
    if _git(repo_path, "add", DEED_FILENAME).returncode != 0:
        return False
    if _git(repo_path, "commit", "-m", message).returncode == 0:
        return True
    # No committer identity configured — the deed still has to land.
    return _git(repo_path, *_FALLBACK_IDENT, "commit", "-m", message).returncode == 0


def ensure_deed(repo_path: Path, slot: str, *, commit: bool = True) -> bool:
    """Seed *repo_path* with its deed; optionally commit it. Returns whether written.

    - README already present → untouched, nothing committed.
    - Unborn HEAD → the deed commit **is** the founding commit and carries
      :func:`founding_commit_message` (this is where ``"brnrd: seed"`` died).
    - Repo already has history (a dominion the capture net has been
      committing to) → the deed is committed on its own, so a following
      push carries it instead of leaving it stranded in the worktree.
    """
    written = write_deed(repo_path, slot)
    if not written or not commit:
        return written
    if not (repo_path / ".git").exists():
        return written
    has_head = _git(repo_path, "rev-parse", "--verify", "-q", "HEAD").returncode == 0
    message = (
        f"brnrd: add the deed README to this {slot} repo"
        if has_head
        else founding_commit_message(slot)
    )
    _commit_deed(repo_path, message)
    return written
