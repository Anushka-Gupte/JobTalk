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
        yield ("item", LineItem(description="Test Labour", kind="labor", qty=1.0, unit_price=90.0))
        yield ("item", LineItem(description="P-trap", kind="material", qty=1.0, unit_price=15.0))
        done_payload = json.dumps({
            "customer": "Jane Smith",
            "address": "456 Elm St",
            "job_summary": "Fixed leak",
            "notes": "Done",
            "items": [
                {"description": "Test Labour", "kind": "labor", "qty": 1.0, "unit_price": 90.0},
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
            "currency_code": "INR",
            "currency_symbol": "₹",
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
    assert "GST" in html
    assert "Labour" in html


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
    assert data["tax_label"] == "GST"
    assert data["currency_code"] == "INR"
    assert data["currency_symbol"] == "₹"


def test_api_recalculate_computes_totals_in_python(client):
    payload = {
        "items": [
            # Use quantities that produce an integer total: 2*100=200 labour + 1*100=100 material
            # Subtotal=300, tax=10%=30, raw_total=330.0 (already integer), round_off=0.0
            {"description": "Labour", "kind": "labor", "qty": 2.0, "unit_price": 100.0},
            {"description": "Shutoff valve", "kind": "material", "qty": 1.0, "unit_price": 100.0},
            {"description": "Unpriced fitting", "kind": "material", "qty": 1.0, "unit_price": None},
        ],
        "tax_rate": 0.10,
    }
    resp = client.post("/api/recalculate", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    totals = data["totals"]
    # Labour: 2 * 100 = 200.0
    # Materials: 1 * 100 = 100.0
    # Subtotal: 300.0
    # Tax: 300.0 * 0.10 = 30.0
    # Total: 330.0 (integer, round_off = 0.0)
    assert totals["labor"] == 200.0
    assert totals["materials"] == 100.0
    assert totals["subtotal"] == 300.0
    assert totals["tax"] == 30.0
    assert totals["total"] == 330.0
    assert totals["round_off"] == 0.0
    assert totals["unpriced"] == 1
    assert totals["formatted_total"] == "₹330.00"
    assert totals["formatted_subtotal"] == "₹300.00"


def test_api_export_blocks_unpriced_when_not_accepted(client):
    payload = {
        "customer": "John Doe",
        "address": "123 Main St",
        "job_summary": "Replaced valve",
        "notes": "",
        "items": [
            {"description": "Labour", "kind": "labor", "qty": 1.0, "unit_price": 90.0},
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
            {"description": "Labour", "kind": "labor", "qty": 1.0, "unit_price": 90.0},
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
    assert "₹" in html
    assert "GST" in html


def test_api_export_empty_business_renders_no_daves_plumbing(client):
    payload = {
        "customer": "John Doe",
        "address": "123 Main St",
        "job_summary": "Replaced valve",
        "notes": "",
        "items": [
            {"description": "Labour", "kind": "labor", "qty": 1.0, "unit_price": 90.0},
        ],
        "business": "",
        "tax_rate": 0.18,
    }
    resp = client.post("/api/export", json=payload)
    assert resp.status_code == 200
    html = resp.text
    assert "Dave's Plumbing" not in html
    assert "<h1>" not in html
    assert "GST (18%)" in html
    assert "Print / Save as PDF" in html
    assert "Share on WhatsApp" in html


def test_api_extract_streams_sse_from_json(client):
    resp = client.post("/api/extract", json={"transcript": "Repaired sink"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    content = resp.text

    assert "event: transcript" in content
    assert "Repaired sink" in content
    assert "event: item" in content
    assert "Test Labour" in content
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


def test_api_export_blocks_unconfirmed_price_flags(client):
    payload = {
        "customer": "John Doe",
        "address": "123 Main St",
        "job_summary": "Replaced valve",
        "notes": "",
        "items": [
            {
                "description": "Labour",
                "kind": "labor",
                "qty": 1.0,
                "unit_price": 90.0,
                "price_flag": "price not found in recording, confirm",
            },
        ],
        "allow_unpriced": False,
    }
    resp = client.post("/api/export", json=payload)
    assert resp.status_code == 400
    assert "prices not found in recording" in resp.json()["detail"]


def test_api_extract_broken_json_returns_error_event_and_keeps_transcript():
    """When model output is completely broken, transcript is still delivered and a clean error event is emitted."""
    from backend.app import create_app

    def broken_extract(transcript, rates, settings):
        # Yields no valid line items and invalid final string
        yield ("token", "broken model output that is not json")
        yield ("done", "broken model output that is not json")

    app = create_app(
        stream_extract_fn=broken_extract,
        transcribe_fn=lambda path, s: "Customer spoken transcript to preserve",
        warmup=False,
    )
    with TestClient(app) as test_client:
        resp = test_client.post("/api/extract", json={"transcript": "Customer spoken transcript to preserve"})
        assert resp.status_code == 200
        text = resp.text
        # Transcript event was sent first
        assert "event: transcript" in text
        assert "Customer spoken transcript to preserve" in text
        # Error event was sent on failure
        assert "event: error" in text
        assert "Couldn't read that recording, try again" in text


def test_api_export_omits_dropped_items_unless_restored(client):
    payload = {
        "customer": "Rohan",
        "address": "MG Road",
        "job_summary": "Bathroom plumbing",
        "notes": "",
        "items": [
            {
                "description": "old-style brass tap",
                "kind": "material",
                "qty": 1.0,
                "unit_price": 600.0,
                "dropped_reason": "looks like a removed/old item",
            },
            {
                "description": "PVC connector",
                "kind": "material",
                "qty": 2.0,
                "unit_price": 50.0,
                "dropped_reason": None,
            },
        ],
        "allow_unpriced": False,
    }
    # Unrestored: old-style brass tap must NOT be in the exported HTML
    resp = client.post("/api/export", json=payload)
    assert resp.status_code == 200
    html = resp.text
    assert "PVC connector" in html
    assert "old-style brass tap" not in html

    # Restored: user sets dropped_reason to None
    payload["items"][0]["dropped_reason"] = None
    resp2 = client.post("/api/export", json=payload)
    assert resp2.status_code == 200
    html2 = resp2.text
    assert "PVC connector" in html2
    assert "old-style brass tap" in html2


def test_api_recalculate_returns_missing_items_reminder(client):
    transcript = "Replaced 3 switches. Installed 2 lights. Fixed 1 fan."
    # 3 number phrases in transcript, but only 1 item in items list
    resp = client.post(
        "/api/recalculate",
        json={
            "transcript": transcript,
            "items": [
                {"description": "Switches", "kind": "material", "qty": 3, "unit_price": 100},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["missing_items_reminder"] == "2 items may be missing, check the transcript"


def test_api_extract_empty_transcript_returns_error_event():
    from backend.app import create_app

    app = create_app(
        transcribe_fn=lambda path, s: "",
        warmup=False,
    )
    with TestClient(app) as test_client:
        files = {"audio": ("silent.wav", b"RIFFfakeaudio", "audio/wav")}
        resp = test_client.post("/api/extract", files=files)
        assert resp.status_code == 200
        text = resp.text
        assert "event: transcript" in text
        assert "event: error" in text
        assert "No speech detected in recording, try again" in text


def test_api_extract_transcription_failure_returns_400():
    from backend.app import create_app

    def failing_transcribe(path, s):
        raise RuntimeError("Corrupted audio file")

    app = create_app(
        transcribe_fn=failing_transcribe,
        warmup=False,
    )
    with TestClient(app) as test_client:
        files = {"audio": ("corrupt.wav", b"corrupted", "audio/wav")}
        resp = test_client.post("/api/extract", files=files)
        assert resp.status_code == 400
        assert "Could not transcribe audio" in resp.json()["detail"]


def test_pyav_patch_strips_metadata_errors():
    import io
    import wave

    from jobtalk.transcribe import _patch_pyav

    _patch_pyav()
    try:
        import av

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 1600)
        buf.seek(0)
        container = av.open(buf, mode="r", metadata_errors="ignore")
        assert container is not None
        container.close()
    except ImportError:
        pass

