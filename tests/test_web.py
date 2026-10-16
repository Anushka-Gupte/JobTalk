import json
import re
import socket
import threading
import urllib.error
import urllib.request
from typing import Any

import pytest
from jobtalk.config import Settings
from jobtalk.models import LineItem
from jobtalk.web import _parse_multipart, run_server


@pytest.fixture
def test_server():
    """Start an in-memory ThreadingHTTPServer on an ephemeral port in a daemon thread."""
    settings = Settings(llm_model="qwen2.5:1.5b")
    rates = {"labor_hourly": 90.0, "items": {"shutoff valve": 25.0}}

    def fake_stream(transcript: str, user_rates: dict, settings: Settings):
        yield ("item", LineItem(description="Test Labor", kind="labor", qty=1.0, unit_price=90.0))
        yield ("item", LineItem(description="P-trap", kind="material", qty=1.0, unit_price=15.0))
        done_payload = json.dumps({
            "customer": "Jane Smith",
            "address": "456 Elm St",
            "job_summary": "Fixed leak",
            "notes": "Done",
            "items": [
                {"description": "Test Labor", "kind": "labor", "qty": 1.0, "unit_price": 90.0},
                {"description": "P-trap", "kind": "material", "qty": 1.0, "unit_price": 15.0},
            ],
        })
        yield ("done", done_payload)

    def fake_transcribe(path: Any, settings: Settings) -> str:
        return "Fake audio transcript"

    server = run_server(
        host="127.0.0.1",
        port=0,
        settings=settings,
        rates=rates,
        business="Acme Plumbing",
        tax_rate=0.05,
        warmup=False,
        stream_extract_fn=fake_stream,
        transcribe_fn=fake_transcribe,
    )
    port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def test_index_serves_html_and_has_no_external_dependencies(test_server):
    base_url = test_server
    req = urllib.request.Request(f"{base_url}/")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        assert "text/html" in resp.headers["Content-Type"]
        html = resp.read().decode("utf-8")

    # Assert local-first offline stack: no external CDNs, fonts, or scripts
    external_scripts = re.findall(r'<script[^>]+src=["\'](http[s]?://[^"\']+|//[^"\'])["\']', html)
    external_links = re.findall(r'<link[^>]+href=["\'](http[s]?://[^"\']+|//[^"\'])["\']', html)
    assert not external_scripts, f"Found external scripts: {external_scripts}"
    assert not external_links, f"Found external stylesheets: {external_links}"
    assert "JobTalk" in html
    assert 'accept="audio/*"' in html
    assert "isSecureContext" in html
    assert "Add missing line" in html


def test_api_status_endpoint(test_server):
    base_url = test_server
    with urllib.request.urlopen(f"{base_url}/api/status", timeout=5) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["model"] == "qwen2.5:1.5b"
        assert data["business"] == "Acme Plumbing"
        assert data["tax"] == 0.05


def test_api_recalculate_computes_totals_in_python(test_server):
    base_url = test_server
    payload = {
        "items": [
            {"description": "Labor", "kind": "labor", "qty": 2.5, "unit_price": 90.0},
            {"description": "Shutoff valve", "kind": "material", "qty": 2.0, "unit_price": 25.0},
            {"description": "Unpriced fitting", "kind": "material", "qty": 1.0, "unit_price": None},
        ],
        "tax_rate": 0.10,
    }
    req = urllib.request.Request(
        f"{base_url}/api/recalculate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))

    totals = data["totals"]
    # Labor: 2.5 * 90 = 225.0
    # Materials: 2 * 25 = 50.0
    # Subtotal: 275.0
    # Tax: 275.0 * 0.10 = 27.50
    # Total: 302.50
    assert totals["labor"] == 225.0
    assert totals["materials"] == 50.0
    assert totals["subtotal"] == 275.0
    assert totals["tax"] == 27.50
    assert totals["total"] == 302.50
    assert totals["unpriced"] == 1


def test_api_export_blocks_unpriced_when_not_accepted(test_server):
    base_url = test_server
    payload = {
        "customer": "John Doe",
        "address": "123 Main St",
        "job_summary": "Replaced valve",
        "notes": "",
        "items": [
            {"description": "Labor", "kind": "labor", "qty": 1.0, "unit_price": 90.0},
            {"description": "Mystery fitting", "kind": "material", "qty": 1.0, "unit_price": None},
        ],
        "allow_unpriced": False,
    }
    req = urllib.request.Request(
        f"{base_url}/api/export",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)

    assert exc_info.value.code == 400
    err_body = json.loads(exc_info.value.read().decode("utf-8"))
    assert "unpriced item(s) must be priced or explicitly accepted" in err_body["error"]


def test_api_export_succeeds_when_allow_unpriced_checked(test_server):
    base_url = test_server
    payload = {
        "customer": "John Doe",
        "address": "123 Main St",
        "job_summary": "Replaced valve",
        "notes": "",
        "items": [
            {"description": "Labor", "kind": "labor", "qty": 1.0, "unit_price": 90.0},
            {"description": "Mystery fitting", "kind": "material", "qty": 1.0, "unit_price": None},
        ],
        "allow_unpriced": True,
        "business": "Acme Plumbing",
    }
    req = urllib.request.Request(
        f"{base_url}/api/export",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
        assert "Acme Plumbing" in html
        assert "John Doe" in html
        assert "needs price" in html


def test_api_extract_streams_sse_events(test_server):
    base_url = test_server
    req = urllib.request.Request(
        f"{base_url}/api/extract",
        data=json.dumps({"transcript": "Repaired sink"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        assert "text/event-stream" in resp.headers["Content-Type"]
        raw = resp.read().decode("utf-8")

    assert "event: transcript" in raw
    assert "Repaired sink" in raw
    assert "event: item" in raw
    assert "Test Labor" in raw
    assert "P-trap" in raw
    assert "event: done" in raw
    assert "Jane Smith" in raw


def test_no_outbound_network_calls_outside_localhost(test_server, monkeypatch):
    """Assert server operations never make outbound calls outside 127.0.0.1 / localhost."""
    base_url = test_server
    real_socket_connect = socket.socket.connect

    outbound_calls: list[tuple[str, int]] = []

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and len(address) >= 2:
            host, port = address[0], address[1]
            if str(host) not in ("127.0.0.1", "localhost", "::1"):
                outbound_calls.append((str(host), port))
                raise OSError(f"Illegal external outbound connection to {host}:{port}!")
        return real_socket_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)

    # Perform typical UI operations with timeout=5
    with urllib.request.urlopen(f"{base_url}/api/status", timeout=5) as resp:
        assert resp.status == 200

    req = urllib.request.Request(
        f"{base_url}/api/recalculate",
        data=json.dumps({"items": [{"description": "Pipe", "kind": "material", "qty": 1, "unit_price": 10}]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200

    assert len(outbound_calls) == 0, f"Found illegal outbound network calls: {outbound_calls}"


def test_parse_multipart_helper():
    boundary = "----WebKitFormBoundaryXYZ"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="audio"; filename="job.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
        "FAKE_AUDIO_DATA\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="rates"\r\n\r\n'
        '{"labor_hourly": 95}\r\n'
        f"--{boundary}--\r\n"
    ).encode()

    fields, audio_bytes, filename = _parse_multipart(f"multipart/form-data; boundary={boundary}", body)
    assert audio_bytes == b"FAKE_AUDIO_DATA"
    assert filename == "job.wav"
    assert fields.get("rates") == '{"labor_hourly": 95}'
