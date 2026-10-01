"""Public-provider connection faults remain named, bounded, and visible in usage evidence."""
import http.client
import io
import json
import urllib.error

import pytest

from evaluation.external_review import AutomaticReviewError
from tools.external_review import OpenAICompatChat


@pytest.mark.parametrize("failure,expected", [
    (http.client.IncompleteRead(b"private partial reply", 9), "transport_error"),
    (http.client.BadStatusLine("private malformed reply"), "transport_error"),
    (http.client.RemoteDisconnected("private disconnect"), "transport_error"),
    (TimeoutError("private read timeout"), "transport_timeout"),
    (urllib.error.URLError(TimeoutError("private connect timeout")), "transport_timeout"),
    (urllib.error.URLError(OSError("private connect failure")), "transport_error"),
], ids=["incomplete", "bad-status", "disconnected", "read-timeout", "connect-timeout", "connect-error"])
def test_transport_fault_is_named_and_recorded_without_partial_reply(failure, expected):
    chat = OpenAICompatChat("github-models", "synthetic-key")

    class Response:
        headers = {}
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, _limit):
            raise failure

    class Opener:
        def open(self, _request, **_kwargs):
            return Response()

    chat.opener = Opener()
    with pytest.raises(AutomaticReviewError) as caught:
        chat("meta/Llama-3.3-70B-Instruct", "public-system", "public-input", {})
    assert caught.value.code == expected
    assert len(chat.provider_usage) == 1
    row = chat.provider_usage[0]
    assert row["status"] == "error" and row["error"] == expected
    assert row["request_sent"] is True
    assert row["cost_usd"] is None and row["cost_status"] == "not_reported"
    assert "private" not in json.dumps([row, chat.failures])


def test_truncated_http_error_body_preserves_http_status_without_partial_text():
    chat = OpenAICompatChat("github-models", "synthetic-key")

    class Body(io.BytesIO):
        def read(self, _limit=-1):
            raise http.client.IncompleteRead(b"private rejected body", 12)

    class Opener:
        def open(self, request, **_kwargs):
            raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, Body())

    chat.opener = Opener()
    with pytest.raises(AutomaticReviewError) as caught:
        chat("meta/Llama-3.3-70B-Instruct", "public-system", "public-input", {})
    assert caught.value.code == "forbidden"
    assert len(chat.provider_usage) == 1 and chat.provider_usage[0]["error"] == "forbidden"
    shape = chat.failures["meta/Llama-3.3-70B-Instruct"]
    assert shape["status"] == 403 and shape["bytes"] == 0 and shape["top"] == "not_json"
    assert "private" not in json.dumps([shape, chat.provider_usage])
