"""A fuel collector's error is an ask to the person, with the act that fixes it.

Each usage refresh writes ``loom/usage/<shell>.json`` with the collector's
``error`` (an expired grok key, a claude login gone). Read alone, the error
only showed as a missing row in the fuel line. On the tick the loom asks the
person, once per error: on the home thread, under its own standing wish (as
setup's onboarding asks are), mailed to the person's direct chat with the ask
linked so a reply answers it. The same error read again files nothing, even
after the person dropped or answered the ask; a different error on the same
collector ends the old ask and files a fresh one; a reading with windows and
no error ends the open ask as moot. One reading is no error: a network blip
heals by the next refresh, so an error asks only once two readings carry it.
"""

from __future__ import annotations

import json
from pathlib import Path

from brr.daemon2.facts import Fact

from .home import mint
from .ledger import append

FUEL_WISH = "every fuel collector reads"
#: What fixes a collector's error, by shell; others get the generic line.
FIXES = {
    "grok": "log in to grok again (`grok login`) or set a new key",
    "claude": "log in to claude again (`claude`, then /login)",
    "codex": "log in to codex again (`codex login`), or check the account it runs on",
}
#: How many refreshes in a row must carry the same error before it is an ask.
READINGS = 2
#: The drop ref that marks an ask the loom ended; the same error may ask again after it.
MOOT = "loom: the collector "
_SEEN: dict[tuple[Path, str], tuple[str, set[float]]] = {}


def line(shell: str, error: str) -> str:
    fix = FIXES.get(shell, f"check what the {shell} usage collector needs")
    return f"{_prefix(shell)}fails ({' '.join(error.split())[:160]}); {fix}"


def _prefix(shell: str) -> str:
    return f"fuel: the {shell} collector "


def _reading(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("at"), (int, float)):
        return None
    return payload


def _held(home, shell: str, payload: dict) -> str | None:
    """The error this shell's collector has carried for ``READINGS`` refreshes, or None."""
    key = (home.root, shell)
    error = payload.get("error")
    if not isinstance(error, str) or not error.strip():
        _SEEN.pop(key, None)
        return None
    seen, ats = _SEEN.get(key, (error, set()))
    if seen != error:
        ats = set()
    ats.add(float(payload["at"]))
    _SEEN[key] = (error, ats)
    return error if len(ats) >= READINGS else None


def observe_collectors(home, facts, armed: bool):
    """File, end or leave each collector's ask from what its last refresh wrote."""
    if not armed:
        return facts
    from . import usage
    from .wishes import told
    by, thread = f"loom:{home.install_id()}", home.main_thread()
    if not home.routable(thread):
        return facts
    for shell in usage.shells(home):
        payload = _reading(home.root / "loom/usage" / f"{shell}.json")
        if payload is None:
            continue
        error = _held(home, shell, payload)
        clean = not payload.get("error") and bool(payload.get("windows"))
        if error is None and not clean:
            continue
        found = told(facts)
        parent = next((w.id for w in found.values() if (w.thread, w.line) == (thread, FUEL_WISH)), None)
        mine = sorted((w for w in found.values() if parent and w.parent == parent
                       and w.line.startswith(_prefix(shell))), key=lambda w: (w.born, w.id))
        want = line(shell, error) if error else None
        for ask in mine:
            if ask.open and ask.line != want:
                why = "reads again" if clean else "fails otherwise now"
                facts.append(append(home, Fact(kind="wish.verdict", by=by, id="wish.verdict:" + mint(12), data={
                    "wish": ask.id, "as": "dropped", "evidence": "word", "ref": MOOT + why})))
        if want is None:
            continue
        last = mine[-1] if mine else None
        if last is not None and last.line == want and not (
                last.state == "dropped" and str((last.verdict or {}).get("ref", "")).startswith(MOOT)):
            continue
        if parent is None:
            # The loom's own wish, the one these asks unblock: nobody said it.
            parent = "w-" + mint(12)
            facts.append(append(home, Fact(kind="wish", by=by, id=parent, data={
                "wish": parent, "says": [], "from": "resident", "line": FUEL_WISH, "thread": thread})))
        ident = "w-" + mint(12)
        facts.append(append(home, Fact(kind="wish", by=by, id=ident, data={
            "wish": ident, "says": [], "from": "resident", "line": want, "thread": thread,
            "parent": parent})))
        _mail(home, facts, ident, want, thread)
    return facts


def _mail(home, facts, ident: str, what: str, thread: str) -> None:
    """The ask goes where the person is; with no direct chat it stays on the board."""
    from .loom import _log, notice
    from .verbs_wishes import _person_dm
    to = _person_dm(home, facts, None)
    if to is None:
        _log(home, f"collectors: ask {ident} is on the board only: no person has a direct channel")
        return
    notice(home, facts, "collector", ident, f"ask {ident} from thread:{thread}: {what}",
           f"Reply to this message to answer &{ident}.", channel=to, links=[ident])
