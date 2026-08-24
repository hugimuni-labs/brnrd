#!/usr/bin/env python3
"""The envoy's browser half — a persistent, human-logged-in browser session
for X, giving the resident the verbs the API forbids (reply/quote to
accounts that haven't mentioned it since April 2026 — an API-only wall).

Installed shim, same shape as ``x-post.py`` / ``x-read.py``: the mechanics
live in the product tree (``brr.envoy_x_browser``, covered by tests and the
gate), this file holds nothing but its own directory. Every path (the
persistent Chromium profile, the shared receipt log, the hourly-cap config,
the kill switch, draft screenshots) resolves relative to *here* — install
by dropping this file beside the other envoy files, mode ``0700`` (it opens
a browser tied to a live logged-in session; the account/gates directory
convention next to it is also owner-only). ``brr`` and ``playwright`` must
both be importable from wherever this runs — ``pip install playwright &&
playwright install chromium`` if the second one isn't there yet; the
``check`` verb below reports that error legibly if it's missing rather
than crashing partway through a launch. Run this script with an interpreter
where ``brr`` is importable (usually ``<repo>/.venv/bin/python3``).

    <python-with-brr> x-browser.py login                       -> one-time human login
    <python-with-brr> x-browser.py check                        -> session live? as whom? cap left?
    <python-with-brr> x-browser.py read <url>                    -> structured JSON
    <python-with-brr> x-browser.py search <query>                -> structured JSON
    <python-with-brr> x-browser.py mentions                       -> structured JSON, notifications/mentions tab
    <python-with-brr> x-browser.py draft <url> --text "<s>"       -> screenshot only, never sends
    <python-with-brr> x-browser.py send <url> --text "<s>" --confirm
        -> ships disarmed: also needs BRR_X_BROWSER_SEND=1 in the environment,
           and refuses past the hourly cap
    <python-with-brr> x-browser.py draft-post --text "<s>"        -> compose, no reply target; screenshot only
    <python-with-brr> x-browser.py post --text "<s>" --confirm
        -> the compose lane's send: same two brakes, same hourly cap bucket

Nothing here posts by default — see ``x-browser.disabled`` (kill switch,
presence alone refuses every verb but ``check``) and the disarmed-send
guard in ``brr.envoy_x_browser`` for the two independent brakes on `send`.
"""
import os
import sys

try:
    from brr import envoy_x_browser
except ImportError as exc:
    # Only claim "wrong interpreter" when brr itself is what is missing.
    # A ModuleNotFoundError raised *inside* brr names some other module, and
    # telling that reader to switch interpreters sends them to a remedy that
    # cannot work -- the message would contradict the very check it tells
    # them to run. An ImportError that is not a ModuleNotFoundError (a stale
    # or half-copied install where ``brr`` imports but the submodule is gone)
    # carries name="brr" and is caught here on purpose: it used to fall
    # through as a bare traceback, which is the thing this guard exists to
    # stop.
    _missing = getattr(exc, "name", None)
    if _missing and _missing != "brr" and not _missing.startswith("brr."):
        raise
    raise SystemExit(
        "x-browser.py needs the brr package, and this interpreter cannot import it.\n"
        f"  you ran: {sys.executable}\n"
        "Use an interpreter that already has brr -- usually <repo>/.venv/bin/python3 --\n"
        "or install brr into this one: pip install -e <repo>\n"
        "The system python3 on PATH usually cannot import brr; that is the common\n"
        "cause, but a checkout that was never installed anywhere lands here too."
    ) from exc

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    envoy_x_browser.main(sys.argv[1:], HERE)
