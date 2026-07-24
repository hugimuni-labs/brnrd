"""Tests for protocol module — event/response CRUD and frontmatter parsing."""

import pytest

from brr import protocol


class TestFrontmatter:
    def test_parse_basic(self):
        text = "---\nid: evt-123\nstatus: pending\n---\nbody here\n"
        fm = protocol.parse_frontmatter(text)
        assert fm["id"] == "evt-123"
        assert fm["status"] == "pending"

    def test_parse_nested(self):
        text = "---\nclaude:\n  cmd: claude --print\n  extra: --yes\n---\n"
        fm = protocol.parse_frontmatter(text)
        assert fm["claude"]["cmd"] == "claude --print"

    def test_parse_no_frontmatter(self):
        assert protocol.parse_frontmatter("# Just a doc") == {}

    def test_frontmatter_body(self):
        text = "---\nid: x\n---\nthe body\n"
        assert protocol.frontmatter_body(text) == "the body\n"

    def test_frontmatter_body_no_fm(self):
        text = "just text"
        assert protocol.frontmatter_body(text) == "just text"

    def test_coerce_bool(self):
        text = "---\na: true\nb: false\n---\n"
        fm = protocol.parse_frontmatter(text)
        assert fm["a"] is True
        assert fm["b"] is False

    def test_coerce_int(self):
        text = "---\ncount: 42\n---\n"
        fm = protocol.parse_frontmatter(text)
        assert fm["count"] == 42


class TestParseOutboxMessage:
    """Tolerant routing-frontmatter parse for outbox messages.

    Guards the footgun that misrouted live replies: a resident writing
    ``event: <id>\\n---\\nbody`` (no opening fence) used to fall through the
    strict parser, leaking the selector into the message and quoting the
    run's lead event instead of the target.
    """

    def test_canonical_fenced_block(self):
        meta, body = protocol.parse_outbox_message(
            "---\nevent: evt-9\n---\nthe answer\n")
        assert meta == {"event": "evt-9"}
        assert body == "the answer\n"

    def test_lenient_missing_opening_fence(self):
        meta, body = protocol.parse_outbox_message(
            "event: evt-9\n---\nthe answer\n")
        assert meta == {"event": "evt-9"}
        assert body == "the answer\n"

    def test_lenient_gate_with_extra_keys(self):
        meta, body = protocol.parse_outbox_message(
            "gate: forge\nhead: brr/x\nbase: main\ntitle: T\n---\nPR body\n")
        assert meta == {
            "gate": "forge", "head": "brr/x", "base": "main", "title": "T",
        }
        assert body == "PR body\n"

    def test_lenient_respawn_selector(self):
        meta, body = protocol.parse_outbox_message(
            "respawn: true\nshell: codex-mini\ndefer_until: +30m\n---\ncarry this forward\n")
        assert meta == {
            "respawn": True, "shell": "codex-mini", "defer_until": "+30m",
        }
        assert body == "carry this forward\n"

    def test_lenient_spawn_selector(self):
        meta, body = protocol.parse_outbox_message(
            "spawn: true\nshell: codex\n---\nbounded child\n")
        assert meta == {"spawn": True, "shell": "codex"}
        assert body == "bounded child\n"

    def test_canonical_fenced_spawn_selector(self):
        meta, body = protocol.parse_outbox_message(
            "---\nspawn: true\nshell: codex\n---\nbounded child\n")
        assert meta == {"spawn": True, "shell": "codex"}
        assert body == "bounded child\n"

    def test_lenient_runner_policy_selector(self):
        meta, body = protocol.parse_outbox_message(
            "runner_policy: propose\nscope: account\n---\nPrefer economy runners.\n")
        assert meta == {"runner_policy": "propose", "scope": "account"}
        assert body == "Prefer economy runners.\n"

    def test_plain_message_with_dividers_is_not_frontmatter(self):
        # A PLAN-style message leads with prose and uses --- as a section
        # divider — it must never be mistaken for routing frontmatter.
        text = "Here is the PLAN.\n\n---\n\n1. step one\n"
        meta, body = protocol.parse_outbox_message(text)
        assert meta == {}
        assert body == text

    def test_leading_non_routing_key_is_left_alone(self):
        # A message that happens to start "note: ..." is not a routing
        # selector, so it stays a plain body.
        text = "note: heads up\n---\nmore\n"
        meta, body = protocol.parse_outbox_message(text)
        assert meta == {}
        assert body == text

    def test_routing_selector_leading_prose_stays_body(self):
        # A routing key leading a prose value (spaces) is not a selector;
        # leave intact rather than guess an event id out of prose.
        text = "event: the meeting is moved\nsee you there\n"
        meta, body = protocol.parse_outbox_message(text)
        assert meta == {}
        assert body == text

    def test_routing_selector_leading_prose_with_blank_stays_body(self):
        # Same guard across a blank line: the token test, not the
        # terminator shape, is what decides.
        text = "event: the meeting is moved\n\nsee you there\n"
        meta, body = protocol.parse_outbox_message(text)
        assert meta == {}
        assert body == text

    def test_lenient_blank_line_terminated_block(self):
        # Found live 2026-07-18: ``event: <id>`` + blank line + body — the
        # natural Markdown shape — used to degrade silently to a
        # current-thread message with the selector leaked as prose.
        meta, body = protocol.parse_outbox_message(
            "event: evt-9\n\nthe answer\n")
        assert meta == {"event": "evt-9"}
        assert body == "the answer\n"

    def test_lenient_blank_line_terminated_spawn_block(self):
        meta, body = protocol.parse_outbox_message(
            "spawn: true\nshell: codex\ncore: gpt-5.6-terra\n\n# Task: do the thing\n\ndetails\n")
        assert meta == {"spawn": True, "shell": "codex", "core": "gpt-5.6-terra"}
        assert body == "# Task: do the thing\n\ndetails\n"

    def test_lenient_heading_terminated_block(self):
        # A validated selector followed directly by a heading: the block
        # ends where the kv-lines do; nothing leaks, nothing is dropped.
        meta, body = protocol.parse_outbox_message(
            "spawn: true\n# Task: sweep\nprose\n")
        assert meta == {"spawn": True}
        assert body == "# Task: sweep\nprose\n"

    def test_lenient_blank_then_fence_still_fences(self):
        # A blank line between the kv-block and its ``---`` fence: the
        # fence still terminates, and never leaks into the body.
        meta, body = protocol.parse_outbox_message(
            "event: evt-9\n\n---\nthe answer\n")
        assert meta == {"event": "evt-9"}
        assert body == "the answer\n"


class TestEvents:
    def test_create_and_list(self, tmp_path):
        inbox = tmp_path / "inbox"
        protocol.create_event(inbox, source="test", body="hello")
        events = protocol.list_pending(inbox)
        assert len(events) == 1
        assert events[0]["source"] == "test"
        assert events[0]["body"] == "hello"
        assert events[0]["status"] == "pending"

    def test_set_status(self, tmp_path):
        inbox = tmp_path / "inbox"
        protocol.create_event(inbox, source="test", body="task")
        events = protocol.list_pending(inbox)
        ev = events[0]
        protocol.set_status(ev, "processing")
        reloaded = protocol.list_pending(inbox)
        assert len(reloaded) == 1
        assert reloaded[0]["status"] == "processing"

    def test_done_events(self, tmp_path):
        inbox = tmp_path / "inbox"
        protocol.create_event(inbox, source="tg", body="do it")
        events = protocol.list_pending(inbox)
        protocol.set_status(events[0], "done")
        assert protocol.list_pending(inbox) == []
        done = protocol.list_done(inbox, "tg")
        assert len(done) == 1

    def test_metadata(self, tmp_path):
        inbox = tmp_path / "inbox"
        protocol.create_event(
            inbox, source="telegram", body="hi",
            telegram_chat_id=123, telegram_user="alice",
        )
        ev = protocol.list_pending(inbox)[0]
        assert ev["telegram_chat_id"] == 123
        assert ev["telegram_user"] == "alice"

    def test_update_event_meta_sets_and_clears_key(self, tmp_path):
        inbox = tmp_path / "inbox"
        protocol.create_event(inbox, source="test", body="hello")
        ev = protocol.list_pending(inbox)[0]

        protocol.update_event_meta(ev, defer_until="2099-01-01T00:00:00Z")
        updated = protocol.list_pending(inbox)[0]
        assert updated["defer_until"] == "2099-01-01T00:00:00Z"

        protocol.update_event_meta(updated, defer_until=None)
        cleared = protocol.list_pending(inbox)[0]
        assert "defer_until" not in cleared

    def test_list_dispatchable_skips_future_deferred_events(self, tmp_path):
        inbox = tmp_path / "inbox"
        protocol.create_event(
            inbox,
            source="test",
            body="wait",
            defer_until="2099-01-01T00:00:00Z",
        )
        protocol.create_event(inbox, source="test", body="now")

        all_pending = protocol.list_pending(inbox)
        dispatchable = protocol.list_dispatchable(inbox, now=0)

        assert [ev["body"] for ev in all_pending] == ["wait", "now"]
        assert [ev["body"] for ev in dispatchable] == ["now"]

    def test_list_dispatchable_includes_expired_or_invalid_defer(self, tmp_path):
        inbox = tmp_path / "inbox"
        protocol.create_event(
            inbox,
            source="test",
            body="expired",
            defer_until="2000-01-01T00:00:00Z",
        )
        protocol.create_event(
            inbox,
            source="test",
            body="invalid",
            defer_until="not-a-time",
        )

        dispatchable = protocol.list_dispatchable(inbox)

        assert [ev["body"] for ev in dispatchable] == ["expired", "invalid"]


class TestAttachments:
    """Image attachments — event files referencing local downloaded files.

    The shape both gates converge on (Telegram photos/documents, GitHub
    inline image links): ``create_event(attachment_files=[...])`` moves
    the caller's already-downloaded files into
    ``attachments_dir_for_event`` and records their names, and
    ``event_attachment_paths`` resolves that field back to real paths.
    """

    def test_single_attachment_keeps_its_own_name(self, tmp_path):
        inbox = tmp_path / "inbox"
        src_dir = tmp_path / "downloads"
        src_dir.mkdir()
        src = src_dir / "photo.jpg"
        src.write_bytes(b"fake-jpeg-bytes")

        protocol.create_event(
            inbox, source="telegram", body="look at this",
            attachment_files=[src],
        )
        ev = protocol.list_pending(inbox)[0]

        assert ev["attachments"] == "photo.jpg"
        paths = protocol.event_attachment_paths(ev)
        assert len(paths) == 1
        assert paths[0].read_bytes() == b"fake-jpeg-bytes"
        # The source file was moved, not copied — the caller's temp file
        # (or temp dir) is fully consumed.
        assert not src.exists()

    def test_multiple_attachments_get_index_prefixed(self, tmp_path):
        inbox = tmp_path / "inbox"
        src_dir = tmp_path / "downloads"
        src_dir.mkdir()
        first = src_dir / "a.png"
        second = src_dir / "b.png"
        first.write_bytes(b"one")
        second.write_bytes(b"two")

        protocol.create_event(
            inbox, source="github", body="two screenshots",
            attachment_files=[first, second],
        )
        ev = protocol.list_pending(inbox)[0]

        assert ev["attachments"] == "00-a.png,01-b.png"
        paths = protocol.event_attachment_paths(ev)
        assert [p.name for p in paths] == ["00-a.png", "01-b.png"]

    def test_no_attachments_field_when_none_given(self, tmp_path):
        inbox = tmp_path / "inbox"
        protocol.create_event(inbox, source="test", body="plain")
        ev = protocol.list_pending(inbox)[0]

        assert "attachments" not in ev
        assert protocol.event_attachment_paths(ev) == []

    def test_event_attachment_paths_drops_missing_files(self, tmp_path):
        # A hand-edited event file, or an attachments dir already cleaned
        # up, must degrade to an empty list rather than handing back a
        # dangling path for Read to fail on.
        inbox = tmp_path / "inbox"
        path = protocol.create_event(inbox, source="test", body="x")
        ev = protocol.list_pending(inbox)[0]
        protocol.update_event_meta(ev, attachments="ghost.png")

        assert protocol.event_attachment_paths(ev) == []

    def test_cleanup_removes_attachments_dir(self, tmp_path):
        inbox = tmp_path / "inbox"
        responses = tmp_path / "responses"
        src = tmp_path / "src.png"
        src.write_bytes(b"data")
        path = protocol.create_event(
            inbox, source="test", body="x", attachment_files=[src],
        )
        eid = protocol.list_pending(inbox)[0]["id"]
        adir = protocol.attachments_dir_for_event(inbox, eid)
        assert adir.exists()

        protocol.cleanup(path, protocol.response_path(responses, eid))

        assert not adir.exists()


class TestResponses:
    def test_write_and_read(self, tmp_path):
        responses = tmp_path / "responses"
        protocol.write_response(responses, "evt-1", "done!")
        assert protocol.response_exists(responses, "evt-1")
        assert protocol.read_response(responses, "evt-1") == "done!"

    def test_missing_response(self, tmp_path):
        responses = tmp_path / "responses"
        responses.mkdir()
        assert protocol.read_response(responses, "evt-nope") is None

    def test_cleanup(self, tmp_path):
        inbox = tmp_path / "inbox"
        responses = tmp_path / "responses"
        path = protocol.create_event(inbox, source="x", body="y")
        rpath = protocol.write_response(responses, "evt-1", "ok")
        protocol.cleanup(path, rpath)
        assert not path.exists()
        assert not rpath.exists()


class TestListActive:
    def test_includes_processing_and_done(self, tmp_path):
        inbox = tmp_path / "inbox"
        protocol.create_event(inbox, source="tg", body="a")
        protocol.create_event(inbox, source="tg", body="b")
        protocol.create_event(inbox, source="tg", body="c")
        evs = protocol.list_pending(inbox)
        protocol.set_status(evs[0], "processing")
        protocol.set_status(evs[1], "done")
        # evs[2] stays pending
        active = protocol.list_active(inbox, "tg")
        bodies = {e["body"] for e in active}
        assert bodies == {"a", "b"}  # pending excluded

    def test_source_filtered(self, tmp_path):
        inbox = tmp_path / "inbox"
        protocol.create_event(inbox, source="tg", body="a")
        protocol.create_event(inbox, source="slack", body="b")
        for ev in protocol.list_pending(inbox):
            protocol.set_status(ev, "processing")
        assert [e["body"] for e in protocol.list_active(inbox, "tg")] == ["a"]


class TestPartials:
    def test_write_list_order_and_read(self, tmp_path):
        responses = tmp_path / "responses"
        protocol.write_partial(responses, "evt-1", "first")
        protocol.write_partial(responses, "evt-1", "second")
        partials = protocol.list_partials(responses, "evt-1")
        assert [p.name for p in partials] == ["000001.md", "000002.md"]
        assert protocol.read_partial(partials[0]) == "first"
        assert protocol.read_partial(partials[1]) == "second"

    def test_sequence_continues_past_present_files(self, tmp_path):
        responses = tmp_path / "responses"
        first = protocol.write_partial(responses, "evt-1", "first")
        protocol.write_partial(responses, "evt-1", "second")
        # The gate delivers + deletes the *oldest* first; a partial
        # written after must still sort *after* the present ones, so the
        # not-yet-delivered tail keeps its chronological order.
        first.unlink()
        protocol.write_partial(responses, "evt-1", "third")
        bodies = [protocol.read_partial(p)
                  for p in protocol.list_partials(responses, "evt-1")]
        assert bodies == ["second", "third"]

    def test_no_partials_is_empty(self, tmp_path):
        responses = tmp_path / "responses"
        assert protocol.list_partials(responses, "evt-nope") == []

    def test_cleanup_removes_partials_dir(self, tmp_path):
        inbox = tmp_path / "inbox"
        responses = tmp_path / "responses"
        path = protocol.create_event(inbox, source="x", body="y")
        rpath = protocol.write_response(responses, "evt-1", "ok")
        protocol.write_partial(responses, "evt-1", "interim")
        pdir = protocol.partials_dir(responses, "evt-1")
        assert pdir.exists()
        protocol.cleanup(path, rpath, pdir)
        assert not pdir.exists()
        assert not rpath.exists()


class TestInboxWake:
    """The process-local wake signal create_event raises for the daemon loop."""

    def test_pending_event_sets_wake(self, tmp_path):
        protocol.inbox_wake().clear()
        protocol.create_event(tmp_path / "inbox", source="x", body="hi")
        assert protocol.inbox_wake().is_set()

    def test_done_event_does_not_set_wake(self, tmp_path):
        # Outbound-only events are delivered by gate threads, not the
        # spawn loop, so they must not wake it.
        protocol.inbox_wake().clear()
        protocol.create_event(
            tmp_path / "inbox", source="x", body="hi", status="done",
        )
        assert not protocol.inbox_wake().is_set()

    def test_wake_is_a_stable_singleton(self):
        assert protocol.inbox_wake() is protocol.inbox_wake()


class TestCreateEventMetaValidation:
    """Guards the frontmatter injection seam (§7 S3).

    Every test here is a security property, not a convenience assertion.
    The headline invariant: a meta *value* containing ``\\n`` must never
    produce extra frontmatter fields on the far side of ``parse_frontmatter``.
    ``trust_tier`` is the highest-value target because it is the entire
    basis of ``trust.resolve_tier`` — forging it escalates any run to
    ``owner``.
    """

    # ── newline / CR injection ──────────────────────────────────────────

    def test_newline_in_value_raises(self, tmp_path):
        """The primary guard: a newline in any value must raise ValueError."""
        inbox = tmp_path / "inbox"
        with pytest.raises(ValueError, match="newline"):
            protocol.create_event(
                inbox, source="test", body="hi",
                some_key="x\ntrust_tier: owner",
            )

    def test_newline_escalation_without_guard(self, tmp_path):
        """Negative proof: shows the vulnerability the raise is closing.

        Without the seam guard the injected ``trust_tier: owner`` line is
        parsed as a real frontmatter field and ``resolve_tier`` escalates
        the event to ``owner``.  This test documents the *shape* of the
        attack so a future reader cannot dismiss the guard as defensive
        theatre — it also drives the guard red before the fix lands.

        Assertion: ``create_event`` raises (the guard is present).  If
        someone removes the guard this test should flip to documenting the
        attack, not silently pass — so we assert the raise, not the
        absence of a trust_tier field.
        """
        from brr import trust
        inbox = tmp_path / "inbox"
        with pytest.raises(ValueError):
            protocol.create_event(
                inbox, source="test", body="hi",
                some_key="x\ntrust_tier: owner",
            )
        # If the raise were absent the attack would have worked:
        # forge the file directly to prove the parser's behaviour.
        eid = protocol._generate_id()
        path = inbox / f"{eid}.md"
        inbox.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nid: {eid}\nsource: test\nstatus: pending\n"
            f"some_key: x\ntrust_tier: owner\ncreated: 2026-01-01T00:00:00Z\n---\nhi\n",
            encoding="utf-8",
        )
        ev = protocol.list_pending(inbox)[0]
        assert trust.resolve_tier(ev) == "owner", (
            "Parser vulnerability confirmed: injected trust_tier is parsed "
            "as a real field.  The seam guard in create_event is what "
            "prevents this from being reachable via the normal call path."
        )

    def test_carriage_return_in_value_raises(self, tmp_path):
        inbox = tmp_path / "inbox"
        with pytest.raises(ValueError, match="newline"):
            protocol.create_event(
                inbox, source="test", body="hi", evil="foo\rbar",
            )

    def test_value_containing_separator_line_raises(self, tmp_path):
        """A value containing ``---`` on its own line must raise."""
        inbox = tmp_path / "inbox"
        with pytest.raises(ValueError):
            protocol.create_event(
                inbox, source="test", body="hi",
                evil="before\n---\nafter",
            )

    def test_newline_in_key_raises(self, tmp_path):
        """Keys that are not plain identifiers must also be rejected."""
        inbox = tmp_path / "inbox"
        # dict() literal syntax can't put \n in a key; use ** unpacking
        with pytest.raises(ValueError):
            protocol.create_event(
                inbox, source="test", body="hi",
                **{"bad\nkey": "val"},
            )

    # ── reserved key rejection ──────────────────────────────────────────
    #
    # ``source`` and ``status`` are explicit parameters of ``create_event``
    # and are therefore captured by Python's own function signature — they
    # can never reach ``**meta`` and do not need a seam guard.  The three
    # keys below (``id``, ``created``, ``attachments``) are NOT explicit
    # params and CAN be injected via ``**meta``.

    def test_reserved_key_id_raises(self, tmp_path):
        with pytest.raises(ValueError, match="reserved"):
            protocol.create_event(
                tmp_path / "inbox", source="test", body="hi", id="evil",
            )

    def test_reserved_key_created_raises(self, tmp_path):
        with pytest.raises(ValueError, match="reserved"):
            protocol.create_event(
                tmp_path / "inbox", source="test", body="hi", created="evil",
            )

    def test_reserved_key_attachments_raises(self, tmp_path):
        with pytest.raises(ValueError, match="reserved"):
            protocol.create_event(
                tmp_path / "inbox", source="test", body="hi", attachments="evil",
            )

    # ── trust_tier must remain legal (gate seam) ────────────────────────

    def test_trust_tier_collaborator_is_accepted(self, tmp_path):
        """Gates stamp trust_tier legitimately — must not be blocked."""
        inbox = tmp_path / "inbox"
        protocol.create_event(
            inbox, source="telegram", body="hello",
            trust_tier="collaborator",
        )
        ev = protocol.list_pending(inbox)[0]
        assert ev["trust_tier"] == "collaborator"

    def test_trust_tier_owner_is_accepted(self, tmp_path):
        """spawn/respawn inheritance path passes trust_tier=owner — must work."""
        inbox = tmp_path / "inbox"
        protocol.create_event(
            inbox, source="spawn", body="work",
            trust_tier="owner",
        )
        ev = protocol.list_pending(inbox)[0]
        assert ev["trust_tier"] == "owner"

    # ── well-formed callers must be unaffected ──────────────────────────

    def test_normal_meta_unaffected(self, tmp_path):
        inbox = tmp_path / "inbox"
        protocol.create_event(
            inbox, source="telegram", body="task",
            telegram_chat_id=42,
            telegram_user="Alice",
            telegram_username="alice_tg",
            telegram_message_id=7,
        )
        ev = protocol.list_pending(inbox)[0]
        assert ev["telegram_chat_id"] == 42
        assert ev["telegram_user"] == "Alice"
