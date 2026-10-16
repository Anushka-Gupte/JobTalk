import json
import re
import socket
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jobtalk.config import Settings
from jobtalk.models import LineItem

from backend.app import create_app


@pytest.fixture
def fake_stream():
    def _stream(transcript: str, user_rates: dict, settings: Settings):
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

    return _stream


@pytest.fixture
def fake_transcribe():
    def _transcribe(path: Any, settings: Settings) -> str:
        return "Fake audio transcript from mock"

    return _transcribe


@pytest.fixture
def client(fake_stream, fake_transcribe):
    app = create_app(
        config_overrides={
            "business_name": "Acme Plumbing",
            "tax_rate": 0.05,
            "upload_max_size_mb": 1,
        },
        stream_extract_fn=fake_stream,
        transcribe_fn=fake_transcribe,
        warmup=False,
    )
    return TestClient(app)


def test_index_serves_html_and_has_no_external_dependencies(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    html = resp.text

    # Assert local-only offline stack: no external CDNs, fonts, or tracking scripts
    external_scripts = re.findall(r'<script[^>]+src=["\'](http[s]?://[^"\']+|//[^"\'])["\']', html)
    external_links = re.findall(r'<link[^>]+href=["\'](http[s]?://[^"\']+|//[^"\'])["\']', html)
    assert not external_scripts, f"Found external scripts: {external_scripts}"
    assert not external_links, f"Found external stylesheets: {external_links}"
    assert "JobTalk" in html
    assert 'accept="audio/*"' in html
    assert "Add missing line" in html


def test_static_assets_served_locally(client):
    css_resp = client.get("/style.css")
    assert css_resp.status_code == 200
    assert "text/css" in css_resp.headers.get("content-type", "")

    js_resp = client.get("/app.js")
    assert js_resp.status_code == 200
    assert "javascript" in js_resp.headers.get("content-type", "")


def test_api_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["model"] == "qwen2.5:1.5b"
    assert data["business"] == "Acme Plumbing"
    assert data["tax"] == 0.05


def test_api_recalculate_computes_totals_in_python(client):
    payload = {
        "items": [
            {"description": "Labor", "kind": "labor", "qty": 2.5, "unit_price": 90.0},
            {"description": "Shutoff valve", "kind": "material", "qty": 2.0, "unit_price": 25.0},
            {"description": "Unpriced fitting", "kind": "material", "qty": 1.0, "unit_price": None},
        ],
        "tax_rate": 0.10,
    }
    resp = client.post("/api/recalculate", json=payload)
    assert resp.status_code == 200
    data = resp.json()

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


def test_api_export_blocks_unpriced_when_not_accepted(client):
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
    resp = client.post("/api/export", json=payload)
    assert resp.status_code == 400
    assert "unpriced item(s) must be priced or explicitly accepted" in resp.json()["detail"]


def test_api_export_succeeds_when_allow_unpriced_checked(client):
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
    resp = client.post("/api/export", json=payload)
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    html = resp.text
    assert "Acme Plumbing" in html
    assert "John Doe" in html
    assert "needs price" in html


def test_api_extract_streams_sse_from_json(client):
    resp = client.post("/api/extract", json={"transcript": "Repaired sink"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    content = resp.text

    assert "event: transcript" in content
    assert "Repaired sink" in content
    assert "event: item" in content
    assert "Test Labor" in content
    assert "P-trap" in content
    assert "event: done" in content
    assert "Jane Smith" in content


def test_api_extract_streams_sse_from_multipart_audio(client):
    files = {"audio": ("dictation.wav", b"RIFFfakeaudiobytes", "audio/wav")}
    data = {"rates": json.dumps({"labor_hourly": 95.0})}
    resp = client.post("/api/extract", files=files, data=data)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    content = resp.text

    assert "event: transcript" in content
    assert "Fake audio transcript from mock" in content
    assert "event: item" in content
    assert "event: done" in content


def test_api_extract_enforces_upload_size_limit(client):
    oversized = b"X" * (2 * 1024 * 1024)  # 2MB exceeds 1MB upload_max_size_mb
    files = {"audio": ("huge.wav", oversized, "audio/wav")}
    resp = client.post("/api/extract", files=files)
    assert resp.status_code == 413
    assert "exceeds maximum size" in resp.json()["detail"]


def test_no_outbound_network_calls_outside_localhost(client, monkeypatch):
    """Assert server operations never make outbound calls outside 127.0.0.1 / localhost."""
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

    client.get("/api/health")
    client.post(
        "/api/recalculate",
        json={"items": [{"description": "Pipe", "kind": "material", "qty": 1, "unit_price": 10}]},
    )

    assert len(outbound_calls) == 0, f"Found illegal outbound network calls: {outbound_calls}"
