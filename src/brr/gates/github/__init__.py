"""GitHub gate — turns GitHub activity into events.

The gate polls the GitHub REST API for three configurable triggers:

- ``label-on-issue``: a new (or updated) open issue carrying the
  configured label becomes one inbox event.
- ``mention-in-comment``: a new comment containing the configured
  mention string becomes one event. Covers issue/PR timeline comments
  (``/issues/comments``) and inline PR review comments on diffs
  (``/pulls/comments``). PR-anchored comments carry the PR head branch
  as ``branch_target`` so the daemon's pre-task fetch+ff refreshes that
  branch before the worker runs. For ``@handle``-style triggers,
  comments authored by ``handle`` are filtered so the named account
  cannot self-loop; the PAT holder can still @-mention that account
  from their own comments.
- ``any``: every new issue, PR, and comment fires an event. Overrides
  label and mention when set. Token-expensive on busy repos; off by
  default. PR events include ``branch_target``; bot's own comments are
  still filtered.

Replies are posted as comments on the originating issue or PR; inline
PR review-comment events reply in-thread via the review-replies API.

State lives at ``.brr/gates/github.json``. Auth resolution at setup
time, in order:

1. ``gh auth token`` shell-out when ``gh`` is on PATH.
2. ``GITHUB_TOKEN`` / ``GH_TOKEN`` environment variable.
3. Interactive paste, stored in the state file.

The gate is built-in but ``is_configured`` returns false until setup
runs — there is no surprise auto-enable. Webhooks are deliberately
out of scope for the OSS path (require a public URL + signature
verification + reverse-proxy setup); polling matches the rest of
brr's gate model. The managed brnrd GitHub App owns the webhook side;
see [`kb/design-github-gate-vs-brnrd-app.md`] for the OSS-vs-brnrd
split and what code each side reuses.

The module is structured as a package so the OSS daemon and the brnrd
backend can share the transport-agnostic core. ``paths``, ``cache``
and ``parse`` are pure modules brnrd re-uses behind its own async
client; ``client``, ``state``, ``wizard``, ``polling``, ``delivery``,
``progress`` and ``loop`` are OSS-only. (The wizard module would be
called ``setup`` if Python didn't shadow the submodule with the
re-exported ``setup`` function.)
"""

from __future__ import annotations

# Public surface used by the daemon, the gate registry, the CLI, and
# external integrations. Submodules carry the implementation; keep
# this surface intentional so refactors inside the package stay private.
from .client import GitHubAPIError
from .loop import run_loop
from .parse import parse_origin_url
from .progress import render_update
from .state import resolve_token
from .wizard import auth, autodetect_repo, bind, is_configured, setup

__all__ = [
    "GitHubAPIError",
    "auth",
    "autodetect_repo",
    "bind",
    "is_configured",
    "parse_origin_url",
    "render_update",
    "resolve_token",
    "run_loop",
    "setup",
]
