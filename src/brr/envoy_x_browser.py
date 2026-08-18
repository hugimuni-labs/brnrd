"""envoy_x_browser.py — the browser envoy: a persistent, human-logged-in
browser session for X, giving the resident the verbs the API forbids.

**Why this exists.** X's API has forbidden reply/quote to accounts that
have not mentioned you since April 2026 (re-verified live 403 on
2026-08-13). That wall is API-only. brnrd's whole shape is that the
resident runs on the *user's own machine*, so it can hold a real
logged-in session the way the user does — a differentiator, not a
workaround. See ``design-the-envoy-as-product.md`` in the kb for the
argument in full and the measured stakes that motivated it.

**Two lanes, one receipt log.** ``envoy_x.py`` is the API lane (token
auth, ``x-post-log.jsonl``); this module is the browser lane (cookie
auth via a persistent Chromium profile). They share the receipt log
(every browser send appends to the same JSONL, marked ``lane:
"browser"``) but nothing else — this module never touches
``x-post.py`` / ``x-read.py`` / ``x-refresh.py`` or their single-writer
token discipline.

**The guardrails, in one place, because they're the load-bearing part:**

- ``send`` and ``post`` both ship **disarmed**: each refuses unless
  *both* ``--confirm`` (argv) and ``BRR_X_BROWSER_SEND=1`` (environment)
  are present, and refuses independently if the hourly cap
  (:data:`DEFAULT_HOURLY_CAP` unless overridden by ``Paths.config``,
  shared by both verbs) is spent. Neither arm alone is enough, on
  purpose — an argv typo or a leaked env var must not be sufficient on
  its own to post.
- A **kill switch** — the mere *presence* of ``Paths.kill_switch`` — is
  checked before every verb except ``check`` and refuses immediately, no
  browser launched, no side effects.
- ``draft``/``draft-post`` fill the composer, screenshot it, and
  **stop**. Neither calls the driver's ``click_send``; that call exists
  in exactly two places in this module — the disarmed, capped, guarded
  verbs :func:`_run_send` and :func:`_run_post` — never in a draft path.
- The profile directory holds live session cookies — the whole account,
  if it leaks. It is created mode ``0700`` and the account home's own
  ``.gitignore`` (``account.py``'s ``GITIGNORE``) excludes it by name;
  see that module for the matching rule.
- Playwright is **not** a runtime dependency of this project (see the
  ``browser`` extra in ``pyproject.toml``) — the import is lazy, and a
  missing install fails with the exact command to fix it
  (:func:`_playwright_driver`), never a bare ``ModuleNotFoundError``.
- Every verb that renders a page runs **headed** (``headless=False``);
  the only headless-eligible verbs are the read-only ones (``check``,
  ``read``, ``search``) that write nothing back to X.

**The browser seam.** Every verb takes an optional ``driver_factory`` —
``(paths, *, headless) -> driver`` returning a context-managed object
shaped like the methods :class:`_PlaywrightDriver` implements
(``whoami``, ``wait_for_manual_login``, ``read_url``, ``search``,
``open_reply_composer``, ``open_post_composer``, ``fill_text``,
``screenshot``, ``click_send``). Tests inject a fake at this seam and
never touch a real browser — the guardrail logic (kill switch, cap
arithmetic, the disarmed-send refusal, argv guards, the receipt-log
shape) is exercised without Playwright installed, which is also why
none of it lives inside :class:`_PlaywrightDriver`.

**Two write lanes, same vocabulary, same guardrails.** ``draft``/``send``
fill and (optionally) submit the **reply** box at a target URL;
``draft-post``/``post`` do the identical fill-then-stop or
fill-then-submit dance against ``x.com/compose/post`` instead — an
**original**, no target URL, no ``open_reply_composer`` call. They share
every guardrail function (``_send_arming``, ``_refuse_if_killed``,
``cap_status``/``_record_send``) rather than duplicating them, so the
hourly cap is one shared bucket across both lanes and the kill switch
and disarmed-send checks cannot drift between them. Kept as two verb
pairs rather than one branching on "was a URL given" — a reply and an
original are different acts, and a typo that turns one into the other
(dropping a URL argument, say) is exactly the failure this whole module
exists to make hard.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

#: Conservative by design — the whole point of this envoy is a lane that
#: currently earns real engagement (unlike the API reply lane, ranked to
#: worthlessness by X against a 2-follower graph); a runaway loop spamming
#: replies through a *working* channel is a worse failure than a slow one.
#: Overridden by ``{"hourly_cap": N}`` in ``Paths.config``, never hardcoded
#: past this fallback.
DEFAULT_HOURLY_CAP = 3

HOUR_SECONDS = 3600.0

LOGIN_URL = "https://x.com/login"
HOME_URL = "https://x.com/home"
SEARCH_URL = "https://x.com/search"
COMPOSE_URL = "https://x.com/compose/post"

#: X's search tabs, as the `f=` parameter names them. `live` is Latest —
#: reverse-chronological, unfiltered, and what this verb has always used;
#: `top` is the ranked tab. They answer different questions and neither is
#: a better default: Latest is right for *monitoring a term* (nothing is
#: dropped for being small), Top is right for *finding a conversation worth
#: joining* (a two-follower account replying under an unread post is a
#: reply nobody sees). The caller knows which question it is asking.
SEARCH_TABS = {"live": "live", "top": "top"}
# How long to wait for X's client-side search render before calling a query
# empty. Generous on purpose: a false `[]` is indistinguishable from a real
# one at the CLI, so the cost of waiting too long is a slow command and the
# cost of waiting too little is a lie.
SEARCH_RESULT_TIMEOUT_MS = 15000

#: The narrower of X's two cookie-banner answers. Named rather than inlined
#: because it is a *choice made inside someone else's account*, and a choice
#: like that belongs where a reader can find and change it.
CONSENT_REFUSE_LABEL = "Refuse non-essential cookies"

#: The banner renders client-side like everything else on X, so a `count()`
#: taken at `domcontentloaded` answers 0 and the dismissal silently skips —
#: the same not-waiting bug as the search above, in the same file, found the
#: same night. Short: a profile that has already answered renders no banner
#: and every verb pays this once.
CONSENT_BANNER_TIMEOUT_MS = 4000

#: How long to wait for the focal post's inline reply box to render.
COMPOSER_TIMEOUT_MS = 15000

TOP_USAGE = """\
Usage: envoy-x-browser login
       envoy-x-browser check [--json]
       envoy-x-browser read <url> [--json]
       envoy-x-browser search <query> [--top] [--json]
       envoy-x-browser draft <url> --text "<s>"
       envoy-x-browser send <url> --text "<s>" --confirm
       envoy-x-browser draft-post --text "<s>"
       envoy-x-browser post --text "<s>" --confirm

send/post ship disarmed: BRR_X_BROWSER_SEND=1 in the environment AND
--confirm on argv are both required, and both still refuse past the
hourly cap (one shared bucket across both verbs).
A kill-switch file (see Paths.kill_switch) refuses every verb but check.\
"""

LOGIN_USAGE = """\
Usage: envoy-x-browser login

Launches a headed, persistent browser profile, waits for you to log in by
hand, then verifies the session and reports which handle is logged in.
One-time human step; the profile persists after this.\
"""

CHECK_USAGE = """\
Usage: envoy-x-browser check [--json]

Is the session live, and as whom? Cheap, read-only — the probe every
other verb runs first. Also reports the hourly-cap arithmetic.\
"""

READ_USAGE = """\
Usage: envoy-x-browser read <url> [--json]

Returns structured data for the post at <url>: author, text, timestamp,
metrics if rendered.\
"""

SEARCH_USAGE = """\
Usage: envoy-x-browser search <query> [--top] [--json]

Returns structured results for a search: author, text, permalink.
Default is X's *Latest* tab — reverse-chronological, everything that
matched, however small. `--top` asks for X's *Top* tab instead: ranked by
engagement, which is the only one of the two that answers "what is
actually being read about this right now."\
"""

DRAFT_USAGE = """\
Usage: envoy-x-browser draft <url> --text "<s>"

Opens the reply composer at <url>, fills <s>, screenshots the result to a
path this prints, and stops. Never sends.\
"""

SEND_USAGE = """\
Usage: envoy-x-browser send <url> --text "<s>" --confirm

Ships disarmed: refuses unless BOTH --confirm (this argv) and
BRR_X_BROWSER_SEND=1 (environment) are present, and refuses independently
once the hourly cap is spent.\
"""

DRAFT_POST_USAGE = """\
Usage: envoy-x-browser draft-post --text "<s>"

Opens the compose box (an original post, no reply target), fills <s>,
screenshots the result to a path this prints, and stops. Never sends.\
"""

POST_USAGE = """\
Usage: envoy-x-browser post --text "<s>" --confirm

Ships disarmed: refuses unless BOTH --confirm (this argv) and
BRR_X_BROWSER_SEND=1 (environment) are present, and refuses independently
once the hourly cap is spent (shared with the reply lane's `send`).
Posts an original — no reply target, no <url> argument.\
"""


# ── paths ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Paths:
    """The account-scoped files this module touches — never hardcoded.

    - ``log`` — the receipt-trail JSONL **shared with the API lane**
      (``envoy_x.Paths.log``); a browser send appends here too, marked
      ``lane: "browser"``, so a reader audits both lanes from one file.
    - ``profile_dir`` — the persistent Chromium profile. Holds live
      session cookies: the whole account if it leaks. Created mode
      ``0700``; excluded from the account home's git tracking by
      ``account.py``'s ``GITIGNORE`` (matched by directory name — see
      that module).
    - ``config`` — ``{"hourly_cap": N}``; missing or malformed falls back
      to :data:`DEFAULT_HOURLY_CAP`.
    - ``state`` — send timestamps in the trailing hour, for the cap
      arithmetic. Not secret, just runtime bookkeeping.
    - ``kill_switch`` — presence alone (any content, or none) refuses
      every verb but ``check``.
    - ``shots_dir`` — where ``draft`` screenshots land.
    """

    log: Path
    profile_dir: Path
    config: Path
    state: Path
    kill_switch: Path
    shots_dir: Path

    @classmethod
    def in_dir(cls, directory: Path | str) -> "Paths":
        """The well-known filenames, resolved under *directory*."""
        d = Path(directory)
        return cls(
            log=d / "x-post-log.jsonl",
            profile_dir=d / "x-browser-profile",
            config=d / "x-browser.json",
            state=d / "x-browser-state.json",
            kill_switch=d / "x-browser.disabled",
            shots_dir=d / "x-browser-shots",
        )


#: The profile directory's basename — the identity ``account.py``'s
#: gitignore rule matches on. Exported so a cross-module test can assert
#: the two never drift apart without duplicating the full relative path
#: in two files.
PROFILE_DIRNAME = "x-browser-profile"


# ── guardrails: kill switch, hourly cap, the disarmed-send arming ──────


def kill_switch_active(paths: Paths) -> bool:
    """Whether the kill-switch file is present — refuses every verb but
    ``check`` when true. Existence only; content is never read."""
    return paths.kill_switch.exists()


def _load_cap(paths: Paths) -> int:
    if not paths.config.exists():
        return DEFAULT_HOURLY_CAP
    try:
        data = json.loads(paths.config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_HOURLY_CAP
    if not isinstance(data, dict):
        return DEFAULT_HOURLY_CAP
    try:
        cap = int(data.get("hourly_cap", DEFAULT_HOURLY_CAP))
    except (TypeError, ValueError):
        return DEFAULT_HOURLY_CAP
    return cap if cap >= 0 else DEFAULT_HOURLY_CAP


def _recent_send_times(paths: Paths, now: float) -> list[float]:
    if not paths.state.exists():
        return []
    try:
        data = json.loads(paths.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw = data.get("sends") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [t for t in raw if isinstance(t, (int, float)) and now - t < HOUR_SECONDS]


def cap_status(paths: Paths, *, now: float | None = None) -> dict[str, int]:
    """``{"cap", "used", "remaining"}`` over the trailing hour — the
    arithmetic both ``check`` and ``send`` read."""
    now = time.time() if now is None else now
    cap = _load_cap(paths)
    used = len(_recent_send_times(paths, now))
    return {"cap": cap, "used": used, "remaining": max(0, cap - used)}


def _record_send(paths: Paths, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    recent = _recent_send_times(paths, now)
    recent.append(now)
    tmp = paths.state.with_suffix(paths.state.suffix + ".tmp")
    tmp.write_text(json.dumps({"sends": recent}), encoding="utf-8")
    tmp.replace(paths.state)


def _send_arming(argv: list[str]) -> tuple[bool, list[str], list[str]]:
    """Whether *both* arms of the disarmed-send guard are present.

    Returns ``(armed, missing, remaining_argv)`` — ``remaining_argv`` has
    ``--confirm`` stripped so downstream url/text parsing sees only its
    own arguments. Neither arm is checked lazily: both are evaluated so
    ``missing`` can name every absent arm, not just the first.
    """
    args = list(argv)
    has_confirm = "--confirm" in args
    if has_confirm:
        args.remove("--confirm")
    env_armed = os.environ.get("BRR_X_BROWSER_SEND") == "1"
    missing = []
    if not has_confirm:
        missing.append("--confirm")
    if not env_armed:
        missing.append("BRR_X_BROWSER_SEND=1")
    return (not missing), missing, args


# ── argv parsing helpers (the guards this module exists to pin) ────────


def _dash_guard(value: str, label: str) -> str:
    """A leading ``-`` reads as a flag, not content — refuse, with a
    leading-space escape hatch for text that legitimately starts with a
    dash. Same discipline as ``envoy_x.run_post``'s text guard, applied
    to every free-text argument this module accepts (url, query, text) —
    after the incident that guard exists to pin (buildlog/0001.md)."""
    if value.startswith(" -"):
        return value[1:]
    if value.startswith("-"):
        raise SystemExit(
            f"refusing: {label} starts with '-' — looks like a flag, not "
            f"{label}. Quote deliberately dash-led text as ' -…' with a "
            "leading space."
        )
    return value


def _parse_single_arg(args: list[str], usage: str, label: str) -> str:
    if not args or "-h" in args or "--help" in args:
        raise SystemExit(usage.strip())
    if len(args) != 1 or not args[0].strip():
        raise SystemExit(usage.strip() + f"\n(exactly one {label} argument required)")
    return _dash_guard(args[0], label)


def _parse_url_and_text(args: list[str], usage: str) -> tuple[str, str]:
    if not args or "-h" in args or "--help" in args:
        raise SystemExit(usage.strip())
    args = list(args)
    if "--text" not in args:
        raise SystemExit(usage.strip() + "\n(missing --text)")
    i = args.index("--text")
    if i + 1 >= len(args):
        raise SystemExit(usage.strip() + "\n(--text needs a value)")
    text = args[i + 1]
    del args[i : i + 2]
    if len(args) != 1 or not args[0].strip():
        raise SystemExit(usage.strip() + "\n(exactly one url argument required)")
    url = _dash_guard(args[0], "url")
    text = _dash_guard(text, "text")
    return url, text


def _parse_text_only(args: list[str], usage: str) -> str:
    """Same ``--text`` extraction as :func:`_parse_url_and_text`, minus the
    url — the compose lane has no reply target to name. A leftover
    positional argument after ``--text`` is pulled out refuses rather than
    being silently ignored, since a stray url-shaped argument here usually
    means the caller meant ``send``/``draft``, not this verb."""
    if not args or "-h" in args or "--help" in args:
        raise SystemExit(usage.strip())
    args = list(args)
    if "--text" not in args:
        raise SystemExit(usage.strip() + "\n(missing --text)")
    i = args.index("--text")
    if i + 1 >= len(args):
        raise SystemExit(usage.strip() + "\n(--text needs a value)")
    text = args[i + 1]
    del args[i : i + 2]
    if args:
        raise SystemExit(
            usage.strip() + "\n(no positional arguments expected — this "
            "verb has no reply target; did you mean `send`/`draft`?)"
        )
    return _dash_guard(text, "text")


# ── the receipt trail (shared with the API lane) ────────────────────────


def _append_receipt(paths: Paths, record: dict[str, Any]) -> None:
    with open(paths.log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# ── the playwright seam ──────────────────────────────────────────────


def _playwright_driver(paths: Paths, *, headless: bool) -> "_PlaywrightDriver":
    """The real driver factory — lazy import, legible failure.

    Playwright is not installed on every machine this project runs on
    and is deliberately not a runtime dependency (see the ``browser``
    extra in ``pyproject.toml``): a class of adopter who never touches
    the browser envoy must not be forced to pull a browser binary. The
    failure here names the exact fix instead of surfacing a bare
    ``ModuleNotFoundError`` from three frames down.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "the browser envoy needs playwright: "
            "pip install playwright && playwright install chromium"
        ) from exc
    return _PlaywrightDriver(paths, headless=headless, sync_playwright=sync_playwright)


class _PlaywrightDriver:
    """The real browser backend. Never imported or instantiated by the
    guardrail/argv-guard tests — those inject a fake at the
    ``driver_factory`` seam instead (see this module's docstring)."""

    def __init__(self, paths: Paths, *, headless: bool, sync_playwright: Any) -> None:
        self._paths = paths
        self._headless = headless
        self._sync_playwright = sync_playwright
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None
        # Set by `_goto_composer`; `click_send` refuses to guess without
        # them. `None` is the honest pre-open state — a send that reaches
        # this class without a composer having been opened is a bug, and it
        # should read as one rather than fall through to a page-wide
        # `.first` on a send button.
        self._composer_scope: Any = None
        self._send_testid: str | None = None

    def __enter__(self) -> "_PlaywrightDriver":
        self._paths.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._paths.profile_dir.chmod(0o700)
        except OSError:
            pass
        self._pw = self._sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            str(self._paths.profile_dir), headless=self._headless,
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        return self

    def __exit__(self, *exc: Any) -> bool:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
        return False

    @staticmethod
    def _first_text(scope: Any, selector: str) -> str | None:
        try:
            return scope.locator(selector).first.inner_text(timeout=5000)
        except Exception:  # noqa: BLE001 - best-effort scrape, never fatal
            return None

    def whoami(self) -> str | None:
        self._page.goto(HOME_URL, wait_until="domcontentloaded")
        if "/login" in self._page.url or "/flow/login" in self._page.url:
            return None
        try:
            href = self._page.locator(
                '[data-testid="AppTabBar_Profile_Link"]'
            ).first.get_attribute("href", timeout=5000)
        except Exception:  # noqa: BLE001
            href = None
        if not href:
            return None
        return href.strip("/").split("/")[-1]

    def wait_for_manual_login(self) -> None:
        input("Press Enter once you've logged in in the opened browser window... ")

    def read_url(self, url: str) -> dict[str, Any]:
        self._page.goto(url, wait_until="domcontentloaded")
        article = self._page.locator('article[data-testid="tweet"]').first
        timestamp = None
        try:
            timestamp = article.locator("time").first.get_attribute("datetime", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        metrics: dict[str, str] = {}
        for testid in ("reply", "retweet", "like"):
            try:
                label = article.locator(f'[data-testid="{testid}"]').first.get_attribute(
                    "aria-label", timeout=2000
                )
            except Exception:  # noqa: BLE001
                label = None
            if label:
                metrics[testid] = label
        return {
            "url": url,
            "author": self._first_text(article, '[data-testid="User-Name"]'),
            "text": self._first_text(article, '[data-testid="tweetText"]'),
            "timestamp": timestamp,
            "metrics": metrics,
        }

    def search(self, query: str, *, tab: str = "live") -> list[dict[str, Any]]:
        params = {"q": query, "src": "typed_query", "f": SEARCH_TABS[tab]}
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
        self._page.goto(url, wait_until="domcontentloaded")
        articles = self._page.locator('article[data-testid="tweet"]')
        # X renders search results client-side, so at `domcontentloaded` there
        # are zero articles on the page and `count()` — which does NOT
        # auto-wait, unlike every locator call in `read_url` above — answered
        # 0 every time. The verb returned `[]` for every query ever run
        # through it, including ones with obvious live matches, and an empty
        # list reads exactly like "nothing found". Wait for the first result
        # before counting; a genuine no-match times out here and still
        # returns `[]`, which is the one case that empty list is allowed to
        # mean.
        try:
            articles.first.wait_for(state="visible", timeout=SEARCH_RESULT_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - a real no-match, or a search wall
            return []
        rows = []
        for i in range(min(articles.count(), 20)):
            article = articles.nth(i)
            rows.append({
                "author": self._first_text(article, '[data-testid="User-Name"]'),
                "text": self._first_text(article, '[data-testid="tweetText"]'),
                # The permalink, because a result you cannot open is not a
                # result: `read` and `draft`/`send` all take a URL, so a
                # search that returns only author+text hands the caller a
                # list it has no verb for. X puts the canonical
                # `/<handle>/status/<id>` on the timestamp's own anchor.
                "url": self._status_url(article),
            })
        return rows

    def _status_url(self, article: Any) -> str | None:
        """The `/status/<id>` permalink for one rendered article, or ``None``.

        ``None`` is honest here rather than fatal: a promoted post or a
        rendering X changes tomorrow simply has no link this way, and one
        unlinkable row must not lose the caller the other nineteen.
        """
        try:
            href = article.locator('a:has(time)').first.get_attribute("href", timeout=2000)
        except Exception:  # noqa: BLE001
            return None
        if not href or "/status/" not in href:
            return None
        return href if href.startswith("http") else f"https://x.com{href}"

    def _goto_composer(self, url: str) -> None:
        """Navigate to *url* and leave a focused, typable composer behind.

        **One door for every lane**, and that is the point rather than a
        tidiness preference. Both write lanes need the same three things in
        the same order — navigate, answer the consent banner, wait for the
        text box — and the two were built a branch apart, each correct, each
        knowing only its own. Enumerating "the composers that must dismiss
        consent" is a list, and a list meets the member nobody added to it;
        a single door is a structural property. A third lane either comes
        through here or is not a composer.
        """
        self._page.goto(url, wait_until="domcontentloaded")
        self._dismiss_consent()
        box = self._page.locator('[data-testid="tweetTextarea_0"]').first
        box.wait_for(state="visible", timeout=COMPOSER_TIMEOUT_MS)
        box.click()
        # Remember *which* composer this is, so `click_send` presses this
        # one's button and not another on the same page. The compose modal
        # is a `[role="dialog"]` rendered over the live home timeline —
        # and that timeline carries its own inline composer, with its own
        # `tweetButtonInline`, which Playwright considers perfectly
        # visible even behind an overlay. Scoping is not
        # tidiness here: an unscoped `.first` on a *send* button is a
        # public act aimed by DOM order.
        dialog = self._page.locator(
            '[role="dialog"]:has([data-testid="tweetTextarea_0"])'
        ).first
        if dialog.count():
            self._composer_scope = dialog
            self._send_testid = "tweetButton"
        else:
            self._composer_scope = self._page
            self._send_testid = "tweetButtonInline"

    def _dismiss_consent(self) -> None:
        """Answer X's cookie banner, or every click on the page is swallowed.

        The banner renders inside ``#layers`` — the same stacking context
        the composer uses — and intercepts pointer events for the whole
        document, so ``open_reply_composer``'s click below never reaches
        the reply button and Playwright retries until it times out. The
        read verbs never noticed because they only read text. This is what
        a silently dead reply lane looks like from the inside.

        **Refuse, never accept.** The resident is acting inside someone
        else's account; when a consent dialog offers a narrower and a
        wider choice, the narrower one is the only one that is ours to
        make. Answered once per persistent profile; a no-op every time
        after that, and a no-op if X renders no banner at all.
        """
        button = self._page.get_by_role("button", name=CONSENT_REFUSE_LABEL).first
        try:
            button.wait_for(state="visible", timeout=CONSENT_BANNER_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - no banner: answered already, or none shown
            return
        try:
            button.click(timeout=3000)
            self._page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001 - never block a verb on the banner
            pass

    def open_reply_composer(self, url: str) -> None:
        """Focus the reply box **bound to the post at *url***.

        Deliberately the inline composer under the focal post, and not the
        modal behind the reply button: ``[data-testid="reply"]`` matches
        every reply button rendered on the page, and ``.first`` on any
        status that quotes or embeds another post is *that* post's button.
        Measured 2026-08-17 on a status by ``@saen_dev`` quoting an
        article — the modal opened captioned "Replying to @beamnxw", for a
        URL naming neither. A reply lane that can silently address a
        different post is worse than no reply lane, because the failure is
        invisible from the calling side and public from everyone else's.
        The inline box is bound to the focal post by construction.
        """
        self._goto_composer(url)

    def open_post_composer(self) -> None:
        """The compose lane's counterpart to :meth:`open_reply_composer` —
        no target url, because an original has nothing to reply to. X's
        full-screen composer at :data:`COMPOSE_URL` renders the same
        ``tweetTextarea_0`` / ``tweetButton`` testids the reply modal does,
        so :meth:`fill_text` / :meth:`screenshot` / :meth:`click_send` are
        shared unmodified across both lanes.

        Answers the consent banner for the same reason the reply lane does
        — it renders inside ``#layers`` and intercepts pointer events
        document-wide, so without this the send click is swallowed and
        Playwright retries to timeout. This lane was written on a branch
        that predated that finding; the two arrived correct and separately
        and the collision only exists in the merge."""
        self._goto_composer(COMPOSE_URL)

    def fill_text(self, text: str) -> None:
        box = self._page.locator('[data-testid="tweetTextarea_0"]').first
        box.click()
        box.fill(text)

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._page.screenshot(path=str(path))

    def click_send(self) -> str | None:
        """Click send; best-effort return the new post's id.

        **Two buttons, because there are two composers — and the choice
        is made where the composer was opened, not here.** The inline reply
        box under a focal post carries ``tweetButtonInline``; the
        full-screen compose modal carries ``tweetButton``.
        :meth:`_goto_composer` records which one it opened and the element
        that scopes it, and this method presses that. Trying one testid
        page-wide and falling back to the other is the version that ships a
        public misfire: the compose modal renders over the live home
        timeline, whose own inline composer carries ``tweetButtonInline``
        and is "visible" to a query that does not know an overlay is on top
        of it. ``.first`` on a *send* button is aiming by DOM order.

        X shows a confirmation toast with a "View" link to the new post
        (``.../status/<id>``) after a send. This is read on a short timeout
        and the id parsed out of it when present. The toast is UI, not an
        API contract, and no test here is allowed to actually post — so it
        was verified the only way left: a live send on 2026-08-18T00:11Z
        returned ``2089505353159381235``, and reading that id back gave the
        posted text, this account as author, and a timestamp one second off
        the receipt's. Anything short of a clean href returns ``None``
        rather than guessing: the receipt this feeds must be able to say
        "no id" honestly.

        **The href is scoped to the toast, never to the page.** A bare
        ``a[href*="/status/"]`` search takes ``.first`` across the whole
        document, and the compose modal renders over the home timeline —
        so the first status link on the page is somebody else's post, and
        the receipt would name it with total confidence. Honest ``None``
        is only honest if the alternative cannot be a *wrong* id. Same
        defect class as the reply button above, one layer over.
        """
        if self._composer_scope is None or self._send_testid is None:
            raise RuntimeError(
                "click_send called before a composer was opened — "
                "open_reply_composer/open_post_composer record which button "
                "belongs to which composer, and pressing one without that is "
                "aiming a public act by DOM order"
            )
        scope, testid = self._composer_scope, self._send_testid
        button = scope.locator(f'[data-testid="{testid}"]').first
        try:
            button.wait_for(state="visible", timeout=5000)
            button.click()
        except Exception:  # noqa: BLE001 - fall back to the other composer's button
            scope.locator(
                '[data-testid="tweetButton"]'
                if testid == "tweetButtonInline"
                else '[data-testid="tweetButtonInline"]'
            ).first.click()
        try:
            href = (
                self._page.locator('[data-testid="toast"]')
                .locator('a[href*="/status/"]')
                .first.get_attribute("href", timeout=5000)
            )
        except Exception:  # noqa: BLE001 - best-effort; absence is honest, not fatal
            return None
        if not href or "/status/" not in href:
            return None
        return href.rstrip("/").split("/")[-1] or None


DriverFactory = Callable[..., Any]


# ── verb mechanics ──────────────────────────────────────────────────


def _refuse_if_killed(paths: Paths) -> None:
    if kill_switch_active(paths):
        raise SystemExit(
            f"browser envoy disarmed: kill switch present at {paths.kill_switch} "
            "(remove that file to re-arm; `check` still works while it's there)"
        )


def _require_session(driver: Any, verb: str) -> None:
    """Refuse a scrape verb outright when the session is dead.

    X will not render a tweet or a search result page to a logged-out
    browser — it silently redirects to ``/login`` instead — so a scraper
    that doesn't check first gets a well-formed, empty-looking result
    indistinguishable from "logged in, genuinely nothing here." That is
    the defect this guards: a consumer counting mentions or checking
    ``len(results)`` cannot tell "the account went blind" from "no one
    replied," and a session that expires unattended (cookies age out
    weeks into a schedule tick) fails silent instead of loud.

    Reuses :meth:`whoami` — the same probe :func:`_run_check` already
    trusts — on the **same driver instance** the calling verb opened, so
    this never launches a second browser. A hard refusal (matching how
    :func:`_playwright_driver` handles a missing playwright install) beats
    an annotated result here: an annotation is a key a `len(results)`-only
    consumer can simply never read, which is the same silence in a new
    costume. A ``SystemExit`` cannot be skipped that way — the caller gets
    a non-zero exit and an actionable fix instead of data to misinterpret.

    The refusal names **both** causes on purpose. :meth:`whoami` returns
    ``None`` for a redirect to ``/login`` *and* for a profile link that did
    not resolve inside its 5s timeout, and those are different worlds: one
    needs a login, the other needs a retry. A remedy is part of a
    diagnostic's truth claim — a message confidently naming the login
    branch sends its reader to re-authenticate a session that was never
    dead, which is this same defect one layer up.
    """
    if driver.whoami() is None:
        raise SystemExit(
            f"refusing: no X session confirmed — `{verb}` cannot tell a "
            "logged-out session from a genuinely empty result, so it "
            "refuses rather than hand back a look-alike. Two causes look "
            "identical from here: the session is dead, or X did not render "
            "the profile link inside whoami's timeout. Run "
            "`x-browser.py login` if a browser shows you logged out; retry "
            "once if it does not."
        )


def _run_login(args: list[str], paths: Paths, factory: DriverFactory) -> None:
    if "-h" in args or "--help" in args:
        raise SystemExit(LOGIN_USAGE.strip())
    _refuse_if_killed(paths)
    with factory(paths, headless=False) as driver:
        print(f"opening a browser window on the persistent profile at {paths.profile_dir}")
        print("log in to X by hand in that window, then come back here.")
        driver.wait_for_manual_login()
        handle = driver.whoami()
    if not handle:
        raise SystemExit(
            "could not verify a logged-in session — log in fully (past any "
            "two-factor step) and run `login` again"
        )
    print(f"logged in as @{handle} · profile persisted at {paths.profile_dir}")


def _run_check(args: list[str], paths: Paths, factory: DriverFactory) -> None:
    if "-h" in args or "--help" in args:
        raise SystemExit(CHECK_USAGE.strip())
    as_json = "--json" in args
    killed = kill_switch_active(paths)
    cap = cap_status(paths)
    handle = None
    error = None
    try:
        with factory(paths, headless=True) as driver:
            handle = driver.whoami()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - check must report, not crash
        error = str(exc)
    status = {
        "logged_in_as": handle,
        # Which profile was actually asked. This shim resolves its paths from
        # its *own* directory, and more than one copy of it exists (the repo's
        # examples/envoy/ copy and an account's own). Run the wrong one and it
        # finds an empty profile beside itself and reports `logged_in_as: null`
        # — byte-identical to a live copy whose session died. Measured
        # 2026-08-15: a human logged in through the account copy, a check ran
        # the repo copy, and the answer read as "the login did not take."
        # Naming the directory is what separates the two, and the third case
        # (a session that really is dead) keeps the same field to disagree
        # with.
        "profile_dir": str(paths.profile_dir),
        "kill_switch": killed,
        "send_env_armed": os.environ.get("BRR_X_BROWSER_SEND") == "1",
        **cap,
    }
    if error:
        status["error"] = error
    if as_json:
        print(json.dumps(status))
        return
    who = f"@{handle}" if handle else "not logged in"
    print(
        f"{who} · profile {paths.profile_dir} · "
        f"kill-switch {'ON' if killed else 'off'} · "
        f"cap {cap['used']}/{cap['cap']} used this hour, {cap['remaining']} left"
        + (f" · error: {error}" if error else "")
    )


def _run_read(args: list[str], paths: Paths, factory: DriverFactory) -> None:
    if "-h" in args or "--help" in args:
        raise SystemExit(READ_USAGE.strip())
    _refuse_if_killed(paths)
    rest = [a for a in args if a != "--json"]
    url = _parse_single_arg(rest, READ_USAGE, "url")
    with factory(paths, headless=True) as driver:
        _require_session(driver, "read")
        data = driver.read_url(url)
    print(json.dumps(data))


def _run_search(args: list[str], paths: Paths, factory: DriverFactory) -> None:
    if "-h" in args or "--help" in args:
        raise SystemExit(SEARCH_USAGE.strip())
    _refuse_if_killed(paths)
    tab = "top" if "--top" in args else "live"
    rest = [a for a in args if a not in ("--json", "--top")]
    query = _parse_single_arg(rest, SEARCH_USAGE, "query")
    with factory(paths, headless=True) as driver:
        _require_session(driver, "search")
        rows = driver.search(query, tab=tab)
    print(json.dumps(rows))


def _run_draft(args: list[str], paths: Paths, factory: DriverFactory) -> None:
    if "-h" in args or "--help" in args:
        raise SystemExit(DRAFT_USAGE.strip())
    _refuse_if_killed(paths)
    url, text = _parse_url_and_text(args, DRAFT_USAGE)
    paths.shots_dir.mkdir(parents=True, exist_ok=True)
    shot_path = paths.shots_dir / f"draft-{int(time.time())}.png"
    with factory(paths, headless=False) as driver:
        driver.open_reply_composer(url)
        driver.fill_text(text)
        driver.screenshot(shot_path)
    print(str(shot_path))


def _run_draft_post(args: list[str], paths: Paths, factory: DriverFactory) -> None:
    if "-h" in args or "--help" in args:
        raise SystemExit(DRAFT_POST_USAGE.strip())
    _refuse_if_killed(paths)
    text = _parse_text_only(args, DRAFT_POST_USAGE)
    paths.shots_dir.mkdir(parents=True, exist_ok=True)
    shot_path = paths.shots_dir / f"draft-post-{int(time.time())}.png"
    with factory(paths, headless=False) as driver:
        driver.open_post_composer()
        driver.fill_text(text)
        driver.screenshot(shot_path)
    print(str(shot_path))


def _run_post(args: list[str], paths: Paths, factory: DriverFactory) -> None:
    if "-h" in args or "--help" in args:
        raise SystemExit(POST_USAGE.strip())
    _refuse_if_killed(paths)
    armed, missing, rest = _send_arming(args)
    text = _parse_text_only(rest, POST_USAGE)
    if not armed:
        raise SystemExit(
            "refusing: post is disarmed — missing " + ", ".join(missing) +
            ". Both --confirm (argv) and BRR_X_BROWSER_SEND=1 (env) are "
            "required; neither arm alone is enough."
        )
    cap = cap_status(paths)
    if cap["remaining"] <= 0:
        raise SystemExit(
            f"refusing: hourly cap reached ({cap['used']}/{cap['cap']} sent "
            "in the last hour)"
        )
    with factory(paths, headless=False) as driver:
        driver.open_post_composer()
        driver.fill_text(text)
        post_id = driver.click_send()
    _record_send(paths)
    _append_receipt(paths, {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lane": "browser",
        "kind": "post",
        "text": text,
        "confirm": True,
        # Explicit absence, never invented — the toast-scrape in
        # _PlaywrightDriver.click_send() returns None on anything short of
        # a clean id, and that None is written here, not papered over.
        "post_id": post_id,
    })
    print(
        f"posted (browser lane) · id {post_id}" if post_id
        else "posted (browser lane) · id unknown (page did not yield one)"
    )


def _run_send(args: list[str], paths: Paths, factory: DriverFactory) -> None:
    if "-h" in args or "--help" in args:
        raise SystemExit(SEND_USAGE.strip())
    _refuse_if_killed(paths)
    armed, missing, rest = _send_arming(args)
    url, text = _parse_url_and_text(rest, SEND_USAGE)
    if not armed:
        raise SystemExit(
            "refusing: send is disarmed — missing " + ", ".join(missing) +
            ". Both --confirm (argv) and BRR_X_BROWSER_SEND=1 (env) are "
            "required; neither arm alone is enough."
        )
    cap = cap_status(paths)
    if cap["remaining"] <= 0:
        raise SystemExit(
            f"refusing: hourly cap reached ({cap['used']}/{cap['cap']} sent "
            "in the last hour)"
        )
    with factory(paths, headless=False) as driver:
        driver.open_reply_composer(url)
        driver.fill_text(text)
        driver.click_send()
    _record_send(paths)
    _append_receipt(paths, {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lane": "browser",
        "url": url,
        "text": text,
        "confirm": True,
    })
    print(f"sent (browser lane) · reply-to {url}")


# ── dispatch ─────────────────────────────────────────────────────────

_VERBS: dict[str, Callable[[list[str], Paths, DriverFactory], None]] = {
    "login": _run_login,
    "check": _run_check,
    "read": _run_read,
    "search": _run_search,
    "draft": _run_draft,
    "send": _run_send,
    "draft-post": _run_draft_post,
    "post": _run_post,
}


def run(argv: list[str], paths: Paths, *, driver_factory: DriverFactory | None = None) -> None:
    """Argv-compatible entrypoint: ``argv[0]`` is the verb, the rest are
    its own arguments. ``-h``/``--help`` (top-level or per-verb) resolve
    before any driver is created, kill-switch and all — the regression
    ``envoy_x.py`` exists to pin, extended to this module's own verbs.
    """
    args = list(argv)
    if not args:
        raise SystemExit(TOP_USAGE.strip())
    if args[0] in ("-h", "--help"):
        raise SystemExit(TOP_USAGE.strip())
    verb, rest = args[0], args[1:]
    handler = _VERBS.get(verb)
    if handler is None:
        raise SystemExit(TOP_USAGE.strip() + f"\n(unknown verb: {verb!r})")
    factory = driver_factory or _playwright_driver
    handler(rest, paths, factory)


def main(argv: list[str], home_dir: Path | str) -> None:
    """Convenience wrapper: :func:`run` over ``Paths.in_dir(home_dir)``."""
    run(argv, Paths.in_dir(home_dir))
