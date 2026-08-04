"""The shells-and-doors support matrix (#1070 follow-up) — one status
computation, checked against both the doors it describes and the docs
page that used to hand-maintain the same claim and went stale in a day.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from brr import support_matrix

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_INDEX = REPO_ROOT / "docs" / "src" / "content" / "docs" / "index.md"


def _settings(**overrides):
    base = {
        "telegram_bot_token": "",
        "telegram_bot_username": "",
        "github_app_id": "",
        "github_app_private_key_b64": "",
        "whatsapp_access_token": "",
        "whatsapp_phone_number_id": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# --- shipped_status (docs page / self-host truth) ---------------------------


def test_all_six_doors_are_shipped_on_main():
    """#1072 (Signal) and #1074 (WhatsApp) landed after #1070 shipped the
    docs shelf with both tagged ``soon`` — this is the check that would
    have caught the shelf going stale the moment they merged."""
    for door in support_matrix.DOORS:
        assert support_matrix.shipped_status(door) == "live", door.slug


def test_shipped_status_reads_live_code_not_a_memory_of_having_checked():
    unshipped = replace(support_matrix.door("signal"), shipped=lambda: False)
    assert support_matrix.shipped_status(unshipped) == "soon"


def test_module_shipped_answers_false_for_a_non_import_error_too(monkeypatch):
    """``_module_shipped``'s docstring promises a missing/broken module
    answers ``False`` rather than raising — but a module can fail to
    import with more than ``ImportError`` (a bad top-level statement, a
    broken third-party dependency). This function feeds the live
    ``/v1/stats/support`` endpoint, so a narrower except would 500 the
    whole matrix the day some gate's import starts raising something
    else. Regression for that: import_module raising RuntimeError must
    still answer False, not propagate."""
    import importlib

    def _boom(name, *args, **kwargs):
        raise RuntimeError("this module explodes at import time")

    monkeypatch.setattr(importlib, "import_module", _boom)
    assert support_matrix._module_shipped("brr.gates.not_a_real_module") is False


# --- hosted_status (brnrd.dev app landing truth) -----------------------------


def test_hosted_status_is_soon_when_brnrd_dev_has_no_credentials():
    settings = _settings()
    assert support_matrix.hosted_status("telegram", settings) == "soon"
    assert support_matrix.hosted_status("whatsapp", settings) == "soon"
    assert support_matrix.hosted_status("github", settings) == "soon"


def test_hosted_status_is_live_once_brnrd_dev_is_configured():
    settings = _settings(
        telegram_bot_token="t", telegram_bot_username="brnrd_bot",
        whatsapp_access_token="w", whatsapp_phone_number_id="123",
        github_app_id="1", github_app_private_key_b64="key",
    )
    assert support_matrix.hosted_status("telegram", settings) == "live"
    assert support_matrix.hosted_status("whatsapp", settings) == "live"
    assert support_matrix.hosted_status("github", settings) == "live"


def test_hosted_status_needs_every_configured_field_not_just_one():
    settings = _settings(telegram_bot_token="t")  # username still empty
    assert support_matrix.hosted_status("telegram", settings) == "soon"


def test_hosted_status_never_promotes_a_door_that_has_no_hosted_axis():
    """Slack and Signal never touch brnrd.dev's backend at all (self-hosted
    gates, no Settings fields exist for them) — hosted truth mirrors
    shipped truth for these regardless of what garbage a settings object
    might otherwise carry, and the web dashboard is brnrd.dev itself."""
    settings = _settings()
    assert support_matrix.hosted_status("slack", settings) == "live"
    assert support_matrix.hosted_status("signal", settings) == "live"
    assert support_matrix.hosted_status("dashboard", settings) == "live"


def test_hosted_status_never_claims_live_for_code_that_is_not_shipped():
    """The property the maintainer's brief calls out by name: a merged-but-
    unconfigured gate must be visible as exactly that, never silently
    promoted past code that does not even exist yet."""
    unshipped = replace(support_matrix.door("whatsapp"), shipped=lambda: False)
    settings = _settings(whatsapp_access_token="w", whatsapp_phone_number_id="123")
    assert support_matrix.hosted_status(unshipped, settings) == "soon"


# --- shells -------------------------------------------------------------


def test_bundled_shells_are_all_labeled():
    """A third bundled Shell provider with no shelf label fails here first,
    not as a silently-missing row on the landing."""
    bundled = set(support_matrix.bundled_shells())
    labeled = set(support_matrix.SHELL_LABELS)
    assert bundled == labeled, (
        f"bundled shells {bundled} and shelf labels {labeled} disagree — "
        "add the missing label(s) to support_matrix.SHELL_LABELS and to "
        "Landing.svelte's shells column"
    )


# --- the docs shelf must agree with shipped_status ---------------------------


def _docs_door_tags() -> dict[str, bool]:
    """Parse which doors the docs page's hand-written shelf currently tags
    ``soon`` vs plain (== claimed live) — regex over the vendored-SVG
    markdown block, not a markdown parser, since the block is hand-authored
    HTML-in-Markdown (#1070) with no structured data to read instead.
    Matched by label text since the docs shelf has no ``data-slug`` to key
    on. Returns ``{slug: is_tagged_soon}`` for every door slug the page
    mentions."""
    if not DOCS_INDEX.exists():
        pytest.skip("docs/ not present in this checkout")
    text = DOCS_INDEX.read_text(encoding="utf-8")
    items = re.findall(r'<li class="brr-shelf-item">.*?</li>', text, re.DOTALL)
    label_to_slug = {door.label: door.slug for door in support_matrix.DOORS}
    tags: dict[str, bool] = {}
    for item in items:
        label_match = re.search(r'brr-shelf-label">([^<]+)<', item)
        if not label_match:
            continue
        slug = label_to_slug.get(label_match.group(1))
        if slug:
            tags[slug] = "brr-shelf-tag--soon" in item
    return tags


def test_docs_shelf_soon_tags_match_what_is_actually_shipped():
    """#1070's failure mode, made checkable: the docs page hand-typed
    ``soon`` for Signal and WhatsApp on 2026-08-03, and #1072/#1074 shipped
    both gates on 2026-08-03/04 without anyone touching the shelf. This
    test reads ``src/brr/gates/`` live (via ``shipped_status``) and fails
    the moment the two disagree, instead of a day later."""
    docs_tags = _docs_door_tags()
    docs_soon = {slug for slug, is_soon in docs_tags.items() if is_soon}
    live_slugs = {
        door.slug for door in support_matrix.DOORS if support_matrix.shipped_status(door) == "live"
    }
    stale = docs_soon & live_slugs
    assert not stale, (
        f"docs/src/content/docs/index.md still tags {sorted(stale)} as `soon` "
        "but the gate code has shipped — update the shelf's `brr-shelf-tag--soon` "
        "spans (see kb note / commit for #1070's original reasoning)"
    )


def test_docs_shelf_never_claims_live_for_code_that_has_not_shipped():
    """The mirror-image hazard: a door plain (no `soon` tag, read as live)
    on the docs page before its gate code actually merged would be the
    same "silence reads as a claim of completeness" failure #96-97's
    Landing.svelte rule names, just committed a day early instead of a day
    late."""
    docs_tags = _docs_door_tags()
    claimed_live = {slug for slug, is_soon in docs_tags.items() if not is_soon}
    unshipped = {
        door.slug for door in support_matrix.DOORS if support_matrix.shipped_status(door) == "soon"
    }
    premature = claimed_live & unshipped
    assert not premature, (
        f"docs/src/content/docs/index.md claims {sorted(premature)} as live "
        "but the gate code has not shipped on main yet"
    )
