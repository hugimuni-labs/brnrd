"""envoy_x.py — the X (Twitter) envoy's mechanics: post, reply, delete, read.

Ported from the account home's ``account/x-post.py`` / ``x-read.py``
(2026-08-13 — see ``buildlog/0001.md``'s postscript: the account-local
script tweeted the literal string ``"--help"`` because argv was payload,
no flag handling. The guards below (``-h``/``--help``, flag-shaped-text
refusal) already existed in the account scripts by the time this module
was written; porting them here with tests is what pins them against a
silent revert — see ``design-the-envoy-as-product.md``, w-14:
machinery-is-product).

Every function here is parameterized by :class:`Paths` — built from a
single account-home directory, or handed explicit files — **never a
hardcoded account**. Same discipline the twin scripts had:

- single-writer token discipline: a 401 shells out to the refresh script
  named in ``Paths.refresh`` and retries once; the access token is never
  printed or logged.
- one receipt line appended to ``Paths.log`` per post, reply, and delete
  (a delete's row carries ``action: "deleted"``) — the envoy's own audit
  trail, so a reader can check the mouth without the platform's
  cooperation.
- dry-run prints the would-be payload and returns before any network call.
- ``-h``/``--help`` prints usage and returns before any network call —
  the regression this module exists to pin.
- text that looks flag-shaped (``args[0].lstrip().startswith("-")``) is
  refused; a leading space is the deliberate escape hatch for text that
  legitimately starts with a dash.
- a reply threads through ``--reply-to <id>``.

The human-readable receipt line prints ``https://x.com/i/status/<id>`` —
X's account-agnostic canonical status link — rather than a hardcoded
handle, since this module has no account identity to hardcode.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

API = "https://api.x.com/2"

POST_USAGE = """\
Usage: envoy-x post "text"                  -> tweet
       envoy-x post "text" --reply-to <id>  -> reply in thread
       envoy-x post "text" --dry-run        -> print what would post
       envoy-x post delete <tweet-id>       -> delete a post
       add --json for the raw API response

Every post appends one line to the receipt log beside the account env
file (what went out, when, in reply to what; a delete appends
action: deleted), so a reader can audit the mouth without the platform's
cooperation.\
"""

READ_USAGE = """\
Usage: envoy-x read           -> mentions since last look + metrics
       envoy-x read --all     -> ignore the since-cursor this once
       envoy-x read --json    -> machine shape\
"""


@dataclass(frozen=True)
class Paths:
    """The account-scoped files this module touches — never hardcoded.

    - ``env`` — the token/secrets file (a ``x_Access_Token=`` line, read
      fresh on every call; the token is never cached in memory beyond one
      call's lifetime).
    - ``log`` — the receipt-trail JSONL, one line appended per post,
      reply, or delete.
    - ``refresh`` — the single-writer refresh script, shelled out to on a
      401 and never otherwise.
    - ``state`` — the read-cursor file (``since_id`` of the newest
      mention seen).
    """

    env: Path
    log: Path
    refresh: Path
    state: Path

    @classmethod
    def in_dir(cls, directory: Path | str) -> "Paths":
        """The four well-known filenames, resolved under *directory*."""
        d = Path(directory)
        return cls(
            env=d / "x-brnrd-resident.env",
            log=d / "x-post-log.jsonl",
            refresh=d / "x-refresh.py",
            state=d / "x-read-state.json",
        )


# ── token + the single-writer refresh lane ───────────────────────────


def token(env_path: Path) -> str:
    """The current access token from *env_path*'s ``x_Access_Token=`` line."""
    for line in open(env_path):
        if line.startswith("x_Access_Token="):
            return line.strip().split("=", 1)[1]
    raise SystemExit("no access token in env file")


def _refresh(refresh_path: Path) -> str:
    """Shell out to the single-writer refresh script; return its fresh token.

    The refresh script owns persisting the rotated pair — this module
    never writes the env file itself.
    """
    return subprocess.run(
        [sys.executable, str(refresh_path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


# ── the wire ──────────────────────────────────────────────────────────


def _request(url: str, tok: str, *, data: bytes | None = None, method: str = "GET") -> Request:
    headers = {"Authorization": f"Bearer {tok}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    return Request(url, data=data, headers=headers, method=method)


def get(
    path: str, params: dict[str, Any], tok: str, paths: Paths, retried: bool = False
) -> tuple[dict[str, Any], str]:
    """``GET`` *path* against the X API; 401 refreshes once and retries.

    Returns ``(body, tok)`` — the (possibly refreshed) token, so a caller
    chaining several ``get`` calls reuses the fresh one instead of
    refreshing again on the next 401.
    """
    url = f"{API}{path}?{urllib.parse.urlencode(params)}" if params else f"{API}{path}"
    req = _request(url, tok)
    try:
        return json.load(urlopen(req)), tok
    except urllib.error.HTTPError as e:
        if e.code == 401 and not retried:
            fresh = _refresh(paths.refresh)
            return get(path, params, fresh, paths, retried=True)
        raise


def post(payload: dict[str, Any], tok: str, paths: Paths, retried: bool = False) -> dict[str, Any]:
    """``POST /tweets``; 401 refreshes once and retries."""
    req = _request(f"{API}/tweets", tok, data=json.dumps(payload).encode(), method="POST")
    try:
        return json.load(urlopen(req))
    except urllib.error.HTTPError as e:
        if e.code == 401 and not retried:
            fresh = _refresh(paths.refresh)
            return post(payload, fresh, paths, retried=True)
        detail = e.read().decode(errors="replace")[:500]
        raise SystemExit(f"post failed: HTTP {e.code} — {detail}")


def delete(tweet_id: str, tok: str, paths: Paths, retried: bool = False) -> dict[str, Any]:
    """``DELETE /tweets/<id>``; 401 refreshes once and retries."""
    req = _request(f"{API}/tweets/{tweet_id}", tok, method="DELETE")
    try:
        return json.load(urlopen(req))
    except urllib.error.HTTPError as e:
        if e.code == 401 and not retried:
            fresh = _refresh(paths.refresh)
            return delete(tweet_id, fresh, paths, retried=True)
        raise SystemExit(
            f"delete failed: HTTP {e.code} — {e.read().decode(errors='replace')[:300]}"
        )


# ── the receipt trail ────────────────────────────────────────────────


def _append_receipt(log_path: Path, record: dict[str, Any]) -> None:
    with open(log_path, "a") as fh:
        fh.write(json.dumps(record) + "\n")


# ── post / reply / delete: the CLI mechanics ─────────────────────────


def run_post(argv: list[str], paths: Paths) -> None:
    """The post/reply/delete mechanics — argv-compatible with the account
    shim's ``x-post.py``. Errors and usage raise ``SystemExit(message)``,
    exactly as the original script did; success paths ``print`` and
    return.

    ``-h``/``--help`` (and empty argv) resolve *before* anything else in
    this function reaches for the token, the env file, or the wire — the
    regression this module exists to pin.
    """
    args = list(argv)
    if not args or "-h" in args or "--help" in args:
        raise SystemExit(POST_USAGE.strip())
    if args[0] == "delete":
        if len(args) != 2:
            raise SystemExit("usage: envoy-x post delete <tweet-id>")
        out = delete(args[1], token(paths.env), paths)
        _append_receipt(paths.log, {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "id": args[1], "action": "deleted",
        })
        print(json.dumps(out))
        return
    as_json = "--json" in args and (args.remove("--json") or True)
    dry = "--dry-run" in args and (args.remove("--dry-run") or True)
    reply_to = None
    if "--reply-to" in args:
        i = args.index("--reply-to")
        reply_to = args[i + 1]
        del args[i : i + 2]
    if len(args) != 1 or not args[0].strip():
        raise SystemExit(
            POST_USAGE.strip().split("\n", 1)[0] + "\n(one non-empty text argument required)"
        )
    text = args[0]
    if text.startswith(" -"):
        # The escape hatch: a single leading space marks the dash that
        # follows as deliberate text, not a flag — consumed here, so it
        # never reaches the wire as part of the post.
        text = text[1:]
    elif text.startswith("-"):
        raise SystemExit(
            "refusing: text starts with '-' — looks like a flag, not a post. "
            "Quote deliberately dash-led text as ' -…' with a leading space."
        )

    payload: dict[str, Any] = {"text": text}
    if reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": str(reply_to)}

    if dry:
        print(json.dumps({"would_post": payload}) if as_json
              else f"dry-run · {len(text)} chars"
              + (f" · reply-to {reply_to}" if reply_to else "") + f"\n{text}")
        return

    out = post(payload, token(paths.env), paths)
    tweet_id = (out.get("data") or {}).get("id")
    _append_receipt(paths.log, {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "id": tweet_id, "reply_to": reply_to, "text": text,
    })
    if as_json:
        print(json.dumps(out))
    else:
        print(f"posted · https://x.com/i/status/{tweet_id}"
              + (f" · reply-to {reply_to}" if reply_to else ""))


def main_post(argv: list[str], home_dir: Path | str) -> None:
    """Convenience wrapper: :func:`run_post` over ``Paths.in_dir(home_dir)``."""
    run_post(argv, Paths.in_dir(home_dir))


# ── read: mentions + own-tweet metrics ───────────────────────────────


def run_read(argv: list[str], paths: Paths) -> None:
    """The read mechanics — argv-compatible with the account shim's
    ``x-read.py``. On-demand only: this never wakes anything, it is a
    door peeked through when a run reaches for it.
    """
    show_all = "--all" in argv
    as_json = "--json" in argv
    tok = token(paths.env)

    me, tok = get("/users/me", {"user.fields": "public_metrics"}, tok, paths)
    uid = me["data"]["id"]
    pm = me["data"].get("public_metrics", {})

    state: dict[str, Any] = {}
    if paths.state.exists():
        state = json.loads(paths.state.read_text(encoding="utf-8"))

    params = {
        "tweet.fields": "created_at,author_id",
        "expansions": "author_id",
        "user.fields": "username",
        "max_results": 25,
    }
    if state.get("since_id") and not show_all:
        params["since_id"] = state["since_id"]
    mentions, tok = get(f"/users/{uid}/mentions", params, tok, paths)

    tweets, tok = get(
        f"/users/{uid}/tweets",
        {"tweet.fields": "public_metrics,created_at", "max_results": 5},
        tok, paths,
    )

    rows = mentions.get("data") or []
    users = {u["id"]: u["username"] for u in mentions.get("includes", {}).get("users", [])}
    if rows:
        newest = max(int(r["id"]) for r in rows)
        if newest > int(state.get("since_id") or 0):
            paths.state.write_text(json.dumps({"since_id": str(newest)}), encoding="utf-8")

    if as_json:
        print(json.dumps({"metrics": pm, "mentions": rows, "own_recent": tweets.get("data") or []}))
        return

    print(f"@{me['data']['username']} · followers {pm.get('followers_count')} · "
          f"following {pm.get('following_count')} · posts {pm.get('tweet_count')} · "
          f"listed {pm.get('listed_count')}")
    if not rows:
        print("mentions: none new since last look" + (" (use --all for history)" if not show_all else ""))
    for r in rows:
        who = users.get(r["author_id"], r["author_id"])
        text = r["text"].replace("\n", " ")
        print(f"@{who} · {r.get('created_at', '?')} · {text[:200]} · "
              f"https://x.com/{who}/status/{r['id']}")
    for t in tweets.get("data") or []:
        m = t.get("public_metrics", {})
        text = t["text"].replace("\n", " ")
        print(f"own · {text[:60]!r} · ❤ {m.get('like_count', 0)} · rt {m.get('retweet_count', 0)} · "
              f"replies {m.get('reply_count', 0)} · views {m.get('impression_count', 0)}")


def main_read(argv: list[str], home_dir: Path | str) -> None:
    """Convenience wrapper: :func:`run_read` over ``Paths.in_dir(home_dir)``."""
    run_read(argv, Paths.in_dir(home_dir))
