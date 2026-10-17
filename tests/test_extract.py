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
    with pytest.raises(RuntimeError, match="Couldn't read that recording, try again"):
        extract_job("x", {}, chat=fake_chat("not json"))


def test_unreachable_llm_raises_clear_error():
    def boom(*a):
        raise OSError("down")
    with pytest.raises(RuntimeError, match="Ollama"):
        extract_job("x", {}, chat=boom)


def test_extract_repairs_json_surrounded_by_text():
    # Model returns conversational preamble and postscript around valid JSON
    messy = (
        "Sure, here is the quote JSON object you requested:\n"
        '{"customer": "Sharma ji", "address": "14 Nehru Colony", "job_summary": "Geyser", "notes": "", '
        '"items": [{"description": "Geyser", "kind": "material", "qty": 1.0, "unit_price": 6500.0}]}\n'
        "Hope this helps!"
    )
    job = extract_job("Installed geyser 6500 rupees", {}, chat=fake_chat(messy))
    assert job.customer == "Sharma ji"
    assert len(job.items) == 1
    assert job.items[0].unit_price == 6500.0


def test_extract_retry_succeeds_on_second_attempt():
    calls = 0

    def flaky_chat(system, user, settings):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "broken garbage"
        return json.dumps({
            "customer": "Vikram", "address": "Park Road", "job_summary": "MCB", "notes": "",
            "items": [{"description": "MCB", "kind": "material", "qty": 1.0, "unit_price": 500.0}]
        })

    job = extract_job("Vikram 500 rupees", {}, chat=flaky_chat)
    assert calls == 2
    assert job.customer == "Vikram"
    assert job.items[0].unit_price == 500.0


def test_clean_customer_never_puts_address_in_customer():
    raw = json.dumps({
        "customer": "401 Chestnut Street",
        "address": "401 Chestnut Street",
        "job_summary": "Nest thermostat",
        "notes": "",
        "items": [{"description": "Wire", "kind": "material", "qty": 1.0, "unit_price": 25.0}],
    })
    transcript = "Job at 401 Chestnut Street. Customer is Greg Kowalski. Installed thermostat for 25."
    job = extract_job(transcript, {}, chat=fake_chat(raw))
    assert job.customer == "Greg Kowalski"
    assert job.address == "401 Chestnut Street"


def test_filter_old_removed_parts_flags_dropped_reason():
    raw = json.dumps({
        "customer": "Rajesh",
        "address": "Palm Heights",
        "job_summary": "Wiring",
        "notes": "",
        "items": [
            {"description": "4 old tripping breakers", "kind": "material", "qty": 4.0, "unit_price": 150.0},
            {"description": "16-amp Havells MCB", "kind": "material", "qty": 4.0, "unit_price": 150.0},
            {"description": "Haul-away of old geyser", "kind": "labor", "qty": 1.0, "unit_price": 200.0},
        ],
    })
    job = extract_job("Replaced old tripping breakers with 4 MCBs for 150 each and haul-away 200", {}, chat=fake_chat(raw))
    # '4 old tripping breakers' must NOT be deleted, but flagged with dropped_reason
    assert len(job.items) == 3
    dropped = [it for it in job.items if it.dropped_reason]
    active = [it for it in job.items if not it.dropped_reason]
    assert len(dropped) == 1
    assert dropped[0].description == "4 old tripping breakers"
    assert dropped[0].dropped_reason == "looks like a removed/old item"
    assert len(active) == 2
    assert active[0].description == "16-amp Havells MCB"
    assert active[1].description == "Haul-away of old geyser"


def test_legitimate_old_style_brass_tap_can_be_restored():
    from jobtalk.pricing import compute_totals

    raw = json.dumps({
        "customer": "Vikram",
        "address": "Civil Lines",
        "job_summary": "Plumbing",
        "notes": "",
        "items": [
            {"description": "old-style brass tap", "kind": "material", "qty": 2.0, "unit_price": 650.0},
        ],
    })
    job = extract_job("Installed two old-style brass taps at 650 each", {}, chat=fake_chat(raw))
    # Initially flagged because of 'old'
    assert len(job.items) == 1
    tap = job.items[0]
    assert tap.dropped_reason == "looks like a removed/old item"

    # When dropped, totals must exclude it
    tot_dropped = compute_totals(job)
    assert tot_dropped.subtotal == 0.0

    # User restores the item in the UI
    tap.dropped_reason = None
    tot_restored = compute_totals(job)
    assert tot_restored.subtotal == 1300.0
    assert tot_restored.total == 1300.0


def test_missing_items_reminder_deterministic():
    from jobtalk.extract import count_number_bearing_phrases, get_missing_items_reminder

    transcript = (
        "Put in a one hundred amp subpanel. Ran fifty feet of aluminum wire. "
        "Installed five 20-amp breakers. Put in one eight foot ground rod."
    )
    # 4 distinct number-bearing phrases
    phrases = count_number_bearing_phrases(transcript)
    assert phrases == 4

    # If only 2 items extracted: reminder says 2 items may be missing
    reminder = get_missing_items_reminder(transcript, items_count=2)
    assert reminder == "2 items may be missing, check the transcript"

    # If all 4 items extracted: no reminder
    assert get_missing_items_reminder(transcript, items_count=4) is None

    # Address numbers must be ignored
    t2 = "Job for Dave at 904 Sycamore Drive. Installed two breakers."
    assert count_number_bearing_phrases(t2, address="904 Sycamore Drive") == 1
    assert get_missing_items_reminder(t2, items_count=1, address="904 Sycamore Drive") is None


def test_price_guard_flags_unsupported_prices():
    raw = json.dumps({
        "customer": "Anil",
        "address": "Delhi",
        "job_summary": "Tap repair",
        "notes": "",
        "items": [
            {"description": "Tap washer", "kind": "material", "qty": 1.0, "unit_price": 999.0},
        ],
    })
    # Transcript only mentions 1 hour labor, not 999
    transcript = "Customer Anil at Delhi. Repaired tap, took 1 hour."
    rates = {"labor_hourly": 400}
    job = extract_job(transcript, rates, chat=fake_chat(raw))
    assert job.items[0].price_flag == "price not found in recording, confirm"


def test_price_guard_allows_spoken_and_rate_prices():
    raw = json.dumps({
        "customer": "Anil",
        "address": "Delhi",
        "job_summary": "Tap repair",
        "notes": "",
        "items": [
            {"description": "Labour", "kind": "labor", "qty": 1.0, "unit_price": 400.0},
            {"description": "Tap washer", "kind": "material", "qty": 1.0, "unit_price": 30.0},
        ],
    })
    # 400 is in rates; 30 is spoken in transcript
    transcript = "Customer Anil. Changed washer for thirty rupees. One hour labour."
    rates = {"labor_hourly": 400}
    job = extract_job(transcript, rates, chat=fake_chat(raw))
    assert job.items[0].price_flag is None
    assert job.items[1].price_flag is None


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
