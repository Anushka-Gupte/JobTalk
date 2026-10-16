import json

import pytest
from jobtalk.extract import _iter_events, extract_job, stream_extract_job
from jobtalk.models import Job, LineItem


def fake_chat(payload):
    return lambda system, user, settings: payload


# ---------------------------------------------------------------------------
# Non-streaming tests (existing, updated)
# ---------------------------------------------------------------------------

def test_extract_parses_model_json():
    raw = json.dumps({"customer": "Sam", "items": [
        {"description": "Labor", "kind": "labor", "qty": "2", "unit_price": "85"}]})
    job = extract_job("x", {}, chat=fake_chat(raw))
    assert job.customer == "Sam" and job.items[0].qty == 2.0 and job.items[0].unit_price == 85.0


def test_messy_model_output_is_tolerated():
    job = Job.from_dict({"items": [{"description": "Pipe", "qty": "lots", "unit_price": None}, "junk", {}]})
    assert len(job.items) == 1 and job.items[0].qty == 1.0 and job.items[0].unit_price is None


def test_invalid_json_raises_clear_error():
    with pytest.raises(RuntimeError, match="valid JSON"):
        extract_job("x", {}, chat=fake_chat("not json"))


def test_unreachable_llm_raises_clear_error():
    def boom(*a):
        raise OSError("down")
    with pytest.raises(RuntimeError, match="Ollama"):
        extract_job("x", {}, chat=boom)


# ---------------------------------------------------------------------------
# Streaming: _iter_events unit tests (no LLM required)
# ---------------------------------------------------------------------------

def _fake_token_iter(full_json: str, chunk_size: int = 3):
    """Yield fixed-size string chunks from a JSON string (simulates streaming)."""
    for i in range(0, len(full_json), chunk_size):
        yield full_json[i:i + chunk_size]


def _make_job_json(items: list[dict]) -> str:
    return json.dumps({
        "customer": "Test Customer",
        "address": "123 Main St",
        "job_summary": "Test job",
        "notes": "",
        "items": items,
    })


def test_iter_events_yields_token_events():
    payload = _make_job_json([{"description": "Labor", "kind": "labor", "qty": 1.5, "unit_price": 90}])
    events = list(_iter_events(_fake_token_iter(payload)))
    token_events = [e for e in events if e[0] == "token"]
    assert len(token_events) > 0
    assert "".join(e[1] for e in token_events) == payload


def test_iter_events_yields_one_item():
    payload = _make_job_json([{"description": "Copper pipe", "kind": "material", "qty": 5.0, "unit_price": 3.5}])
    events = list(_iter_events(_fake_token_iter(payload)))
    item_events = [e for e in events if e[0] == "item"]
    assert len(item_events) == 1
    item: LineItem = item_events[0][1]
    assert item.description == "Copper pipe"
    assert item.kind == "material"
    assert item.qty == 5.0
    assert item.unit_price == 3.5


def test_iter_events_yields_multiple_items():
    payload = _make_job_json([
        {"description": "Labor", "kind": "labor", "qty": 2.5, "unit_price": 95.0},
        {"description": "Shutoff valve", "kind": "material", "qty": 1.0, "unit_price": 24.0},
        {"description": "Copper fittings", "kind": "material", "qty": 1.0, "unit_price": None},
    ])
    events = list(_iter_events(_fake_token_iter(payload, chunk_size=1)))
    item_events = [e for e in events if e[0] == "item"]
    assert len(item_events) == 3
    assert item_events[0][1].kind == "labor"
    assert item_events[0][1].qty == 2.5
    assert item_events[2][1].unit_price is None


def test_iter_events_done_event_contains_full_json():
    payload = _make_job_json([{"description": "P-trap", "kind": "material", "qty": 1.0, "unit_price": 14.0}])
    events = list(_iter_events(_fake_token_iter(payload)))
    done_events = [e for e in events if e[0] == "done"]
    assert len(done_events) == 1
    assert json.loads(done_events[0][1])["customer"] == "Test Customer"


def test_iter_events_items_arrive_progressively():
    """Items should yield before the 'done' event (streaming order)."""
    payload = _make_job_json([
        {"description": "A", "kind": "material", "qty": 1.0, "unit_price": 10.0},
        {"description": "B", "kind": "labor", "qty": 0.5, "unit_price": 90.0},
    ])
    events = list(_iter_events(_fake_token_iter(payload, chunk_size=2)))
    kinds = [e[0] for e in events]
    last_item_idx = max(i for i, k in enumerate(kinds) if k == "item")
    done_idx = kinds.index("done")
    assert last_item_idx < done_idx  # items arrive before done


# ---------------------------------------------------------------------------
# stream_extract_job integration test (injectable fake stream_chat)
# ---------------------------------------------------------------------------

def fake_stream_chat(full_json: str):
    def _stream(system, user, settings):
        yield from _fake_token_iter(full_json, chunk_size=4)
    return _stream


def test_stream_extract_job_yields_items_and_done():
    payload = _make_job_json([
        {"description": "Labor", "kind": "labor", "qty": 1.0, "unit_price": 85.0},
        {"description": "P-trap", "kind": "material", "qty": 1.0, "unit_price": None},
    ])
    events = list(stream_extract_job("transcript", {}, stream_chat=fake_stream_chat(payload)))
    item_events = [e for e in events if e[0] == "item"]
    done_events = [e for e in events if e[0] == "done"]
    assert len(item_events) == 2
    assert len(done_events) == 1
    assert item_events[0][1].kind == "labor"
    assert item_events[1][1].unit_price is None


def test_stream_extract_job_unreachable_llm():
    def boom_stream(system, user, settings):
        raise OSError("connection refused")
    with pytest.raises(RuntimeError, match="Ollama"):
        list(stream_extract_job("x", {}, stream_chat=boom_stream))
