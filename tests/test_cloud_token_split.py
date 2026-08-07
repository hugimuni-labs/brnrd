"""The account daemon token leaves ``cloud.json`` — issue: the capture net
cannot tell a secret from a note.

``dominion.commit()`` -> ``gitops.commit_all()`` runs ``git add -A`` on the
whole account home root, and that has always been able to see
``account/gates/cloud.json``: a file this codebase keeps *tracked* on
purpose so the pairing identity survives a restore. The bearer token used to
live in that same file, which meant the daemon's own credential rode every
capture — the same value landed in 107 commits before this split existed.

The fix keeps ``cloud.json`` tracked with its non-secret fields and moves
``token`` to a sibling (``cloud.token``, gitignored) the capture net cannot
reach. Every existing install on disk has the token in ``cloud.json`` right
now, so the compatibility shim is a *reader*'s job: ``_load_state_from_dir``
resolves the new location first, falls back to the legacy field, and
migrates on the spot so the fallback drains instead of living forever.
Writers (``_save_state_to_dir``) stamp only the new location.

Pinned structurally rather than by call-site list, mirroring the shape
``tests/test_strand_key_migration.py`` used for the ``worker``/``strand``
rename: an AST walk fails any function that calls ``runtime.load_state`` /
``runtime.save_state`` for the cloud gate outside the one designated raw
accessor, and a second walk fails any function that references the token's
own file path outside the functions allowed to touch it. Both carry their
own sanity assertion, so a rename cannot turn either into a no-op passing
over an empty set.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from brr import account
from brr.gates import cloud, runtime


# ── the split: cloud.json never carries the field on disk ─────────────


def test_save_state_never_writes_token_into_cloud_json(tmp_path):
    brr_dir = tmp_path / ".brr"
    cloud._save_state(
        brr_dir,
        {"brnrd_url": "http://brnrd", "token": "bd_secret", "repo_id": "p1", "since": 3},
    )

    raw_path = runtime.state_path(cloud._state_dir(brr_dir), "cloud")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert "token" not in raw
    assert raw["brnrd_url"] == "http://brnrd"
    assert raw["repo_id"] == "p1"
    assert raw["since"] == 3
    # And the raw file text never carries the secret substring either — the
    # property that actually matters to a capture net that just diffs bytes.
    assert "bd_secret" not in raw_path.read_text(encoding="utf-8")

    token_path = cloud._token_path(cloud._state_dir(brr_dir))
    assert token_path.read_text(encoding="utf-8") == "bd_secret"
    if hasattr(token_path.stat(), "st_mode"):
        import stat

        mode = stat.S_IMODE(token_path.stat().st_mode)
        assert mode == 0o600


def test_load_state_still_returns_the_merged_token(tmp_path):
    """Every existing read call site does ``state["token"]`` on the dict
    :func:`_load_state` returns — that contract must not change."""
    brr_dir = tmp_path / ".brr"
    cloud._save_state(brr_dir, {"brnrd_url": "http://brnrd", "token": "bd_secret"})

    state = cloud._load_state(brr_dir)
    assert state["token"] == "bd_secret"
    assert state.get("brnrd_url") == "http://brnrd"


def test_load_state_with_no_token_omits_the_key(tmp_path):
    brr_dir = tmp_path / ".brr"
    cloud._save_state(brr_dir, {"brnrd_url": "http://brnrd"})

    state = cloud._load_state(brr_dir)
    assert "token" not in state
    assert cloud.is_configured(brr_dir) is False


# ── the reader shim: new-location-first, legacy fallback, drains once ─


def test_legacy_token_field_is_read_and_migrated(tmp_path):
    """An install that predates this fix has the token in ``cloud.json``.

    The very next read must still see it (the gate cannot go dark), and
    must migrate it out so the fallback drains instead of costing a
    field-presence check on every future read forever.
    """
    brr_dir = tmp_path / ".brr"
    state_dir = cloud._state_dir(brr_dir)
    runtime.save_state(
        state_dir, "cloud",
        {"brnrd_url": "http://brnrd", "token": "bd_legacy", "since": 5},
    )
    assert not cloud._token_path(state_dir).exists()

    state = cloud._load_state(brr_dir)
    assert state["token"] == "bd_legacy"

    # Migrated: the new file now holds it, and cloud.json was rewritten
    # without the field — verified by reading the raw file directly, not
    # through the accessor, since the accessor's whole job is to hide this.
    assert cloud._token_path(state_dir).read_text(encoding="utf-8") == "bd_legacy"
    raw = json.loads(runtime.state_path(state_dir, "cloud").read_text(encoding="utf-8"))
    assert "token" not in raw
    assert raw["since"] == 5  # every other field survives the rewrite untouched

    # The fallback drained: a second read finds the new location directly
    # and never needed the legacy field again.
    state2 = cloud._load_state(brr_dir)
    assert state2["token"] == "bd_legacy"


def test_new_location_wins_over_a_stale_legacy_field(tmp_path):
    """New-location-first: if both exist (a half-drained or hand-edited
    install), the authoritative copy is the one the migration itself would
    have produced, not whatever is still sitting in the old field."""
    brr_dir = tmp_path / ".brr"
    state_dir = cloud._state_dir(brr_dir)
    runtime.save_state(state_dir, "cloud", {"token": "bd_stale_legacy"})
    cloud._write_token(state_dir, "bd_current")

    state = cloud._load_state(brr_dir)
    assert state["token"] == "bd_current"


def test_absent_both_reads_as_no_token(tmp_path):
    brr_dir = tmp_path / ".brr"
    cloud._save_state(brr_dir, {"brnrd_url": "http://brnrd"})
    assert cloud._load_state(brr_dir).get("token") is None


# ── cleanup: disconnect must not leave the token behind ────────────────


def test_disconnect_removes_the_token_file_too(tmp_path):
    brr_dir = tmp_path / ".brr"
    cloud._save_state(brr_dir, {"brnrd_url": "http://brnrd", "token": "bd_secret"})
    state_dir = cloud._state_dir(brr_dir)
    assert cloud._token_path(state_dir).exists()

    removed = cloud.disconnect(brr_dir)

    assert removed is True
    assert not cloud._token_path(state_dir).exists()
    assert not cloud.is_configured(brr_dir)


# ── the structural half: only the accessor touches the raw forms ──────


CLOUD_PY = Path(cloud.__file__)

#: Functions allowed to call ``runtime.load_state`` / ``runtime.save_state``
#: for the cloud gate directly — the raw accessor pair itself.
_RAW_STATE_IO_EXEMPT = {"_load_state_from_dir", "_save_state_to_dir"}

#: Functions allowed to reference the token's own on-disk path or filename
#: directly. ``disconnect`` earns its spot for a stated reason: removing the
#: gate's local identity on ``account disconnect`` has to reach the token
#: file to delete it, same as it already does for the state and health
#: files beside it.
_TOKEN_PATH_EXEMPT = {"_token_path", "_read_token", "_write_token", "disconnect"}


def _call_targets(tree: ast.AST, *, attr: str) -> dict[str, list[ast.Call]]:
    """``<enclosing function> -> [Call nodes whose .<attr>(...) matches]``."""
    found: dict[str, list[ast.Call]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == attr:
                found.setdefault(fn.name, []).append(node)
    return found


def _has_cloud_gate_arg(call: ast.Call) -> bool:
    return any(
        isinstance(arg, ast.Constant) and arg.value == "cloud" for arg in call.args
    )


def test_only_the_raw_accessor_loads_or_saves_cloud_gate_state():
    """Nobody bypasses ``_load_state_from_dir`` / ``_save_state_to_dir`` to
    talk to ``runtime.load_state`` / ``runtime.save_state`` directly for the
    cloud gate — that bypass is exactly how a call site could see (or write)
    a raw dict that still carries ``token``, defeating the split.
    """
    tree = ast.parse(CLOUD_PY.read_text(encoding="utf-8"))

    loaders = _call_targets(tree, attr="load_state")
    savers = _call_targets(tree, attr="save_state")

    # Sanity: a rename or a moved module would make the walk find nothing
    # and this test would pass over an empty set, proving nothing.
    assert loaders, f"AST walk of {CLOUD_PY} found no `.load_state(...)` calls at all"
    assert savers, f"AST walk of {CLOUD_PY} found no `.save_state(...)` calls at all"

    load_offenders = {
        name: len(calls)
        for name, calls in loaders.items()
        if name not in _RAW_STATE_IO_EXEMPT
        and any(_has_cloud_gate_arg(c) for c in calls)
    }
    save_offenders = {
        name: len(calls)
        for name, calls in savers.items()
        if name not in _RAW_STATE_IO_EXEMPT
        and any(_has_cloud_gate_arg(c) for c in calls)
    }
    assert not load_offenders, (
        "these functions call runtime.load_state(..., 'cloud') directly, "
        f"bypassing _load_state_from_dir: {load_offenders}. A raw load "
        "outside the accessor can observe cloud.json before the token "
        "migration runs, or never resolve the new-location file at all."
    )
    assert not save_offenders, (
        "these functions call runtime.save_state(..., 'cloud', ...) "
        f"directly, bypassing _save_state_to_dir: {save_offenders}. A raw "
        "save outside the accessor can write 'token' straight back into "
        "cloud.json, which is the exact defect this split closes."
    )
    # And confirm the exempt functions really are the ones doing it — a
    # renamed accessor would otherwise leave both assertions above green
    # over nobody at all, which proves nothing either.
    assert "_load_state_from_dir" in loaders, (
        "_load_state_from_dir no longer calls runtime.load_state — the "
        "exemption above is now vacuous"
    )
    assert "_save_state_to_dir" in savers, (
        "_save_state_to_dir no longer calls runtime.save_state — the "
        "exemption above is now vacuous"
    )


def test_only_the_accessor_touches_the_token_file_path():
    """Nobody besides the accessor (and the stated ``disconnect`` cleanup
    exception) constructs or references the token's own file path."""
    tree = ast.parse(CLOUD_PY.read_text(encoding="utf-8"))

    referrers: dict[str, int] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            hit = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_token_path"
            ) or (
                isinstance(node, ast.Name) and node.id == "_TOKEN_FILENAME"
            )
            if hit:
                referrers[fn.name] = referrers.get(fn.name, 0) + 1

    assert referrers, (
        f"AST walk of {CLOUD_PY} found no reference to _token_path/"
        "_TOKEN_FILENAME at all — the parse missed the module, so a green "
        "result here would prove nothing"
    )
    offenders = {
        name: count for name, count in referrers.items() if name not in _TOKEN_PATH_EXEMPT
    }
    assert not offenders, (
        "these functions reference the token's on-disk path directly, "
        f"outside the accessor: {offenders}. Route through _read_token / "
        "_write_token instead — a second way to name the file is a second "
        "place the split can drift out of sync."
    )


def test_cloud_token_filename_matches_account_module():
    """``gates/cloud.py`` and ``account.py`` each duplicate the same
    basename (see both modules' comments for why); this pins them together
    so a rename in one is caught rather than silently drifting."""
    assert cloud._TOKEN_FILENAME == account.CLOUD_TOKEN_FILENAME


def test_account_security_filename_matches_config_module():
    from brr import config as conf

    assert account.SECURITY_CONFIG_FILENAME == conf.SECURITY_CONFIG_FILENAME
