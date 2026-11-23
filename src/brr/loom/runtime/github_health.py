"""Check the configured GitHub identity off the loom's tick, without notices."""

import os
import subprocess
import threading
import time
from queue import Empty, SimpleQueue

from brr.daemon2.facts import Fact

from .config import ConfigError, resolve_secret
from .home import atomic_write
from .ledger import LedgerConflict, append, kind_facts, read_facts

_KIND = "loom.github-credential"
_INTERVAL = 600


def refusal_since(facts, install: str) -> str:
    checks = [f for f in kind_facts(facts, _KIND) if f.data.get("install") == install]
    last = checks[-1] if checks else None
    return last.at if last is not None and last.data["refused"] else ""


def _refused(spec: str) -> bool | None:
    try:
        token = resolve_secret(spec)
    except ConfigError:
        return True
    try:
        # Works for both user and App installation tokens. GH_TOKEN takes
        # precedence over host credentials. Never surface gh's output.
        result = subprocess.run(
            ["gh", "api", "--hostname", "github.com", "--silent", "rate_limit"],
            env={**os.environ, "GH_TOKEN": token, "GH_PROMPT_DISABLED": "1", "GH_DEBUG": ""},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return False
    if b"(HTTP 401)" in result.stderr:
        return True
    return None  # No verdict from connectivity, rate limits or server errors.


class CredentialCheck:
    def __init__(self, home, log):
        self.home, self.log = home, log
        self.stamp = home.root / "loom" / "github-credential-checked"
        try:
            elapsed = time.time() - float(self.stamp.read_text())
        except (OSError, ValueError):
            elapsed = _INTERVAL
        self.next_check = time.monotonic() + max(0, _INTERVAL - max(0, elapsed))
        self.spec = ""  # clear any stale verdict if the loom starts without a source
        self.worker = None
        self.results = SimpleQueue()

    def step(self, spec: str | None) -> None:
        try:
            checked, refused = self.results.get_nowait()
        except Empty:
            pass
        else:
            if refused is not None and checked == spec:
                self._record(refused)
        if spec is None:
            if self.spec is not None:
                self._record(False)
                self.spec = None
            return
        self.spec = spec
        now = time.monotonic()
        if now < self.next_check or (self.worker is not None and self.worker.is_alive()):
            return
        self.next_check = now + _INTERVAL
        try:
            atomic_write(self.stamp, str(time.time()))
        except OSError:
            self.log(self.home, "github credential check could not record its attempt")
        self.worker = threading.Thread(target=self._check, args=(spec,),
                                       daemon=True, name="github-credential")
        self.worker.start()

    def _check(self, spec: str) -> None:
        self.results.put((spec, _refused(spec)))

    def _record(self, refused: bool) -> None:
        install = self.home.install_id()
        try:
            if refused != bool(refusal_since(read_facts(self.home), install)):
                append(self.home, Fact(kind=_KIND, by=f"loom:{install}",
                                       data={"install": install, "refused": refused}))
        except (OSError, LedgerConflict):
            self.log(self.home, "github credential reading could not be recorded")
