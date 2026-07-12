"""Interactive setup: token paste / repo binding / trigger selection.

``setup(brr_dir)`` is the one-step CLI entry; ``auth`` and ``bind``
stay separate so ``brnrd auth github`` and ``brnrd bind github`` keep
working independently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import gitops
from . import state
from .parse import parse_origin_url


def autodetect_repo(repo_root: Path) -> str | None:
    remote = gitops.default_remote(repo_root)
    if not remote:
        return None
    url = gitops.remote_url(repo_root, remote)
    if not url:
        return None
    return parse_origin_url(url)


def auth(brr_dir: Path) -> None:
    state_dict = state._load_state(brr_dir)
    token = state.resolve_token(state_dict)
    source = "stored" if state_dict.get("token") else None
    if token is None:
        token = input("GitHub personal access token (repo scope): ").strip()
        if not token:
            print("[brnrd] No token provided.")
            return
        source = "stored"
    elif not state_dict.get("token"):
        # We picked one up from gh CLI or env. Don't store it; just
        # validate now so the operator knows it works.
        source = "gh-cli" if state._gh_cli_token() == token else "env"

    try:
        login = state._validate_token(token)
    except Exception as exc:
        print(f"[brnrd] GitHub auth failed: {exc}")
        return

    state_dict["bot_login"] = login
    if source == "stored":
        state_dict["token"] = token
    else:
        # Make sure we don't keep a stale stored token if the operator
        # is rotating to a gh CLI / env-based flow.
        state_dict.pop("token", None)
    state_dict["token_source"] = source
    state._save_state(brr_dir, state_dict)
    print(f"[brnrd] GitHub auth ok: @{login} (source={source})")


def _prompt_trigger(label: str, default: str) -> str | None:
    """Prompt for a trigger string.

    - Enter → accepts the bracketed default as-is.
    - ``off`` / ``none`` / ``disable`` → remove the trigger (returns ``None``).
    - Anything else → use that literal value.
    """
    raw = input(f"{label} (off to disable) [{default}]: ").strip()
    if not raw:
        return default
    if raw.lower() in ("off", "none", "disable"):
        return None
    return raw


def _prompt_bool_trigger(label: str, default: bool = False) -> bool:
    """Prompt for an on/off trigger."""
    default_label = "on" if default else "off"
    raw = input(f"{label} (on/off) [{default_label}]: ").strip().lower()
    if not raw:
        return default
    if raw in ("on", "yes", "true", "enable", "enabled"):
        return True
    if raw in ("off", "no", "false", "none", "disable", "disabled"):
        return False
    print(f"[brnrd] Unrecognised value '{raw}' — keeping {default_label}.")
    return default


def bind(brr_dir: Path) -> None:
    state_dict = state._load_state(brr_dir)
    if state.resolve_token(state_dict) is None:
        print("[brnrd] Run `brnrd auth github` first.")
        return

    repo_root = brr_dir.parent
    detected = autodetect_repo(repo_root)
    prompt = (
        f"GitHub repo (owner/name) [{detected}]: "
        if detected else "GitHub repo (owner/name): "
    )
    repo = input(prompt).strip() or (detected or "")
    if not repo or "/" not in repo:
        print("[brnrd] Repo must look like 'owner/name'.")
        return
    state_dict["repo"] = repo

    triggers: dict[str, Any] = state_dict.get("triggers") or {}

    # 'any' fires on every issue, PR, and comment — overrides other triggers.
    print("Watch all activity fires on every new issue, PR, and comment without")
    print("filtering. Token-expensive on busy repos. Off by default.")
    if _prompt_bool_trigger("Watch all activity", bool(triggers.get("any"))):
        triggers = {"any": True}
        state_dict["triggers"] = triggers
        state._save_state(brr_dir, state_dict)
        print(f"[brnrd] GitHub gate bound: repo={repo} triggers=['any']")
        return
    triggers.pop("any", None)

    opened = _prompt_bool_trigger(
        "Watch newly opened issues and PRs",
        bool(triggers.get("opened")),
    )
    if opened:
        triggers["opened"] = True
    else:
        triggers.pop("opened", None)

    label = _prompt_trigger(
        "Label to watch on issues",
        str(triggers.get("label") or "brnrd"),
    )
    if label is None:
        triggers.pop("label", None)
    else:
        triggers["label"] = label

    mention = _prompt_trigger(
        "Mention string to watch in comments",
        str(triggers.get("mention") or "@brnrd-bot"),
    )
    if mention is None:
        triggers.pop("mention", None)
    else:
        triggers["mention"] = mention

    if not triggers:
        print(
            "[brnrd] No triggers configured — at least one of opened / label / mention "
            "required.",
        )
        return
    state_dict["triggers"] = triggers
    state._save_state(brr_dir, state_dict)
    print(f"[brnrd] GitHub gate bound: repo={repo} triggers={list(triggers)}")


def setup(brr_dir: Path) -> None:
    auth(brr_dir)
    if "bot_login" in state._load_state(brr_dir):
        bind(brr_dir)


def is_configured(brr_dir: Path) -> bool:
    state_dict = state._load_state(brr_dir)
    return (
        bool(state_dict.get("repo"))
        and state.resolve_token(state_dict) is not None
    )
