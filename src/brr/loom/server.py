"""``brnrd loom`` — the daemon's first local listener: the loom screen on
``http://127.0.0.1:<port>/loom``.

Stdlib only (``http.server.ThreadingHTTPServer``), bound to ``127.0.0.1`` and
nothing else, read-only in v1. Routes:

- ``GET /loom``, ``/loom/`` → ``static/index.html`` (the page is a sibling
  hand's; this only serves it);
- ``GET /loom/tree.json`` → :func:`brr.loom.tree.build`, the whole tracked
  tree (repo + the home's own trees), rebuilt at most every 30 s — the
  ground a map lays out at real scale, with light coming from ``state.json``;
- ``GET /loom/state.json`` → :func:`brr.loom.state.build`, rebuilt at most
  once per beat (:class:`StateCache`) so a page polling on the beat — or
  several — never doubles the work;
- ``GET /loom/bench?path=<repo>/<place>/<commit>`` → one bench file's text,
  resolved inside ``<account_home>/bench`` only;
- ``GET /loom/page/<bead|pass|item|place|heddle>?…`` → one bench page
  (``place`` takes ``path`` and optionally ``from``/``to`` and ``run``: the
  line window, and the one pass to scope the record to)
  (:mod:`brr.loom.pages`), JSON with the files it ``read``;
- ``GET /loom/events`` → Server-Sent Events: one ``state`` frame per beat while
  the client stays connected;
- ``GET /loom/<asset>`` → a file under ``static/``.

Trust is the loopback bind plus a ``Host`` check: a page on another origin
that re-binds its DNS name to ``127.0.0.1`` still sends its own name as
``Host`` and is refused, so the state (command lines, paths) is readable by
this machine's browser at this address only.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from . import state as state_mod
from . import tree as tree_mod

BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 7777
STATIC_DIR = Path(__file__).resolve().parent / "static"
_LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")
_BENCH_MAX_BYTES = 2 * 1024 * 1024


class StateCache:
    """``state.json``'s bytes, rebuilt at most once per *beat_ms*.

    The build runs under the lock, so concurrent requests inside one beat
    wait for the one build rather than starting their own.
    """

    def __init__(
        self,
        build: Callable[[], dict[str, Any]],
        beat_ms: int = state_mod.BEAT_MS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._build = build
        self._beat = max(0, beat_ms) / 1000.0
        self._clock = clock
        self._lock = threading.Lock()
        self._body: bytes | None = None
        self._payload: dict[str, Any] | None = None
        self._at = 0.0
        self.builds = 0

    def get(self) -> bytes:
        with self._lock:
            now = self._clock()
            if self._body is None or now - self._at >= self._beat:
                payload = self._build()
                self._body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self._payload = payload
                self._at = now
                self.builds += 1
            return self._body

    def payload(self) -> dict[str, Any]:
        """The same beat's state as a dict (the bench pages read it)."""
        self.get()
        with self._lock:
            return self._payload if self._payload is not None else {}


def resolve_inside(root: Path, raw: str, *, suffix: str = "") -> Path | None:
    """The existing file *raw* names under *root*, or ``None``.

    *raw* is a relative, ``/``-separated path from a URL — untrusted. Empty,
    ``.``/``..`` and hidden segments and absolute paths are refused outright;
    containment is then decided on the resolved path, so a symlink out of
    *root* is outside it too. *suffix* (``.md``) is appended when missing.
    """
    text = (raw or "").replace("\\", "/")
    if not text or text.startswith("/"):
        return None
    parts = text.split("/")
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        return None
    candidate = root.joinpath(*parts)
    if suffix and not candidate.name.endswith(suffix):
        candidate = candidate.with_name(candidate.name + suffix)
    try:
        real = candidate.resolve(strict=True)
        real.relative_to(root.resolve(strict=True))
    except (OSError, ValueError, RuntimeError):
        return None
    return real if real.is_file() else None


class LoomServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        port: int,
        cache: StateCache,
        *,
        account_home: Path | None,
        repo_root: Path | None = None,
        static_dir: Path = STATIC_DIR,
        beat_ms: int = state_mod.BEAT_MS,
    ) -> None:
        self.cache = cache
        self.repo_root = Path(repo_root) if repo_root else None
        self.account_home = Path(account_home) if account_home else None
        self.static_dir = Path(static_dir)
        self.tree_cache = StateCache(
            lambda: tree_mod.build(self.repo_root or Path.cwd(), self.account_home), beat_ms=tree_mod.TREE_BEAT_MS
        )
        self.beat_ms = beat_ms
        self.stopping = threading.Event()
        super().__init__((BIND_HOST, port), LoomHandler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{BIND_HOST}:{self.port}/loom"

    def shutdown(self) -> None:
        self.stopping.set()
        super().shutdown()


class LoomHandler(BaseHTTPRequestHandler):
    server: LoomServer
    server_version = "brnrd-loom/1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return  # a beat-rate poller would flood the terminal

    # ── plumbing ──

    def _send(self, status: int, body: bytes, content_type: str, *, head: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _text(self, status: int, text: str, *, head: bool = False) -> None:
        self._send(status, (text + "\n").encode("utf-8"), "text/plain; charset=utf-8", head=head)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return True  # HTTP/1.0 without Host: only a local client talks like that
        return host in {f"{name}:{self.server.port}" for name in _LOOPBACK_NAMES} or host in _LOOPBACK_NAMES

    # ── routes ──

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
        self._route(head=True)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._route(head=False)

    def _route(self, *, head: bool) -> None:
        if not self._host_ok():
            self._text(HTTPStatus.FORBIDDEN, "brnrd loom answers on 127.0.0.1 / localhost only", head=head)
            return
        url = urlsplit(self.path)
        path = url.path
        try:
            if path in ("/loom", "/loom/"):
                self._static("index.html", head=head)
            elif path == "/loom/state.json":
                self._send(HTTPStatus.OK, self.server.cache.get(), "application/json; charset=utf-8", head=head)
            elif path == "/loom/tree.json":
                self._send(HTTPStatus.OK, self.server.tree_cache.get(), "application/json; charset=utf-8", head=head)
            elif path == "/loom/bench":
                self._bench(parse_qs(url.query).get("path", [""])[0], head=head)
            elif path.startswith("/loom/page/"):
                self._page(path[len("/loom/page/"):], parse_qs(url.query), head=head)
            elif path == "/loom/events":
                if head:
                    self._send(HTTPStatus.OK, b"", "text/event-stream", head=True)
                else:
                    self._events()
            elif path.startswith("/loom/"):
                self._static(unquote(path[len("/loom/"):]), head=head)
            else:
                self._text(HTTPStatus.NOT_FOUND, "not here — the loom is at /loom", head=head)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _static(self, rel: str, *, head: bool) -> None:
        target = resolve_inside(self.server.static_dir, rel)
        if target is None:
            if rel == "index.html":
                self._text(HTTPStatus.NOT_FOUND, "the loom page is not built yet (brr/loom/static/index.html)", head=head)
            else:
                self._text(HTTPStatus.NOT_FOUND, "no such asset", head=head)
            return
        try:
            body = target.read_bytes()
        except OSError:
            self._text(HTTPStatus.NOT_FOUND, "no such asset", head=head)
            return
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind in ("application/javascript", "application/json", "image/svg+xml"):
            kind += "; charset=utf-8"
        self._send(HTTPStatus.OK, body, kind, head=head)

    def _page(self, kind: str, query: dict[str, list[str]], *, head: bool) -> None:
        """``/loom/page/<kind>`` — one bench page (:mod:`brr.loom.pages`) as
        JSON; ``404 {error, read}`` when the thing is not on disk."""
        from . import pages

        def arg(name: str) -> str:
            return (query.get(name) or [""])[0]

        repo_root = self.server.repo_root or Path.cwd()
        home = self.server.account_home
        if kind == "bead":
            try:
                n = int(arg("n"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "n must be an integer", "read": []}, head=head)
                return
            payload, read = pages.bead_page(repo_root, home, arg("run"), n)
        elif kind == "pass":
            payload, read = pages.pass_page(repo_root, home, arg("id"))
        elif kind == "item":
            payload, read = pages.item_page(home, arg("id"))
        elif kind == "place":
            try:
                text_from = int(arg("from")) if arg("from") else None
                text_to = int(arg("to")) if arg("to") else None
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "from/to must be integers", "read": []}, head=head)
                return
            payload, read = pages.place_page(
                repo_root, home, arg("path"), self.server.cache.payload(),
                text_from=text_from, text_to=text_to, run=arg("run") or None,
            )
        elif kind == "heddle":
            payload, read = pages.heddle_page(home, arg("slug"), self.server.cache.payload())
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": f"no page kind {kind!r}", "read": []}, head=head)
            return
        if payload is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": f"no such {kind}", "read": read}, head=head)
            return
        self._json(HTTPStatus.OK, {**payload, "read": read}, head=head)

    def _json(self, status: int, payload: dict[str, Any], *, head: bool) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", head=head)

    def _bench(self, raw: str, *, head: bool) -> None:
        home = self.server.account_home
        target = resolve_inside(home / "bench", raw, suffix=".md") if home else None
        if target is None:
            self._text(HTTPStatus.NOT_FOUND, "no such bench file", head=head)
            return
        try:
            with target.open("rb") as handle:
                body = handle.read(_BENCH_MAX_BYTES)
        except OSError:
            self._text(HTTPStatus.NOT_FOUND, "no such bench file", head=head)
            return
        self._send(HTTPStatus.OK, body, "text/markdown; charset=utf-8", head=head)

    def _events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        beat = max(50, self.server.beat_ms) / 1000.0
        stopping = self.server.stopping
        while not stopping.is_set():
            body = self.server.cache.get()
            self.wfile.write(b"event: state\ndata: " + body + b"\n\n")
            self.wfile.flush()
            if stopping.wait(beat):
                break


def make_server(
    repo_root: Path | str,
    account_home: Path | str | None,
    *,
    port: int = DEFAULT_PORT,
    static_dir: Path | None = None,
    build: Callable[[], dict[str, Any]] | None = None,
    beat_ms: int = state_mod.BEAT_MS,
) -> LoomServer:
    """A bound, not-yet-serving listener on ``127.0.0.1:<port>`` (``0`` ⇒ any free port)."""
    home = Path(account_home) if account_home else None
    builder = build or (lambda: state_mod.build(repo_root, home))
    cache = StateCache(builder, beat_ms)
    return LoomServer(
        port, cache, account_home=home, repo_root=Path(repo_root), static_dir=static_dir or STATIC_DIR, beat_ms=beat_ms
    )
