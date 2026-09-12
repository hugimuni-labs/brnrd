"""The failover the correspondent is told about (#1930), and the one the
ledger can count (#1929).

Measured on ``run-260911-1808-1r0b``, 2026-09-11: the Claude CLI died on a
``401 OAuth access token has been revoked`` at 19:19:17Z, brr fell back to
``codex-gpt-5.6-sol``, and the conversation continued — with the person on
the other end never told that the author had changed. The swap was announced
to the daemon log, to the ``retrying`` emit, to the run card's attempts block,
and — via ``fallback_notice`` — into the resident's own wake prompt. Everyone
was told except the one party waiting for the answer.

The failure mode is the ugly one: **the better the recovery works, the more
silent it is.**
"""

from brr import daemon, protocol
from brr.gates import runtime as gate_runtime
from brr.run import Run


def _event(tmp_path, eid: str = "evt-sub", status: str = "processing"):
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    (inbox_dir / f"{eid}.md").write_text(
        f"---\nid: {eid}\nsource: telegram\nstatus: {status}\n---\nwhat's up?\n",
        encoding="utf-8",
    )
    responses = tmp_path / ".brr" / "responses"
    responses.mkdir(parents=True, exist_ok=True)
    return inbox_dir, responses


class TestTheCorrespondentIsTold:
    def test_the_swap_reaches_the_chat_while_the_run_continues(self, tmp_path):
        inbox_dir, responses = _event(tmp_path)

        daemon._announce_runner_substitution(
            responses, "evt-sub", "claude-opus", "codex-gpt-5.6-sol", "auth_error",
        )

        sent: list[str] = []

        def _deliver(_event, body):
            sent.append(body)
            return {"message_id": len(sent)}

        gate_runtime.deliver_stream(inbox_dir, responses, "telegram", _deliver)

        assert len(sent) == 1
        body = sent[0]
        # Both names, because "a runner changed" without saying which is the
        # same silence with a noise in front of it.
        assert "claude-opus" in body and "codex-gpt-5.6-sol" in body
        # The cause in the reader's language, not the taxonomy's.
        assert "lost its credential" in body
        # And the part that actually costs him: continuity did not survive.
        assert "resumed from" in body

    def test_an_unmapped_failure_kind_still_says_something_true(self, tmp_path):
        """A new ``failure_kind`` must degrade to a true sentence, never to a
        raw enum leaking into a chat message or to no message at all."""
        _, responses = _event(tmp_path)
        daemon._announce_runner_substitution(
            responses, "evt-sub", "a", "b", "some_future_kind",
        )
        body = protocol.read_partial(
            sorted(protocol.partials_dir(responses, "evt-sub").glob("*.md"))[0]
        )
        assert "failed operationally" in body
        assert "some_future_kind" not in body

    def test_a_broken_responses_dir_never_sinks_the_recovery(self, tmp_path):
        """An announcement that could kill a fallback would be worse than the
        silence it replaces."""
        blocked = tmp_path / "not-a-dir"
        blocked.write_text("i am a file", encoding="utf-8")
        daemon._announce_runner_substitution(
            blocked, "evt-sub", "a", "b", "auth_error",
        )  # must not raise


class TestTheLedgerCanCountIt:
    def test_the_substitution_is_stamped_on_the_manifest(self):
        task = Run(id="run-x", event_id="evt-x", body="", source="telegram", meta={})
        daemon._record_runner_substitution(
            task, "claude-opus", "codex-gpt-5.6-sol", "auth_error",
        )
        daemon._record_runner_substitution(
            task, "codex-gpt-5.6-sol", "claude-haiku", "quota_exhausted",
        )
        rows = task.meta["runner_substitutions"]
        # A list, not a scalar: two failures in one run are two rows.
        assert [r["from"] for r in rows] == ["claude-opus", "codex-gpt-5.6-sol"]
        assert rows[0]["failure_kind"] == "auth_error"
        assert rows[0]["at"].endswith("Z")
