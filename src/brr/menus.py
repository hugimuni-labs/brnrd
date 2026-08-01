"""Live menu schema, generation store, and answer resolution.

The resident authors one ``menu.json`` control file in its run outbox.
The daemon validates that file, promotes it into this runtime store, and
renders the stored generation at gates and at the next resident boundary.

Each correspondent has one ``live.json`` pointer plus a bounded archive of
generations. The originating conversation thread remains on the generation as
rendering provenance, but does not split one person's controls by ingress
lane. Superseded generations stay resolvable for honest stale-tap answers
without leaving their controls live. The archive retains the newest 128
generations per correspondent; an older tap still becomes an ``unknown``
answer event rather than disappearing.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import protocol

MENU_NAME = "menu.json"
STORE_NAME = "menus"
ARCHIVE_LIMIT = 128

_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_ID_CHARS = 48
_MAX_HANDLE_CHARS = 48
_MAX_THREAD_CHARS = 512
_MAX_OPTIONS = 24
_MAX_LABEL_CHARS = 120
_MAX_DETAIL_CHARS = 1000


class MenuValidationError(ValueError):
    """The resident-authored menu does not satisfy the v1 schema."""


def _utc_now(now: float | None = None) -> str:
    epoch = time.time() if now is None else now
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _parse_iso(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _required_text(
    value: object,
    field: str,
    *,
    max_chars: int,
    token: bool = False,
) -> str:
    if not isinstance(value, str):
        raise MenuValidationError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise MenuValidationError(f"{field} must not be empty")
    if len(text) > max_chars:
        raise MenuValidationError(
            f"{field} is too long ({len(text)} > {max_chars} characters)"
        )
    if token and not _TOKEN_RE.fullmatch(text):
        raise MenuValidationError(
            f"{field} must contain only letters, digits, '.', '_', or '-'"
        )
    return text


def validate_menu(
    payload: object,
    *,
    expected_thread: str | None = None,
) -> dict[str, Any]:
    """Validate and canonicalize one resident-authored menu generation."""
    if not isinstance(payload, dict):
        raise MenuValidationError("menu.json must contain a JSON object")
    if "version" in payload and payload["version"] != 1:
        raise MenuValidationError("version must be 1 when present")

    menu_id = _required_text(
        payload.get("menu_id"), "menu_id", max_chars=_MAX_ID_CHARS, token=True,
    )
    thread = _required_text(
        payload.get("thread"), "thread", max_chars=_MAX_THREAD_CHARS,
    )
    if expected_thread is not None and thread != expected_thread:
        raise MenuValidationError(
            f"thread {thread!r} does not match this run's thread "
            f"{expected_thread!r}"
        )

    raw_options = payload.get("options")
    if not isinstance(raw_options, list):
        raise MenuValidationError("options must be an array")
    if len(raw_options) > _MAX_OPTIONS:
        raise MenuValidationError(
            f"options has too many entries ({len(raw_options)} > {_MAX_OPTIONS})"
        )

    options: list[dict[str, Any]] = []
    handles: set[str] = set()
    for index, raw in enumerate(raw_options):
        if not isinstance(raw, dict):
            raise MenuValidationError(f"options[{index}] must be an object")
        handle = _required_text(
            raw.get("handle"),
            f"options[{index}].handle",
            max_chars=_MAX_HANDLE_CHARS,
            token=True,
        )
        if handle in handles:
            raise MenuValidationError(f"option handle {handle!r} is duplicated")
        handles.add(handle)
        label = _required_text(
            raw.get("label"),
            f"options[{index}].label",
            max_chars=_MAX_LABEL_CHARS,
        )
        option: dict[str, Any] = {"handle": handle, "label": label}
        if "detail" in raw:
            detail = _required_text(
                raw.get("detail"),
                f"options[{index}].detail",
                max_chars=_MAX_DETAIL_CHARS,
            )
            option["detail"] = detail
        if "rec" in raw:
            if not isinstance(raw["rec"], bool):
                raise MenuValidationError(f"options[{index}].rec must be a boolean")
            if raw["rec"]:
                option["rec"] = True
        # Telegram callback_data is capped at 64 UTF-8 bytes. Keeping the
        # generation + stable handle directly in the callback makes stale
        # taps self-describing and avoids a second opaque-token registry.
        callback = callback_data(menu_id, handle)
        if len(callback.encode("utf-8")) > 64:
            raise MenuValidationError(
                f"menu_id + options[{index}].handle exceed Telegram's "
                "64-byte callback limit"
            )
        options.append(option)

    canonical: dict[str, Any] = {
        "version": 1,
        "menu_id": menu_id,
        "thread": thread,
        "options": options,
    }
    if "expires_at" in payload:
        expires_at = _required_text(
            payload.get("expires_at"), "expires_at", max_chars=64,
        )
        if _parse_iso(expires_at) is None:
            raise MenuValidationError(
                "expires_at must be an ISO-8601 timestamp"
            )
        canonical["expires_at"] = expires_at
    return canonical


def read_outbox_menu(
    outbox_dir: Path,
    *,
    expected_thread: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Read, validate, and fingerprint ``outbox/menu.json``."""
    path = Path(outbox_dir) / MENU_NAME
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MenuValidationError(f"could not read {MENU_NAME}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MenuValidationError(f"{MENU_NAME} is not valid JSON: {exc}") from exc
    return validate_menu(payload, expected_thread=expected_thread), digest


def menu_store_key(thread: str, correspondent_key: str | None = None) -> str:
    """Return the identity that owns one live-menu generation stream.

    Correspondent keys deliberately carry a namespace prefix so they cannot
    collide with a legacy raw thread string. Threads remain the fallback for
    sources that do not expose a stable correspondent identity.
    """
    correspondent = str(correspondent_key or "").strip()
    if correspondent:
        return f"correspondent:{correspondent}"
    return thread


def _store_dir(
    brr_dir: Path,
    thread: str,
    correspondent_key: str | None = None,
) -> Path:
    key = menu_store_key(thread, correspondent_key)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return Path(brr_dir) / STORE_NAME / digest


def _generation_path(
    brr_dir: Path,
    thread: str,
    menu_id: str,
    correspondent_key: str | None = None,
) -> Path:
    return (
        _store_dir(brr_dir, thread, correspondent_key)
        / "generations"
        / f"{menu_id}.json"
    )


def _live_path(
    brr_dir: Path,
    thread: str,
    correspondent_key: str | None = None,
) -> Path:
    return _store_dir(brr_dir, thread, correspondent_key) / "live.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    protocol._atomic_write(
        path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def _canonical_part(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record[key]
        for key in ("version", "menu_id", "thread", "options", "expires_at")
        if key in record
    }


def _prune_generations(
    brr_dir: Path,
    thread: str,
    correspondent_key: str | None = None,
) -> None:
    root = _store_dir(brr_dir, thread, correspondent_key) / "generations"
    try:
        entries = sorted(
            (path for path in root.glob("*.json") if path.is_file()),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
    except OSError:
        return
    for path in entries[ARCHIVE_LIMIT:]:
        try:
            path.unlink()
        except OSError:
            continue


def _generation_order(menu: dict[str, Any], path: Path) -> tuple[float, int, str]:
    try:
        modified_ns = path.stat().st_mtime_ns
    except OSError:
        modified_ns = 0
    written = _parse_iso(menu.get("written_at"))
    if written is None:
        written = modified_ns / 1_000_000_000
    return written, modified_ns, str(
        menu.get("menu_id") or ""
    )


def _reconcile_legacy_threads(
    brr_dir: Path,
    thread: str,
    correspondent_key: str | None,
    legacy_threads: Iterable[str],
) -> None:
    """Merge legacy per-thread live pointers into the correspondent store.

    This is intentionally lazy: the first read or write that knows both the
    correspondent and their related thread keys performs the migration. The
    newest generation becomes the correspondent's live pointer and every
    older live generation is archived as superseded. Legacy live pointers are
    then removed so a later process cannot resurrect the split-brain state.
    """
    correspondent = str(correspondent_key or "").strip()
    if not correspondent:
        return

    canonical_live_path = _live_path(brr_dir, thread, correspondent)
    candidates: list[tuple[dict[str, Any], Path, bool]] = []
    canonical = _read_json(canonical_live_path)
    if canonical and canonical.get("menu_id"):
        candidates.append((canonical, canonical_live_path, True))

    seen_threads: set[str] = set()
    for raw_thread in (thread, *legacy_threads):
        legacy_thread = str(raw_thread or "").strip()
        if not legacy_thread or legacy_thread in seen_threads:
            continue
        seen_threads.add(legacy_thread)
        legacy_path = _live_path(brr_dir, legacy_thread)
        legacy = _read_json(legacy_path)
        if legacy and legacy.get("menu_id"):
            candidates.append((legacy, legacy_path, False))

    if not candidates:
        return
    if len(candidates) == 1 and candidates[0][2]:
        return

    winner, _winner_path, _winner_is_canonical = max(
        candidates,
        key=lambda item: _generation_order(item[0], item[1]),
    )
    menu_id = str(winner["menu_id"])
    migrated_at = _utc_now()
    winner = {
        **winner,
        "state": "live",
        "correspondent_key": correspondent,
    }

    # Copy older archived generations too, so a tap on a superseded legacy
    # keyboard remains an honest stale answer after the live pointer migrates.
    live_candidate_ids = {
        str(candidate["menu_id"]) for candidate, _path, _canonical in candidates
    }
    for legacy_thread in seen_threads:
        generations = _store_dir(brr_dir, legacy_thread) / "generations"
        for legacy_generation_path in generations.glob("*.json"):
            legacy_generation = _read_json(legacy_generation_path)
            if not legacy_generation or not legacy_generation.get("menu_id"):
                continue
            legacy_id = str(legacy_generation["menu_id"])
            if legacy_id in live_candidate_ids:
                continue
            _write_json(
                _generation_path(brr_dir, thread, legacy_id, correspondent),
                {**legacy_generation, "correspondent_key": correspondent},
            )

    # Write every legacy live generation into the unified archive before the
    # pointer moves. Same-id collisions are inherently ambiguous in the old
    # split store; the newest content wins because callback data cannot name
    # the lane that authored it.
    ordered = sorted(
        candidates,
        key=lambda item: _generation_order(item[0], item[1]),
    )
    for candidate, path, _is_canonical in ordered:
        candidate_id = str(candidate["menu_id"])
        migrated = {**candidate, "correspondent_key": correspondent}
        if candidate_id != menu_id:
            migrated.update(
                state="superseded",
                superseded_by=menu_id,
                superseded_at=migrated_at,
            )
        else:
            migrated["state"] = "live"
        _write_json(
            _generation_path(brr_dir, thread, candidate_id, correspondent),
            migrated,
        )
        if path != canonical_live_path:
            legacy_thread = str(candidate.get("thread") or "").strip()
            if legacy_thread:
                _write_json(
                    _generation_path(brr_dir, legacy_thread, candidate_id),
                    migrated,
                )
            path.unlink(missing_ok=True)

    # A same-id collision may have let an older candidate overwrite the
    # unified archive in the loop; pin the selected newest generation last.
    _write_json(
        _generation_path(brr_dir, thread, menu_id, correspondent),
        winner,
    )
    _write_json(canonical_live_path, winner)
    _prune_generations(brr_dir, thread, correspondent)


def promote_menu(
    brr_dir: Path,
    menu: dict[str, Any],
    *,
    run_id: str = "",
    now: float | None = None,
    correspondent_key: str | None = None,
    legacy_threads: Iterable[str] = (),
) -> tuple[dict[str, Any], str | None]:
    """Make *menu* the one live generation for its correspondent.

    Returns ``(stored_generation, superseded_menu_id)``. Reusing a generation
    id with changed content is rejected: ``menu_id`` names an immutable
    generation, which is what makes stale answers trustworthy.
    """
    canonical = validate_menu(menu)
    thread = canonical["thread"]
    menu_id = canonical["menu_id"]
    _reconcile_legacy_threads(
        brr_dir, thread, correspondent_key, legacy_threads,
    )
    existing_generation = _read_json(
        _generation_path(brr_dir, thread, menu_id, correspondent_key)
    )
    if existing_generation is not None:
        if _canonical_part(existing_generation) != canonical:
            raise MenuValidationError(
                f"menu_id {menu_id!r} was already used for different content"
            )
        current = _read_json(_live_path(brr_dir, thread, correspondent_key))
        if current and current.get("menu_id") == menu_id:
            return current, None
        raise MenuValidationError(
            f"menu_id {menu_id!r} names an older generation and cannot become "
            "live again"
        )

    written_at = _utc_now(now)
    current = _read_json(_live_path(brr_dir, thread, correspondent_key))
    superseded_id: str | None = None
    if current and current.get("menu_id") != menu_id:
        superseded_id = str(current.get("menu_id") or "") or None
        current["state"] = "superseded"
        current["superseded_by"] = menu_id
        current["superseded_at"] = written_at
        _write_json(
            _generation_path(
                brr_dir,
                thread,
                str(current["menu_id"]),
                correspondent_key,
            ),
            current,
        )

    stored = {
        **canonical,
        "state": "live",
        "written_at": written_at,
    }
    if run_id:
        stored["run_id"] = run_id
    if correspondent_key:
        stored["correspondent_key"] = correspondent_key
    # Archive first, pointer last: readers either see the previous complete
    # generation or the new complete one, never a live pointer with no record.
    _write_json(
        _generation_path(brr_dir, thread, menu_id, correspondent_key), stored,
    )
    _write_json(_live_path(brr_dir, thread, correspondent_key), stored)
    _prune_generations(brr_dir, thread, correspondent_key)
    return stored, superseded_id


def is_expired(menu: dict[str, Any], *, now: float | None = None) -> bool:
    expires = _parse_iso(menu.get("expires_at"))
    return expires is not None and expires <= (time.time() if now is None else now)


def load_live_menu(
    brr_dir: Path,
    thread: str,
    *,
    now: float | None = None,
    correspondent_key: str | None = None,
    legacy_threads: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Return the current unexpired menu for the resolved identity."""
    _reconcile_legacy_threads(
        brr_dir, thread, correspondent_key, legacy_threads,
    )
    menu = _read_json(_live_path(brr_dir, thread, correspondent_key))
    if not menu or menu.get("state") != "live" or is_expired(menu, now=now):
        return None
    return menu


def load_generation(
    brr_dir: Path,
    thread: str,
    menu_id: str,
    *,
    correspondent_key: str | None = None,
    legacy_threads: Iterable[str] = (),
) -> dict[str, Any] | None:
    _reconcile_legacy_threads(
        brr_dir, thread, correspondent_key, legacy_threads,
    )
    return _read_json(
        _generation_path(brr_dir, thread, menu_id, correspondent_key)
    )


def resolve_answer(
    brr_dir: Path,
    *,
    thread: str,
    menu_id: str,
    option: str,
    text: str | None = None,
    now: float | None = None,
    correspondent_key: str | None = None,
    legacy_threads: Iterable[str] = (),
) -> dict[str, Any]:
    """Resolve one tap without discarding stale, expired, or unknown input."""
    generation = load_generation(
        brr_dir,
        thread,
        menu_id,
        correspondent_key=correspondent_key,
        legacy_threads=legacy_threads,
    )
    result: dict[str, Any] = {
        "menu_id": menu_id,
        "option": option,
        "status": "unknown",
        "option_known": False,
    }
    if text:
        result["text"] = text
    if generation is None:
        return result

    current = _read_json(_live_path(brr_dir, thread, correspondent_key))
    if is_expired(generation, now=now):
        result["status"] = "expired"
    elif not current or current.get("menu_id") != menu_id:
        result["status"] = "stale"
    else:
        result["status"] = "live"

    selected = next(
        (
            item for item in generation.get("options", [])
            if isinstance(item, dict) and item.get("handle") == option
        ),
        None,
    )
    if selected is not None:
        result["option_known"] = True
        result["label"] = selected.get("label")
        if selected.get("detail"):
            result["detail"] = selected["detail"]
        if selected.get("rec"):
            result["rec"] = True
    if generation.get("expires_at"):
        result["expires_at"] = generation["expires_at"]
    if generation.get("superseded_by"):
        result["superseded_by"] = generation["superseded_by"]
    return result


def create_answer_event(
    brr_dir: Path,
    inbox_dir: Path,
    *,
    source: str,
    thread: str,
    menu_id: str,
    option: str,
    text: str | None = None,
    now: float | None = None,
    correspondent_key: str | None = None,
    legacy_threads: Iterable[str] = (),
    **meta: object,
) -> Path:
    """Create the structured ``menu_answer`` event for a gate interaction."""
    answer = resolve_answer(
        brr_dir,
        thread=thread,
        menu_id=menu_id,
        option=option,
        text=text,
        now=now,
        correspondent_key=correspondent_key,
        legacy_threads=legacy_threads,
    )
    return protocol.create_event(
        inbox_dir,
        source=source,
        body=json.dumps(answer, indent=2, sort_keys=True),
        kind="menu_answer",
        conversation_key=thread,
        menu_id=menu_id,
        option=option,
        menu_status=answer["status"],
        menu_option_known=answer["option_known"],
        **meta,
    )


def callback_data(menu_id: str, handle: str) -> str:
    return f"m:{menu_id}:{handle}"


def parse_callback_data(value: object) -> tuple[str, str] | None:
    text = str(value or "")
    if not text.startswith("m:"):
        return None
    parts = text.split(":", 2)
    if len(parts) != 3:
        return None
    menu_id, handle = parts[1], parts[2]
    if (
        not menu_id
        or not handle
        or len(menu_id) > _MAX_ID_CHARS
        or len(handle) > _MAX_HANDLE_CHARS
        or not _TOKEN_RE.fullmatch(menu_id)
        or not _TOKEN_RE.fullmatch(handle)
    ):
        return None
    return menu_id, handle


def render_numbered(menu: dict[str, Any]) -> str:
    """Render the canonical menu as the prompt-contract numbered handles."""
    lines: list[str] = []
    for index, option in enumerate(menu.get("options", []), start=1):
        if not isinstance(option, dict):
            continue
        handle = str(option.get("handle") or "")
        label = str(option.get("label") or "")
        rec = " — recommended" if option.get("rec") else ""
        lines.append(f"{index}) `{handle}` — {label}{rec}")
        detail = str(option.get("detail") or "").strip()
        if detail:
            lines.append(f"   {detail}")
    return "\n".join(lines)
