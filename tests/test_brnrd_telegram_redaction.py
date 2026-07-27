"""Telegram transport failures never expose bot credentials."""

from __future__ import annotations

import httpx
import pytest

from brnrd.platforms import telegram


_TOKEN = "123456789:" + "A" * 35
_OTHER_ACCOUNT_TOKEN = "987654321:" + "b" * 35


def _status_response(method: str, url: str) -> httpx.Response:
    return httpx.Response(401, request=httpx.Request(method, url))


def _request_failure(method: str, url: str) -> httpx.RequestError:
    request = httpx.Request(method, url)
    return httpx.ConnectError(
        f"connection refused for url '{request.url}'",
        request=request,
    )


class _FailingStream:
    def __init__(self, method: str, url: str):
        self.method = method
        self.url = url

    def __enter__(self):
        raise _request_failure(self.method, self.url)

    def __exit__(self, *_args):
        return False


@pytest.mark.parametrize(
    ("operation", "cause", "error_class"),
    [
        ("send_card", "401 Unauthorized", httpx.HTTPStatusError),
        ("edit_card", "401 Unauthorized", httpx.HTTPStatusError),
        ("resolve_file", "connection refused", httpx.ConnectError),
        ("fetch_file_bytes", "connection refused", RuntimeError),
    ],
)
def test_telegram_transport_failure_redacts_token_but_keeps_cause(
    monkeypatch, operation, cause, error_class
):
    if operation in {"send_card", "edit_card"}:
        monkeypatch.setattr(
            telegram.httpx,
            "post",
            lambda url, **_kwargs: _status_response("POST", url),
        )
    elif operation == "resolve_file":
        def fail_post(url, **_kwargs):
            raise _request_failure("POST", url)

        monkeypatch.setattr(telegram.httpx, "post", fail_post)
    else:
        monkeypatch.setattr(
            telegram.httpx,
            "stream",
            lambda method, url, **_kwargs: _FailingStream(method, url),
        )

    with pytest.raises(error_class) as raised:
        if operation == "send_card":
            telegram.send_card(_TOKEN, "42", "status")
        elif operation == "edit_card":
            telegram.edit_card(_TOKEN, "42", 7, "status")
        elif operation == "resolve_file":
            telegram.resolve_file(_TOKEN, "file-id")
        else:
            telegram.fetch_file_bytes(_TOKEN, "photos/file.jpg", max_bytes=1024)

    message = str(raised.value)
    assert cause in message
    assert _TOKEN not in message
    assert "bot<redacted>" in message
    http_error = (
        raised.value.__cause__
        if isinstance(raised.value.__cause__, httpx.HTTPError)
        else raised.value
    )
    assert _TOKEN not in str(http_error)
    assert _TOKEN not in str(http_error.request.url)


def test_redaction_matches_a_token_from_an_unconfigured_account():
    text = (
        "failed for url "
        f"'https://api.telegram.org/bot{_OTHER_ACCOUNT_TOKEN}/sendMessage'"
    )

    redacted = telegram.redact_secrets(text)

    assert _OTHER_ACCOUNT_TOKEN not in redacted
    assert redacted.endswith(
        "'https://api.telegram.org/bot<redacted>/sendMessage'"
    )
