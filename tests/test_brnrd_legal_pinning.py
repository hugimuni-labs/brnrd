"""The rendered legal page and its pinned plain text must not drift apart.

``brnrd.terms`` hashes a plain-text file and writes that sha256 onto every
acceptance row, so the hash is only evidence if the file is genuinely what
the user read. Nothing but this test connects the two: the document a user
sees is a Svelte component, the pin is a ``.txt``, and a Svelte edit cannot
know it invalidated a hash.

So: re-extract the visible text of the ``LEGAL-TEXT`` region from the
component and compare it, word for word, against the pin. A one-word edit
to either side fails here, which is the point — repinning is then a
deliberate act (and, if the change is material, a version bump with a new
file beside the old one, never an edit in place).

The extractor is deliberately dumb and strict: it refuses to guess. An
unresolvable ``{expression}`` inside the region raises rather than being
dropped, because silently dropping it would let a whole clause vanish from
the comparison while both sides still "matched".
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from brnrd import terms

_FRONTEND_ROUTES = Path(__file__).resolve().parents[1] / "src" / "frontend" / "src" / "routes"

_PAGES = {
    terms.DOC_TOS: _FRONTEND_ROUTES / "terms" / "+page.svelte",
    terms.DOC_HOSTED: _FRONTEND_ROUTES / "beta-hosted-execution" / "+page.svelte",
}

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG = re.compile(r"<[^>]+>", re.DOTALL)
_EXPR = re.compile(r"\{([^{}]*)\}")
_SCRIPT_CONST = re.compile(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*'([^']*)'")
_FALLBACK = re.compile(r"\?\?\s*'([^']*)'\s*$")


def _region(source: str, kind: str) -> str:
    begin = f"<!-- LEGAL-TEXT:BEGIN {kind}"
    end = f"<!-- LEGAL-TEXT:END {kind} -->"
    start = source.index(begin)
    start = source.index("-->", start) + len("-->")
    return source[start : source.index(end)]


def _resolve_expression(expr: str, consts: dict[str, str]) -> str:
    """Render a Svelte ``{...}`` the way a browser would, or refuse."""
    expr = expr.strip()
    fallback = _FALLBACK.search(expr)
    if fallback is not None:
        # ``{status?.terms_version ?? '2026-07-08'}`` — the literal is what a
        # reader sees before the fetch resolves, and the server value it is
        # replaced by is asserted equal to it in ``test_pinned_version_...``.
        return fallback.group(1)
    if expr in consts:
        return consts[expr]
    raise AssertionError(
        f"legal text contains an expression this extractor cannot render: {{{expr}}}. "
        "Inline the value, or give it a `?? 'literal'` fallback."
    )


def extract_document_text(source: str, kind: str) -> str:
    """The visible text of a page's ``LEGAL-TEXT`` region."""
    consts = dict(_SCRIPT_CONST.findall(source.split("</script>", 1)[0]))
    body = _COMMENT.sub(" ", _region(source, kind))
    # Tags go before expressions: `href={resolve('/terms')}` is markup, not
    # text, and only the expressions that survive tag-stripping are ones the
    # reader actually sees rendered.
    body = _TAG.sub(" ", body)
    body = _EXPR.sub(lambda m: _resolve_expression(m.group(1), consts), body)
    return html.unescape(body)


_ORPHAN_PUNCT = re.compile(r"\s+([,.;:!?])")


def _words(text: str) -> list[str]:
    """Both sides reduced to the same comparable word stream.

    Stripping ``<strong>`` out of "brnrd.dev</strong>:" leaves a space the
    reader never saw, and the pin is hand-formatted for a human to read, so
    neither line breaks nor a space before punctuation may count as a
    difference. Anything else does.
    """
    return _ORPHAN_PUNCT.sub(r"\1", " ".join(text.split())).split()


@pytest.mark.parametrize("kind", sorted(_PAGES))
def test_pinned_text_matches_the_rendered_page(kind: str) -> None:
    rendered = _words(extract_document_text(_PAGES[kind].read_text(encoding="utf-8"), kind))
    pinned = _words(terms.current(kind).text)
    assert rendered == pinned, (
        f"the {kind} page and {terms.current(kind).path.name} disagree. "
        "The page is what the user reads; the pin is what the acceptance record "
        "hashes. Re-pin the text (new file + version bump if material)."
    )


@pytest.mark.parametrize("kind", sorted(_PAGES))
def test_pinned_version_matches_the_page(kind: str) -> None:
    """The version the page shows is the version the record writes down."""
    source = _PAGES[kind].read_text(encoding="utf-8")
    assert terms.current(kind).version in extract_document_text(source, kind)


def test_every_pinned_file_is_recoverable_from_its_hash() -> None:
    """The whole point: a stored sha256 must resolve back to a document."""
    for kind in _PAGES:
        doc = terms.current(kind)
        assert terms.text_for_sha256(doc.sha256) == doc.text


def test_unknown_hash_recovers_nothing_rather_than_guessing() -> None:
    assert terms.text_for_sha256("0" * 64) is None
    assert terms.text_for_sha256("") is None


def test_extractor_refuses_an_expression_it_cannot_render() -> None:
    source = (
        "<script>const A = 'x';</script>"
        "<!-- LEGAL-TEXT:BEGIN tos --><p>{someRuntimeThing}</p><!-- LEGAL-TEXT:END tos -->"
    )
    with pytest.raises(AssertionError, match="cannot render"):
        extract_document_text(source, terms.DOC_TOS)
