"""Allowlist CONNECT proxy for the ``solitary`` execution environment.

Runs as a sidecar container attached to both the run's ``--internal``
network and the default bridge. The runner container can reach *only*
this proxy; the proxy tunnels TLS (CONNECT) to explicitly allowlisted
hosts — normally just the model provider endpoints for the run's Shell.

Deliberately stdlib-only and single-file: the sidecar runs it with the
runner image's ``python3`` via a read-only bind mount, so no extra image
or pip install is required. Pure tunnel, no MITM — TLS passes through
untouched.

Configuration (environment):
- ``BRR_SOLITARY_ALLOW`` — comma-separated hostnames. A leading dot
  allows the domain and every subdomain (``.example.com`` matches
  ``example.com`` and ``a.example.com``).
- ``BRR_SOLITARY_PORT`` — listen port (default 3128).

Denials and tunnel opens are logged to stdout so ``docker logs`` on the
preserved sidecar answers "what did the run try to reach".
"""

from __future__ import annotations

import os
import socket
import sys
import threading

_BUF = 65536
_HEADER_LIMIT = 65536


def _allow_entries() -> list[str]:
    raw = os.environ.get("BRR_SOLITARY_ALLOW", "")
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def host_allowed(host: str, entries: list[str]) -> bool:
    """True when *host* matches an allowlist entry.

    Plain entries match exactly; a leading-dot entry matches the bare
    domain and any subdomain. Comparison is case-insensitive and ignores
    a trailing root dot.
    """
    host = host.strip().lower().rstrip(".")
    if not host:
        return False
    for entry in entries:
        if entry.startswith("."):
            if host == entry[1:] or host.endswith(entry):
                return True
        elif host == entry:
            return True
    return False


def _log(message: str) -> None:
    print(f"[solitary-proxy] {message}", flush=True)


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(_BUF)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _read_request_head(conn: socket.socket) -> bytes:
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = conn.recv(_BUF)
        if not chunk:
            return head
        head += chunk
        if len(head) > _HEADER_LIMIT:
            return b""
    return head


def _reject(conn: socket.socket, status: str) -> None:
    try:
        conn.sendall(
            f"HTTP/1.1 {status}\r\nConnection: close\r\n\r\n".encode()
        )
    except OSError:
        pass


def _handle(conn: socket.socket, peer: str, entries: list[str]) -> None:
    try:
        head = _read_request_head(conn)
        request_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = request_line.split()
        if len(parts) != 3 or parts[0].upper() != "CONNECT":
            _log(f"deny {peer}: non-CONNECT request {request_line!r}")
            _reject(conn, "403 Forbidden")
            return
        target = parts[1]
        host, sep, port_text = target.rpartition(":")
        if not sep or not host:
            _log(f"deny {peer}: malformed target {target!r}")
            _reject(conn, "400 Bad Request")
            return
        try:
            port = int(port_text)
        except ValueError:
            _log(f"deny {peer}: malformed port {target!r}")
            _reject(conn, "400 Bad Request")
            return
        if not host_allowed(host, entries):
            _log(f"deny {peer}: {host}:{port} not on allowlist")
            _reject(conn, "403 Forbidden")
            return
        try:
            upstream = socket.create_connection((host, port), timeout=30)
        except OSError as exc:
            _log(f"fail {peer}: {host}:{port} unreachable ({exc})")
            _reject(conn, "502 Bad Gateway")
            return
        _log(f"open {peer}: {host}:{port}")
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        back = threading.Thread(
            target=_pump, args=(upstream, conn), daemon=True,
        )
        back.start()
        _pump(conn, upstream)
        back.join(timeout=5)
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main() -> int:
    entries = _allow_entries()
    port = int(os.environ.get("BRR_SOLITARY_PORT", "3128"))
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(64)
    _log(f"listening on :{port}, allow={','.join(entries) or '(nothing)'}")
    while True:
        conn, addr = server.accept()
        threading.Thread(
            target=_handle,
            args=(conn, f"{addr[0]}:{addr[1]}", entries),
            daemon=True,
        ).start()


if __name__ == "__main__":
    sys.exit(main())
