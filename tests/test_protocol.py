"""Tests for protocol module — event/response CRUD and frontmatter parsing."""

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
