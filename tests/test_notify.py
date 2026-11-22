"""The new-request ping.

Two promises matter more than the feature: it carries no customer data, and it
can't cost a request. The receiving end here is a real HTTP server on a local
port, so what's asserted is what actually went over the wire.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from intake import auth, notify, web_requests
from intake.app import SUBMIT_PATH, app
from intake.db import connect
from intake.schema import initialise

SUBMIT_TOKEN = "test-submit-token-not-a-real-one"

BODY = {
    "customer_name": "Renata Quist",
    "email": "renata-secret@example.com",
    "phone": "555-0142-SECRET",
    "device": "Pioneer CDJ-3000 SECRET",
    "symptoms": "jog wheel unresponsive SECRET",
    "handoff": "mail_in",
    "return_address": "14 Hidden Lane SECRET",
}


@pytest.fixture
def hook():
    """A webhook receiver. `hook.calls` is every request it got."""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        status = 200

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            calls.append(
                {
                    "path": self.path,
                    "content_type": self.headers.get("Content-Type"),
                    "body": self.rfile.read(length).decode(),
                }
            )
            self.send_response(Handler.status)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.calls = calls
    server.handler = Handler
    server.url = f"http://127.0.0.1:{server.server_port}/hook/topic"
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "intake.db"
    monkeypatch.setenv("INTAKE_DB_PATH", str(path))
    monkeypatch.setenv(auth.SUBMIT_TOKEN_ENV_VAR, SUBMIT_TOKEN)
    monkeypatch.delenv(notify.URL_ENV_VAR, raising=False)
    monkeypatch.delenv(notify.FORMAT_ENV_VAR, raising=False)
    monkeypatch.delenv(web_requests.MAX_PENDING_ENV_VAR, raising=False)
    with connect(path) as conn:
        initialise(conn)
    return TestClient(app)


def _submit(client, body=BODY, **headers):
    return client.post(
        SUBMIT_PATH, json=body, headers={"Authorization": f"Bearer {SUBMIT_TOKEN}", **headers}
    )


# --- it pings ---------------------------------------------------------------


def test_a_new_request_pings_the_hook(client, hook, monkeypatch) -> None:
    monkeypatch.setenv(notify.URL_ENV_VAR, hook.url)

    assert _submit(client).status_code == 202

    (call,) = hook.calls
    assert call["path"] == "/hook/topic"
    assert call["content_type"] == "application/json"
    assert json.loads(call["body"]) == {
        "text": "benchhud-intake: new repair request (1 waiting)",
        "content": "benchhud-intake: new repair request (1 waiting)",
        "pending": 1,
    }


def test_the_count_is_how_many_are_waiting(client, hook, monkeypatch) -> None:
    monkeypatch.setenv(notify.URL_ENV_VAR, hook.url)

    _submit(client)
    _submit(client)

    assert [json.loads(c["body"])["pending"] for c in hook.calls] == [1, 2]
    assert "(2 waiting)" in hook.calls[1]["body"]


def test_text_format_is_one_plain_line(client, hook, monkeypatch) -> None:
    """What ntfy wants: the body is the message."""
    monkeypatch.setenv(notify.URL_ENV_VAR, hook.url)
    monkeypatch.setenv(notify.FORMAT_ENV_VAR, "text")

    _submit(client)

    (call,) = hook.calls
    assert call["content_type"].startswith("text/plain")
    assert call["body"] == "benchhud-intake: new repair request (1 waiting)"


# --- it carries nothing about the customer ----------------------------------


@pytest.mark.parametrize("fmt", notify.FORMATS)
def test_the_ping_carries_no_customer_data_and_no_id(client, hook, monkeypatch, fmt) -> None:
    monkeypatch.setenv(notify.URL_ENV_VAR, hook.url)
    monkeypatch.setenv(notify.FORMAT_ENV_VAR, fmt)

    request_id = _submit(client).json()["id"]

    (call,) = hook.calls
    sent = call["body"] + call["path"]
    for value in (*BODY.values(), "Renata", "Quist", "SECRET", "Pioneer", request_id):
        assert value not in sent


def test_the_message_is_a_function_of_the_count_alone() -> None:
    """Nothing about a request can reach it: the count is its only input."""
    assert notify.message(1) == "benchhud-intake: new repair request (1 waiting)"
    assert (
        notify.build(3, "json")[0]
        == json.dumps(
            {
                "text": "benchhud-intake: new repair request (3 waiting)",
                "content": "benchhud-intake: new repair request (3 waiting)",
                "pending": 3,
            }
        ).encode()
    )


# --- when it stays quiet ----------------------------------------------------


def test_no_url_means_no_call(client, hook) -> None:
    assert _submit(client).status_code == 202
    assert hook.calls == []
    assert notify.request_received(1) is False


def test_a_replayed_retry_does_not_ping_again(client, hook, monkeypatch) -> None:
    monkeypatch.setenv(notify.URL_ENV_VAR, hook.url)

    _submit(client, **{"Idempotency-Key": "retry-1"})
    _submit(client, **{"Idempotency-Key": "retry-1"})

    assert len(hook.calls) == 1


def test_a_rejected_submission_does_not_ping(client, hook, monkeypatch) -> None:
    monkeypatch.setenv(notify.URL_ENV_VAR, hook.url)
    monkeypatch.setenv(web_requests.MAX_PENDING_ENV_VAR, "0")

    assert _submit(client).status_code == 429
    assert _submit(client, {**BODY, "device": ""}).status_code == 400
    assert client.post(SUBMIT_PATH, json=BODY).status_code == 401
    assert hook.calls == []


@pytest.mark.parametrize("url", ["file:///etc/hosts", "ftp://example.com/x", "example.com", " "])
def test_only_http_urls_are_used(monkeypatch, url) -> None:
    monkeypatch.setenv(notify.URL_ENV_VAR, url)

    assert notify.notify_url() is None
    assert notify.request_received(1) is False


def test_an_unknown_format_falls_back_to_json(monkeypatch) -> None:
    monkeypatch.setenv(notify.FORMAT_ENV_VAR, "carrier-pigeon")

    assert notify.notify_format() == "json"


# --- it can't cost a request ------------------------------------------------


def test_a_dead_hook_still_gets_the_relay_its_202(client, monkeypatch, caplog) -> None:
    # Port 9 on loopback: nothing listens, the connection is refused.
    monkeypatch.setenv(notify.URL_ENV_VAR, "http://127.0.0.1:9/secret-topic")

    response = _submit(client)

    assert response.status_code == 202
    assert set(response.json()) == {"id"}
    # And the failure is logged without the URL, which is the secret.
    assert "request notification failed" in caplog.text
    assert "secret-topic" not in caplog.text and "127.0.0.1" not in caplog.text


def test_a_url_urllib_chokes_on_fails_quietly_and_unlogged(client, monkeypatch, caplog) -> None:
    """A space in the topic makes http.client raise an error that quotes the
    URL. It must not escape into the log from the background task."""
    monkeypatch.setenv(notify.URL_ENV_VAR, "http://127.0.0.1:9/secret topic-abc123")

    assert notify.request_received(1) is False
    assert _submit(client).status_code == 202
    assert "secret" not in caplog.text and "topic-abc123" not in caplog.text


def test_an_answer_that_is_not_http_fails_quietly(monkeypatch) -> None:
    import socket
    import threading

    listener = socket.create_server(("127.0.0.1", 0))

    def answer():
        conn, _ = listener.accept()
        conn.recv(4096)
        conn.sendall(b"not http at all\r\n\r\n")
        conn.close()

    threading.Thread(target=answer, daemon=True).start()
    monkeypatch.setenv(notify.URL_ENV_VAR, f"http://127.0.0.1:{listener.getsockname()[1]}/x")

    assert notify.request_received(1) is False
    listener.close()


def test_a_hook_that_errors_still_gets_the_relay_its_202(client, hook, monkeypatch) -> None:
    hook.handler.status = 500
    monkeypatch.setenv(notify.URL_ENV_VAR, hook.url)

    assert _submit(client).status_code == 202
    assert len(hook.calls) == 1
    assert notify.request_received(1) is False


def test_the_request_is_stored_even_when_the_hook_is_dead(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(notify.URL_ENV_VAR, "http://127.0.0.1:9/x")

    _submit(client)

    with connect(tmp_path / "intake.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1
