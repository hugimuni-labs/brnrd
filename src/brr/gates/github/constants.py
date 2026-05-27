"""Module-level constants for the GitHub gate.

Kept dependency-free so any submodule can import without cycles. The
poll / backoff / lookback knobs were chosen for the OSS daemon's
single-repo polling model; the brnrd backend ignores them and works
off webhook deliveries.
"""

from __future__ import annotations

import re
from datetime import timedelta

_API_ROOT = "https://api.github.com"
_USER_AGENT = "brr-github-gate"
_API_VERSION = "2022-11-28"
_POLL_INTERVAL = 60
_BACKOFF_MAX = 120
_HTTP_TIMEOUT = 30

# Cap how far back we look on first poll so a freshly-configured gate
# doesn't re-process a year of historical comments.
_INITIAL_LOOKBACK = timedelta(hours=1)

# Cap how many seen IDs we keep per trigger to bound state file size.
_SEEN_CAP = 500

_GITHUB_HOSTS = {"github.com", "www.github.com"}
_HTTPS_RE = re.compile(r"^https?://([^/]+)/([^/]+)/([^/]+?)(?:\.git)?/?$")
_SSH_RE = re.compile(r"^git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?$")
_ISSUE_URL_RE = re.compile(r"/issues/(\d+)$")
_PR_URL_RE = re.compile(r"/pulls/(\d+)(?:/|$)")

# Update-packet types that trigger a fresh render of the GitHub
# progress card. Anything not in this set is ignored so render_update
# stays a thin pass-through on noise.
_RENDERABLE_PACKETS = {
    "task_created",
    "env_prepared",
    "container_started",
    "container_preserved",
    "run_started",
    "attempt_started",
    "attempt_failed",
    "retrying",
    "artifact_created",
    "heartbeat",
    "finalizing",
    "push_started",
    "push_done",
    "done",
    "failed",
    "conflict",
}

# Event kinds that originate from a comment (issue/PR timeline or
# inline review-line) or a PR review summary body. Replies to these
# include a quote pointer back at the source; replies to label-triggered
# issues do not (the issue itself is the source).
_COMMENT_KINDS = frozenset({
    "issue-comment", "pr-comment", "pr-review-comment", "pr-review",
})
