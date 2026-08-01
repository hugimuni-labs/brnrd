"""The credential-free forge-read portal (#516)."""

from __future__ import annotations

from brr import daemon, forge_fetch, protocol
from brr.run import Run


def _issue_payload(body: str = "issue body") -> dict:
    return {
        "data": {
            "repository": {
                "issueOrPullRequest": {
                    "__typename": "Issue",
                    "number": 7,
                    "title": "Seven",
                    "body": body,
                    "comments": {"totalCount": 0, "nodes": []},
                },
            },
            "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": "later"},
        },
    }


def test_fetch_view_uses_one_fixed_repo_scoped_query(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(forge_fetch, "resolve_host_token", lambda _brr: "gate-token")

    def request(token, method, path, *, body):
        calls.append((token, method, path, body))
        return _issue_payload(), {}

    monkeypatch.setattr(forge_fetch.client, "_request", request)

    result = forge_fetch.fetch_view(tmp_path, "acme/widget", "issue", 7)

    assert "issue body" in result.body
    assert calls[0][:3] == ("gate-token", "POST", "/graphql")
    request_body = calls[0][3]
    assert request_body["variables"] == {
        "owner": "acme", "name": "widget", "number": 7,
    }
    assert "mutation" not in request_body["query"].casefold()
    assert "search(" not in request_body["query"].casefold()
    assert "patch" not in request_body["query"].casefold()
    assert "files(first: 100)" in request_body["query"]
    assert "comments(first: 50)" in request_body["query"]


def test_fetch_view_caps_oversized_response(monkeypatch, tmp_path):
    monkeypatch.setattr(forge_fetch, "resolve_host_token", lambda _brr: "gate-token")
    monkeypatch.setattr(
        forge_fetch,
        "_graphql",
        lambda _token, _variables: _issue_payload("é" * forge_fetch.MAX_RESPONSE_BYTES),
    )

    result = forge_fetch.fetch_view(tmp_path, "acme/widget", "issue", 7)

    assert result.truncated is True
    assert len(result.body.encode("utf-8")) <= forge_fetch.MAX_RESPONSE_BYTES
    assert result.body.endswith(
        f"[forge response truncated at {forge_fetch.MAX_RESPONSE_BYTES} bytes]"
    )


def _drain(
    tmp_path,
    monkeypatch,
    frontmatter: str,
    *,
    response: str = "# Forge read\n\npermitted",
):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    inbox.mkdir(parents=True)
    outbox.mkdir(parents=True)
    (outbox / "fetch.md").write_text(frontmatter, encoding="utf-8")
    monkeypatch.setattr(
        forge_fetch,
        "fetch_view",
        lambda *_args: forge_fetch.FetchResult(
            response, len(response.encode("utf-8")), False,
        ),
    )
    monkeypatch.setattr(daemon.updates, "emit", lambda _brr, _packet: None)
    emit = daemon._WorkerEmit(
        brr_dir=brr_dir,
        conversation_key="telegram:1:",
        event_id="evt-current",
    )
    task = Run(
        id="run-fetch",
        event_id="evt-current",
        body="work",
        source="github",
        meta={"repo_label": "acme/widget"},
    )
    promoted = daemon._drain_outbox(
        emit,
        task,
        responses,
        "evt-current",
        outbox,
        inbox,
        repo_root=tmp_path,
    )
    return promoted, task, inbox, outbox


def test_permitted_issue_read_returns_as_pending_run_edge_event(tmp_path, monkeypatch):
    promoted, _task, inbox, outbox = _drain(
        tmp_path,
        monkeypatch,
        "---\nfetch: issue\nnumber: 7\n---\n",
    )

    assert promoted == 1
    pending = protocol.list_pending(inbox)
    assert len(pending) == 1
    assert pending[0]["source"] == "forge_fetch"
    assert pending[0]["forge_fetch_status"] == "ok"
    assert pending[0]["spawn_message_for_event"] == "evt-current"
    assert "permitted" in pending[0]["body"]
    assert not daemon._read_outbox_notices(outbox)


def test_write_verb_is_refused_and_visible(tmp_path, monkeypatch):
    promoted, _task, inbox, outbox = _drain(
        tmp_path,
        monkeypatch,
        "---\nfetch: write\nnumber: 7\n---\n",
    )

    assert promoted == 1
    pending = protocol.list_pending(inbox)
    assert pending[0]["forge_fetch_status"] == "refused"
    assert "not a read view" in pending[0]["body"]
    notices = daemon._read_outbox_notices(outbox)
    assert any("fetch refused" in row["text"] for row in notices)


def test_request_naming_another_repo_is_refused_and_visible(tmp_path, monkeypatch):
    promoted, _task, inbox, outbox = _drain(
        tmp_path,
        monkeypatch,
        "---\nfetch: issue\nnumber: 7\nrepo: other/private\n---\n",
    )

    assert promoted == 1
    pending = protocol.list_pending(inbox)
    assert pending[0]["forge_fetch_status"] == "refused"
    assert "bound to acme/widget" in pending[0]["body"]
    notices = daemon._read_outbox_notices(outbox)
    assert any("cross-repository read refused" in row["text"] for row in notices)


def test_arbitrary_method_field_is_refused(tmp_path, monkeypatch):
    promoted, _task, inbox, _outbox = _drain(
        tmp_path,
        monkeypatch,
        "---\nfetch: issue\nnumber: 7\nmethod: PATCH\n---\n",
    )

    assert promoted == 1
    pending = protocol.list_pending(inbox)
    assert pending[0]["forge_fetch_status"] == "refused"
    assert "refused fields: method" in pending[0]["body"]


def test_request_body_refusal_is_visible(tmp_path, monkeypatch):
    promoted, _task, inbox, outbox = _drain(
        tmp_path,
        monkeypatch,
        "---\nfetch: issue\nnumber: 7\n---\nthis must not become query input",
    )

    assert promoted == 1
    pending = protocol.list_pending(inbox)
    assert pending[0]["forge_fetch_status"] == "refused"
    assert "request bodies are not accepted" in pending[0]["body"]
    notices = daemon._read_outbox_notices(outbox)
    assert any("request bodies are not accepted" in row["text"] for row in notices)


def test_pathological_number_is_refused_without_conversion(tmp_path, monkeypatch):
    promoted, _task, inbox, _outbox = _drain(
        tmp_path,
        monkeypatch,
        "---\nfetch: issue\nnumber: " + ("9" * 10_000) + "\n---\n",
    )

    assert promoted == 1
    pending = protocol.list_pending(inbox)
    assert pending[0]["forge_fetch_status"] == "refused"
    assert "positive 32-bit decimal integer" in pending[0]["body"]


def test_fifth_forge_read_in_one_run_is_rate_limited(tmp_path, monkeypatch):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    inbox.mkdir(parents=True)
    outbox.mkdir(parents=True)
    for index in range(forge_fetch.MAX_REQUESTS_PER_RUN + 1):
        (outbox / f"{index}.md").write_text(
            "---\nfetch: issue\nnumber: 7\n---\n", encoding="utf-8",
        )
    monkeypatch.setattr(
        forge_fetch,
        "fetch_view",
        lambda *_args: forge_fetch.FetchResult("ok", 2, False),
    )
    monkeypatch.setattr(daemon.updates, "emit", lambda _brr, _packet: None)
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="", event_id="evt-current")
    task = Run(
        id="run-fetch",
        event_id="evt-current",
        body="work",
        meta={"repo_label": "acme/widget"},
    )

    assert daemon._drain_outbox(
        emit, task, responses, "evt-current", outbox, inbox,
    ) == forge_fetch.MAX_REQUESTS_PER_RUN + 1
    pending = protocol.list_pending(inbox)
    statuses = [event["forge_fetch_status"] for event in pending]
    assert statuses.count("ok") == forge_fetch.MAX_REQUESTS_PER_RUN
    assert statuses.count("refused") == 1
    assert "run limit reached" in pending[-1]["body"]


def test_fetch_is_a_lenient_outbox_routing_key():
    fm, body = protocol.parse_outbox_message(
        "fetch: issue\nnumber: 7\n\nignored request body"
    )
    assert fm == {"fetch": "issue", "number": 7}
    assert body == "ignored request body"
