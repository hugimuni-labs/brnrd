"""Phone-operability regressions: desired recovery through the actual loom loop.

Selected recovery cases pass; remaining xfails track deferred ranks. Fixtures use
real subprocess bodies, ports, leases and relay polling; no helper is the
outcome under test. Setup failures are RuntimeErrors, not expected failures.
"""

from contextlib import contextmanager
import json
import os
import signal
import sys
import time

import pytest

from brr.daemon2.facts import Fact
from brr.daemon2.leases import LocalLeaseAuthority
from brr.loom.runtime import loom as runtime, speak
from brr.loom.runtime.channels import relay
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter
from brr.loom.runtime.project import fold, holder, unrunnable_ids
from brr.loom.runtime.selfrepo import init_self

from _step import Loom, hand_note, notices, wait_until, write_thread


def stuck(reason):
    return pytest.mark.xfail(strict=True, raises=AssertionError,
                             reason=f"stuck: {reason}")


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "home"
    init_self(path)
    (path / "loom").mkdir()
    write_thread(path, "inbox", (Home(path).thread_dir("inbox") / "README.md").read_text(),
                 "answer-fast", wait="0.1")
    return path


def thread(root, policy="answer-fast", wait="0.1"):
    write_thread(root, "work", "# Work", policy, wait=wait)


def reach(predicate, loom, seconds=5):
    try:
        wait_until(predicate, seconds, loom.dump)
    except AssertionError as exc:
        raise RuntimeError("reproduction setup failed:\n" + str(exc)) from exc


def answered(loom, ident):
    return ident in fold(loom.facts()).handled


def told_and_settled(loom, about, thread, *words, seconds=5):
    """A body on ``thread`` was shown one notice standing for ``about``, and settled it.

    The outcome a person would see: nothing the loom wrote is still owed,
    and the letter or draft the notice stood for is no longer owed either.
    """
    def shown_and_settled():
        facts = loom.facts()
        told = [f for f in facts if f.kind == "letter" and f.data.get("stands") == about]
        seen = {i for f in facts if f.kind == "shown" for i in f.data.get("ids", ())}
        return (len(told) == 1 and told[0].data["id"] in seen
                and not notices(facts) and answered(loom, about))

    wait_until(shown_and_settled, seconds, loom.dump)
    (notice,) = [f for f in loom.facts() if f.data.get("stands") == about]
    body = notice.data["body"]
    assert notice.data["to"] == f"thread:{thread}"
    assert about in body and body.endswith(f"re: {notice.data['id']}")
    for word in words:
        assert word in body
    return notice


@contextmanager
def running(root):
    loom = Loom(root)
    loom.start()
    try:
        yield loom
    finally:
        loom.halt()
        if loom.errors:
            raise RuntimeError(loom.dump())


def drain_before_death(root, monkeypatch):
    # A fast subprocess exit can overtake ingestion of its real shown port.
    # Keep the process alive until those files have drained, as a real body
    # doing work after its hook would; this removes the test's timing race.
    script = root / "drained-fake.py"
    script.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "from brr.loom.runtime.fakebody import main\n"
        "code = main(sys.argv[1:])\n"
        "room = Path(sys.argv[sys.argv.index('--room') + 1])\n"
        "deadline = time.monotonic() + 3\n"
        "while list((room / 'port' / 'out').glob('*.md')) and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "sys.exit(code)\n"
    )
    monkeypatch.setattr(runtime, "fake_argv", lambda room, policy: [
        sys.executable, str(script), "--room", str(room), "--policy", policy])


def test_an_unrunnable_letter_is_handed_to_a_body(root, monkeypatch):
    drain_before_death(root, monkeypatch)
    thread(root, "die-on-unrunnable")
    original = inject_letter(root, to="thread:work", body="unrunnable")
    with running(root) as loom:
        reach(lambda: original in unrunnable_ids(loom.facts()), loom)
        # Nothing else on its thread can wake a body, so inbox is told.
        told_and_settled(loom, original, "inbox", "quarantined", "thread work")
        # The fuse on its thread is told too, and that notice is settled as well.
        assert any("thread work is fused" in f.data["body"] for f in loom.facts()
                   if f.kind == "letter" and f.data.get("from") == "loom")


@stuck("a silent live body has no progress deadline or message-triggered replacement")
def test_new_message_can_recover_a_silent_live_body(root):
    thread(root, "hold")
    inject_letter(root, to="thread:work", body="begin")
    with running(root) as loom:
        reach(lambda: any(f.kind == "shown" for f in loom.facts()), loom)
        (loom.home.thread_dir("work") / "policy").write_text("answer-fast\n")
        fresh = inject_letter(root, to="thread:work", body="please restart")
        wait_until(lambda: answered(loom, fresh), 2, loom.dump)


def test_restart_recovers_a_body_that_outlived_its_loom(root, monkeypatch):
    thread(root, "answer-pings")
    inject_letter(root, to="thread:work", body="begin")
    first = Loom(root)
    first.start()
    pid = None
    try:
        reach(lambda: any(f.kind == "body.started" for f in first.facts())
              and any(runtime.body_alive(path) for path in (root / "rooms").iterdir()), first)
        pid = next(f.data["pid"] for f in first.facts() if f.kind == "body.started")
        # Simulate a loom dying while its separately-sessioned body survives.
        def leave_body(bodies):
            for body in bodies.values():
                body.close_log()
        with monkeypatch.context() as patch:
            patch.setattr(runtime, "_shutdown", leave_body)
            first.halt()
        with running(root) as second:
            reach(lambda: second.thread.is_alive() and bool(holder(second.facts(), "work")),
                  second)
            # The orphan still hears a person's new letter through the new loom.
            live = inject_letter(root, to="thread:work", body="while you survived")
            wait_until(lambda: answered(second, live), 2, second.dump)
            (second.home.thread_dir("work") / "policy").write_text("answer-fast\n")
            os.killpg(pid, signal.SIGKILL)
            fresh = inject_letter(root, to="thread:work", body="back after restart")
            wait_until(lambda: answered(second, fresh), 2, second.dump)
    finally:
        if first.thread.is_alive():
            first.halt()
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@stuck("a router lease granted before its fact was recorded leaves catch-up false forever")
def test_router_recovers_acquisition_without_predecessor_fact(root):
    thread(root)
    authority = LocalLeaseAuthority(root / "ledger" / "leases")
    lease = authority.acquire("router", "dead", 30)
    authority.release(lease)  # A crash/expiry before router:1 was appended.
    original = inject_letter(root, to="thread:work", body="begin")
    with running(root) as loom:
        reach(lambda: any(f.kind == "router" for f in loom.facts()), loom)
        inject_letter(root, to="thread:work", body="are you there?")
        wait_until(lambda: answered(loom, original), 2, loom.dump)


@stuck("router_ttl <= max_skew + margin renews forever with no granting window")
def test_impossible_router_window_does_not_silently_abandon_messages(root):
    thread(root)
    config = root / "loom" / "config.toml"
    config.write_text("router_ttl = 0.2\nmax_skew = 0.2\nmargin = 0.1\n")
    original = inject_letter(root, to="thread:work", body="begin")
    with running(root) as loom:
        reach(lambda: any(f.kind == "router.renewed" for f in loom.facts()), loom)
        wait_until(lambda: answered(loom, original), 2, loom.dump)


@pytest.mark.parametrize("destination", ["thread:removed", "nowhere", "thread:inbox"])
def test_unroutable_letter_gets_a_disposition(root, destination):
    readme = Home(root).thread_dir("inbox") / "README.md"
    saved = readme.read_text()
    if destination == "thread:inbox":
        readme.unlink()
    original = "p-test/lost"
    append(Home(root), Fact(kind="letter", by="person:p-test", id=original,
                           data={"id": original, "from": "p-test", "to": destination,
                                 "body": "please help"}))
    with running(root) as loom:
        reach(lambda: any(f.data.get("stands") == original for f in loom.facts()), loom)
        if destination == "thread:inbox":
            # No body can be told yet: the notice waits, owed, and says so.
            time.sleep(0.3)
            assert [f.data.get("stands") for f in notices(loom.facts())] == [original]
            readme.write_text(saved)
        told_and_settled(loom, original, "inbox", repr(destination))


def relay_event(ident):
    return {"event_id": ident, "body": "hello", "attachments": [],
            "reply_to": {"platform": "telegram", "user_id": 42, "chat_id": 42}}


def test_bad_relay_event_does_not_starve_later_chat(root, tmp_path, monkeypatch):
    broken = relay_event("broken")
    del broken["event_id"]
    state = tmp_path / "relay"
    (root / "loom" / "config.toml").write_text(f'relay_state = "{state}"\n')

    class Client:
        def __init__(self, _state):
            pass

        def pull(self, cursor):
            # As with a real cursor, the bad event keeps returning until acknowledged.
            time.sleep(0.02)
            return {"cursor": 2, "events": [broken, relay_event("later")] if cursor < 2 else []}

    monkeypatch.setattr(relay, "RelayClient", Client)
    with running(root) as loom:
        log = root / "loom" / "loom.log"
        reach(lambda: log.exists() and "relay: poll failed" in log.read_text(), loom)
        wait_until(lambda: answered(loom, "letter:relay:later"), 3.5, loom.dump)


def test_uncertain_speech_gets_a_visible_notice(root, monkeypatch):
    thread(root, "emit-channel")
    effect = speak.EFFECTS["fake"]

    def lost(home, channel, key, text, context):
        if text == "msg-0":
            raise OSError("connection lost before receipt")
        return effect(home, channel, key, text, context)

    monkeypatch.setitem(speak.EFFECTS, "fake", lost)
    inject_letter(root, to="thread:work", body="begin")
    with running(root) as loom:
        reach(lambda: any(f.kind == "speech" and f.data.get("state") == "intended"
                          for f in loom.facts()), loom)
        lost = next(f.data["key"] for f in loom.facts()
                    if f.kind == "speech" and f.data.get("state") == "intended")
        # The drafting body is still alive: it reads the notice at a boundary.
        wait_until(lambda: [f.data.get("stands") for f in notices(loom.facts())] == [lost],
                   2, loom.dump)
        (notice,) = notices(loom.facts())
        assert notice.data["to"] == "thread:work"
        assert "may or may not have arrived" in notice.data["body"]
        assert f"part {lost} " in notice.data["body"]
        assert "send a fresh message" in notice.data["body"]
        # Preserve at-most-once: an explanation, never a resend.
        time.sleep(0.3)
        path = root / "loom" / "channel-fake.jsonl"
        assert "msg-0" not in [json.loads(line)["text"] for line in path.read_text().splitlines()]
        # This body only talks; a person settles the notice by hand, and with
        # it the draft it stands for.
        hand_note(root, notice.data["id"])
        wait_until(lambda: not notices(loom.facts()) and answered(loom, lost), 2, loom.dump)


def test_bad_thread_directory_does_not_block_healthy_thread(root):
    thread(root)
    bad = root / "self" / "threads" / "bad name"
    bad.mkdir()
    (bad / "README.md").write_text("# Bad\n")
    original = inject_letter(root, to="thread:work", body="begin")
    with running(root) as loom:
        log = root / "loom" / "loom.log"
        reach(lambda: log.exists() and "unsafe thread id" in log.read_text(), loom)
        wait_until(lambda: answered(loom, original), 2, loom.dump)


def test_unavailable_channel_does_not_block_a_live_threads_new_letter(root, monkeypatch):
    thread(root, "answer-pings", wait="0.1")
    opener = inject_letter(root, to="thread:work", body="begin")
    with running(root) as loom:
        reach(lambda: answered(loom, opener), loom)
        strand, gen = holder(loom.facts(), "work")
        router_gen = max(f.data["gen"] for f in loom.facts() if f.kind == "router")
        monkeypatch.delitem(speak.EFFECTS, "fake")
        key = f"{strand}/unavailable"
        append(loom.home, Fact(kind="letter", by=f"strand:{strand}", id=key,
               data={"id": key, "from": strand, "to": "channel:fake", "body": "answer",
                     "gen": gen, "router_gen": router_gen}))
        log = root / "loom" / "loom.log"
        reach(lambda: "speak: no effect" in log.read_text(), loom)
        fresh = inject_letter(root, to="thread:work", body="new task mid-work")
        wait_until(lambda: answered(loom, fresh), 2, loom.dump)


def test_stale_speech_gets_a_visible_disposition(root):
    thread(root)
    home = Home(root)
    authority = LocalLeaseAuthority(root / "ledger" / "leases")
    lease = authority.acquire("router", home.install_id(), 30)
    append(home, Fact(kind="router", by=f"loom:{home.install_id()}", id="router:1",
                     data={"gen": 1, "install": home.install_id(), "until": lease.until}))
    strand = f"s-{home.install_id()}-old123"
    append(home, Fact(kind="lease", by=f"loom:{home.install_id()}", id="lease:work:1",
           data={"strand": strand, "thread": "work", "gen": 1, "router_gen": 1}))
    key = f"{strand}/reply"
    append(home, Fact(kind="letter", by=f"strand:{strand}", id=key,
           data={"id": key, "from": strand, "to": "channel:fake", "body": "old answer",
                 "gen": 1, "router_gen": 1}))
    authority.release(lease)
    with running(root) as loom:
        reach(lambda: any(f.kind == "router" and f.data["gen"] == 2
                          for f in loom.facts()), loom)
        # The thread that drafted it is woken, told, and settles it; never sent.
        told_and_settled(loom, key, "work", "was not sent", "send a fresh message")
        assert not (root / "loom" / "channel-fake.jsonl").exists()


def test_relay_poll_recovers_after_saved_cursor_is_repaired(root, tmp_path, monkeypatch):
    state = tmp_path / "relay"
    (root / "loom" / "config.toml").write_text(f'relay_state = "{state}"\n')
    saved = root / "loom" / "relay-cursor.json"
    saved.write_text("not JSON")

    class Client:
        def __init__(self, _state):
            pass

        def pull(self, cursor):
            time.sleep(0.02)
            return {"cursor": 1, "events": [relay_event("after-repair")] if cursor == 0 else []}

    monkeypatch.setattr(relay, "RelayClient", Client)
    with running(root) as loom:
        log = root / "loom" / "loom.log"
        reach(lambda: log.exists() and "JSONDecodeError" in log.read_text(), loom)
        saved.write_text('{"cursor": 0}\n')
        wait_until(lambda: answered(loom, "letter:relay:after-repair"), 2, loom.dump)


@stuck("inbox addressed to an absent brnrd leaves phone messages without even a refusal")
def test_unavailable_inbox_owner_explains_the_refusal_remotely(root, tmp_path, monkeypatch):
    readme = Home(root).thread_dir("inbox") / "README.md"
    readme.write_text(readme.read_text().replace('for: ""', 'for: "absent-brnrd"'))
    person = root / "self" / "people" / "owner"
    person.mkdir(parents=True)
    (person / "channels.md").write_text("telegram:42\n")
    state = tmp_path / "relay"
    (root / "loom" / "config.toml").write_text(f'relay_state = "{state}"\n')
    sent = []

    class Client:
        def __init__(self, _state):
            pass

        def pull(self, cursor):
            time.sleep(0.02)
            return {"cursor": 1, "events": [relay_event("help")] if cursor == 0 else []}

        def send(self, payload):
            sent.append(payload)
            return {"message_id": 1}

    monkeypatch.setattr(relay, "RelayClient", Client)
    with running(root) as loom:
        reach(lambda: any(f.id == "attention:for-other:inbox" for f in loom.facts()), loom)
        # Respect ownership; the requested outcome is a refusal the person sees.
        wait_until(lambda: any(p.get("body_markdown") for p in sent), 2, loom.dump)
