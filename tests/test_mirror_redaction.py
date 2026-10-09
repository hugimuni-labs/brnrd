import ast
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from brr.gates import cloud, cloud_publisher, mirror_redaction
from brr.run import Run


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    brr = tmp_path / "repo" / ".brr"
    brr.mkdir(parents=True)
    home = tmp_path / "home"
    (home / "account").mkdir(parents=True)
    sibling = tmp_path / "sibling" / ".brr"
    sibling.mkdir(parents=True)
    ctx = SimpleNamespace(home_root=home, repos={
        "repo": SimpleNamespace(root=brr.parent),
        "sibling": SimpleNamespace(root=sibling.parent),
    })
    monkeypatch.setattr(mirror_redaction.account, "resolve_context", lambda *a, **k: ctx)
    monkeypatch.setattr(mirror_redaction.account, "context_home_root", lambda c: c.home_root)
    monkeypatch.setattr(mirror_redaction.presence, "account_dirs", lambda b: [brr])
    monkeypatch.delenv("BRNRD_MANAGED_GITHUB_TOKEN", raising=False)
    mirror_redaction._notified.clear()
    return brr, home, sibling


def token_file(root, value):
    path = root / "credentials" / "github" / "token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n")
    return path


def redact(brr, payload, **kwargs):
    return mirror_redaction.redact_payload(payload, brr_dir=brr, lane="/v1/daemons/surface", **kwargs)


def test_all_held_sources_and_nested_strings(inventory, monkeypatch):
    brr, home, sibling = inventory
    token_file(brr, "fake-github-token-value")
    token_file(sibling, "fake-sibling-token-value")
    token_file(home / "account", "fake-account-token-value")
    (home / "account" / "social.env").write_text(
        "export ACCESS_TOKEN='fake-social-token-value' # comment\n"
        'OTHER="fake-other-token-value"\nSHORT=tiny\n'
    )
    monkeypatch.setenv("BRNRD_MANAGED_GITHUB_TOKEN", "fake-managed-token-value")
    values = ["fake-github-token-value", "fake-sibling-token-value", "fake-account-token-value",
              "fake-social-token-value", "fake-other-token-value", "fake-managed-token-value",
              "fake-cloud-token-value"]
    payload = {values[0]: [{"markdown": " ".join(values)}, 3, False, None]}
    result = redact(brr, payload, cloud_token=values[-1])
    encoded = json.dumps(result)
    assert all(value not in encoded for value in values)
    assert "[redacted:github-token]" in encoded
    assert "[redacted:social.env:ACCESS_TOKEN]" in encoded
    assert "[redacted:BRNRD_MANAGED_GITHUB_TOKEN]" in encoded
    assert "[redacted:cloud-token]" in encoded
    assert payload[values[0]][0]["markdown"] == " ".join(values)


def test_unchanged_page_is_byte_identical_and_short_values_are_ignored(inventory, capsys):
    brr, _, _ = inventory
    token_file(brr, "short-value")
    payload = {"markdown": "# café\nshort-value\n\"quote\" \\ slash\n", "n": 2}
    before = json.dumps(payload, ensure_ascii=False).encode()
    assert json.dumps(redact(brr, payload), ensure_ascii=False).encode() == before
    token_file(brr, "fake-secret-not-in-page")
    assert json.dumps(redact(brr, payload), ensure_ascii=False).encode() == before
    assert capsys.readouterr().out == ""


def test_values_are_read_at_each_publish_and_minimum_is_inclusive(inventory):
    brr, _, _ = inventory
    path = token_file(brr, "123456789012")
    assert redact(brr, "123456789012") == "[redacted:github-token]"
    path.write_text("rotated-fake-token-value")
    assert redact(brr, "123456789012 rotated-fake-token-value") == "123456789012 [redacted:github-token]"


def test_literal_metacharacters_unicode_and_overlapping_values(inventory):
    brr, home, _ = inventory
    value = 'fake-秘密-"quote\\.*token'
    token_file(brr, value)
    (home / "account" / "extra.env").write_text("LONG=fake-overlapping-secret-long\nSHORT=fake-overlapping-secret\n")
    assert redact(brr, value + " fake-overlapping-secret-long") == (
        "[redacted:github-token] [redacted:extra.env:LONG]"
    )


def test_notice_reaches_resident_without_value_and_deduplicates(inventory, capsys):
    brr, _, _ = inventory
    value = "fake-notice-secret-value"
    token_file(brr, value)
    Run(id="run-resident", event_id="event-resident", source="cloud", status="running", body="").save(brr / "runs")
    for _ in range(2):
        redact(brr, {"markdown": value})
    notice = (brr / "outbox" / "event-resident" / ".notices.jsonl").read_text()
    assert value not in notice + capsys.readouterr().out
    rows = [json.loads(line) for line in notice.splitlines()]
    assert len(rows) == 1
    assert rows[0]["kind"] == "advisory"
    assert "github-token" in rows[0]["text"]


def test_unreadable_inventory_refuses_publish_without_value(inventory):
    brr, _, _ = inventory
    path = token_file(brr, "fake-invalid-value")
    path.write_bytes(b"\xff")
    with pytest.raises(RuntimeError, match="mirror secret inventory could not be read"):
        redact(brr, "page")


def test_every_registered_lane_uses_the_shared_boundary():
    tree = ast.parse(Path(cloud_publisher.__file__).read_text())
    publishers = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                          and d.func.id == "_publish_lane" for d in node.decorator_list)]
    assert len(publishers) == len(cloud_publisher._PUBLISH_TICK_ORDER) == 8
    for publisher in publishers:
        calls = [node for node in ast.walk(publisher) if isinstance(node, ast.Call)
                 and any(isinstance(arg, ast.Constant) and arg.value == "PUT" for arg in node.args)]
        assert len(calls) == 1
        assert isinstance(calls[0].func, ast.Name) and calls[0].func.id == "_mirror_request"


def test_real_corpus_publish_to_fake_http_server(inventory, monkeypatch):
    brr, home, _ = inventory
    value = "fake-http-secret-value"
    token_file(brr, value)
    page = home / "surface" / "page.md"
    page.parent.mkdir()
    page.write_text("# Page\n" + value + "\n")
    files = [SimpleNamespace(path="surface/page.md", abspath=page, layer="authored")]
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, raw))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(cloud, "_corpus_resolve", lambda b: (files, home / "knowledge"))
    monkeypatch.setattr(cloud_publisher, "_corpus_bases", lambda b: {})
    monkeypatch.setattr(cloud_publisher, "_git_committed_times", lambda *a, **k: {})
    state = {"token": "fake-auth-token-value", "brnrd_url": f"http://127.0.0.1:{server.server_port}"}
    try:
        cloud._publish_corpus(brr, None, state)
        assert len(received) == 1
        endpoint, raw = received[0]
        assert endpoint == "/v1/daemons/surface"
        assert value.encode() not in raw
        assert json.loads(raw)["files"][0]["markdown"] == "# Page\n[redacted:github-token]\n"
        assert page.read_text() == "# Page\n" + value + "\n"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
