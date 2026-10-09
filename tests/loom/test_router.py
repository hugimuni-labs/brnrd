"""Two loom processes, one ledger, skewed clocks. Never two holders, never two sends."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from brr.loom.runtime.attention import view
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.project import fold

from _step import wait_until, write_thread

SRC = str(Path(__file__).resolve().parents[2] / "src")
A = "aaaa"
B = "bbbb"


def _config(root: Path, **values) -> None:
    path = root / "loom" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in values.items():
        if isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        else:
            lines.append(f"{key} = {value}")
    path.write_text("\n".join(lines) + "\n")


class Proc:
    def __init__(self, root: Path, install: str, skew: float = 0.0):
        self.install = install
        self.root = Path(root)
        env = os.environ.copy()
        env["PYTHONPATH"] = SRC + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["BRNRD_LOOM_CLOCK_SKEW_S"] = str(skew)
        self.err = root / "loom" / f"proc-{install}.err"
        self.err.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.err, "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "brr.loom.runtime", "loom",
             "--home", str(root), "--install", install,
             "--adapter", "fake", "--tick", "0.05"],
            env=env, stdout=subprocess.DEVNULL, stderr=self.handle,
        )

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        self.handle.close()


def _facts(root: Path):
    return read_facts(Home(root))


def _routers(facts):
    return [fact for fact in facts if fact.kind == "router"]


def _leases(facts, thread=None):
    return [
        fact for fact in facts
        if fact.kind == "lease" and (thread is None or fact.data.get("thread") == thread)
    ]


def _kill_bodies(root: Path) -> None:
    try:
        facts = _facts(root)
    except Exception:
        return
    for fact in facts:
        if fact.kind != "body.started":
            continue
        try:
            os.kill(int(fact.data["pid"]), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, KeyError, TypeError):
            pass


def _dump(root: Path, procs: list[Proc]) -> str:
    lines = []
    for proc in procs:
        lines.append(f"proc {proc.install} code {proc.proc.poll()}")
        if proc.err.is_file():
            text = proc.err.read_text(errors="replace")[-1500:]
            if text:
                lines.append(text)
    log = root / "loom" / "loom.log"
    if log.is_file():
        lines.append(log.read_text(errors="replace")[-2500:])
    try:
        for fact in _facts(root):
            lines.append(f"{fact.kind} {fact.id} {fact.data}")
    except Exception as exc:
        lines.append(f"facts unreadable: {exc}")
    return "\n".join(lines)


def _alive(procs: list[Proc], dump) -> None:
    dead = [proc for proc in procs if proc.proc.poll() not in (None, 0, -9)]
    if dead:
        raise AssertionError(dump() if callable(dump) else dump)


def _one_holder(facts) -> None:
    """No thread has two accepted leases open at any lease in the union."""
    for index, fact in enumerate(facts):
        if fact.kind != "lease":
            continue
        live: dict[str, set] = {}
        for item in fold(facts[:index + 1]).accepted:
            if item.kind not in {"lease", "released"}:
                continue
            thread = str(item.data["thread"])
            pair = (item.data["strand"], int(item.data["gen"]))
            if item.kind == "lease":
                live.setdefault(thread, set()).add(pair)
            else:
                live.get(thread, set()).discard(pair)
        for thread, pairs in live.items():
            assert len(pairs) <= 1, (thread, pairs)


def _pair(root: Path, skew_b: float = 0.0) -> list[Proc]:
    return [Proc(root, A), Proc(root, B, skew=skew_b)]


def _stop(root: Path, procs: list[Proc]) -> None:
    for proc in procs:
        proc.kill()
    _kill_bodies(root)


def test_one_router(tmp_path):
    root = tmp_path / "home"
    _config(root, name="brnrd", router_ttl=2, max_skew=0.3, margin=0.1)
    write_thread(root, "t1", "thread t1", "answer-fast", wait="3")
    inject_letter(root, to="thread:t1", body="begin")
    procs = _pair(root)
    started = time.monotonic()
    try:
        def ready():
            _alive(procs, lambda: _dump(root, procs))
            facts = _facts(root)
            routers = _routers(facts)
            return len(routers) == 1 and len(_leases(facts)) == 1

        wait_until(ready, 4, lambda: _dump(root, procs))
        assert time.monotonic() - started < 4
        facts = _facts(root)
        install = _routers(facts)[0].data["install"]
        assert {fact.data["install"] for fact in _leases(facts)} == {install}
        assert {fact.by for fact in _leases(facts)} == {f"loom:{install}"}
    finally:
        _stop(root, procs)


def test_never_two_holders(tmp_path):
    root = tmp_path / "home"
    _config(root, name="brnrd", router_ttl=2, max_skew=0.3, margin=0.1)
    for name in ("t0", "t1", "t2", "t3", "t4"):
        write_thread(root, name, f"thread {name}", "answer-fast", wait="3")
        inject_letter(root, to=f"thread:{name}", body="begin")
    procs = _pair(root)
    try:
        def ready():
            _alive(procs, lambda: _dump(root, procs))
            facts = _facts(root)
            return len({fact.data["thread"] for fact in _leases(facts)}) == 5

        wait_until(ready, 8, lambda: _dump(root, procs))
        _one_holder(_facts(root))
    finally:
        _stop(root, procs)


def _body_of(facts) -> bool:
    starts = [fact for fact in facts if fact.kind == "body.started"]
    if not starts:
        return False
    pid = int(starts[-1].data["pid"])
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _router_install(facts) -> str | None:
    routers = _routers(facts)
    if not routers:
        return None
    return str(routers[-1].data.get("install"))


def _failover(root: Path, procs: list[Proc], thread: str, letter: str):
    """Kill whichever install won the router race. The other takes the thread
    only after that strand's body is gone."""

    def held():
        _alive(procs, lambda: _dump(root, procs))
        facts = _facts(root)
        return (
            _router_install(facts) in {A, B}
            and len(_leases(facts, thread)) == 1
            and _body_of(facts)
        )

    wait_until(held, 6, lambda: _dump(root, procs))
    facts = _facts(root)
    first = _router_install(facts)
    other = B if first == A else A
    first_gen = int(_routers(facts)[-1].data.get("gen") or 0)
    pid = int([fact for fact in facts if fact.kind == "body.started"][-1].data["pid"])
    next(proc for proc in procs if proc.install == first).kill()

    def other_is_router():
        _alive([proc for proc in procs if proc.install == other], lambda: _dump(root, procs))
        return any(
            fact.data.get("install") == other and int(fact.data.get("gen") or 0) > first_gen
            for fact in _routers(_facts(root))
        )

    wait_until(other_is_router, 15, lambda: _dump(root, procs))
    # The first strand still holds the flock. The successor has not re-granted.
    facts = _facts(root)
    assert len(_leases(facts, thread)) == 1
    assert _body_of(facts)
    successor = next(
        fact for fact in _routers(facts)
        if fact.data.get("install") == other and int(fact.data.get("gen") or 0) > first_gen
    )
    policy = root / "self" / "threads" / thread / "policy"
    policy.write_text("answer-fast\n")
    os.kill(pid, signal.SIGKILL)

    def answered():
        facts = _facts(root)
        notes = [
            fact for fact in facts
            if fact.kind == "note" and fact.data.get("re") == letter
        ]
        return len(notes) == 1 and any(
            fact.data.get("install") == other for fact in _leases(facts, thread)
        )

    wait_until(answered, 12, lambda: _dump(root, procs))
    facts = _facts(root)
    other_leases = [fact for fact in _leases(facts, thread) if fact.data.get("install") == other]
    assert other_leases
    assert ordered_index(facts, successor.id) < ordered_index(facts, other_leases[0].id)
    _one_holder(facts)
    assert len([
        fact for fact in facts
        if fact.kind == "note" and fact.data.get("re") == letter
    ]) == 1


def ordered_index(facts, fact_id: str) -> int:
    return next(index for index, fact in enumerate(facts) if fact.id == fact_id)


def test_failover_keeps_a_live_strand_then_regrants(tmp_path):
    root = tmp_path / "home"
    _config(root, name="brnrd", router_ttl=2, max_skew=0.3, margin=0.1)
    write_thread(root, "tf", "thread tf", "hold", wait="3")
    letter = inject_letter(root, to="thread:tf", body="begin")
    procs = _pair(root)
    try:
        _failover(root, procs, "tf", letter)
    finally:
        _stop(root, procs)


def test_skew_still_one_holder_and_the_slow_clock_stops_first(tmp_path):
    root = tmp_path / "home"
    _config(root, name="brnrd", router_ttl=8, max_skew=5, margin=1)
    write_thread(root, "tk", "thread tk", "hold", wait="3")
    letter = inject_letter(root, to="thread:tk", body="begin")
    procs = _pair(root, skew_b=3)
    try:
        def routed():
            _alive(procs, lambda: _dump(root, procs))
            facts = _facts(root)
            started = any(fact.kind == "body.started" for fact in facts)
            return _router_install(facts) in {A, B} and len(_leases(facts, "tk")) == 1 and started

        wait_until(routed, 6, lambda: _dump(root, procs))
        # Whoever won the opening race is the clock under test. Kill that one.
        # The successor may acquire `skew` seconds early on its own clock and
        # no earlier: B is +3s, A is not.
        winner = _router_install(_facts(root))
        assert winner in {A, B}
        early = 3.0 if winner == A else 0.0
        successor = B if winner == A else A
        lease_file = json.loads((root / "ledger" / "leases" / "router.json").read_text())
        until = float(lease_file["until"])
        prior = {
            fact.id for fact in _routers(_facts(root)) if fact.data.get("install") == successor
        }
        next(proc for proc in procs if proc.install == winner).kill()
        killed = time.time()

        def successor_router():
            _alive([proc for proc in procs if proc.install == successor], lambda: _dump(root, procs))
            return any(
                fact.id not in prior and fact.data.get("install") == successor
                for fact in _routers(_facts(root))
            )

        # ttl 8 plus the 3s skew, and a little slack for the tick.
        wait_until(successor_router, 16, lambda: _dump(root, procs))
        facts = _facts(root)
        taken = next(
            fact for fact in _routers(facts)
            if fact.id not in prior and fact.data.get("install") == successor
        )
        from datetime import datetime
        taken_at = datetime.fromisoformat(taken.at).timestamp()
        assert taken_at + 0.75 >= until - early
        assert taken_at >= killed - 1
        _one_holder(facts)
        pid = int([fact for fact in facts if fact.kind == "body.started"][-1].data["pid"])
        (root / "self" / "threads" / "tk" / "policy").write_text("answer-fast\n")
        os.kill(pid, signal.SIGKILL)

        def answered():
            return any(
                fact.kind == "note" and fact.data.get("re") == letter
                for fact in _facts(root)
            )

        wait_until(answered, 16, lambda: _dump(root, procs))
        _one_holder(_facts(root))
    finally:
        _stop(root, procs)


def test_channel_keys_are_sent_at_most_once(tmp_path):
    root = tmp_path / "home"
    _config(root, name="brnrd", router_ttl=2, max_skew=0.3, margin=0.1)
    write_thread(root, "tc", "thread tc", "emit-channel", wait="3")
    inject_letter(root, to="thread:tc", body="begin")
    procs = _pair(root)
    try:
        def sending():
            _alive(procs, lambda: _dump(root, procs))
            return len(_channel_keys(root)) >= 1 or _out_count(root) >= 3

        wait_until(sending, 8, lambda: _dump(root, procs))
        holder = _router_install(_facts(root))
        assert holder in {A, B}
        next(proc for proc in procs if proc.install == holder).kill()

        def settled():
            facts = _facts(root)
            letters = [
                fact for fact in facts
                if fact.kind == "letter" and fact.data.get("to") == "channel:fake"
            ]
            if len(letters) < 10:
                return False
            keys = _channel_keys(root)
            rows = {(row.kind, row.subject) for row in view(facts)}
            missing = [str(fact.data["id"]) for fact in letters if fact.data["id"] not in keys]
            return all(
                ("maybe-sent", key) in rows or ("stale-speech", key) in rows
                for key in missing
            )

        wait_until(settled, 20, lambda: _dump(root, procs))
        keys = _channel_keys(root)
        assert len(keys) == len(set(keys))
    finally:
        _stop(root, procs)


def _channel_keys(root: Path) -> list[str]:
    path = root / "loom" / "channel-fake.jsonl"
    if not path.is_file():
        return []
    keys = []
    for line in path.read_text().splitlines():
        if line.strip():
            keys.append(json.loads(line)["key"])
    return keys


def _out_count(root: Path) -> int:
    rooms = root / "rooms"
    if not rooms.is_dir():
        return 0
    count = 0
    for room in rooms.iterdir():
        out = room / "port" / "out"
        if out.is_dir():
            count += len(list(out.glob("*.md")))
    return count


def test_an_unrunnable_letter_stops_waking_and_clear_puts_it_back(tmp_path):
    root = tmp_path / "home"
    _config(root, name="brnrd", router_ttl=2, max_skew=0.3, margin=0.1)
    write_thread(root, "tp", "thread tp", "die-on-unrunnable", wait="3")
    letter = inject_letter(root, to="thread:tp", body="unrunnable")
    procs = _pair(root)
    try:
        def set_aside():
            _alive(procs, lambda: _dump(root, procs))
            facts = _facts(root)
            starts = [fact for fact in facts if fact.kind == "body.started"]
            rows = {(row.kind, row.subject) for row in view(facts)}
            return ("unrunnable", letter) in rows and len(starts) == 2

        wait_until(set_aside, 8, lambda: _dump(root, procs))
        strand = _facts(root)[-1].data.get("strand") or [
            fact.data["strand"] for fact in _facts(root) if fact.kind == "body.started"
        ][0]
        other = inject_letter(root, to="thread:tp", body="other")

        def answered():
            facts = _facts(root)
            notes = [fact for fact in facts if fact.kind == "note" and fact.data.get("re") == other]
            starts = [
                fact for fact in facts
                if fact.kind == "body.started" and fact.data.get("strand") == strand
            ]
            return len(notes) == 1 and len(starts) == 2

        wait_until(answered, 8, lambda: _dump(root, procs))
        install = _routers(_facts(root))[-1].data["install"]
        subprocess.run(
            [sys.executable, "-m", "brr.loom.runtime", "attention",
             "--home", str(root), "--install", install, "--clear", letter],
            check=True, env={**os.environ, "PYTHONPATH": SRC},
            capture_output=True, text=True,
        )
        rows = {(row.kind, row.subject) for row in view(_facts(root))}
        assert ("unrunnable", letter) not in rows

        def woke_again():
            facts = _facts(root)
            return len([fact for fact in facts if fact.kind == "body.started"]) >= 4

        wait_until(woke_again, 8, lambda: _dump(root, procs))
    finally:
        _stop(root, procs)


def test_for_other_gets_no_lease_and_one_row(tmp_path):
    root = tmp_path / "home"
    _config(root, name="brnrd", router_ttl=2, max_skew=0.3, margin=0.1)
    readme = (
        "---\n"
        "id: to\n"
        "status: open\n"
        "tense: plan\n"
        "for: someone-else\n"
        "---\n"
        "# Held\n"
    )
    write_thread(root, "to", readme, "answer-fast", wait="3")
    inject_letter(root, to="thread:to", body="begin")
    procs = _pair(root)
    try:
        def ready():
            _alive(procs, lambda: _dump(root, procs))
            facts = _facts(root)
            rows = [row for row in view(facts) if row.kind == "for-other"]
            return len(rows) == 1 and not _leases(facts, "to")

        wait_until(ready, 6, lambda: _dump(root, procs))
        rows = [row for row in view(_facts(root)) if row.kind == "for-other"]
        assert rows[0].subject == "to"
        assert rows[0].why == "for: other brnrd"
        assert _leases(_facts(root), "to") == []
    finally:
        _stop(root, procs)
