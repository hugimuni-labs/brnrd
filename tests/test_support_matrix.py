"""The shells-and-doors support matrix (#1070 follow-up) — one status
computation, checked against both the doors it describes and the docs
page that used to hand-maintain the same claim and went stale in a day.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace


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


def test_hosted_status_is_ready_when_brnrd_dev_has_no_credentials():
    """Shipped code with an unconfigured hosted axis is "ready", not
    "soon" — "soon" is reserved for code that does not exist yet, and
    conflating the two misreports finished work as unwritten (the
    WhatsApp half of the maintainer's brief: #1074 shipped the cloud lane
    a day before Meta business verification, and the shelf called it
    "soon" throughout, indistinguishable from code not merged at all)."""
    settings = _settings()
    assert support_matrix.hosted_status("telegram", settings) == "ready"
    assert support_matrix.hosted_status("whatsapp", settings) == "ready"
    assert support_matrix.hosted_status("github", settings) == "ready"


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
    assert support_matrix.hosted_status("telegram", settings) == "ready"


def test_hosted_status_never_claims_live_for_a_door_with_no_hosted_axis():
    """Slack and Signal never touch brnrd.dev's backend at all (self-hosted
    gates, no Settings fields exist for them) — shipped code with nothing
    to configure reads "ready", the same "no confirmed identity yet" state
    an unconfigured WhatsApp reads, never a fabricated "live" (the Signal/
    Slack half of the maintainer's brief: a live tile with no gate behind
    it reads as an address to message, and there isn't one). The web
    dashboard *is* brnrd.dev, so it is the one door that stays "live" with
    no cloud_settings at all."""
    settings = _settings()
    assert support_matrix.hosted_status("slack", settings) == "ready"
    assert support_matrix.hosted_status("signal", settings) == "ready"
    assert support_matrix.hosted_status("dashboard", settings) == "live"


def test_hosted_status_never_claims_live_for_code_that_is_not_shipped():
    """The property the maintainer's brief calls out by name: a merged-but-
    unconfigured gate must be visible as exactly that, never silently
    promoted past code that does not even exist yet."""
    unshipped = replace(support_matrix.door("whatsapp"), shipped=lambda: False)
    settings = _settings(whatsapp_access_token="w", whatsapp_phone_number_id="123")
    assert support_matrix.hosted_status(unshipped, settings) == "soon"


def test_hosted_status_never_claims_ready_for_a_door_with_no_hosted_axis_and_no_code():
    """The no-cloud_settings branch must still check `shipped` first — an
    unshipped self-hosted-only door (no such door exists in DOORS today,
    but the function must not assume it never will) reads "soon", not a
    premature "ready"."""
    unshipped = replace(support_matrix.door("slack"), shipped=lambda: False)
    assert support_matrix.hosted_status(unshipped, _settings()) == "soon"


# --- shells -------------------------------------------------------------


# --- the docs shelf must agree with shipped_status ---------------------------


