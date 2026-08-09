"""The `?pack=` / token-relay innerHTML sink (A-2).

`GET /r/{token}` and `GET /r?pack=<url>` both end up running
`src/brr/diffense/template.html`'s inline app script against a review pack
that is not trusted input: the token-relay pack is whatever a producer's
daemon sent (`render.py`'s own docstring), and the `?pack=` shell fetches
whatever URL the caller named. That script builds a few DOM nodes via
`el(tag, { html: "..." })` — a deliberate `innerHTML` sink used to mix a
literal bit of markup (the blinking cursor, a `+`/`-` highlight span) with
pack-derived text — and until this fix, the pack-derived part went in
unescaped: a pack whose `metadata.pr.repo` or a card's `stats` value
contained `<img src=x onerror=...>` would execute it in the reviewer's
browser.

These tests run the *actual* shipped script (extracted verbatim from HTML
`render()` produces, the same function `render.py` and the `/r/{token}`
route call) against a minimal DOM stub in Node — see
`tests/support/diffense_render_harness.js` for why a real interpreter
beats reimplementing the escaping logic in Python. Skipped, not failed,
where `node` isn't on PATH — the real path is worth more than a stub when
available, but this repo's backend CI job (`.github/workflows/ci.yml`)
doesn't explicitly install Node, only the frontend job does.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from brr.diffense.render import render

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "tests" / "support" / "diffense_render_harness.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _run_harness(html_path: Path, initial_hash: str = "") -> str:
    proc = subprocess.run(
        ["node", str(HARNESS), str(html_path), initial_hash],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"harness failed: {proc.stderr}"
    return json.loads(proc.stdout)["html"]


def _malicious_pack() -> dict:
    return {
        "metadata": {
            "pr": {
                "repo": "<img src=x onerror=alert(1)>evil/repo",
                "number": "<b>99</b>",
            }
        },
        "cards": [
            {
                "id": "c1",
                "kind": "code-review",
                "identity": {"label": "c1"},
                "stats": {"payload": "<img src=x onerror=alert(2)>+5", "clean": "-3"},
                "locator": {"forge": "javascript:alert(3)//"},
            }
        ],
    }


def test_malicious_repo_and_number_cannot_inject_markup_into_the_title(tmp_path):
    html_path = tmp_path / "pack.html"
    html_path.write_text(render(_malicious_pack()), encoding="utf-8")

    dom = _run_harness(html_path)

    assert not re.search(r"<img\b", dom), f"raw <img survived into the DOM: {dom}"
    assert "&lt;img src=x onerror=alert(1)&gt;evil/repo" in dom
    assert "&lt;b&gt;99&lt;/b&gt;" in dom


def test_malicious_stats_value_cannot_inject_markup_via_rows(tmp_path):
    html_path = tmp_path / "pack.html"
    html_path.write_text(render(_malicious_pack()), encoding="utf-8")

    dom = _run_harness(html_path, "#c1")

    assert not re.search(r"<img\b", dom), f"raw <img survived into the DOM: {dom}"
    assert "&lt;img src=x onerror=alert(2)&gt;" in dom


def test_the_plus_minus_highlight_still_renders_around_escaped_text(tmp_path):
    """The fix escapes before highlighting; confirm the feature it must not
    break — a legitimate `+N` / `-N` stat still gets its span."""
    html_path = tmp_path / "pack.html"
    html_path.write_text(render(_malicious_pack()), encoding="utf-8")

    dom = _run_harness(html_path, "#c1")

    assert '<span class="add">+5</span>' in dom
    assert '<span class="del">-3</span>' in dom


def test_a_javascript_uri_locator_never_becomes_a_clickable_link(tmp_path):
    html_path = tmp_path / "pack.html"
    html_path.write_text(render(_malicious_pack()), encoding="utf-8")

    dom = _run_harness(html_path, "#c1")

    assert "javascript:" not in dom
    assert "<a " not in dom


def test_an_ordinary_pack_still_renders_its_real_content_cleanly(tmp_path):
    """The escaping change must not mangle legitimate data — the other half
    of "least-breaking" from the task: nothing double-escapes, nothing
    that used to show up stops showing up."""
    pack = {
        "metadata": {"pr": {"repo": "hugimuni-labs/brnrd", "number": 1284}},
        "cards": [
            {
                "id": "c1",
                "kind": "code-review",
                "identity": {"label": "render.py"},
                "stats": {"files": "+5", "lines": "-120"},
                "locator": {"forge": "https://github.com/hugimuni-labs/brnrd/pull/1284"},
            }
        ],
    }
    html_path = tmp_path / "pack.html"
    html_path.write_text(render(pack), encoding="utf-8")

    dom = _run_harness(html_path, "#c1")

    assert "hugimuni-labs/brnrd" in dom
    assert "#1284" in dom
    assert '<span class="add">+5</span>' in dom
    assert '<span class="del">-120</span>' in dom
    assert 'href="https://github.com/hugimuni-labs/brnrd/pull/1284"' in dom
    # Nothing in this pack contains &, <, > — so nothing should have grown
    # an entity that wasn't there, which would signal double-escaping.
    assert "&amp;" not in dom and "&lt;" not in dom and "&gt;" not in dom
