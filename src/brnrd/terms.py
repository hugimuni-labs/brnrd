"""The legal documents a user can accept, pinned so the record can reproduce them.

An acceptance record that names a version but cannot produce the text is not
evidence — the version is a label the operator controls, and the document it
labels is a Svelte component git can change underneath it. ``hosted_terms_version``
proved that in production: one version string, ``2026-07-08``, has already named
two materially different drafts of ``/beta-hosted-execution``.

So a ``TermsAcceptance`` row stores both, and they answer different questions:

* ``version`` — the operator's label. It decides **re-consent**: a user is asked
  again when the current version differs from the one they accepted. Section 15
  of the ToS makes that promise explicitly ("the version and date at the top of
  this page always identify the current text"), so a typo fix must not re-prompt
  the world.
* ``sha256`` — the evidence. It records **which bytes** were on the page at the
  moment of acceptance, and ``text_for_sha256`` turns it back into the document.

The pinned text lives here rather than in the frontend because the server is
what writes the record, and a record whose evidence depends on a separate
build artifact is a record with a dependency it cannot check.
``tests/test_brnrd_legal_pinning.py`` re-extracts each page's ``LEGAL-TEXT``
region and fails if the page and its pin disagree by one word.

**Never edit a pinned file in place.** Every ``sha256`` ever written must stay
resolvable, so a new text is a new file beside the old one — and, if the change
is material, a new version in ``_CURRENT`` too.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DOC_TOS = "tos"
DOC_HOSTED = "hosted-execution"

_LEGAL_DIR = Path(__file__).parent / "legal"

# kind -> (current version label, pinned filename).
#
# ``hosted-execution``'s label predates content pinning: it was minted on
# 2026-07-08 and deliberately not bumped when the text was redrafted (see the
# non-bump note in the page source, and #664 for why a bump prompts nobody
# today). Its pin is therefore the text as of 2026-07-26, and acceptances
# recorded before that date carry no hash at all — see ``migrations``.
#
# ``tos``'s label survives the ``-r2`` repin deliberately. #773 published
# /legal-notice and /privacy right after #735 pinned the page, and corrected
# the two sentences that had called both "not yet published". A corrected
# cross-reference changes no right or obligation, section 15 re-prompts (and
# owes thirty days' dashboard notice) only for material changes, and #735
# gates login on this version — so a bump here would re-prompt every user
# over two links. The old pin stays beside the new one, and ``sha256`` on the
# acceptance row says which of the two texts a user actually read.
_CURRENT: dict[str, tuple[str, str]] = {
    DOC_TOS: ("2026-07-24", "tos-2026-07-24-r2.txt"),
    DOC_HOSTED: ("2026-07-08", "hosted-execution-2026-07-08.txt"),
}

# Where a user goes to read a document and accept it.
_ACCEPT_PATH = {DOC_TOS: "/terms", DOC_HOSTED: "/beta-hosted-execution"}


@dataclass(frozen=True)
class TermsDocument:
    kind: str
    version: str
    path: Path

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def accept_path(self) -> str:
        return _ACCEPT_PATH[self.kind]


def kinds() -> tuple[str, ...]:
    return tuple(_CURRENT)


def current(kind: str) -> TermsDocument:
    """The document a user is asked to accept today."""
    version, filename = _CURRENT[kind]
    return TermsDocument(kind=kind, version=version, path=_LEGAL_DIR / filename)


@lru_cache(maxsize=1)
def _by_sha256() -> dict[str, str]:
    """Every pinned text, indexed by its own hash — current and superseded."""
    index: dict[str, str] = {}
    for path in sorted(_LEGAL_DIR.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        index[hashlib.sha256(text.encode("utf-8")).hexdigest()] = text
    return index


def text_for_sha256(sha256: str) -> str | None:
    """The exact document behind a recorded hash, or ``None`` if unpinned.

    ``None`` is a real answer, not a failure: acceptances recorded before this
    module existed carry an empty hash, and saying "the text is not
    recoverable" is the honest report for them.
    """
    if not sha256:
        return None
    return _by_sha256().get(sha256)
