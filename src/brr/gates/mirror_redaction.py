"""Literal held-secret redaction at the dashboard mirror's send boundary.

This protects the mirror against accidental or injected verbatim disclosure.
It cannot stop a host run encoding a secret or using a different egress path.
Secret values live only in the current call, never in a disk cache or notice.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
from typing import Any

from .. import account, config, presence
from ..outbox import notices
from ..run import list_runs

_MIN_SECRET_CHARS = 12
# This is the token brnrd itself mints into its environment. Operator-supplied
# harness env vars are outside this inventory (no heuristic token-name scan).
_TOKEN_ENV_NAMES = ("BRNRD_MANAGED_GITHUB_TOKEN",)
_notified: set[tuple[Path, str]] = set()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError):
        # Refuse this publish without echoing file contents or an exception
        # whose path might itself contain a value copied by a run.
        raise RuntimeError("mirror secret inventory could not be read") from None


def _held_secrets(brr_dir: Path, cloud_token: str | None) -> dict[str, str]:
    held: dict[str, str] = {}

    def add(value: str, source: str) -> None:
        if len(value) >= _MIN_SECRET_CHARS:
            held.setdefault(value, source)

    if cloud_token:
        add(cloud_token, "cloud-token")
    for name in _TOKEN_ENV_NAMES:
        add(os.environ.get(name, ""), name)

    roots = {brr_dir}
    # The account gate may be started with the home itself as brr_dir.
    if (brr_dir / "account").is_dir():
        home = brr_dir
    else:
        ctx = account.resolve_context(
            brr_dir.parent, config.load_config(brr_dir.parent), create=False,
        )
        home = account.context_home_root(ctx)
        roots.update(repo.root / ".brr" for repo in ctx.repos.values())
    roots.add(home / "account")
    for root in sorted(roots):
        for path in sorted((root / "credentials").glob("**/token")):
            add(_read(path).strip(), "github-token")
    for path in sorted((home / "account").glob("*.env")):
        for line in _read(path).splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            name, sep, raw = line.partition("=")
            name = name.strip()
            if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                continue
            try:
                # Quotes and inline comments are env-file syntax, not part
                # of the held value. No shell evaluation or expansion.
                value = " ".join(shlex.split(raw, comments=True))
            except ValueError:
                raise RuntimeError("mirror secret env file could not be parsed") from None
            add(value, f"{path.name}:{name}")
    return held


def _notice(brr_dir: Path, lane: str, sources: set[str]) -> None:
    text = f"mirror redacted held secret(s) on {lane}: {', '.join(sorted(sources))}"
    targets: set[Path] = set()
    for root in presence.account_dirs(brr_dir):
        for run in list_runs(root / "runs"):
            if run.status == "running" and run.source != "spawn" and run.event_id:
                targets.add(root / "outbox" / run.event_id)
    if not targets:
        notices.write("advisory", text, outbox_dir=None, lifetime="standing")
    for target in sorted(targets):
        key = (target, text)
        if key not in _notified:
            notices.write("advisory", text, outbox_dir=target, lifetime="standing")
            _notified.add(key)


def redact_payload(
    payload: Any, *, brr_dir: Path, lane: str, cloud_token: str | None = None,
) -> Any:
    """Replace exact held values in JSON strings (including object keys).

    Work on strings before JSON escaping so quotes, backslashes and Unicode
    in a secret do not let serialization hide the literal match. One pass,
    longest first, prevents overlapping values corrupting the replacement.
    """
    held = _held_secrets(brr_dir, cloud_token)
    if not held:
        return payload
    pattern = re.compile("|".join(re.escape(value) for value in sorted(held, key=len, reverse=True)))
    sources: set[str] = set()

    def replace(match: re.Match) -> str:
        source = held[match.group()]
        # Source names are names only, even if a hostile env filename happens
        # to include one of the values we hold.
        source = pattern.sub("secret", source)
        sources.add(source)
        return f"[redacted:{source}]"

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return pattern.sub(replace, value)
        if isinstance(value, dict):
            return {walk(key): walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(walk(item) for item in value)
        return value

    redacted = walk(payload)
    if sources:
        _notice(brr_dir, lane, sources)
    return redacted
